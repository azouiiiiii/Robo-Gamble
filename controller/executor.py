# controller/executor.py
import pyautogui
import time

class PokerExecutor:
    def __init__(self):
        # 这里的坐标在移到 Windows 后需要根据 100% 缩放重新测量
        self.COORDS = {
            "FOLD": (400, 700),     # 左下第一个圆圈
            "CALL": (500, 700),     # 左下第二个圆圈
            "RAISE": (600, 700),    # 左下第三个圆圈
            "INPUT_BOX": (650, 750) # 下注额输入框
        }

    def execute(self, decision):
        """
        根据 AI 决策执行动作
        decision: {"action": "raise", "amount": 2400, ...}
        """
        action = decision.get("action", "check").lower()
        amount = decision.get("amount", 0)

        print(f"[EXECUTOR] 执行动作: {action}, 金额: {amount}")

        if action == "fold":
            self._click_button("FOLD")
        elif action in ["call", "check"]:
            self._click_button("CALL")
        elif action == "raise":
            self._do_raise(amount)

    def _click_button(self, btn_name):
        x, y = self.COORDS[btn_name]
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click()
        print(f"已点击 {btn_name}")

    def _do_raise(self, amount):
        """处理加注逻辑：点击输入框 -> 输入数字 -> 点击 Raise"""
        # 1. 点击输入框
        ix, iy = self.COORDS["INPUT_BOX"]
        pyautogui.click(ix, iy)
        time.sleep(0.1)
        
        # 2. 清除旧数字并输入新数字
        pyautogui.hotkey('ctrl', 'a') # 全选
        pyautogui.press('backspace')  # 删除
        pyautogui.write(str(amount))
        
        # 3. 点击加注按钮
        self._click_button("RAISE")

    def look_at_cards(self):
        """
        实现『按住 C 键』的逻辑
        这个函数会被 perception/capture.py 调用
        """
        with pyautogui.hold('c'):
            time.sleep(0.5) # 等待 0.5 秒让画面显示
            # 这里触发截图逻辑
            print("正在按住 C 键并观察...")