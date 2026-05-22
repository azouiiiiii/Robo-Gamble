import time
import sys
from utils.config_manager import Config
from utils.logger import setup_logger
from server.statemachine import PokerStateMachine, AgentState, GamePhase
from server.memory import GameMemory
from server.decision import PokerDecision
from perception.capture import CardCapturer
from perception.detect import PokerDetector
from controller.executor import PokerExecutor
from server.evaluator import preflop_hand_strength, evaluate_hand, pot_odds_analysis

log = setup_logger()


def main():
    log.info("德州扑克 AI Agent 启动中")

    try:
        cfg = Config("config.json")
    except FileNotFoundError as e:
        log.critical(f"找不到配置文件: {e}")
        sys.exit(1)

    sm = PokerStateMachine()
    memory = GameMemory()
    executor = PokerExecutor(cfg)
    capturer = CardCapturer(cfg, executor)
    detector = PokerDetector(sm, cfg, capturer)
    brain = PokerDecision(cfg)

    log.info("系统就绪，开始扫描游戏窗口")

    try:
        while True:
            detector.detect_and_update()

            if sm.agent_state == AgentState.MY_TURN:
                log.info(f">>> 行动回合 | 阶段={sm.current_phase.name}")

                sm.agent_state = AgentState.THINKING

                # 手牌
                if not sm.data["hand"]:
                    hand = capturer.capture_hand()
                    sm.data["hand"] = hand
                    log.info(f"手牌: {capturer.get_readable_cards(hand)}")

                # 公共牌
                public = capturer.capture_public()
                sm.data["public_cards"] = public
                log.info(f"公共牌: {capturer.get_readable_cards(public)}")

                # 底池 + 筹码
                pot = capturer.capture_pot()
                if pot > 0:
                    sm.update_pot(pot)
                chips = capturer.capture_my_chips()
                if chips > 0:
                    sm.data["my_chips"] = chips

                # 悬停 Call 读取跟注金额（有数字=call，无=check）
                executor.hover_call()
                to_call = capturer.capture_call_amount()
                log.info(f"底池={sm.data['pot']}  筹码={sm.data['my_chips']}  "
                         f"跟注={to_call} ({'call' if to_call > 0 else 'check'})")

                # 手牌评估
                eval_data = evaluate_hand(sm.data["hand"], sm.data["public_cards"])
                preflop = preflop_hand_strength(sm.data["hand"])
                odds_ratio, odds_pct = pot_odds_analysis(sm.data["pot"], to_call)

                log.info(f"成牌={eval_data['made_hand']}  "
                         f"听牌={eval_data['draws'] or '无'}  "
                         f"outs={eval_data['outs']}  "
                         f"起手={preflop[0]}")

                # 记忆
                current_event = {
                    "phase": sm.current_phase,
                    "pot": sm.data["pot"],
                    "action": "detect"
                }
                memory.add_event(current_event)
                semantic_hist = memory.get_semantic_history()

                # AI 决策
                log.info("请求 AI 决策...")
                game_context = {
                    "current_phase": sm.current_phase,
                    "hand": sm.data["hand"],
                    "public_cards": sm.data["public_cards"],
                    "pot": sm.data["pot"],
                    "my_chips": sm.data["my_chips"],
                    "to_call": to_call,
                    "hand_strength": preflop[0],
                    "made_hand": eval_data["made_hand"],
                    "draws": eval_data["draws"],
                    "outs": eval_data["outs"],
                    "outs_detail": eval_data["outs_detail"],
                    "pot_odds": odds_ratio,
                    "required_equity": odds_pct,
                }

                decision = brain.ask_ai(game_context, semantic_hist)
                log.info(f"AI={decision.get('action').upper()}  "
                         f"金额={decision.get('amount')}  "
                         f"理由={decision.get('analysis')}")

                # 执行
                sm.agent_state = AgentState.ACTING
                executor.execute(decision, capturer.capture_raise_amount)
                log.info("动作执行完毕，等待下一轮")

            # 新局清理
            if sm.current_phase == GamePhase.INIT and len(memory.history) > 0:
                memory.reset()
                log.info("新对局，记忆已清空")

            time.sleep(0.1)

    except KeyboardInterrupt:
        log.info("程序由用户停止")
    except Exception as e:
        log.critical(f"运行时崩溃: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
