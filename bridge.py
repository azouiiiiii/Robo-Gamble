"""
CDP WebSocket 桥接器：通过 Playwright 控制浏览器，在 CDP 层拦截 WebSocket 帧，
提取游戏数据并转发给 AI Agent 决策引擎。

启动方式：
  python bridge.py                    # 交互模式：打开浏览器，展示建议
  python bridge.py --auto             # 自动模式：打开浏览器，自动执行决策
  python bridge.py --url <game_url>   # 指定游戏 URL（默认读 config）

依赖：pip install playwright && playwright install chromium
"""

import argparse
import asyncio

from bridge import try_connect_existing, launch_new, CDP_PORT, DEFAULT_GAME_URL
from bridge.modes import BridgeMode, normalize_mode
from utils.config_manager import Config


async def amain():
    parser = argparse.ArgumentParser(description="德州扑克 AI Bridge - CDP WebSocket 拦截")
    parser.add_argument("--mode", choices=[mode.value for mode in BridgeMode],
                        help="运行模式：learn=学习，verify=验证，auto=自动打牌")
    parser.add_argument("--auto", action="store_true", help="自动模式：Agent 自动执行决策")
    parser.add_argument("--url", type=str, default=DEFAULT_GAME_URL,
                        help=f"游戏页面 URL（默认: {DEFAULT_GAME_URL}）")
    parser.add_argument("--agent", type=str, default="http://localhost:8765",
                        help="Agent 引擎地址（默认: http://localhost:8765）")
    parser.add_argument("--cdp-port", type=int, default=CDP_PORT,
                        help=f"Chrome 调试端口（默认: {CDP_PORT}）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出所有捕获的 WebSocket 事件（调试用）")
    parser.add_argument("--network", action="store_true", help="启用页面 HTTP/Fetch/XHR 网络采集")
    args = parser.parse_args()

    print("=" * 55)
    print("  德州扑克 AI Bridge (CDP WebSocket 拦截)")
    print("=" * 55)
    print(f"  游戏 URL:    {args.url}")
    print(f"  Agent:       {args.agent}")
    print(f"  CDP 端口:    {args.cdp_port}")
    print(f"  调试:        {'开' if args.verbose else '关'}")
    print("=" * 55)
    print()

    try:
        cfg = Config("config.json")
        configured_network = cfg.get("bridge.network_capture", {})
        profile_id = cfg.get("bridge.mapping_profile", "legacy_socketio_v1")
        profile_dir = cfg.get("learning.profile_dir", "profiles")
        configured_mode = cfg.get("bridge.mode", "verify")
        learning_session_dir = cfg.get("learning.session_dir", "learning_sessions")
        learning_config = cfg.get("learning", {})
    except FileNotFoundError:
        configured_network = {}
        profile_id = "legacy_socketio_v1"
        profile_dir = "profiles"
        configured_mode = "verify"
        learning_session_dir = "learning_sessions"
        learning_config = {}

    mode = normalize_mode("auto" if args.auto else (args.mode or configured_mode))
    labels = {
        BridgeMode.LEARN: "学习（仅采集）",
        BridgeMode.VERIFY: "验证（固定 Profile 浮层）",
        BridgeMode.AUTO: "自动打牌",
    }
    print(f"  运行模式:    {labels[mode]}")

    network_config = dict(configured_network)
    network_config["enabled"] = args.network or configured_network.get("enabled", False)
    network_config.setdefault("resource_types", ["fetch", "xhr"])
    network_config.setdefault("content_types", ["application/json", "text/"])

    # 1. 先尝试连已有 Chrome
    result = await try_connect_existing(
        args.agent, args.auto, args.cdp_port, args.verbose, network_config,
        profile_id, profile_dir, mode.value, learning_session_dir, learning_config,
    )

    # 2. 连不上就拉起新浏览器兜底
    if result is None:
        result = await launch_new(
            args.url, args.agent, args.auto, args.cdp_port, args.verbose, network_config,
            profile_id, profile_dir, mode.value, learning_session_dir, learning_config,
        )

    interceptor, pw, browser, page = result

    if mode == BridgeMode.LEARN:
        print("[Bridge] 学习模式 - 记录原始网络数据，边界后异步学习并保存 draft Profile")
    elif mode == BridgeMode.VERIFY:
        print(f"[Bridge] 验证模式 - Profile={profile_id}，仅显示映射结果")
    else:
        print(f"[Bridge] 自动模式 - Profile={profile_id}，Agent 将自动执行决策")

    print("[Bridge] 运行中，按 Ctrl+C 停止...")

    try:
        while True:
            await asyncio.sleep(1)
            if page.is_closed():
                print("[Bridge] 页面已关闭")
                break
    except KeyboardInterrupt:
        print("\n[Bridge] 正在停止...")
    finally:
        try:
            await interceptor.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass
        await pw.stop()
        print("[Bridge] 已停止")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
