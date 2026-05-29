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
        悬停开滑块 → 探两端极值 → 垂直二分插值 → 滑轮微调 → 就地点击确认
        起点=raise按钮(底端/小额), 终点=raise_slider_end(顶端/大额)
        """
        if read_raise_amount is None:
            print("[EXECUTOR] 无法读取 raise 金额，直接点击 raise")
            self._click("buttons.raise")
            return

        # 1. 悬停 raise 弹出滑块
        self._hover("buttons.raise")
        time.sleep(0.5)

        # 2. 滑块垂直范围：同X轴，Y从底端(小额)→顶端(大额)
        slider_x, lo_y = self.cfg.get_coord("buttons.raise")
        _, hi_y = self.cfg.get_coord("signals.raise_slider_end")

        # 3. 探两端极值
        pyautogui.moveTo(slider_x, lo_y, duration=0.1)
        time.sleep(0.3)
        lo_val = read_raise_amount()

        pyautogui.moveTo(slider_x, hi_y, duration=0.1)
        time.sleep(0.3)
        hi_val = read_raise_amount()

        print(f"[EXECUTOR] 滑块范围: {lo_val} ~ {hi_val}, 目标={target}")

        if lo_val <= 0 or hi_val <= 0:
            print("[EXECUTOR] OCR 失败，就地点击确认")
            pyautogui.click()
            return

        if target <= lo_val:
            print("[EXECUTOR] 目标≤最小值，底端点击确认")
            pyautogui.moveTo(slider_x, lo_y, duration=0.1)
            pyautogui.click()
            return

        if target >= hi_val:
            print("[EXECUTOR] 目标≥最大值，顶端点击确认")
            pyautogui.moveTo(slider_x, hi_y, duration=0.1)
            pyautogui.click()
            return

        # 4. 垂直二分插值
        for i in range(8):
            ratio = (target - lo_val) / (hi_val - lo_val) if hi_val != lo_val else 0.5
            mid_y = int(lo_y + (hi_y - lo_y) * ratio)
            pyautogui.moveTo(slider_x, mid_y, duration=0.1)
            time.sleep(0.25)
            current = read_raise_amount()

            if current <= 0:
                break

            print(f"[EXECUTOR] iter={i} y={mid_y} → {current}")

            if abs(current - target) < max(target * 0.03, 1):
                break

            if current < target:
                lo_y = mid_y
                lo_val = current
            else:
                hi_y = mid_y
                hi_val = current

        # 5. 滑轮微调
        current = read_raise_amount()
        if current > 0 and abs(current - target) >= max(target * 0.03, 1):
            print(f"[EXECUTOR] 二分后={current}, 目标={target}, 滑轮微调...")
            scroll_dir = 1 if target > current else -1
            for j in range(15):
                pyautogui.scroll(scroll_dir)
                time.sleep(0.12)
                current = read_raise_amount()
                if current <= 0:
                    break
                print(f"[EXECUTOR] scroll iter={j} → {current}")
                if abs(current - target) < max(target * 0.03, 1):
                    break
                if (target - current) * scroll_dir < 0:
                    pyautogui.scroll(-scroll_dir)
                    time.sleep(0.12)
                    break

        # 6. 就地点击确认
        time.sleep(0.1)
        print(f"[EXECUTOR] 调整完成 → 点击确认")
        pyautogui.click()

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
