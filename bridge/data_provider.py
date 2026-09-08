"""Bridge 侧数据 Provider。

这里暂时包装现有的硬编码聚合器，作为兼容实现。后续只需替换为基于
站点 Profile 的 Provider，Interceptor 和 Agent 编排层无需感知差异。
"""

from __future__ import annotations

from typing import Any

from core.adapter import GameStateParser
from core.data_provider import GameDataSnapshot
from core.mapping_profile import MappingProfile, MappingProfileRegistry

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


class ProfileGameDataProvider:
    """由 MappingProfile 驱动的统一 Provider。

    当前只实现内置 legacy Socket.IO Profile 的执行器；后续学习 Profile
    使用相同接口接入，不需要改动上层 Interceptor 或 Agent 编排。
    """

    def __init__(self, profile: MappingProfile, my_user_id: str, verbose: bool = False):
        if profile.metadata.get("executor") != "legacy_socketio_aggregator":
            raise NotImplementedError(
                f"no executor for mapping profile: {profile.profile_id}"
            )
        self.profile = profile
        self._my_user_id = my_user_id
        self._verbose = verbose
        self._parser = GameStateParser(_LEGACY_FIELD_MAP)
        self.aggregator = GameStateAggregator(my_user_id, verbose=verbose)
        self._snapshot: GameDataSnapshot | None = None

    @classmethod
    def from_registry(
        cls,
        profile_id: str,
        my_user_id: str,
        verbose: bool = False,
        profile_dir: str = "profiles",
    ) -> "ProfileGameDataProvider":
        profile = MappingProfileRegistry(profile_dir).load(profile_id)
        return cls(profile, my_user_id, verbose=verbose)

    @property
    def mapping_version(self) -> str:
        return self.profile.mapping_version

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
        self.aggregator.feed(event)
        raw = self.aggregator.build_game_state()
        state = self._parser.parse(raw)
        self._snapshot = GameDataSnapshot(
            state=state,
            raw=raw,
            source_events=[event],
            mapping_version=self.mapping_version,
            confidence=1.0,
            source_kind="websocket",
            actions_already_observed=True,
        )
        return [self._snapshot]

    def current_snapshot(self) -> GameDataSnapshot | None:
        if self._snapshot is None:
            raw = self.aggregator.build_game_state()
            state = self._parser.parse(raw)
            self._snapshot = GameDataSnapshot(
                state=state,
                raw=raw,
                mapping_version=self.mapping_version,
                confidence=1.0,
                source_kind="websocket",
                actions_already_observed=True,
            )
        return self._snapshot

    def reset(self) -> None:
        self.aggregator.reset()
        self._snapshot = None


class LegacyWebSocketDataProvider(ProfileGameDataProvider):
    """兼容入口：加载内置 legacy_socketio_v1 Profile。"""

    def __init__(self, my_user_id: str, verbose: bool = False, profile_dir: str = "profiles"):
        super().__init__(
            MappingProfileRegistry(profile_dir).load("legacy_socketio_v1"),
            my_user_id,
            verbose=verbose,
        )
