"""网络观测事件信封与局部顺序重建。

这里不假设所有网络流存在一条真实的全局时间线。全局序号只表示采集器
观察到事件的先后；可靠顺序来自同一 stream 的 stream_sequence 和父子关系。
"""

from __future__ import annotations

import heapq
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable


class NetworkEventSequencer:
    """为多种网络采集回调分配统一事件信封。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._ingest_sequence = 0
        self._stream_sequences: dict[str, int] = {}

    def emit(
        self,
        *,
        kind: str,
        stream_id: str,
        stream_type: str,
        payload: Any = None,
        request_id: str = "",
        exchange_id: str = "",
        connection_id: str = "",
        direction: str = "",
        phase: str = "",
        parent_event_id: str = "",
        complete: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stream_id = stream_id or f"unknown:{stream_type}"
        with self._lock:
            self._ingest_sequence += 1
            stream_sequence = self._stream_sequences.get(stream_id, 0) + 1
            self._stream_sequences[stream_id] = stream_sequence
            ingest_sequence = self._ingest_sequence

        return {
            "event_type": "raw_network_event",
            "schema_version": 2,
            "event_id": f"evt-{ingest_sequence}-{uuid.uuid4().hex[:8]}",
            "ingest_sequence": ingest_sequence,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "stream_id": stream_id,
            "stream_type": stream_type,
            "stream_sequence": stream_sequence,
            "request_id": request_id,
            "exchange_id": exchange_id or request_id,
            "connection_id": connection_id,
            "direction": direction,
            "kind": kind,
            "phase": phase,
            "parent_event_id": parent_event_id,
            "complete": complete,
            "payload": payload,
            "metadata": metadata or {},
        }


class NetworkSequenceReconstructor:
    """按可靠的局部关系重建可回放事件序列。"""

    @staticmethod
    def reconstruct(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        items = [event for event in events if isinstance(event, dict)]
        by_id = {event.get("event_id"): event for event in items if event.get("event_id")}
        edges: dict[str, set[str]] = {event_id: set() for event_id in by_id}
        indegree = {event_id: 0 for event_id in by_id}

        # 父事件必须先于子事件，例如 response_headers -> response_body.
        for event_id, event in by_id.items():
            parent_id = event.get("parent_event_id")
            if parent_id in by_id and event_id not in edges[parent_id]:
                edges[parent_id].add(event_id)
                indegree[event_id] += 1

        # 同一 stream 内的顺序可靠；跨 stream 不建立伪因果关系。
        streams: dict[str, list[dict[str, Any]]] = {}
        for event in by_id.values():
            streams.setdefault(str(event.get("stream_id", "")), []).append(event)
        for stream_events in streams.values():
            stream_events.sort(
                key=lambda event: (
                    int(event.get("stream_sequence", 0)),
                    int(event.get("ingest_sequence", 0)),
                )
            )
            for previous, current in zip(stream_events, stream_events[1:]):
                previous_id = previous.get("event_id")
                current_id = current.get("event_id")
                if previous_id in edges and current_id in edges and current_id not in edges[previous_id]:
                    edges[previous_id].add(current_id)
                    indegree[current_id] += 1

        # Kahn 拓扑排序；同层事件用采集顺序稳定排序，仅代表观测先后。
        heap: list[tuple[int, str]] = [
            (int(event.get("ingest_sequence", 0)), event_id)
            for event_id, degree in indegree.items()
            if degree == 0
        ]
        heapq.heapify(heap)
        result: list[dict[str, Any]] = []
        while heap:
            _, event_id = heapq.heappop(heap)
            event = dict(by_id[event_id])
            event["reconstruction_index"] = len(result) + 1
            event["cross_stream_order"] = "observed_sequence_only"
            result.append(event)
            for child_id in edges[event_id]:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    child = by_id[child_id]
                    heapq.heappush(
                        heap,
                        (int(child.get("ingest_sequence", 0)), child_id),
                    )

        # 异常数据形成环时仍可回放，但显式标记，不静默丢失。
        if len(result) < len(by_id):
            remaining = [
                event for event_id, event in by_id.items()
                if event_id not in {item.get("event_id") for item in result}
            ]
            remaining.sort(key=lambda event: int(event.get("ingest_sequence", 0)))
            for event in remaining:
                item = dict(event)
                item["reconstruction_index"] = len(result) + 1
                item["ordering_warning"] = "partial_order_cycle_or_missing_parent"
                result.append(item)

        return result
