"""数据采集模式的全局反引号键监听器。"""

from __future__ import annotations

from typing import Callable


class BoundaryListener:
    """监听 `` ` `` / ``~``，并把按键回调交给调用方。

    pynput 是可选导入，便于无桌面环境和单元测试使用 ``handle_key``；
    macOS 首次运行还需要给终端/IDE 辅助功能权限。
    """

    def __init__(self, callback: Callable[[str], None], key: str = "backquote",
                 verbose: bool = False):
        self.callback = callback
        self.key = key
        self.verbose = verbose
        self._listener = None
        self._pressed = False

    @staticmethod
    def _is_backquote(key) -> bool:
        char = getattr(key, "char", key)
        return char in ("`", "~")

    def handle_key(self, key) -> bool:
        """处理一个 pynput 风格 key；返回是否消费该键。"""
        if self.key != "backquote" or not self._is_backquote(key):
            return False
        if self._pressed:
            return True
        self._pressed = True
        self.callback(self.key)
        return True

    def handle_release(self, key) -> None:
        if self._is_backquote(key):
            self._pressed = False

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except ImportError:
            if self.verbose:
                print("[Capture] 未安装 pynput，无法监听反引号键；可使用 pynput>=1.7")
            return False

        try:
            self._listener = keyboard.Listener(
                on_press=self.handle_key, on_release=self.handle_release
            )
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as exc:
            # 无桌面环境或 macOS 辅助功能权限不足时，不能影响浏览器采集。
            if self.verbose:
                print(f"[Capture] 反引号监听不可用: {exc}")
            self._listener = None
            return False

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
