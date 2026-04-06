# capture.py
import pyautogui
import time
import numpy as np
import cv2
from PIL import Image

class CardCapturer:
    def __init__(self, config, executor):
        """
        config: utils.config_manager.Config 实例
        executor: controller.executor.PokerExecutor 实例 (用于执行物理动作)
        """
        self.cfg = config
        self.executor = executor
        
        # 预加载区域坐标 (由 Config 自动处理 1.5x 缩放)
        self.reg_hand = "regions.hand"
        self.reg_public = "regions.public"
        self.reg_pot = "regions.pot"

    def capture_hand(self):
        """执行『按住 C 看牌』并截图手牌区域"""
        print("[CAPTURE] 正在同步执行器：按住 C 键观察手牌...")
        
        def _get_screenshot():
            region = self.cfg.get_coord(self.reg_hand)
            # region 格式为 (x, y, w, h)
            return pyautogui.screenshot(region=region)
        
        # 调用执行器的统一长按逻辑
        screenshot = self.executor.hold_c_for_capture(_get_screenshot)
        return self._process_to_cards(screenshot, label="HAND")

    def capture_public(self):
        """截取公共牌区域"""
        region = self.cfg.get_coord(self.reg_public)
        screenshot = pyautogui.screenshot(region=region)
        return self._process_to_cards(screenshot, label="PUBLIC")

    def capture_pot_text(self):
        """截取底池数字区域（返回 PIL Image 供后续 OCR 识别）"""
        region = self.cfg.get_coord(self.reg_pot)
        return pyautogui.screenshot(region=region)

    def _process_to_cards(self, pil_image, label="CARD"):
        """
        将截图转化为牌面列表
        后期填坑指南：在这里接入模板匹配 (Template Matching)
        """
        if pil_image is None:
            return []

        # 将 PIL 转换为 OpenCV 格式 (BGR)，方便后续处理
        cv_img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        
        # --- 调试模式：保存截图查看缩放是否正确 ---
        # debug_path = f"debug_{label.lower()}.png"
        # cv2.imwrite(debug_path, cv_img)
        
        # TODO: 接入 OpenCV 模板匹配识别 Ah, Ks 等
        # 目前先返回空列表，确保逻辑链不崩
        return []

    def get_readable_cards(self, cards_list):
        """将识别结果转化为 Prompt 易读格式"""
        if not cards_list:
            return "未知"
        return ", ".join(cards_list)