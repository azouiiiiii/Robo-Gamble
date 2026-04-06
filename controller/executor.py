# executor.py 
import pyautogui
import time
import ctypes

class PokerExecutor:
    def __init__(self, config):
        """
        config: utils.config_manager.Config 实例
        """
        self.cfg = config
        
        # 1. 核心安全设置：将鼠标甩到屏幕四个角落之任一，脚本立即停止
        pyautogui.FAILSAFE = True
        
        # 2. 降低动作间的默认延迟，由我们手动控制 time.sleep 提高拟人度
        pyautogui.PAUSE = 0.1 

    def _click(self, coord_path, duration=0.2):
        """
        内部通用点击逻辑，自动处理缩放
        """
        try:
            x, y = self.cfg.get_coord(coord_path)
            # 使用 moveTo + click 而不是直接 click，模拟真人移动轨迹
            pyautogui.moveTo(x, y, duration=duration, tween=pyautogui.easeOutQuad)
            pyautogui.click()
            return True
        except Exception as e:
            print(f"[EXECUTOR] 点击 {coord_path} 失败: {e}")
            return False

    def execute(self, decision):
        """
        执行来自 AI 的决策对象
        decision 格式: {"action": "raise", "amount": 1500, "analysis": "..."}
        """
        action = decision.get("action", "check").lower()
        amount = decision.get("amount", 0)

        print(f"[EXECUTOR] 收到决策指令: {action.upper()} | 金额: {amount}")

        if action == "fold":
            self._click("buttons.fold")
        elif action in ["call", "check"]:
            self._click("buttons.call")
        elif action == "raise":
            self._do_raise(amount)
        else:
            print(f"[EXECUTOR] 未知动作: {action}，默认执行 Check")
            self._click("buttons.call")

    def _do_raise(self, amount):
        """
        处理加注逻辑：点击输入框 -> 全选清除 -> 键入数字 -> 点击确认
        """
        # 1. 点击输入框 (这里假设点击 pot 区域或者你自定义的 input_box 坐标)
        # 如果 json 里没有 input_box，建议改用 regions.pot 的中心点
        if not self._click("buttons.raise"): # 某些 UI 需要先点 Raise 弹出输入框
            return

        # 2. 定位到输入框并激活（根据你的游戏 UI，可能需要点一下输入框）
        # 这里演示点击 config.json 里的 input_box（如果存在）
        try:
            ix, iy = self.cfg.get_coord("buttons.input_box") # 需确保 json 有此字段
            pyautogui.click(ix, iy)
        except:
            pass # 如果没有独立输入框坐标，跳过

        time.sleep(0.2)

        # 3. 清除旧金额并输入新金额
        # 使用 Ctrl+A + Backspace 是最稳妥的，不受初始值影响
        pyautogui.hotkey('ctrl', 'a')
        pyautogui.press('backspace')
        
        # 关键：使用 write 输入数字。interval 增加一点间隔可以绕过某些简单的反作弊
        # 并确保传入的是整数
        pyautogui.write(str(int(amount)), interval=0.02)
        
        time.sleep(0.3)

        # 4. 再次点击 Raise 确认发出下注
        self._click("buttons.raise", duration=0.1)

    def hold_c_for_capture(self, capture_callback):
        """
        专门供 perception/capture.py 调用的“看牌”接口
        capture_callback: 传进来的截图函数
        """
        print("[EXECUTOR] 物理操作：按住 C 键...")
        # 模拟真人：先按下，等动画，截图，再松开
        pyautogui.keyDown('c')
        time.sleep(0.4)  # 等待手牌翻开动画
        
        try:
            # 在按住的状态下执行传进来的截图逻辑
            result = capture_callback()
        finally:
            # 无论截图成功与否，必须确保松开 C 键，否则系统会卡死在 C 键按下状态
            pyautogui.keyUp('c')
            print("[EXECUTOR] 松开 C 键")
            
        return result