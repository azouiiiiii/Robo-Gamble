"""Socket.IO 站点聚合器到统一 GameDataSnapshot 的适配层。"""

from __future__ import annotations

from typing import Optional

from core.adapter import GameStateParser
from core.data_provider import GameDataSnapshot

from .aggregator import GamePhase, GameStateAggregator


_LEGACY_FIELD_MAP = {
    "phase": "current_stage",
    "hand": "my_cards",
    "public_cards": "community_cards",
    "pot": "total_pot",
    "my_chips": "player.stack",
    "to_call": "required_call",
    "my_position": "player.position",
    "opponent_actions": "action_history",
    "opponent_chips": "opponent.stack",
    "table_id": "table.id",
    "round_id": "round.id",
    "showdown": "showdown",
    "network_context": "network_context",
}


class SocketIOGameDataProvider:
    """将逐条 Socket.IO 事件送入站点聚合器并输出统一快照。"""

    provider_version = "socketio-site-aggregator-v1"

    def __init__(self, my_user_id: str, verbose: bool = False):
        self._my_user_id = my_user_id
        self.aggregator = GameStateAggregator(my_user_id, verbose=verbose)
        self._parser = GameStateParser(_LEGACY_FIELD_MAP)
        self._snapshot: Optional[GameDataSnapshot] = None

    @property
    def my_user_id(self) -> str:
        return self.aggregator.my_user_id

    @property
    def players(self) -> dict:
        return self.aggregator.players

    @property
    def action_history(self) -> list[dict]:
        return self.aggregator._action_history

    @property
    def is_my_turn(self) -> bool:
        return self.aggregator.is_my_turn

    @property
    def phase(self) -> GamePhase:
        return self.aggregator.phase

    def feed(self, event: dict) -> list[GameDataSnapshot]:
        """应用共享解码事件；兼容直接传入旧版协议包。"""
        packet = (
            event.get("packet")
            if event.get("event_type") == "decoded_socketio_event"
            else event
        )
        if isinstance(packet, dict):
            self.aggregator.feed(packet)
        self._snapshot = self._build_snapshot(event)
        return [self._snapshot]

    def current_snapshot(self) -> Optional[GameDataSnapshot]:
        if self._snapshot is None:
            self._snapshot = self._build_snapshot()
        return self._snapshot

    def reset(self) -> None:
        self.aggregator.reset()
        self._snapshot = None

    def _build_snapshot(self, event: dict | None = None) -> GameDataSnapshot:
        raw = self.aggregator.build_game_state()
        state = self._parser.parse(raw)
        return GameDataSnapshot(
            state=state,
            raw=raw,
            source_events=[event] if event else [],
            provider_version=self.provider_version,
            source_kind="websocket",
            actions_already_observed=True,
        )
