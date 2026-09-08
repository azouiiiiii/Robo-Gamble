"""学习牌局的有界活动队列和安全溢出缓冲。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
from typing import Any, Callable, Mapping

from .hand_segments import HandSegment


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LearningJob:
    hand_id: str
    segment: HandSegment
    raw_events: Mapping[str, dict]
    source_session_path: str = ""
    status: str = "queued"
    submitted_at: str = field(default_factory=_now_iso)
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    result_profile_id: str = ""
    result_path: str = ""
    result_confidence: float = 0.0

    @property
    def events(self) -> list[dict]:
        return self.segment.resolve_events(self.raw_events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hand_id": self.hand_id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result_profile_id": self.result_profile_id,
            "result_path": self.result_path,
            "result_confidence": self.result_confidence,
            "source_session_path": self.source_session_path,
            "segment": self.segment.to_dict(),
        }


class LearningJobQueue:
    """后台学习队列。

    ``max_queue_size`` 只限制活动队列。活动队列满时转入内存溢出缓冲，
    因而不会为了保护 worker 而丢弃已经持久化的牌局原始数据。
    """

    def __init__(
        self,
        handler: Callable[[LearningJob], None] | None = None,
        max_queue_size: int = 20,
        worker_count: int = 1,
    ):
        self.handler = handler or (lambda job: None)
        self.max_queue_size = max(1, int(max_queue_size))
        self.worker_count = max(1, int(worker_count))
        self._active: deque[LearningJob] = deque()
        self._overflow: deque[LearningJob] = deque()
        self._outstanding = 0
        self._closed = False
        self._condition = threading.Condition()
        self._workers = [
            threading.Thread(target=self._worker, name=f"learning-worker-{i + 1}", daemon=True)
            for i in range(self.worker_count)
        ]
        for worker in self._workers:
            worker.start()

    def submit(self, job: LearningJob) -> bool:
        with self._condition:
            if self._closed:
                return False
            if len(self._active) < self.max_queue_size:
                self._active.append(job)
            else:
                self._overflow.append(job)
            self._outstanding += 1
            self._condition.notify()
            return True

    @property
    def pending(self) -> int:
        with self._condition:
            return self._outstanding

    @property
    def overflow_count(self) -> int:
        with self._condition:
            return len(self._overflow)

    def join(self) -> None:
        with self._condition:
            while self._outstanding:
                self._condition.wait()

    def close(self, wait: bool = True) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if wait:
            for worker in self._workers:
                worker.join()

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._active:
                    if self._closed and self._outstanding == 0:
                        return
                    self._condition.wait()
                job = self._active.popleft()
                if self._overflow:
                    self._active.append(self._overflow.popleft())
            job.status = "running"
            job.started_at = _now_iso()
            try:
                self.handler(job)
                job.status = "completed"
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
            finally:
                job.finished_at = _now_iso()
                with self._condition:
                    self._outstanding -= 1
                    self._condition.notify_all()
