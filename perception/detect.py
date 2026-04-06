# detect.py
import pyautogui
import time
from server.statemachine import GamePhase, AgentState

class PokerDetector:
    def __init__(self, sm, config):
        self.sm = sm
        self.cfg = config
        
        # 状态记录
        self.last_btn_visible = False
        self.phase_count = 0
        
        # 从 config 加载颜色和坐标（已自动缩放）
        # 注意：此处仅存储路径，调用时再实时 get_coord 确保动态同步
        self.trigger_pixel = "signals.call_btn_pixel"
        self.hand_check_pixel = "regions.hand" # 取区域左上角或中心点
        
        # 获取颜色配置
        self.active_color = self.cfg.get_color("call_active_rgb")
        self.card_back_color = self.cfg.get_color("card_back_rgb")

    def is_button_present(self):
        """检测操作按钮（如 Call 键）是否亮起"""
        x, y = self.cfg.get_coord(self.trigger_pixel)
        # 使用 pixelMatchesColor 并在 Windows 下通过 Config 开启 DPI 感知
        return pyautogui.pixelMatchesColor(x, y, self.active_color, tolerance=15)

    def has_hand_cards(self):
        """
        检测当前是否有手牌
        优化：检测手牌区域中心点的颜色是否匹配背景/背牌色
        """
        region = self.cfg.get_coord("regions.hand")
        # 取手牌区域的中心点进行像素检测
        check_x = region[0] + region[2] // 2
        check_y = region[1] + region[3] // 2
        return pyautogui.pixelMatchesColor(check_x, check_y, self.card_back_color, tolerance=20)

    def detect_and_update(self):
        """主检测循环：同步 UI 状态到 Statemachine"""
        btn_now = self.is_button_present()
        cards_now = self.has_hand_cards()
        
        # --- 逻辑 1：轮到我操作 (MY_TURN) ---
        if btn_now and not self.last_btn_visible:
            # 按钮刚亮起，说明轮到我了
            self.phase_count += 1
            self.sync_game_phase() # 尝试同步阶段
            
            # 只有当不在处理动作中时，才切换状态
            if self.sm.agent_state not in [AgentState.THINKING, AgentState.ACTING, AgentState.FOLDED]:
                self.sm.agent_state = AgentState.MY_TURN
                print(f"[DETECT] 轮到操作 | 阶段: {self.sm.current_phase.name} | 计数器: {self.phase_count}")

        # --- 逻辑 2：动作结束/弃牌判定 ---
        elif not btn_now and self.last_btn_visible:
            # 按钮消失了，判断是点掉了还是弃牌了
            if not cards_now:
                self.sm.agent_state = AgentState.FOLDED
                print("[DETECT] 手牌消失，判定为 FOLDED")
            else:
                self.sm.agent_state = AgentState.IDLE
                print("[DETECT] 按钮消失，等待他人或下一轮")

        # --- 逻辑 3：全局重置 (结算判定) ---
        # 如果检测到长时间没手牌且状态机不是 INIT，说明这局彻底结束了
        if not cards_now and self.sm.current_phase != GamePhase.INIT:
            # 这里可以增加一个延迟检测，防止动画闪烁导致的误判
            print("[DETECT] 检测到牌局空档，重置状态机...")
            self.sm.reset_hand()
            self.phase_count = 0

        self.last_btn_visible = btn_now

    def sync_game_phase(self):
        """
        同步游戏阶段：
        双重验证：优先看 Statemachine 里的公共牌数量，如果为空则参考计数器。
        """
        # 如果 capture.py 已经识别到了公共牌，以牌数为准（更准）
        pc_count = len(self.sm.data.get("public_cards", []))
        
        if pc_count > 0:
            # 这里的逻辑在 statemachine.derive_phase 中已有体现
            self.sm.derive_phase() 
        else:
            # 翻牌前(Pre-flop)阶段通常没有公共牌，依赖计数器
            phase_map = {
                1: GamePhase.PRE_FLOP,
                2: GamePhase.FLOP,
                3: GamePhase.TURN,
                4: GamePhase.RIVER
            }
            self.sm.current_phase = phase_map.get(self.phase_count, GamePhase.INIT)

# 调试用
if __name__ == "__main__":
    from utils.config_manager import Config
    from server.statemachine import PokerStateMachine
    
    cfg = Config("config.json")
    sm = PokerStateMachine()
    det = PokerDetector(sm, cfg)
    
    print("哨兵模式已启动，正在扫描 UI...")
    try:
        while True:
            det.detect_and_update()
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("停止检测")