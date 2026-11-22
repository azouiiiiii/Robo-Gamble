"""短期网络事件上下文和会话记录连接。"""

from __future__ import annotations

from collections import deque


class NetworkContext:
    def __init__(self, max_events: int = 100):
        self.events = deque(maxlen=max_events)

    def add(self, event: dict) -> None:
        if event.get("event_type") == "network_batch":
            self.events.extend(event.get("events", []))
        else:
            self.events.append(event)

    def snapshot(self) -> list[dict]:
        return list(self.events)

    def clear(self) -> None:
        self.events.clear()


class NetworkEventContext:
    """为决策保留短期网络上下文，并在采集模式下写入会话。"""

    def __init__(self, max_events: int = 40, recorder=None):
        self.context = NetworkContext(max_events)
        self.recorder = recorder

    def observe(self, event: dict) -> None:
        self.context.add(event)
        if self.recorder:
            self.recorder.record_event(event)

    def enrich(self, game_state: dict) -> dict:
        enriched = dict(game_state)
        enriched["network_context"] = self.context.snapshot()
        return enriched
