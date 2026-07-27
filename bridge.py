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


async def amain():
    parser = argparse.ArgumentParser(description="德州扑克 AI Bridge - CDP WebSocket 拦截")
    parser.add_argument("--auto", action="store_true", help="自动模式：Agent 自动执行决策")
    parser.add_argument("--url", type=str, default=DEFAULT_GAME_URL,
                        help=f"游戏页面 URL（默认: {DEFAULT_GAME_URL}）")
    parser.add_argument("--agent", type=str, default="http://localhost:8765",
                        help="Agent 引擎地址（默认: http://localhost:8765）")
    parser.add_argument("--cdp-port", type=int, default=CDP_PORT,
                        help=f"Chrome 调试端口（默认: {CDP_PORT}）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="输出所有捕获的 WebSocket 事件（调试用）")
    args = parser.parse_args()

    print("=" * 55)
    print("  德州扑克 AI Bridge (CDP WebSocket 拦截)")
    print("=" * 55)
    print(f"  游戏 URL:    {args.url}")
    print(f"  Agent:       {args.agent}")
    print(f"  CDP 端口:    {args.cdp_port}")
    print(f"  决策模式:    {'自动' if args.auto else '建议（浮层）'}")
    print(f"  调试:        {'开' if args.verbose else '关'}")
    print("=" * 55)
    print()

    # 1. 先尝试连已有 Chrome
    result = await try_connect_existing(args.agent, args.auto, args.cdp_port, args.verbose)

    # 2. 连不上就拉起新浏览器兜底
    if result is None:
        result = await launch_new(args.url, args.agent, args.auto, args.cdp_port, args.verbose)

    interceptor, pw, browser, page = result

    if args.auto:
        print("[Bridge] 自动模式 - Agent 将自动执行决策")
    else:
        print("[Bridge] 建议模式 - Agent 决策显示为页面浮层")

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
            await browser.close()
        except Exception:
            pass
        await pw.stop()
        print("[Bridge] 已停止")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
