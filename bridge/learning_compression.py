"""学习 Agent 的网络事件输入压缩。

这里不解析应用协议，也不判断字段含义。压缩结果只是原始事件的一个可
回溯视图：每条记录都保留 ``event_ids``，被规则排除的事件也会留下摘要。
"""

from __future__ import annotations

from collections import OrderedDict
import re
from typing import Any, Iterable
from urllib.parse import urlsplit


DEFAULT_EXCLUDE_URL_PATTERNS = [
    r"(?:^|://)(?:www\.)?google-analytics\.com/",
    r"(?:^|://)cdn\.[^/]+/[^?#]+\.(?:mp3|wav|ogg|m4a|png|jpg|jpeg|gif|webp|svg|ico|woff2?|ttf|css)(?:[?#]|$)",
]


def _json_size(value: Any) -> int:
    # 延迟导入，避免压缩器的普通元数据路径依赖 JSON 实现细节。
    import json

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


class LearningInputCompressor:
    """把完整原始事件变成适合 LLM 阅读的、可追溯的紧凑文档。"""

    def __init__(
        self,
        exclude_url_patterns: Iterable[str] | None = None,
        compress_repeated_frames: bool = True,
    ):
        patterns = DEFAULT_EXCLUDE_URL_PATTERNS if exclude_url_patterns is None else exclude_url_patterns
        self.exclude_url_patterns = [re.compile(str(pattern), re.IGNORECASE) for pattern in patterns]
        self.compress_repeated_frames = bool(compress_repeated_frames)

    def compress(self, events: list[dict]) -> dict[str, Any]:
        urls: OrderedDict[str, dict[str, Any]] = OrderedDict()
        headers: OrderedDict[str, dict[str, str]] = OrderedDict()
        records: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        included_events = 0
        original_size = sum(_json_size(event) for event in events)

        # 先按明确的 URL 规则过滤；过滤结果也作为审计信息交给 LLM。
        included: list[dict] = []
        for event in events:
            url = self._url(event)
            reason = self._exclude_reason(url)
            if reason:
                excluded.append({
                    "event_id": event.get("event_id", ""),
                    "url": url,
                    "reason": reason,
                })
                continue
            included.append(event)
            included_events += 1

        # HTTP 交换按 exchange_id 合并，WebSocket 先保留原始记录；因此不会跨流重排。
        http_groups: OrderedDict[str, list[dict]] = OrderedDict()
        for event in included:
            if event.get("stream_type") == "http":
                key = str(event.get("exchange_id") or event.get("request_id") or event.get("event_id"))
                http_groups.setdefault(key, []).append(event)
            else:
                records.append(self._websocket_record(event, urls, headers))

        # 把 HTTP 组按组内第一条事件的位置插入整体序列。
        # 多数情况下原始流先后关系对 LLM 很重要，所以使用 event 的最小 session_sequence 排序。
        for group in http_groups.values():
            records.append(self._http_record(group, urls, headers))
        records.sort(key=self._record_order)
        if self.compress_repeated_frames:
            records = self._compress_repeated_records(records)

        document = {
            "compression": {
                "version": 1,
                "semantics": "lossless_view_with_explicit_url_filters",
                "original_event_count": len(events),
                "included_event_count": included_events,
                "excluded_event_count": len(excluded),
                "original_event_chars": original_size,
            },
            "url_catalog": {
                key: value for key, value in urls.items()
            },
            "header_catalog": {
                key: value for key, value in headers.items()
            },
            "excluded_events": excluded,
            "records": records,
        }
        document["compression"]["compressed_document_chars"] = _json_size(document)
        document["compression"]["reduction_ratio"] = round(
            1 - document["compression"]["compressed_document_chars"] / max(original_size, 1), 4
        )
        return document

    def _http_record(
        self,
        events: list[dict],
        urls: OrderedDict[str, dict[str, Any]],
        headers: OrderedDict[str, dict[str, str]],
    ) -> dict[str, Any]:
        children = []
        for event in events:
            child = self._event_common(event)
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            child.update({
                "url_ref": self._catalog_url(metadata.get("url", ""), urls),
                "request_headers_ref": self._catalog_headers(metadata.get("request_headers", {}), headers),
                "method": metadata.get("method", ""),
                "resource_type": metadata.get("resource_type", ""),
                "payload": event.get("payload"),
            })
            children.append(child)
        return {
            "record_type": "http_exchange",
            "event_ids": [event.get("event_id", "") for event in events],
            "order": min(self._event_order(event) for event in events),
            "exchange_id": events[0].get("exchange_id") or events[0].get("request_id", ""),
            "events": children,
        }

    def _websocket_record(
        self,
        event: dict,
        urls: OrderedDict[str, dict[str, Any]],
        headers: OrderedDict[str, dict[str, str]],
    ) -> dict[str, Any]:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        return {
            "record_type": "websocket_frame",
            "event_ids": [event.get("event_id", "")],
            "order": self._event_order(event),
            "stream_id": event.get("stream_id", ""),
            "stream_sequence": event.get("stream_sequence", 0),
            "observed_at": event.get("observed_at", ""),
            "direction": event.get("direction", ""),
            "url_ref": self._catalog_url(metadata.get("url", ""), urls),
            "payload": event.get("payload"),
        }

    @staticmethod
    def _event_common(event: dict) -> dict[str, Any]:
        return {
            "event_id": event.get("event_id", ""),
            "observed_at": event.get("observed_at", ""),
            "session_sequence": event.get("session_sequence", 0),
            "stream_sequence": event.get("stream_sequence", 0),
            "kind": event.get("kind", ""),
            "phase": event.get("phase", ""),
            "parent_event_id": event.get("parent_event_id", ""),
            "complete": event.get("complete", True),
        }

    @staticmethod
    def _event_order(event: dict) -> int:
        try:
            return int(event.get("session_sequence", event.get("ingest_sequence", 0)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _record_order(cls, record: dict) -> int:
        return int(record.get("order", 0))

    @staticmethod
    def _url(event: dict) -> str:
        metadata = event.get("metadata")
        return str(metadata.get("url", "")) if isinstance(metadata, dict) else ""

    def _exclude_reason(self, url: str) -> str:
        if not url:
            return ""
        for pattern in self.exclude_url_patterns:
            if pattern.search(url):
                return f"url_pattern:{pattern.pattern}"
        return ""

    @staticmethod
    def _catalog_url(url: str, catalog: OrderedDict[str, dict[str, Any]]) -> str:
        if not url:
            return ""
        for key, value in catalog.items():
            if value.get("url") == url:
                return key
        key = f"url-{len(catalog) + 1}"
        parts = urlsplit(url)
        catalog[key] = {
            "url": url,
            "scheme": parts.scheme,
            "host": parts.netloc,
            "path": parts.path,
            "query_present": bool(parts.query),
        }
        return key

    @staticmethod
    def _catalog_headers(value: Any, catalog: OrderedDict[str, dict[str, str]]) -> str:
        if not isinstance(value, dict) or not value:
            return ""
        normalized = {str(key).lower(): str(item) for key, item in sorted(value.items())}
        for key, existing in catalog.items():
            if existing == normalized:
                return key
        key = f"headers-{len(catalog) + 1}"
        catalog[key] = normalized
        return key

    @staticmethod
    def _can_repeat(previous: dict, current: dict) -> bool:
        return (
            previous.get("record_type") == "websocket_frame"
            and current.get("record_type") == "websocket_frame"
            and previous.get("stream_id") == current.get("stream_id")
            and previous.get("direction") == current.get("direction")
            and previous.get("url_ref") == current.get("url_ref")
            and previous.get("payload") == current.get("payload")
            and bool(previous.get("event_ids", []) and current.get("event_ids"))
        )

    @classmethod
    def _compress_repeated_records(cls, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compressed: list[dict[str, Any]] = []
        for record in records:
            if compressed and cls._can_repeat(compressed[-1], record):
                cls._merge_repeat(compressed[-1], record)
            else:
                compressed.append(record)
        return compressed

    @staticmethod
    def _merge_repeat(previous: dict, current: dict) -> None:
        previous.setdefault("repeat_count", 1)
        previous["repeat_count"] += len(current.get("event_ids", []))
        previous["event_ids"].extend(current.get("event_ids", []))
        previous["last_observed_at"] = current.get("observed_at", "")
        previous["last_stream_sequence"] = current.get("stream_sequence", 0)


__all__ = ["LearningInputCompressor", "DEFAULT_EXCLUDE_URL_PATTERNS"]
