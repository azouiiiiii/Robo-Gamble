"""
德州扑克 AI 决策引擎 — 三层并行架构。

启动: python main.py → HTTP 服务器监听

三层流水线:
  1. L1 Opponent Profile Workers (x3) — 对手画像 (on_opponent_action + hand_end 触发)
  2. L2 Hand Reader Workers (x3) — 对手读牌 (on_opponent_action 触发)
  3. L3 DecisionAgent          — 等 L1+L2 完成后整合 prompt, 开 thinking 决策

L1 和 L2 并行消费对手队列。每批数据有两个标签:
  profile_written + hand_read 都为 True → 数据移出缓冲区
"""

import sys
from utils.config_manager import Config
from utils.logger import setup_logger
from server.multi_memory import MultiOpponentMemory
from server.decision import PokerDecision
from server.agent_orchestrator import AgentOrchestrator
from core.data_provider import GameDataSnapshot
from core.adapter import create_adapter

log = setup_logger()


def main():
    log.info("德州扑克 AI 决策引擎启动中 (三层并行架构)")

    try:
        cfg = Config("config.json")
    except FileNotFoundError as e:
        log.critical(f"找不到配置文件: {e}")
        sys.exit(1)

    brain = PokerDecision(cfg)
    memory = MultiOpponentMemory(
        llm_client=brain.client,
        opponent_model=cfg.get("ai.opponent_model", cfg.get("ai.model")),
        profile_workers=3,
        hand_reader_workers=3,
    )
    orchestrator = AgentOrchestrator(brain, memory, logger=log)

    adapter = create_adapter(cfg)

    # ═══════════════════════════════════════════════════════════
    # 决策回调: POST /decide → L3 决策
    # ═══════════════════════════════════════════════════════════

    def on_decide(snapshot: GameDataSnapshot) -> dict:
        try:
            gs = snapshot.state
            if not snapshot.is_valid:
                log.warning(
                    f"[DataProvider] 解析结果不可用: version={snapshot.provider_version} "
                    f"unresolved={snapshot.unresolved_fields}"
                )
                return {"action": "check", "amount": 0, "analysis": "invalid game state input"}

            # 对手动作已由 bridge 通过 /observe 持续上报，此处不再重复路由
            log.info(f"[Memory] {memory.status()}")

            # L1画像 + L2读牌 + L3最终决策统一由编排器消费快照
            decision = orchestrator.decide(snapshot, timeout=5.0)

            log.info(
                f"[L3] {gs.phase.name} | pos={gs.my_position} | "
                f"hand={gs.hand} | board={gs.public_cards} | "
                f"pot={gs.pot} to_call={gs.to_call} | "
                f"→ {decision.get('action','?').upper()} "
                f"amt={decision.get('amount')} conf={decision.get('confidence')} "
                f"provider={snapshot.provider_version}"
            )

            return decision
        except Exception:
            import traceback
            log.error(f"on_decide 异常:\n{traceback.format_exc()}")
            return {"action": "check", "amount": 0, "analysis": "internal error"}

    # ═══════════════════════════════════════════════════════════
    # 观察回调: POST /observe → L1+L2 持续更新
    # ═══════════════════════════════════════════════════════════

    def on_observe(event: dict):
        if event.get("event_type") in ("network", "network_batch"):
            log.debug("收到网络上下文事件")
            return

        # 对手动作实时上报 (bridge 持续推送)
        opp_id = event.get("opp_id", "")
        events = event.get("events", [])
        if opp_id and events:
            memory.on_opponent_action(opp_id, events)
            return

        # 结算归档
        phase_str = event.get("phase", "")
        if phase_str in ("SETTLEMENT", "INIT"):
            opponent_ids = event.get("opponent_ids", [])
            sd = event.get("showdown")
            for opp_id in opponent_ids:
                memory.on_hand_end(opp_id, sd)
            log.info(f"[观察] 结算归档: {len(opponent_ids)}个对手")

    adapter.set_observe_handler(on_observe)

    log.info(f"监听 http://{adapter.host}:{adapter.port}")
    log.info(f"DecisionAgent 模型: {brain.model}")
    log.info(f"L1 画像 workers: 3 | L2 读牌 workers: 3 | L3 Decision: 1")

    try:
        adapter.run(on_decide)
    finally:
        orchestrator.shutdown()


# _route_opponent_actions 保留给纯 API 模式 (无 bridge /observe) 使用
# 当前 bridge 模式下对手动作已通过 /observe 持续上报，/decide 不重复路由


if __name__ == "__main__":
    main()
