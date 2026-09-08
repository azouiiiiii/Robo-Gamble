"""未知网页网络数据的短期上下文和简化站点档案。"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from urllib.parse import urlparse
from typing import Any

from core.mapping_profile import MappingProfileStore
from .network import SiteProfileStore
from .network_events import NetworkEventSequencer, NetworkSequenceReconstructor
from .learning_compression import LearningInputCompressor
from .learning_contract import LearningContract


class NetworkContext:
    """保留最近网络事件，供协议识别和决策上下文使用。"""

    def __init__(self, max_events: int = 100):
        self.events = deque(maxlen=max_events)

    def add(self, event: dict):
        if event.get("event_type") == "network_batch":
            self.events.extend(event.get("events", []))
        else:
            self.events.append(event)

    def snapshot(self) -> list[dict]:
        return list(self.events)

    def clear(self):
        self.events.clear()


def site_host(url: str) -> str:
    return urlparse(url).netloc.lower() or "unknown"


def build_site_profile(context: NetworkContext, host: str) -> dict:
    """生成不依赖具体字段名称的初始档案，后续可由识别器补充 mapping/rules。"""
    def metadata(event):
        if event.get("event_type") == "raw_network_event":
            return event.get("metadata", {})
        return event.get("network", {})

    sources = sorted({
        metadata(event).get("resource_type", "")
        for event in context.snapshot()
        if metadata(event).get("resource_type")
    })
    urls = sorted({
        metadata(event).get("url", "")
        for event in context.snapshot()
        if metadata(event).get("url")
    })
    return {
        "host": host,
        "fingerprint": {"host": host, "resource_types": sources, "url_samples": urls[:20]},
        "mapping": {},
        "rules": {},
    }


class LearningAgent:
    """使用 LLM 从牌局原始网络事件生成 canonical Profile JSON。

    该 Agent 不调用本地协议解析器，也不删除原始字段。代码只负责把完整
    事件按边界分块、要求 LLM 输出未来 Aggregator 可消费的固定结构并保存结果。
    """

    def __init__(
        self,
        client,
        model: str,
        profile_dir: str = "profiles",
        max_prompt_chars: int = 100_000,
        input_compression: bool = True,
        exclude_url_patterns: list[str] | None = None,
        compress_repeated_frames: bool = True,
        request_timeout_seconds: float = 180,
        max_output_tokens: int = 0,
        contract_path: str | None = None,
        verbose: bool = False,
    ):
        self.client = client
        self.model = model
        self.store = MappingProfileStore(profile_dir)
        self.contract = LearningContract.load(contract_path)
        self.max_prompt_chars = max(10_000, int(max_prompt_chars))
        self.compressor = LearningInputCompressor(
            exclude_url_patterns=exclude_url_patterns,
            compress_repeated_frames=compress_repeated_frames,
        )
        self.input_compression = bool(input_compression)
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        # DeepSeek 将 max_tokens 同时用于推理与最终回复。0 表示交给服务端，
        # 由 Prompt 的字段白名单控制最终 JSON 的体积。
        self.max_output_tokens = max(0, int(max_output_tokens))
        self.verbose = verbose

    def __call__(self, job):
        return self.handle(job)

    def handle(self, job):
        profile = self.learn(job)
        event_ids = {
            str(event.get("event_id"))
            for event in job.events
            if event.get("event_id")
        }
        self.contract.validate_profile(profile, event_ids)
        path = self.store.save_dict(profile["profile_id"], profile)
        job.result_profile_id = profile["profile_id"]
        job.result_path = str(path)
        job.result_confidence = profile["confidence"]
        if self.verbose:
            print(
                f"[LearnAgent] 已保存 {profile['profile_id']}:v{profile['version']} "
                f"status={profile['status']} confidence={profile['confidence']:.0%}"
            )
        return profile

    def learn(self, job) -> dict[str, Any]:
        events = list(job.events)
        if not events:
            raise ValueError(f"学习牌局没有原始事件: {job.hand_id}")

        host = self._infer_host(events)
        profile_id = self._profile_id(host)
        event_ids = {
            str(event.get("event_id"))
            for event in events
            if event.get("event_id")
        }
        previous = self.store.load_dict(profile_id)
        compatible_previous = (
            previous if self._uses_decision_contract(previous) else None
        )
        compressed = (
            self.compressor.compress(events)
            if self.input_compression
            else {"records": events, "compression": {"version": 0}}
        )
        shared_context = {
            "compression": compressed.get("compression", {}),
            "url_catalog": compressed.get("url_catalog", {}),
            "header_catalog": compressed.get("header_catalog", {}),
            "excluded_events": compressed.get("excluded_events", []),
        }
        if compatible_previous is not None:
            shared_context["existing_profile"] = self._profile_context(compatible_previous)
        chunks = self._chunk_events(
            compressed.get("records", []),
            reserved_chars=len(json.dumps(shared_context, ensure_ascii=False, separators=(",", ":"))),
        )
        if self.verbose and self.input_compression:
            metrics = compressed["compression"]
            print(
                f"[LearnAgent] 输入压缩: {metrics['original_event_chars']} -> "
                f"{metrics['compressed_document_chars']} chars, "
                f"排除={metrics['excluded_event_count']}, 分块={len(chunks)}"
            )
        partial_results = []
        for index, chunk in enumerate(chunks, 1):
            partial_results.append(self._ask_llm(
                job=job,
                events=chunk,
                shared_context=shared_context,
                chunk_index=index,
                chunk_count=len(chunks),
                event_ids=event_ids,
                incremental=compatible_previous is not None,
            ))

        result = self._merge_results(partial_results)
        if compatible_previous is not None:
            result = self._merge_with_previous(compatible_previous, result)
        result["evidence"] = self._append_evidence(
            result.get("evidence", []), len(events), job
        )

        metadata = {
            "learner": "llm_v1",
            "model": self.model,
            "hand_id": job.hand_id,
            "source_event_count": len(events),
            "source_session_path": getattr(job, "source_session_path", ""),
            "chunk_count": len(chunks),
            "raw_events_are_authoritative": True,
            "input_compression": compressed.get("compression", {}),
            "profile_contract": self.contract.contract_id,
            "profile_contract_version": self.contract.version,
            "analysis_mode": "incremental_validation" if compatible_previous else "initial_discovery",
        }
        if previous is not None:
            metadata["previous_mapping_version"] = (
                f"{previous.get('profile_id', profile_id)}:v{previous.get('version', 1)}"
            )

        return {
            "profile_id": profile_id,
            "version": (int(previous.get("version", 0)) + 1) if previous else 1,
            "source": "learned",
            "status": "draft",
            "protocol": self._merged_protocol(compatible_previous, result),
            "host": host,
            "confidence": self._confidence(result),
            "event_rules": self._as_dict(result.get("event_rules")),
            "field_mapping": self._as_dict(result.get("field_mapping")),
            "transforms": self._as_dict(result.get("transforms")),
            "state_rules": self._as_dict(result.get("state_rules")),
            "evidence": result["evidence"],
            "ambiguities": self._as_list(result.get("ambiguities")),
            "validations": self._as_list(result.get("validations")),
            "metadata": metadata,
        }

    def _ask_llm(self, *, job, events: list[dict], chunk_index: int,
                 chunk_count: int, shared_context: dict | None = None,
                 event_ids: set[str] | None = None,
                 incremental: bool = False) -> dict:
        if self.client is None:
            raise RuntimeError("学习 Agent 没有配置 LLM client")

        payload = {
            "hand_id": job.hand_id,
            "chunk": {"index": chunk_index, "count": chunk_count},
            "profile_aggregator_contract": self.contract.prompt_contract(),
            **(shared_context or {}),
            "records": events,
        }
        prompt = (
            "你是扑克决策状态映射学习 Agent。\n"
            "请直接阅读下面由完整原始网络事件生成的可回溯压缩视图，不要假设它一定是 Socket.IO。\n"
            "records 中的 event_ids 指向原始事件；url_catalog 和 header_catalog 是引用目录；"
            "excluded_events 是按明确 URL 规则排除的原始事件摘要。\n"
            "先使用契约中的 scenarios 和 targets 在内部判断牌局生命周期、阶段顺序、字段形状和前后状态变化；"
            "再输出映射。不要只按字段名或出现频率匹配。尤其不要把 hand_identifier 当成 hand，"
            "不要要求 settlement 或 new_hand 必须存在同名网络事件；它们可以由状态转换推导。\n"
            + ("当前只做增量验证：请用 existing_profile 检查当前牌局支持、冲突、未观察到或新发现的契约字段和规则；不要重复抄写未被当前牌局触及的旧内容。\n" if incremental else "当前没有兼容的旧 Profile，请只发现 profile_aggregator_contract 中列出的决策字段和状态规则。\n")
            + "只输出 profile_aggregator_contract 中的字段、规则和白名单转换。showdown 是可选能力，"
            "平台无法提供摊牌时不要强行补齐。不要输出 table_id、round_id、game_id、玩家昵称、版本号、"
            "时间戳、HTTP 调试信息或其他非决策字段。"
            "不能从当前原始数据证明的项必须省略或写 unknown，不能猜测。\n"
            "字段和规则必须使用声明式结构，禁止 description、自然语言伪规则、任意代码或未列出的操作。每项最多保留 2 条简短证据，证据只写当前 records 中的 event_id。输出总长度必须少于 2500 个 token。\n"
            "输出纯 JSON，不要 Markdown，不要解释文字。\n\n"
            "输出结构（这是未来 Aggregator 可直接消费的 Profile 片段）：\n"
            "{\n"
            '  "protocol": "socketio|unknown",\n'
            '  "field_mapping": {"phase": {"source": {"event_rule": "state_snapshot", "path": "data.phase"}, "transform": "identity", "evidence": ["event_id"]}},\n'
            '  "event_rules": {"state_snapshot": {"match": {"event_name": "gC"}, "emits": "state_patch", "evidence": ["event_id"]}},\n'
            '  "transforms": {"card_encoding": {"kind": "card_encoding", "params": {}, "evidence": ["event_id"]}},\n'
            '  "state_rules": {"to_call": {"op": "subtract_clamped", "args": ["previous_bet_size", "hero_action_size"], "evidence": ["event_id"]}},\n'
            '  "ambiguities": [{"topic":"only decision-relevant unknown","alternatives":[],"reason":"..."}],\n'
            '  "validations": [{"target":"field_mapping.x|state_rules.x","status":"supported|conflict|not_observed|new","confidence":0.0,"evidence":["event_id"],"reason":"..."}],\n'
            '  "confidence": 0.0\n'
            "}\n\n"
            "压缩后的原始事件视图 JSON：\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        request = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你只输出符合要求的纯 JSON。原始事件必须视为唯一事实来源。",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "timeout": self.request_timeout_seconds,
        }
        if self.max_output_tokens:
            request["max_tokens"] = self.max_output_tokens
        response = self.client.chat.completions.create(
            **request,
        )
        choice = response.choices[0]
        raw = choice.message.content
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                "学习 Agent 返回空内容 "
                f"(finish_reason={getattr(choice, 'finish_reason', 'unknown')})"
            )
        result = self._parse_json(raw)
        if not isinstance(result, dict):
            raise ValueError("学习 Agent 返回的不是 JSON object")
        self.contract.validate_fragment(
            result,
            event_ids=event_ids or {
                str(event.get("event_id"))
                for event in events
                if event.get("event_id")
            },
        )
        return self._normalize_result(result)

    def _chunk_events(self, events: list[dict], reserved_chars: int = 0) -> list[list[dict]]:
        chunks = []
        current = []
        current_size = 0
        budget = max(10_000, self.max_prompt_chars - int(reserved_chars) - 3_000)
        for event in events:
            event_size = len(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            if current and current_size + event_size > budget:
                chunks.append(current)
                current = []
                current_size = 0
            current.append(event)
            current_size += event_size
        if current:
            chunks.append(current)
        return chunks

    @classmethod
    def _normalize_result(cls, result: dict) -> dict:
        # 原始结果已由 LearningContract 校验；这里不再丢弃字段或隐式修复类型。
        return dict(result)

    @staticmethod
    def _profile_context(profile: dict) -> dict:
        """只发送旧 Profile 的紧凑摘要，不重复发送旧牌局原始事件。"""
        return {
            "mapping_version": f"{profile.get('profile_id', '')}:v{profile.get('version', 1)}",
            "protocol": profile.get("protocol", "unknown"),
            "host": profile.get("host", ""),
            "confidence": profile.get("confidence", 0.0),
            "field_mapping": profile.get("field_mapping", {}),
            "event_rules": profile.get("event_rules", {}),
            "transforms": profile.get("transforms", {}),
            "state_rules": profile.get("state_rules", {}),
            "ambiguities": profile.get("ambiguities", [])[-20:],
            "validations": profile.get("validations", [])[-50:],
        }

    @classmethod
    def _merged_protocol(cls, previous: dict | None, result: dict) -> str:
        candidate = cls._protocol_label(result)
        if candidate.lower() != "unknown":
            return candidate
        return previous.get("protocol", "unknown") if previous is not None else "unknown"

    @classmethod
    def _merge_with_previous(cls, previous: dict, result: dict) -> dict:
        """保留旧映射；新候选只在无冲突时追加，冲突显式记录。"""
        merged = {
            "protocol": result.get("protocol", "unknown"),
            "field_mapping": {
                name: cls._strip_evidence(value)
                for name, value in previous.get("field_mapping", {}).items()
            },
            "event_rules": {
                name: cls._strip_evidence(value)
                for name, value in previous.get("event_rules", {}).items()
            },
            "transforms": {
                name: cls._strip_evidence(value)
                for name, value in previous.get("transforms", {}).items()
            },
            "state_rules": {
                name: cls._strip_evidence(value)
                for name, value in previous.get("state_rules", {}).items()
            },
            "evidence": list(result.get("evidence", [])),
            "ambiguities": list(result.get("ambiguities", [])),
            "validations": list(result.get("validations", [])),
            "confidence": max(previous.get("confidence", 0.0), result.get("confidence", 0.0)),
        }
        for key in ("field_mapping", "event_rules", "transforms", "state_rules"):
            for name, value in result.get(key, {}).items():
                if name not in merged[key]:
                    merged[key][name] = value
                elif merged[key][name] != value:
                    merged["ambiguities"].append({
                        "topic": f"{key}.{name}",
                        "alternatives": [merged[key][name], value],
                        "reason": "当前牌局与已有 Profile 候选不一致，保留旧值并标记冲突",
                    })
        return merged

    @classmethod
    def _strip_evidence(cls, value: Any) -> Any:
        """旧 Profile 的证据属于历史输入，不冒充当前牌局证据。"""
        if isinstance(value, dict):
            return {
                key: cls._strip_evidence(child)
                for key, child in value.items()
                if key != "evidence"
            }
        if isinstance(value, list):
            return [cls._strip_evidence(child) for child in value]
        return value

    @classmethod
    def _merge_results(cls, results: list[dict]) -> dict:
        merged = {
            "protocol": "unknown",
            "field_mapping": {},
            "event_rules": {},
            "transforms": {},
            "state_rules": {},
            "evidence": [],
            "ambiguities": [],
            "confidence": 0.0,
            "validations": [],
        }
        for result in results:
            candidate_protocol = result.get("protocol", "unknown")
            if candidate_protocol and candidate_protocol != "unknown":
                if merged["protocol"] not in ("unknown", candidate_protocol):
                    merged["ambiguities"].append({
                        "topic": "protocol",
                        "alternatives": [merged["protocol"], candidate_protocol],
                        "reason": "不同原始事件分块给出了不同协议候选",
                    })
                else:
                    merged["protocol"] = candidate_protocol
            for key in ("evidence", "ambiguities", "validations"):
                value = result.get(key, [])
                if isinstance(value, list):
                    merged[key].extend(value)
            for key in ("field_mapping", "event_rules", "transforms", "state_rules"):
                for name, value in result.get(key, {}).items():
                    if name not in merged[key]:
                        merged[key][name] = value
                    elif merged[key][name] != value:
                        merged["ambiguities"].append({
                            "topic": f"{key}.{name}",
                            "alternatives": [merged[key][name], value],
                            "reason": "不同原始事件分块给出了不同候选",
                        })
            merged["confidence"] = max(merged["confidence"], result.get("confidence", 0.0))
        return merged

    @staticmethod
    def _parse_json(text: str) -> dict:
        if not isinstance(text, str):
            raise ValueError("学习 Agent 返回内容为空")
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"学习 Agent 返回非法 JSON: {exc}") from exc

    @staticmethod
    def _as_dict(value: Any) -> dict:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _as_list(value: Any) -> list:
        return value if isinstance(value, list) else ([] if value is None else [value])

    @staticmethod
    def _infer_host(events: list[dict]) -> str:
        for event in events:
            metadata = event.get("metadata", {})
            if isinstance(metadata, dict):
                for key in ("page_url", "url"):
                    host = site_host(str(metadata.get(key, "")))
                    if host != "unknown":
                        return host
        return "unknown"

    @staticmethod
    def _profile_id(host: str) -> str:
        safe = "".join(c if c.isalnum() or c in "_.-" else "_" for c in host)
        return f"learned_{safe or 'unknown'}"

    @staticmethod
    def _uses_decision_contract(profile: dict | None) -> bool:
        return bool(
            profile
            and profile.get("metadata", {}).get("profile_contract") == "profile_aggregator_v1"
        )

    @staticmethod
    def _protocol_label(result: dict) -> str:
        return str(result.get("protocol", "unknown") or "unknown")

    @staticmethod
    def _confidence(result: dict) -> float:
        try:
            return max(0.0, min(1.0, float(result.get("confidence", 0.0))))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _append_evidence(evidence: Any, event_count: int, job) -> list:
        result = list(evidence) if isinstance(evidence, list) else []
        result.append({
            "type": "source_segment",
            "hand_id": job.hand_id,
            "event_count": event_count,
            "source_session_path": getattr(job, "source_session_path", ""),
        })
        return result


def create_learning_agent(config, client=None, verbose: bool = False):
    """从运行配置创建学习 Agent；client 可由测试注入。"""
    if client is None:
        from openai import OpenAI

        client = OpenAI(
            api_key=config.get("ai.api_key"),
            base_url=config.get("ai.base_url"),
        )
    return LearningAgent(
        client=client,
        model=config.get("learning.model", config.get("ai.model")),
        profile_dir=config.get("learning.profile_dir", "profiles"),
        max_prompt_chars=config.get("learning.max_prompt_chars", 100_000),
        input_compression=config.get("learning.input_compression.enabled", True),
        exclude_url_patterns=config.get("learning.input_compression.exclude_url_patterns"),
        compress_repeated_frames=config.get(
            "learning.input_compression.compress_repeated_frames", True
        ),
        request_timeout_seconds=config.get("learning.request_timeout_seconds", 180),
        max_output_tokens=config.get("learning.max_output_tokens", 0),
        contract_path=config.get("learning.contract_path"),
        verbose=verbose,
    )


class NetworkDecisionContext:
    """将原始网络上下文附加到一次结构化决策请求。"""

    def __init__(self, max_events: int = 40, profile_dir: str = "profiles",
                 recorder=None):
        self.context = NetworkContext(max_events)
        self.profile_store = SiteProfileStore(profile_dir)
        self.profile: dict = {}
        self.host = ""
        self.recorder = recorder

    def observe(self, event: dict):
        self.context.add(event)
        if self.recorder:
            self.recorder.record_event(event)

    def enrich(self, game_state: dict) -> dict:
        enriched = dict(game_state)
        enriched["network_context"] = self.context.snapshot()
        return enriched

    def load_profile(self, page_url: str) -> dict:
        self.host = site_host(page_url)
        self.profile = self.profile_store.load(self.host)
        return self.profile

    def save_initial_profile(self, page_url: str):
        host = site_host(page_url)
        profile = build_site_profile(self.context, host)
        self.profile_store.save(host, profile)
        self.profile = profile
        self.host = host
        return profile


class LearningSessionRecorder:
    """学习模式的原始事件记录器。

    每条记录保留顺序、类型和观测时间，供后续学习 Agent 或离线回放使用。
    它不参与决策，也不修改事件内容。
    """

    def __init__(self, directory: str = "learning_sessions", on_event=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = self.directory / f"session_{stamp}.jsonl"
        self._sequence = 0
        self._lock = threading.Lock()
        self.on_event = on_event
        self.sequencer = NetworkEventSequencer()
        self._file = self.path.open("a", encoding="utf-8")

    def record(self, kind: str, payload: dict):
        """兼容旧调用；新采集器应直接传入统一事件。"""
        if isinstance(payload, dict) and payload.get("event_type") == "raw_network_event":
            self.record_event(payload)
            return
        event = self.sequencer.emit(
            kind=kind,
            stream_id=f"legacy:{kind}",
            stream_type=kind,
            payload=payload,
        )
        self.record_event(event)

    def record_event(self, event: dict):
        with self._lock:
            self._sequence += 1
            # 各采集器可能有自己的 stream/ingest 序号；session_sequence
            # 是本次落盘会话内唯一的跨 HTTP/WebSocket 观测顺序。
            persisted_event = dict(event)
            persisted_event["session_sequence"] = self._sequence
            row = {
                "sequence": self._sequence,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "event": persisted_event,
            }
            self._file.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._file.flush()
        if self.on_event and persisted_event.get("event_type") == "raw_network_event":
            try:
                self.on_event(persisted_event)
            except Exception:
                # 原始数据已经安全落盘，分段/学习回调不能影响采集。
                pass

    def record_websocket(self, *, direction: str, url: str, payload: str,
                         connection_id: str):
        event = self.sequencer.emit(
            kind="frame",
            stream_id=f"websocket:{connection_id}",
            stream_type="websocket",
            connection_id=connection_id,
            direction=direction,
            payload=payload,
            metadata={"url": url},
        )
        self.record_event(event)

    def record_boundary(self, boundary: dict):
        """持久化人工边界元数据。"""
        payload = dict(boundary)
        payload["event_type"] = "hand_boundary"
        self.record_event(payload)

    def record_segment(self, segment: dict):
        """持久化牌局分段及其事件引用。"""
        payload = dict(segment)
        payload["event_type"] = "hand_segment"
        self.record_event(payload)

    def record_learning_result(self, result: dict):
        """持久化学习任务结果，关联原始 session 与保存的 canonical Profile JSON。"""
        payload = dict(result)
        payload["event_type"] = "learning_result"
        self.record_event(payload)

    @staticmethod
    def load_records(path: str | Path) -> list[dict]:
        """读取 session 中的全部记录（原始事件、边界和 segment 元数据）。"""
        records = []
        with Path(path).open("r", encoding="utf-8") as file:
            for line in file:
                row = json.loads(line)
                event = row.get("event")
                if isinstance(event, dict):
                    records.append(event)
        return records

    @classmethod
    def load_events(cls, path: str | Path) -> list[dict]:
        """读取可回放的原始网络事件，过滤 session 控制记录。"""
        return [
            event for event in cls.load_records(path)
            if event.get("event_type") == "raw_network_event"
        ]

    @classmethod
    def load_segments(cls, path: str | Path) -> list[dict]:
        return [
            event for event in cls.load_records(path)
            if event.get("event_type") == "hand_segment"
        ]

    @classmethod
    def load_boundaries(cls, path: str | Path) -> list[dict]:
        return [
            event for event in cls.load_records(path)
            if event.get("event_type") == "hand_boundary"
        ]

    @classmethod
    def load_learning_results(cls, path: str | Path) -> list[dict]:
        return [
            event for event in cls.load_records(path)
            if event.get("event_type") == "learning_result"
        ]

    @staticmethod
    def _load_legacy_events(path: str | Path) -> list[dict]:
        events = []
        with Path(path).open("r", encoding="utf-8") as file:
            for line in file:
                row = json.loads(line)
                event = row.get("event")
                if isinstance(event, dict):
                    events.append(event)
        return events

    @classmethod
    def reconstruct_file(cls, path: str | Path) -> list[dict]:
        return NetworkSequenceReconstructor.reconstruct(cls.load_events(path))

    def close(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()
