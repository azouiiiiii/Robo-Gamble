# perception/capture.py
import pyautogui
import time
from PIL import Image

class CardCapturer:
    def __init__(self):
        # 坐标配置
        self.REGIONS = {
            "HAND": (1000, 850, 200, 100),    # 手牌区域 (x, y, width, height)
            "PUBLIC": (600, 400, 600, 150),   # 公共牌区域
        }

    def capture_hand(self):
        """执行『按住 C 看牌』并截图"""
        print("正在按住 C 键看牌...")
        # 使用 hold 模拟长按
        with pyautogui.hold('c'):
            time.sleep(0.3)  # 等待游戏动画显示手牌
            # 截取手牌区域
            screenshot = pyautogui.screenshot(region=self.REGIONS["HAND"])
        
        # 此时 C 键已自动松开
        return self._process_image_to_cards(screenshot, is_hand=True)

    def capture_public(self):
        """截取公共牌区域"""
        screenshot = pyautogui.screenshot(region=self.REGIONS["PUBLIC"])
        return self._process_image_to_cards(screenshot, is_hand=False)

    def _process_image_to_cards(self, image, is_hand=True):
        """
        内部方法：将图片转化为文字/列表
        暂时先用伪代码占位，你可以后期接入 EasyOCR 或 OpenCV 模板匹配
        """
        # 保存图片用于调试
        # image.save(f"debug_{'hand' if is_hand else 'public'}.png")
        
        # TODO: 接入你的识别逻辑
        # 示例返回值: ["Ah", "Ks"] 或 ["2d", "5s", "10c"]
        return []