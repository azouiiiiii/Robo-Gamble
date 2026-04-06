import time
import sys
from utils.config_manager import Config
from server.statemachine import PokerStateMachine, AgentState, GamePhase
from server.memory import GameMemory
from server.decision import PokerDecision
from perception.capture import CardCapturer
from perception.detect import PokerDetector
from controller.executor import PokerExecutor

def main():
    print("=== 德州扑克 AI Agent 启动中 ===")
    
    # 1. 初始化各模块
    try:
        cfg = Config("config.json")
    except FileNotFoundError as e:
        print(f"错误: {e}")
        sys.exit(1)

    sm = PokerStateMachine()
    memory = GameMemory()
    executor = PokerExecutor(cfg)
    capturer = CardCapturer(cfg, executor)
    detector = PokerDetector(sm, cfg)
    brain = PokerDecision(cfg)

    print("=== 系统就绪，开始扫描游戏窗口 ===")

    try:
        while True:
            # --- 步骤 A: 状态检测 (无间断运行) ---
            # detector 会实时修改 sm.agent_state 和 sm.current_phase
            detector.detect_and_update()

            # --- 步骤 B: 核心逻辑调度 ---
            
            # 逻辑 1: 如果轮到我操作，且尚未开始思考
            if sm.agent_state == AgentState.MY_TURN:
                print(f"\n[MAIN] >>> 检测到行动回合! 阶段: {sm.current_phase.name}")
                
                # 1. 切换状态防止重复进入
                sm.agent_state = AgentState.THINKING

                # 2. 感知环境 (Perception)
                # 只有在 PRE_FLOP 且手牌为空时才去执行“按住C看牌”
                if not sm.data["hand"]:
                    print("[MAIN] 正在观察手牌...")
                    sm.data["hand"] = capturer.capture_hand()
                
                # 观察公共牌
                print("[MAIN] 正在扫描公共牌...")
                sm.data["public_cards"] = capturer.capture_public()
                
                # 更新底池数据 (这里目前简单处理，后期可接入 OCR)
                # 暂时依赖 detector 的简单计数或手动设置一个值
                # sm.update_pot(...) 

                # 3. 更新记忆 (Memory)
                # 记录当前观察到的局面到历史中，用于生成博弈描述
                current_event = {
                    "phase": sm.current_phase,
                    "pot": sm.data["pot"],
                    "action": "detect"
                }
                memory.add_event(current_event)
                semantic_hist = memory.get_semantic_history()

                # 4. 决策 (Decision)
                print("[MAIN] 正在请求 AI 决策...")
                # 构造传递给 build_poker_prompt 的完整上下文
                game_context = {
                    "current_phase": sm.current_phase,
                    "hand": sm.data["hand"],
                    "public_cards": sm.data["public_cards"],
                    "pot": sm.data["pot"],
                    "my_chips": sm.data["my_chips"]
                }
                
                decision = brain.ask_ai(game_context, semantic_hist)
                print(f"[MAIN] AI 决策结果: {decision.get('action')} | 理由: {decision.get('analysis')}")

                # 5. 执行 (Execution)
                sm.agent_state = AgentState.ACTING
                executor.execute(decision)
                
                # 动作完成后，状态会被 detector 在下一轮扫描中自动修正为 IDLE 或 FOLDED
                print("[MAIN] 动作执行完毕，等待下一轮。")

            # 逻辑 2: 处理一局结束的清理 (当 Detector 重置 SM 时)
            if sm.current_phase == GamePhase.INIT and len(memory.history) > 0:
                memory.reset()
                print("[MAIN] 检测到新对局，记忆已清空。")

            # 降低 CPU 占用
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n=== 程序由用户停止 ===")
    except Exception as e:
        print(f"\n[CRITICAL ERROR] 运行时崩溃: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()