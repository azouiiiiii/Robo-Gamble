"""统一的对局数据获取接口。

数据来源（HTTP API 或 WebSocket 桥接器）都输出 GameDataSnapshot。
上层决策代码只消费 snapshot.state，不依赖具体采集方式。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .gamestate import GameState


@dataclass
class GameDataSnapshot:
    """一次数据解析结果及其可追溯信息。"""

    state: GameState
    raw: dict[str, Any] = field(default_factory=dict)
    source_events: list[dict[str, Any]] = field(default_factory=list)
    provider_version: str = "unknown"
    unresolved_fields: list[str] = field(default_factory=list)
    source_kind: str = "unknown"
    actions_already_observed: bool = False
    observed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def is_valid(self) -> bool:
        """基础结构有效，且数据适配器没有报告未解析字段。"""
        return isinstance(self.state, GameState) and not self.unresolved_fields


class GameDataProvider(Protocol):
    """所有对局数据来源必须实现的最小接口。"""

    def feed(self, event: dict) -> list[GameDataSnapshot]:
        """输入一个原始事件，返回由该事件产生的快照。"""
        ...

    def current_snapshot(self) -> GameDataSnapshot | None:
        ...

    def reset(self) -> None:
        ...


class HttpGameDataProvider:
    """将一次 HTTP 状态 JSON 适配为统一快照。"""

    provider_version = "http-field-map-v1"

    def __init__(self, parser):
        self.parser = parser
        self._snapshot: GameDataSnapshot | None = None

    def feed(self, event: dict) -> list[GameDataSnapshot]:
        state = self.parser.parse(event)
        self._snapshot = GameDataSnapshot(
            state=state,
            raw=event,
            source_events=[event],
            provider_version=self.provider_version,
            source_kind="http",
        )
        return [self._snapshot]

    def current_snapshot(self) -> GameDataSnapshot | None:
        return self._snapshot

    def reset(self) -> None:
        self._snapshot = None
