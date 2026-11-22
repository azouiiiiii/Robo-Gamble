"""持久化原始网络事件和人工分段元数据。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading

class SessionRecorder:
    """保存可回放的原始网络事件，并通知分段器。"""

    def __init__(self, directory: str = "learning_sessions", on_event=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = self.directory / f"session_{stamp}.jsonl"
        self._sequence = 0
        self._lock = threading.Lock()
        self.on_event = on_event
        self._file = self.path.open("a", encoding="utf-8")

    def record_event(self, event: dict) -> None:
        with self._lock:
            self._sequence += 1
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
                # 磁盘记录优先；分段回调不能影响网络采集。
                pass

    def record_boundary(self, boundary: dict) -> None:
        payload = dict(boundary)
        payload["event_type"] = "hand_boundary"
        self.record_event(payload)

    def record_segment(self, segment: dict) -> None:
        payload = dict(segment)
        payload["event_type"] = "hand_segment"
        self.record_event(payload)

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._file.close()

    @staticmethod
    def load_records(path: str | Path) -> list[dict]:
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
        return [
            event for event in cls.load_records(path)
            if event.get("event_type") == "raw_network_event"
        ]
