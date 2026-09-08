"""统一编排 L1/L2/L3 Agent 的输入。

所有 Agent 都从 GameDataSnapshot 的规范化 GameState 消费数据。Bridge 模式下
对手事件通常已经通过 /observe 到达；HTTP 模式则由这里从快照补充路由。
"""

from __future__ import annotations

from core.data_provider import GameDataSnapshot


class AgentOrchestrator:
    """协调对手画像、读牌和最终决策 Agent。"""

    def __init__(self, brain, memory, logger=None):
        self.brain = brain
        self.memory = memory
        self.log = logger
        self._seen_actions: set[tuple] = set()

    def observe_snapshot(self, snapshot: GameDataSnapshot) -> None:
        """将快照中的规范化对手动作送入 L1/L2，避免各 Agent 读取原始帧。"""
        if snapshot.actions_already_observed:
            return

        state = snapshot.state
        for index, action in enumerate(state.opponent_actions):
            opp_id = action.opponent_id or "unknown"
            key = (
                state.table_id,
                state.round_id,
                index,
                opp_id,
                action.action_type,
                action.amount,
                action.phase,
            )
            if key in self._seen_actions:
                continue
            self._seen_actions.add(key)
            self.memory.on_opponent_action(opp_id, [{
                "action_type": action.action_type,
                "amount": action.amount,
                "phase": action.phase,
            }])

        # 防止长期运行时去重集合无限增长；保留最近若干局即可。
        if len(self._seen_actions) > 10000:
            current_round = (state.table_id, state.round_id)
            self._seen_actions = {
                key for key in self._seen_actions
                if key[:2] == current_round
            }

    def decide(self, snapshot: GameDataSnapshot, timeout: float = 5.0) -> dict:
        """统一执行 L1/L2 等待和 L3 最终决策。"""
        self.observe_snapshot(snapshot)
        state = snapshot.state

        if state.phase.name in ("SETTLEMENT", "INIT"):
            for opp_id in self.memory.get_opponent_ids():
                self.memory.on_hand_end(opp_id, state.showdown)

        opponent_context = self.memory.flush_and_wait(timeout=timeout)
        return self.brain.decide(
            snapshot,
            semantic_history="",
            opponent_analysis=opponent_context,
        )

    def shutdown(self):
        self.memory.shutdown()
