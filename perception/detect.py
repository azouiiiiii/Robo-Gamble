import os
import cv2
import pyautogui
import numpy as np
from server.statemachine import GamePhase, AgentState

TEMPLATE_PATH = "templates/round_winner.png"
MATCH_THRESHOLD = 0.6


class PokerDetector:
    def __init__(self, sm, config, capturer=None):
        self.sm = sm
        self.cfg = config
        self.capturer = capturer

        self.last_btn_visible = False
        self.settlement_count = 0
        self.idle_frames = 0

        self.trigger_pixel = "signals.call_btn_pixel"
        self.active_color = self.cfg.get_color("call_active_rgb")

        # 加载结算模板
        self._template = None
        if os.path.exists(TEMPLATE_PATH):
            self._template = cv2.imread(TEMPLATE_PATH, 0)

    def is_button_present(self):
        x, y = self.cfg.get_coord(self.trigger_pixel)
        return pyautogui.pixelMatchesColor(x, y, self.active_color, tolerance=15)

    def is_settlement(self):
        """模板匹配 round_winner 区域"""
        if self._template is None:
            return False

        region = self.cfg.get_coord("regions.round_winner")
        img = pyautogui.screenshot(region=region)
        gray = np.array(img.convert("L"))

        # 确保截图和模板尺寸一致
        if gray.shape != self._template.shape:
            return False

        result = cv2.matchTemplate(gray, self._template, cv2.TM_CCOEFF_NORMED)
        score = result[0][0]
        return score >= MATCH_THRESHOLD

    def detect_and_update(self):
        btn_now = self.is_button_present()

        # ── 1. 轮到我操作 ──
        if btn_now and not self.last_btn_visible:
            self.sync_game_phase()
            self.settlement_count = 0
            self.idle_frames = 0

            if self.sm.agent_state not in (AgentState.THINKING, AgentState.ACTING, AgentState.FOLDED):
                self.sm.agent_state = AgentState.MY_TURN
                print(f"[DETECT] 轮到操作 | 阶段: {self.sm.current_phase.name}")

        # ── 2. 按钮消失 → 回合结束 ──
        elif not btn_now and self.last_btn_visible:
            self.sm.agent_state = AgentState.IDLE
            self.idle_frames = 0
            print("[DETECT] 按钮消失，等待他人或下一轮")

        # ── 3. 结算检测（冷却 20 帧后才开始查）──
        if not btn_now and self.sm.current_phase != GamePhase.INIT:
            if self.idle_frames >= 20:
                if self.is_settlement():
                    self.settlement_count += 1
                    if self.settlement_count >= 3:
                        print("[DETECT] 结算页面确认，重置状态机")
                        self.sm.reset_hand()
                        self.settlement_count = 0
                else:
                    self.settlement_count = 0

        if not btn_now:
            self.idle_frames += 1
        else:
            self.idle_frames = 0

        self.last_btn_visible = btn_now

    def sync_game_phase(self):
        """5 个固定点色匹配统计公共牌数量 → 确定阶段"""
        if self.capturer is None:
            return

        count = self.capturer.count_public_cards()
        phase_map = {0: GamePhase.PRE_FLOP, 3: GamePhase.FLOP,
                     4: GamePhase.TURN, 5: GamePhase.RIVER}
        if count in phase_map:
            self.sm.current_phase = phase_map[count]
        # counts 1, 2 是翻牌中间态，保持当前阶段不变


if __name__ == "__main__":
    from utils.config_manager import Config
    from server.statemachine import PokerStateMachine
    import time

    cfg = Config("config.json")
    sm = PokerStateMachine()
    det = PokerDetector(sm, cfg)

    print("哨兵模式，扫描 UI...")
    try:
        while True:
            det.detect_and_update()
            pyautogui.sleep(0.5)
    except KeyboardInterrupt:
        print("停止")
