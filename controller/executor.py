import pyautogui
import time


class PokerExecutor:
    def __init__(self, config):
        self.cfg = config
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.1

    def execute(self, decision, read_raise_amount=None):
        """
        decision: {"action": "raise", "amount": 1500, "analysis": "..."}
        read_raise_amount: 回调函数，返回当前 raise 金额 (用于上下键调整)
        """
        action = decision.get("action", "check").lower()
        amount = decision.get("amount", 0)

        print(f"[EXECUTOR] 指令: {action.upper()} | 金额: {amount}")

        if action == "fold":
            self._click("buttons.fold")
        elif action in ("call", "check"):
            self._click("buttons.call")
        elif action == "raise":
            self._do_raise(amount, read_raise_amount)
        else:
            print(f"[EXECUTOR] 未知动作: {action}，默认 Check")
            self._click("buttons.call")

    def _click(self, coord_path, duration=0.15):
        try:
            x, y = self.cfg.get_coord(coord_path)
            pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeOutQuad)
            pyautogui.click()
            return True
        except Exception as e:
            print(f"[EXECUTOR] 点击失败 {coord_path}: {e}")
            return False

    def _hover(self, coord_path, duration=0.15):
        try:
            x, y = self.cfg.get_coord(coord_path)
            pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeOutQuad)
            return True
        except Exception as e:
            print(f"[EXECUTOR] 悬停失败 {coord_path}: {e}")
            return False

    def _do_raise(self, target, read_raise_amount):
        """
        悬停 raise → 键盘上下键调金额 → OCR 逼近目标 → 点击确认
        """
        if read_raise_amount is None:
            print("[EXECUTOR] 无法读取 raise 金额，直接点击 raise")
            self._click("buttons.raise")
            return

        # 1. 悬停 raise 按钮
        self._hover("buttons.raise")
        time.sleep(0.4)

        # 2. 读初始值
        current = read_raise_amount()
        print(f"[EXECUTOR] 初始: {current}, 目标: {target}")

        if current <= 0 or current == target:
            self._click("buttons.raise")
            return

        # 3. 校准：按 3 次键，算每次步长
        calibrate_presses = 3
        direction = 'up' if target > current else 'down'
        for _ in range(calibrate_presses):
            pyautogui.press(direction)
            time.sleep(0.05)
        time.sleep(0.3)
        new_val = read_raise_amount()

        step = abs(new_val - current) / calibrate_presses
        print(f"[EXECUTOR] 校准: {current} → {new_val}, 每次={step:.0f}")

        if step == 0:
            self._click("buttons.raise")
            return

        # 4. 按到目标
        remaining = target - new_val
        presses = int(abs(remaining) / step)
        dir_key = 'up' if remaining > 0 else 'down'

        print(f"[EXECUTOR] 剩余差值: {remaining}, 按键 {presses} 次")
        batch = 20
        while presses > 0:
            n = min(presses, batch)
            for _ in range(n):
                pyautogui.press(dir_key)
                time.sleep(0.03)
            presses -= n

        # 5. 微调
        time.sleep(0.25)
        final_val = read_raise_amount()
        diff = target - final_val
        if abs(diff) > step * 0.5:
            adj_key = 'up' if diff > 0 else 'down'
            for _ in range(int(abs(diff) / step)):
                pyautogui.press(adj_key)
                time.sleep(0.03)

        # 6. 点击确认
        time.sleep(0.2)
        final_val = read_raise_amount()
        print(f"[EXECUTOR] 调整完成: {final_val} → 点击确认")
        self._click("buttons.raise")

    def hover_call(self):
        """悬停 Call 按钮以显示 call/check 金额"""
        self._hover("buttons.call")

    def hold_c_for_capture(self, capture_callback):
        print("[EXECUTOR] 按住 C 键...")
        pyautogui.keyDown('c')
        time.sleep(0.4)
        try:
            result = capture_callback()
        finally:
            pyautogui.keyUp('c')
            print("[EXECUTOR] 松开 C 键")
        return result
