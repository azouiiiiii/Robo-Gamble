"""学习 Profile 的结构与语义契约。

契约描述的是未来 ProfileDrivenAggregator 的输入、映射和状态规则，
不是给人阅读的自然语言分析报告。
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterable

import yaml
try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - dependencies are installed from requirements.txt
    Draft202012Validator = None  # type: ignore[assignment,misc]


_DEFAULT_CONTRACT_PATH = Path(__file__).resolve().parent.parent / "learning_contract.yaml"
_EVENT_ID_RE = re.compile(r"^evt-[A-Za-z0-9_-]+$")
_PATH_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|\*))|"
    r"(?:\[(?:\d+|\*|[A-Za-z_][A-Za-z0-9_]*)\]))*$"
)
_DANGEROUS_RE = re.compile(
    r"(?:\b(?:eval|exec|lambda|import|__import__|subprocess|os\.system)\b|"
    r"\b(?:if|else|when|because|otherwise|looks like)\b|[();{}])",
    re.IGNORECASE,
)


class ProfileContractError(ValueError):
    """Profile 不符合契约时抛出，errors 保留可记录的结构化原因。"""

    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        message = "; ".join(
            f"{item.get('path', '$')}: {item.get('message', 'invalid')}"
            for item in errors
        ) or "profile contract validation failed"
        super().__init__(message)


class LearningContract:
    """加载 YAML 契约并校验 Profile 文档或分块结果。"""

    def __init__(self, document: dict[str, Any], path: str | Path = _DEFAULT_CONTRACT_PATH):
        if not isinstance(document, dict):
            raise ValueError("learning contract must be a mapping")
        if Draft202012Validator is None:
            raise RuntimeError(
                "learning contract validation requires jsonschema; "
                "install dependencies from requirements.txt"
            )
        self.document = document
        self.path = Path(path)
        self.version = int(document.get("version", 0))
        self.contract_id = str(document.get("contract_id", ""))
        self.field_names = set(document.get("field_mapping", {}).get("allowed", []))
        self.state_rule_names = set(document.get("state_rules", {}).get("allowed", []))
        self.required_fields = set(
            document.get("profile", {}).get("required_field_mapping", [])
        )
        self.required_state_rules = set(
            document.get("profile", {}).get("required_state_rules", [])
        )
        self.allowed_transforms = set(
            document.get("transforms", {}).get("allowed_kinds", [])
        )
        self.allowed_ops = set(document.get("state_rules", {}).get("allowed_ops", []))
        self.allowed_match_keys = set(
            document.get("event_rules", {}).get("allowed_match_keys", [])
        )
        self.allowed_emits = set(
            document.get("event_rules", {}).get("allowed_emits", [])
        )
        self._profile_validator = Draft202012Validator(
            document.get("schemas", {}).get("profile", {})
        )
        profile_schema = document.get("schemas", {}).get("profile", {})
        fragment_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                name: profile_schema.get("properties", {}).get(name, {})
                for name in (
                    "protocol", "event_rules", "field_mapping", "transforms",
                    "state_rules", "evidence", "ambiguities", "validations",
                    "confidence",
                )
            },
            "$defs": profile_schema.get("$defs", {}),
        }
        self._fragment_validator = Draft202012Validator(fragment_schema)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LearningContract":
        contract_path = Path(path) if path else _DEFAULT_CONTRACT_PATH
        if not contract_path.is_absolute() and not contract_path.exists():
            contract_path = _DEFAULT_CONTRACT_PATH.parent / contract_path
        document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        return cls(document, contract_path)

    def prompt_contract(self) -> dict[str, Any]:
        """给 LLM 的紧凑契约，避免 prompt 自己复制一套白名单。"""
        return {
            "contract_id": self.contract_id,
            "version": self.version,
            "runtime": self.document.get("runtime", {}),
            "analysis_guidance": self.document.get("analysis_guidance", {}),
            "scenarios": self.document.get("scenarios", {}),
            "targets": self.document.get("targets", {}),
            "evidence_policy": self.document.get("evidence_policy", {}),
            "profile": self.document.get("profile", {}),
            "field_mapping": self.document.get("field_mapping", {}),
            "state_rules": self.document.get("state_rules", {}),
            "transforms": self.document.get("transforms", {}),
            "event_rules": self.document.get("event_rules", {}),
        }

    def validate_fragment(
        self, result: dict[str, Any], event_ids: Iterable[str] | None = None
    ) -> None:
        """校验单个 chunk 的 Profile 片段，不要求完整 Profile 字段。"""
        if not isinstance(result, dict):
            raise ProfileContractError([{"path": "$", "message": "must be an object"}])
        errors = [
            {
                "path": ".".join(str(part) for part in error.path) or "$",
                "message": error.message,
            }
            for error in self._fragment_validator.iter_errors(result)
        ]
        self._check_common_semantics(result, set(event_ids or ()), errors)
        if errors:
            raise ProfileContractError(errors)

    def validate_profile(
        self, profile: dict[str, Any], event_ids: Iterable[str] | None = None
    ) -> None:
        """校验最终可被未来 ProfileDrivenAggregator 消费的 Profile 文档。"""
        errors = [
            {
                "path": ".".join(str(part) for part in error.path) or "$",
                "message": error.message,
            }
            for error in self._profile_validator.iter_errors(profile)
        ]
        self._check_common_semantics(profile, set(event_ids or ()), errors)

        missing_fields = self.required_fields - set(profile.get("field_mapping", {}))
        for name in sorted(missing_fields):
            errors.append({
                "path": f"field_mapping.{name}",
                "message": "required aggregator input mapping is missing",
            })
        missing_rules = self.required_state_rules - set(profile.get("state_rules", {}))
        for name in sorted(missing_rules):
            errors.append({
                "path": f"state_rules.{name}",
                "message": "required state rule is missing",
            })
        if errors:
            raise ProfileContractError(errors)

    def _check_common_semantics(
        self, document: dict[str, Any], event_ids: set[str], errors: list[dict[str, Any]]
    ) -> None:
        field_mapping = document.get("field_mapping", {})
        event_rules = document.get("event_rules", {})
        transforms = document.get("transforms", {})
        state_rules = document.get("state_rules", {})

        if isinstance(field_mapping, dict):
            for name, entry in field_mapping.items():
                if name not in self.field_names:
                    errors.append({
                        "path": f"field_mapping.{name}",
                        "message": "field is outside the decision-state contract",
                    })
                    continue
                if not isinstance(entry, dict):
                    continue
                source = entry.get("source", {})
                if isinstance(source, dict):
                    rule_name = source.get("event_rule")
                    if rule_name and rule_name not in event_rules:
                        errors.append({
                            "path": f"field_mapping.{name}.source.event_rule",
                            "message": f"unknown event rule: {rule_name}",
                        })
                    self._check_path(
                        source.get("path"), f"field_mapping.{name}.source.path", errors
                    )
                transform_name = entry.get("transform")
                if transform_name:
                    transform_entry = transforms.get(transform_name, {})
                    if transform_name not in transforms and transform_name not in {
                        "identity", "number", "enum", "card_encoding",
                        "player_lookup", "list_filter",
                    }:
                        errors.append({
                            "path": f"field_mapping.{name}.transform",
                            "message": f"unsupported transform: {transform_name}",
                        })
                    elif isinstance(transform_entry, dict):
                        kind = transform_entry.get("kind")
                        if kind and kind not in self.allowed_transforms:
                            errors.append({
                                "path": f"transforms.{transform_name}.kind",
                                "message": f"unsupported transform kind: {kind}",
                            })

        if isinstance(event_rules, dict):
            for name, rule in event_rules.items():
                if not isinstance(rule, dict):
                    continue
                match = rule.get("match")
                if not isinstance(match, dict) or not match:
                    errors.append({
                        "path": f"event_rules.{name}.match",
                        "message": "event rule requires a non-empty match condition",
                    })
                else:
                    unknown = set(match) - self.allowed_match_keys
                    if unknown:
                        errors.append({
                            "path": f"event_rules.{name}.match",
                            "message": f"unsupported match keys: {sorted(unknown)}",
                        })
                if rule.get("emits") not in self.allowed_emits:
                    errors.append({
                        "path": f"event_rules.{name}.emits",
                        "message": f"unsupported event output: {rule.get('emits')}",
                    })

        declared_fields = set(field_mapping) | self.state_rule_names
        if isinstance(state_rules, dict):
            for name, rule in state_rules.items():
                if name not in self.state_rule_names:
                    errors.append({
                        "path": f"state_rules.{name}",
                        "message": "state rule is outside the contract",
                    })
                if not isinstance(rule, dict):
                    continue
                op = rule.get("op")
                if op not in self.allowed_ops:
                    errors.append({
                        "path": f"state_rules.{name}.op",
                        "message": f"unsupported state operation: {op}",
                    })
                event_rule = rule.get("event_rule")
                if event_rule and event_rule not in event_rules:
                    errors.append({
                        "path": f"state_rules.{name}.event_rule",
                        "message": f"unknown event rule: {event_rule}",
                    })
                source = rule.get("source")
                if source and source not in declared_fields:
                    errors.append({
                        "path": f"state_rules.{name}.source",
                        "message": f"references undeclared field: {source}",
                    })
                for index, arg in enumerate(rule.get("args", [])):
                    if arg not in declared_fields:
                        errors.append({
                            "path": f"state_rules.{name}.args[{index}]",
                            "message": f"references undeclared field: {arg}",
                        })

        self._check_evidence(document, event_ids, errors)
        self._check_conflicts(document, errors)

    @staticmethod
    def _check_path(value: Any, path: str, errors: list[dict[str, Any]]) -> None:
        if not isinstance(value, str) or not _PATH_RE.fullmatch(value):
            errors.append({
                "path": path,
                "message": "path must use the supported declarative path syntax",
            })
        elif _DANGEROUS_RE.search(value):
            errors.append({"path": path, "message": "natural-language or code rule is not allowed"})

    @staticmethod
    def _check_evidence(
        document: dict[str, Any], event_ids: set[str], errors: list[dict[str, Any]]
    ) -> None:
        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "evidence" and isinstance(child, list):
                        for index, item in enumerate(child):
                            if isinstance(item, str):
                                if _EVENT_ID_RE.fullmatch(item) and item not in event_ids:
                                    errors.append({
                                        "path": f"{path}.evidence[{index}]",
                                        "message": f"event_id not found in current input: {item}",
                                    })
                            elif isinstance(item, dict) and "event_id" in item:
                                event_id = item.get("event_id")
                                if event_id not in event_ids:
                                    errors.append({
                                        "path": f"{path}.evidence[{index}].event_id",
                                        "message": f"event_id not found in current input: {event_id}",
                                    })
                    walk(child, f"{path}.{key}")
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk(child, f"{path}[{index}]")

        walk(document, "$")

    @staticmethod
    def _check_conflicts(document: dict[str, Any], errors: list[dict[str, Any]]) -> None:
        validations = document.get("validations", [])
        if not isinstance(validations, list):
            return
        for index, validation in enumerate(validations):
            if isinstance(validation, dict) and validation.get("status") == "conflict":
                errors.append({
                    "path": f"validations[{index}]",
                    "message": "conflicting field or rule cannot be saved",
                })


def load_learning_contract(path: str | Path | None = None) -> LearningContract:
    return LearningContract.load(path)
