"""浏览器生命周期管理：拉起 Chrome、连接已有实例、页面状态恢复。"""

from playwright.async_api import async_playwright, Page

from .interceptor import WebSocketInterceptor
from .aggregator import GameStateAggregator


_INIT_SCRIPT = """
Object.defineProperty(window, '_sockets', {
    value: {},
    writable: true,
    configurable: true
});
"""

CDP_PORT = 9222
DEFAULT_GAME_URL = "http://localhost:8080"


async def try_connect_existing(agent_url: str, auto_mode: bool, cdp_port: int, verbose: bool = False) -> tuple | None:
    """尝试连接已有 Chrome，失败返回 None"""
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")

        page = None
        for ctx in browser.contexts:
            for p in ctx.pages:
                url = p.url or ""
                if "about:blank" not in url:
                    page = p
                    break
            if page:
                break
        if not page:
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await ctx.new_page()

        interceptor = WebSocketInterceptor(agent_url, verbose=verbose)
        interceptor.attach(page, auto_mode=auto_mode)
        await page.evaluate(_INIT_SCRIPT)

        print("[Bridge] 刷新页面以截获 WebSocket 重连...")
        await page.reload(wait_until="networkidle")
        await recover_state(page, interceptor)

        print(f"[Bridge] 已连接已有浏览器 (CDP :{cdp_port}) → {page.url[:80]}")
        return interceptor, pw, browser, page
    except Exception:
        print("[Bridge] 未检测到已有浏览器，拉起新浏览器...")
        return None


async def launch_new(game_url: str, agent_url: str, auto_mode: bool, cdp_port: int, verbose: bool = False) -> tuple:
    """拉起新 Chrome，同时打开 CDP 端口供后续连接"""
    interceptor = WebSocketInterceptor(agent_url, verbose=verbose)
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,
        args=[
            f"--remote-debugging-port={cdp_port}",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    await page.add_init_script(_INIT_SCRIPT)
    interceptor.attach(page, auto_mode=auto_mode)

    if game_url:
        print(f"[Bridge] 打开游戏: {game_url}")
        await page.goto(game_url, wait_until="networkidle", timeout=30000)
    print(f"[Bridge] 已拉起浏览器 (CDP :{cdp_port}) → {page.url[:80]}")

    return interceptor, pw, browser, page


async def recover_state(page: Page, interceptor: WebSocketInterceptor):
    """从已加载的页面中读取当前用户 ID"""
    try:
        result = await page.evaluate("""
        (() => {
            try {
                let userId = null, nickName = null;
                function walk(vm, depth) {
                    if (!vm || depth > 20) return;
                    if (vm.userInfo && vm.userInfo.userId) {
                        userId = vm.userInfo.userId;
                        nickName = vm.userInfo.nickName;
                        return;
                    }
                    if (vm.$children) {
                        vm.$children.forEach(c => walk(c, depth + 1));
                    }
                }
                const root = document.querySelector('#app').__vue__;
                walk(root, 0);
                return { userId, nickName };
            } catch(e) { return null; }
        })()
        """)
        if result and result.get("userId"):
            print(f"[Bridge] 从页面恢复玩家: {result.get('nickName')} (userId={result['userId']})")
            interceptor.aggregator = GameStateAggregator(result["userId"], verbose=interceptor.verbose)
    except Exception:
        pass
