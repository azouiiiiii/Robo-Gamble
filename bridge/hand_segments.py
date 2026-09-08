"""按人工边界切分学习数据，并以引用方式复用 overlap 事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable, Mapping


MAIN = "main"
PREVIOUS_TAIL = "previous_tail"
NEXT_HEAD = "next_head"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SegmentEventRef:
    event_id: str
    roles: set[str] = field(default_factory=set)

    def add_roles(self, *roles: str) -> None:
        self.roles.update(role for role in roles if role)

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "roles": sorted(self.roles)}


@dataclass
class HandSegment:
    hand_id: str
    boundary_id: str = ""
    started_at: str = field(default_factory=_now_iso)
    ended_at: str = ""
    status: str = "open"
    event_refs: list[SegmentEventRef] = field(default_factory=list)

    def add_event(self, event_id: str, *roles: str) -> None:
        for ref in self.event_refs:
            if ref.event_id == event_id:
                ref.add_roles(*roles)
                return
        self.event_refs.append(SegmentEventRef(event_id, set(roles)))

    @property
    def event_ids(self) -> list[str]:
        return [ref.event_id for ref in self.event_refs]

    def resolve_events(self, raw_events: Mapping[str, dict]) -> list[dict]:
        """按 segment 顺序解析引用；不会复制或修改原始事件对象。"""
        return [raw_events[ref.event_id] for ref in self.event_refs if ref.event_id in raw_events]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hand_id": self.hand_id,
            "boundary_id": self.boundary_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "events": [ref.to_dict() for ref in self.event_refs],
        }


@dataclass(frozen=True)
class HandBoundary:
    boundary_id: str
    key: str
    ingest_sequence: int
    session_sequence: int
    observed_at: str
    monotonic_ns: int
    overlap_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "hand_boundary",
            "boundary_id": self.boundary_id,
            "key": self.key,
            "ingest_sequence": self.ingest_sequence,
            "session_sequence": self.session_sequence,
            "observed_at": self.observed_at,
            "monotonic_ns": self.monotonic_ns,
            "overlap_ms": self.overlap_ms,
        }


class HandSegmenter:
    """线程安全的人工牌局分段器。

    边界键按下后，overlap 窗口内的每个原始事件只进入 ``raw_events`` 一次，
    但会被两个 segment 通过引用分别标记为 ``previous_tail`` 和 ``next_head``。
    到达边界和定时器都可能触发封存，因此网络回调始终只做轻量内存操作。
    """

    def __init__(
        self,
        overlap_ms: int = 1000,
        on_boundary: Callable[[HandBoundary], None] | None = None,
        on_segment: Callable[[HandSegment], None] | None = None,
    ):
        self.overlap_ms = max(0, int(overlap_ms))
        self.on_boundary = on_boundary
        self.on_segment = on_segment
        self.raw_events: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._hand_number = 1
        self._boundary_number = 0
        self._last_ingest_sequence = 0
        self._last_session_sequence = 0
        self._current = HandSegment("hand-001")
        self._previous: HandSegment | None = None
        self._next: HandSegment | None = None
        self._overlap_end_ns = 0
        self._boundary_ingest_sequence = 0
        self._boundary_session_sequence = 0
        self._boundary_monotonic_ns = 0
        self._timer: threading.Timer | None = None
        self._closed = False

    @property
    def current(self) -> HandSegment:
        with self._lock:
            return self._current

    def add_event(self, event: dict[str, Any]) -> None:
        event_id = str(event.get("event_id", "")).strip()
        if not event_id:
            raise ValueError("segment event requires event_id")
        callbacks: list[tuple[Callable, Any]] = []
        with self._lock:
            if self._closed:
                return
            event_time = int(event.get("monotonic_ns", time.monotonic_ns()))
            if self._previous is not None and event_time > self._overlap_end_ns:
                callbacks.extend(self._finish_overlap_locked())
            self.raw_events.setdefault(event_id, event)
            self._last_ingest_sequence = max(
                self._last_ingest_sequence, int(event.get("ingest_sequence", 0))
            )
            self._last_session_sequence = max(
                self._last_session_sequence, int(event.get("session_sequence", 0))
            )
            # 事件信封的观测位置优先于回调实际执行时刻：如果事件在按键前
            # 已被采集，只因线程调度晚到，仍属于上一局主段而不是 overlap。
            # monotonic_ns 在事件产生时写入，优先于 recorder 的落盘顺序：
            # 回调若延迟到按键后执行，也不能把按键前产生的事件误算为 overlap。
            is_observed_after_boundary = event_time > self._boundary_monotonic_ns
            if self._previous is not None and not is_observed_after_boundary:
                # 事件可能已经在边界前采集，只是回调线程稍后才执行。
                self._previous.add_event(event_id, MAIN)
            elif (
                self._previous is not None
                and event_time <= self._overlap_end_ns
            ):
                self._previous.add_event(event_id, PREVIOUS_TAIL)
                self._next.add_event(event_id, NEXT_HEAD)  # type: ignore[union-attr]
            else:
                self._current.add_event(event_id, MAIN)
        self._run_callbacks(callbacks)

    def mark_boundary(self, key: str = "backquote") -> HandBoundary:
        callbacks: list[tuple[Callable, Any]] = []
        with self._lock:
            if self._closed:
                raise RuntimeError("hand segmenter is closed")
            if self._previous is not None:
                callbacks.extend(self._finish_overlap_locked())
            self._boundary_number += 1
            boundary = HandBoundary(
                boundary_id=f"boundary-{self._boundary_number:03d}",
                key=key,
                ingest_sequence=self._last_ingest_sequence,
                session_sequence=self._last_session_sequence,
                observed_at=_now_iso(),
                monotonic_ns=time.monotonic_ns(),
                overlap_ms=self.overlap_ms,
            )
            self._hand_number += 1
            self._previous = self._current
            self._previous.boundary_id = boundary.boundary_id
            self._next = HandSegment(
                hand_id=f"hand-{self._hand_number:03d}",
                boundary_id=boundary.boundary_id,
            )
            self._boundary_ingest_sequence = self._last_ingest_sequence
            self._boundary_session_sequence = self._last_session_sequence
            self._boundary_monotonic_ns = boundary.monotonic_ns
            self._overlap_end_ns = boundary.monotonic_ns + self.overlap_ms * 1_000_000
            if self._timer:
                self._timer.cancel()
            if self.overlap_ms == 0:
                callbacks.extend(self._finish_overlap_locked())
            else:
                self._timer = threading.Timer(
                    self.overlap_ms / 1000, self._finish_overlap
                )
                self._timer.daemon = True
                self._timer.start()
        self._run_callbacks(callbacks)
        if self.on_boundary:
            self.on_boundary(boundary)
        return boundary

    def finish_overlap(self) -> None:
        """立即结束 overlap，主要用于关闭流程和确定性测试。"""
        with self._lock:
            callbacks = self._finish_overlap_locked()
        self._run_callbacks(callbacks)

    def _finish_overlap(self) -> None:
        with self._lock:
            callbacks = self._finish_overlap_locked()
        self._run_callbacks(callbacks)

    def _finish_overlap_locked(self) -> list[tuple[Callable, Any]]:
        if self._previous is None or self._next is None:
            return []
        if self._timer:
            self._timer.cancel()
            self._timer = None
        previous = self._previous
        previous.ended_at = _now_iso()
        previous.status = "completed"
        self._current = self._next
        self._previous = None
        self._next = None
        self._overlap_end_ns = 0
        self._boundary_ingest_sequence = 0
        self._boundary_session_sequence = 0
        self._boundary_monotonic_ns = 0
        return [(self.on_segment, previous)] if self.on_segment else []

    def close(self) -> HandSegment:
        """停止接收数据；已触发边界的上一局仍会正常封存。"""
        with self._lock:
            callbacks = self._finish_overlap_locked()
            self._closed = True
            current = self._current
            if current.status == "open":
                current.ended_at = _now_iso()
                current.status = "incomplete"
        self._run_callbacks(callbacks)
        return current

    @staticmethod
    def _run_callbacks(callbacks: list[tuple[Callable, Any]]) -> None:
        for callback, value in callbacks:
            try:
                callback(value)
            except Exception:
                # 分段和采集不能因学习回调异常而中断。
                continue
