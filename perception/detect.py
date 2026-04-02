import pyautogui
import time
from server.statemachine import GamePhase, AgentState

class PokerDetector:
    def __init__(self, sm):
        self.sm = sm
        # 配置区：请根据实际游戏窗口位置修改这些坐标
        self.COORDS = {
            "BTN_FOLD": (1000, 800),     # Fold 按钮中心坐标
            "BTN_CALL": (1150, 800),     # Call 按钮中心坐标
            "BTN_RAISE": (1300, 800),    # Raise 按钮中心坐标
            "HAND_ZONE": (1150, 900),    # 手牌区域的一个检测点
            "POT_ZONE": (800, 500)        # 底池区域的一个检测点
        }
        
        # 颜色配置：按钮亮起时的目标颜色 (R, G, B)
        self.COLORS = {
            "BTN_ACTIVE": (255, 255, 255), # 假设按钮亮起时包含纯白色
            "CARD_BACK": (200, 0, 0)       # 手牌背面的颜色（用于判断是否有牌）
        }

        self.last_btn_visible = False
        self.phase_count = 0

    def is_button_present(self):
        """检测操作按钮（如 Call 键）是否亮起"""
        x, y = self.COORDS["BTN_CALL"]
        # pixelMatchesColor 允许一定的色差(tolerance)
        return pyautogui.pixelMatchesColor(x, y, self.COLORS["BTN_ACTIVE"], tolerance=10)

    def has_hand_cards(self):
        """检测当前是否有手牌（未弃牌且在局中）"""
        x, y = self.COORDS["HAND_ZONE"]
        return pyautogui.pixelMatchesColor(x, y, self.COLORS["CARD_BACK"], tolerance=10)

    def detect_and_update(self):
        """主检测循环逻辑"""
        btn_now = self.is_button_present()
        cards_now = self.has_hand_cards()

        # 情况 1：按钮从无到有 -> 进入新的下注轮
        if btn_now and not self.last_btn_visible:
            self.phase_count += 1
            self.update_phase_by_count()
            self.sm.agent_state = AgentState.MY_TURN
            print(f"--- 轮到操作 --- 阶段: {self.sm.current_phase.name}")

        # 情况 2：按钮消失了
        elif not btn_now and self.last_btn_visible:
            # 如果按钮消失时手牌也没了 -> 判定为 Fold 或 局结束
            if not cards_now:
                self.sm.agent_state = AgentState.FOLDED
                print("检测到手牌消失，进入 FOLDED/等待 状态")
            else:
                self.sm.agent_state = AgentState.IDLE
                print("按钮消失，进入换牌/等待他人操作状态")

        # 情况 3：完全没牌了（结算阶段）
        if not cards_now and self.sm.current_phase != GamePhase.INIT:
            if self.sm.agent_state != AgentState.FOLDED:
                print("本局结束，重置状态机")
                self.sm.reset_hand()
                self.phase_count = 0

        self.last_btn_visible = btn_now

    def update_phase_by_count(self):
        """根据按钮出现的次数切换物理阶段"""
        phase_map = {
            1: GamePhase.PRE_FLOP,
            2: GamePhase.FLOP,
            3: GamePhase.TURN,
            4: GamePhase.RIVER
        }
        self.sm.current_phase = phase_map.get(self.phase_count, GamePhase.INIT)

# 简易测试代码
if __name__ == "__main__":
    from server.statemachine import PokerStateMachine
    sm = PokerStateMachine()
    detector = PokerDetector(sm)
    
    while True:
        detector.detect_and_update()
        time.sleep(0.5)  # 操作时要另外拉长或添加全局开关，保证能操作完成