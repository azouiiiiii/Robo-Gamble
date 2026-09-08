"""统一的网络数据映射 Profile 契约与加载器。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "mapping_profiles"
_USABLE_STATUSES = {"builtin", "verified"}


@dataclass
class MappingProfile:
    """描述一种网络数据流如何映射为统一对局状态。"""

    profile_id: str
    version: int = 1
    source: str = "learned"
    status: str = "draft"
    protocol: str = "unknown"
    host: str = ""
    confidence: float = 0.0
    event_rules: dict[str, Any] = field(default_factory=dict)
    field_mapping: dict[str, Any] = field(default_factory=dict)
    transforms: dict[str, Any] = field(default_factory=dict)
    state_rules: dict[str, Any] = field(default_factory=dict)
    evidence: list[Any] = field(default_factory=list)
    ambiguities: list[Any] = field(default_factory=list)
    validations: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def mapping_version(self) -> str:
        return f"{self.profile_id}:v{self.version}"

    @property
    def is_usable(self) -> bool:
        return self.source == "builtin" or self.status in _USABLE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "source": self.source,
            "status": self.status,
            "protocol": self.protocol,
            "host": self.host,
            "confidence": self.confidence,
            "event_rules": self.event_rules,
            "field_mapping": self.field_mapping,
            "transforms": self.transforms,
            "state_rules": self.state_rules,
            "evidence": self.evidence,
            "ambiguities": self.ambiguities,
            "validations": self.validations,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MappingProfile":
        if not isinstance(data, dict):
            raise ValueError("mapping profile must be an object")
        profile_id = str(data.get("profile_id", "")).strip()
        if not profile_id:
            raise ValueError("mapping profile requires profile_id")
        return cls(
            profile_id=profile_id,
            version=int(data.get("version", 1)),
            source=str(data.get("source", "learned")),
            status=str(data.get("status", "draft")),
            protocol=str(data.get("protocol", "unknown")),
            host=str(data.get("host", "")),
            confidence=float(data.get("confidence", 0.0)),
            event_rules=dict(data.get("event_rules", {})),
            field_mapping=dict(data.get("field_mapping", {})),
            transforms=dict(data.get("transforms", {})),
            state_rules=dict(data.get("state_rules", {})),
            evidence=list(data.get("evidence", [])),
            ambiguities=list(data.get("ambiguities", [])),
            validations=list(data.get("validations", [])),
            metadata=dict(data.get("metadata", {})),
        )


class MappingProfileStore:
    """读写学习得到的 Profile；内置 Profile 由仓库中的 JSON 提供。"""

    def __init__(self, directory: str = "profiles"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(profile_id: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", profile_id) or "unknown"

    def path(self, profile_id: str) -> Path:
        return self.directory / f"{self._safe_name(profile_id)}.json"

    def load(self, profile_id: str) -> MappingProfile | None:
        payload = self.load_dict(profile_id)
        if payload is None:
            return None
        try:
            return MappingProfile.from_dict(payload)
        except (ValueError, TypeError):
            return None

    def load_dict(self, profile_id: str) -> dict[str, Any] | None:
        """读取 canonical Profile JSON，不先转换为 Python 数据模型。"""
        path = self.path(profile_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, TypeError, json.JSONDecodeError):
            return None

    def save(self, profile: MappingProfile) -> Path:
        return self.save_dict(profile.profile_id, profile.to_dict())

    def save_dict(self, profile_id: str, payload: dict[str, Any]) -> Path:
        """原子保存 canonical Profile JSON。"""
        if not isinstance(payload, dict):
            raise TypeError("mapping profile payload must be an object")
        path = self.path(profile_id)
        payload = dict(payload)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        return path


class MappingProfileRegistry:
    """统一管理内置和运行时 Profile。"""

    def __init__(self, directory: str = "profiles"):
        self.store = MappingProfileStore(directory)

    def load(self, profile_id: str) -> MappingProfile:
        runtime = self.store.load(profile_id)
        if runtime is not None:
            return runtime

        path = _BUILTIN_DIR / f"{profile_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"mapping profile not found: {profile_id}")
        try:
            return MappingProfile.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid mapping profile: {profile_id}") from exc
