"""日志网页服务器 — SSE 实时推送，局域网可访问"""
import os
import re
import time
import http.server
import json
from urllib.parse import urlparse, parse_qs
from socketserver import ThreadingMixIn

LOG_DIR = "logs"
PORT = 8080


class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Texas Hold'em Logs</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#1a1a2e;color:#e0e0e0;min-height:100vh}
header{background:#16213e;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #0f3460}
header h1{font-size:18px;color:#e94560}
header select{background:#0f3460;color:#e0e0e0;border:1px solid #533483;padding:6px 12px;border-radius:4px;font-size:14px}
.container{display:flex;height:calc(100vh - 62px)}
.sidebar{width:260px;background:#16213e;overflow-y:auto;border-right:1px solid #0f3460;padding:8px}
.sidebar a{display:block;padding:8px 12px;color:#a0a0c0;text-decoration:none;font-size:13px;border-radius:4px;margin:2px 0;cursor:pointer}
.sidebar a:hover{background:#0f3460;color:#fff}
.sidebar a.active{background:#533483;color:#fff}
.main{flex:1;overflow-y:auto;padding:20px;font-family:'Cascadia Code','Consolas',monospace;font-size:13px;line-height:1.7}
.log-line{padding:2px 8px;border-radius:2px;white-space:pre-wrap;word-break:break-all}
.log-line:hover{background:#ffffff08}
.log-line.info{}
.log-line.warn{color:#f0c040}
.log-line.error{color:#e94560}
.log-line.action{color:#4ecca3;font-weight:bold}
.log-line.ai{color:#e94560;font-weight:bold}
.log-line.hand{color:#53a8b6}
.log-line.stats{color:#c0a0e0}
#status-bar{position:fixed;bottom:0;left:0;right:0;padding:6px 16px;font-size:11px;background:#0f3460;display:flex;justify-content:space-between;align-items:center}
#status-bar .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dot.live{background:#4ecca3}
.dot.dead{background:#e94560}
</style>
</head>
<body>
<header>
<h1>Sure Gamble — 对局日志</h1>
<div style="display:flex;align-items:center;gap:16px">
<select id="fileSelect" onchange="loadFile(this.value)">__OPTIONS__</select>
</div>
</header>
<div class="container">
<div class="sidebar" id="sidebar">__SIDEBAR__</div>
<div class="main" id="main">__CONTENT__</div>
</div>
<div id="status-bar">
<span><span class="dot live" id="dot"></span><span id="statusText">就绪</span></span>
<span id="lineCount"></span>
</div>
<script>
const CURRENT = "__CURRENT__";
const INITIAL_LINES = __INITIAL_LINES__;
let es = null;
let lineCount = INITIAL_LINES;

function parseLine(text) {
    let cls = "info";
    const lower = text.toLowerCase();
    if (text.includes(">>> 行动回合")) cls = "action";
    else if (lower.includes("ai=") || text.includes("理由=")) cls = "ai";
    else if (text.includes("手牌:") || text.includes("公共牌:")) cls = "hand";
    else if (text.includes("底池=") || text.includes("筹码=") || text.includes("成牌=") || text.includes("听牌=") || lower.includes("outs=")) cls = "stats";
    else if (lower.includes("error") || text.includes("错误") || text.includes("失败")) cls = "error";
    else if (lower.includes("warn") || text.includes("警告")) cls = "warn";
    text = text.replace(/AI=(\w+)/g, 'AI=<b style="color:#e94560">$1</b>');
    text = text.replace(/(手牌:)(.+)/g, '$1<span style="color:#53a8b6">$2</span>');
    text = text.replace(/(公共牌:)(.+)/g, '$1<span style="color:#53a8b6">$2</span>');
    return '<div class="log-line ' + cls + '">' + text + '</div>';
}

function appendLine(html) {
    const main = document.getElementById('main');
    const atBottom = main.scrollHeight - main.scrollTop - main.clientHeight < 40;
    main.insertAdjacentHTML('beforeend', html);
    lineCount++;
    document.getElementById('lineCount').textContent = lineCount + ' 行';
    if (atBottom) main.scrollTop = main.scrollHeight;
}

function connectSSE() {
    if (es) es.close();
    const url = '/stream?file=' + encodeURIComponent(CURRENT);
    es = new EventSource(url);
    es.onopen = () => {
        document.getElementById('dot').className = 'dot live';
        document.getElementById('statusText').textContent = '实时';
    };
    es.addEventListener('line', e => { appendLine(parseLine(e.data)); });
    es.addEventListener('newfile', e => {
        document.getElementById('statusText').textContent = '新日志: ' + e.data;
    });
    es.onerror = () => {
        document.getElementById('dot').className = 'dot dead';
        document.getElementById('statusText').textContent = '重连中...';
    };
}

function loadFile(name) {
    es.close();
    window.location.href = '/?file=' + encodeURIComponent(name);
}

document.getElementById('fileSelect').value = CURRENT;
if (CURRENT) connectSSE();
document.getElementById('lineCount').textContent = INITIAL_LINES + ' 行';
</script>
</body>
</html>"""


def parse_log_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    lower = line.lower()
    if ">>> 行动回合" in line:
        cls = "action"
    elif "ai=" in lower or "理由=" in line:
        cls = "ai"
    elif "手牌:" in line or "公共牌:" in line:
        cls = "hand"
    elif "底池=" in line or "筹码=" in line or "成牌=" in line or "听牌=" in line or "outs=" in line:
        cls = "stats"
    elif "error" in lower or "错误" in line or "失败" in line:
        cls = "error"
    elif "warn" in lower or "警告" in line:
        cls = "warn"
    else:
        cls = "info"
    line = re.sub(r'(AI=)(\w+)', r'\1<span style="color:#e94560;font-weight:bold">\2</span>', line)
    line = re.sub(r'(手牌:)(.+)', r'\1<span style="color:#53a8b6">\2</span>', line)
    line = re.sub(r'(公共牌:)(.+)', r'\1<span style="color:#53a8b6">\2</span>', line)
    return f'<div class="log-line {cls}">{line}</div>'


def list_logs():
    if not os.path.isdir(LOG_DIR):
        return []
    files = [f for f in os.listdir(LOG_DIR) if f.endswith(".log")]
    files.sort(reverse=True)
    return files


def build_options(current: str) -> str:
    opts = ""
    for f in list_logs():
        sel = " selected" if f == current else ""
        opts += f'<option value="{f}"{sel}>{f}</option>\n'
    return opts


def render_log(filename: str) -> str:
    filepath = os.path.join(LOG_DIR, filename)
    if not os.path.exists(filepath):
        return HTML.replace("__CONTENT__", "<p>文件不存在</p>").replace("__OPTIONS__", "").replace("__SIDEBAR__", "").replace("__CURRENT__", "").replace("__INITIAL_LINES__", "0")

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    parsed = [parse_log_line(line) for line in lines]
    content = "\n".join(p for p in parsed if p)

    # 侧边栏：按对局分段
    sidebar = ""
    for i, line in enumerate(lines):
        if ">>> 行动回合" in line:
            sidebar += f'<a onclick="document.getElementById(\'main\').scrollTop=document.getElementById(\'L{i+1}\').offsetTop">{line.strip()[:50]}</a>\n'
        elif "新对局" in line:
            sidebar += f'<a style="color:#666">{line.strip()[:50]}</a>\n'

    return (
        HTML.replace("__CONTENT__", content)
        .replace("__OPTIONS__", build_options(filename))
        .replace("__SIDEBAR__", sidebar)
        .replace("__CURRENT__", filename)
        .replace("__INITIAL_LINES__", str(len(lines)))
    )


def render_index() -> str:
    files = list_logs()
    rows = ""
    for f in files:
        fpath = os.path.join(LOG_DIR, f)
        size = os.path.getsize(fpath)
        rows += f'<tr><td><a href="/?file={f}">{f}</a></td><td style="color:#888">{size:,} bytes</td></tr>\n'

    content = f"""
    <h2 style="margin-bottom:16px">日志文件列表</h2>
    <table style="width:100%;border-collapse:collapse">
    <tr style="text-align:left;border-bottom:1px solid #333"><th>文件名</th><th>大小</th></tr>
    {rows}
    </table>
    <p style="margin-top:16px;color:#666">选择文件开始实时监控</p>
    """
    return (
        HTML.replace("__CONTENT__", content)
        .replace("__OPTIONS__", build_options(""))
        .replace("__SIDEBAR__", "")
        .replace("__CURRENT__", "")
        .replace("__INITIAL_LINES__", "0")
    )


class LogHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        filename = params.get("file", [None])[0]

        # SSE 流端点
        if parsed.path == "/stream" and filename:
            self._handle_sse(filename)
            return

        # HTML 页面
        if filename:
            html = render_log(filename)
        elif parsed.path == "/" or parsed.path == "/index":
            html = render_index()
        elif parsed.path.startswith("/logs/"):
            fn = parsed.path[6:]
            fpath = os.path.join(LOG_DIR, fn)
            if os.path.exists(fpath):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_response(404)
                self.end_headers()
                return
        else:
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _handle_sse(self, filename: str):
        filepath = os.path.join(LOG_DIR, filename)
        if not os.path.exists(filepath):
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        last_size = os.path.getsize(filepath)
        # 先发送已有内容
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip('\n')
                if line:
                    self._sse_event("line", line)

        last_mtime = os.path.getmtime(filepath)
        heartbeat = 0

        try:
            while True:
                time.sleep(1)
                heartbeat += 1

                # 检查是否有新日志文件（当前文件没变但目录有更新的）
                if not os.path.exists(filepath):
                    self._sse_event("newfile", "文件已删除")
                    return

                # 检查文件是否被截断/轮转
                cur_size = os.path.getsize(filepath)
                if cur_size < last_size:
                    last_size = 0  # 文件被重置，重新读取

                # 读取新增内容
                if cur_size > last_size:
                    with open(filepath, "r", encoding="utf-8") as f:
                        f.seek(last_size)
                        new_data = f.read()
                    last_size = cur_size

                    for line in new_data.split('\n'):
                        line = line.strip()
                        if line:
                            self._sse_event("line", line)
                    heartbeat = 0

                # 每 15 秒发送心跳
                if heartbeat >= 15:
                    self._sse_event("heartbeat", "")
                    heartbeat = 0

        except (BrokenPipeError, ConnectionResetError):
            pass

    def _sse_event(self, event: str, data: str):
        msg = f"event: {event}\ndata: {data}\n\n"
        try:
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise

    def log_message(self, format, *args):
        pass


def start_tunnel():
    """尝试启动 ngrok 隧道，返回公网 URL 或 None"""
    try:
        from pyngrok import ngrok, conf
        token = os.environ.get("NGROK_AUTH_TOKEN")
        if not token:
            try:
                cfg = json.load(open("config.json", encoding="utf-8"))
                token = cfg.get("log_server", {}).get("ngrok_token", "")
            except (FileNotFoundError, KeyError, json.JSONDecodeError):
                pass
        if not token:
            return None
        conf.get_default().auth_token = token
        tunnel = ngrok.connect(PORT, "http")
        return tunnel.public_url
    except Exception:
        return None


def main():
    import socket
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"日志服务器已启动")
    print(f"  本机访问: http://localhost:{PORT}")
    print(f"  局域网访问: http://{local_ip}:{PORT}")

    public_url = start_tunnel()
    if public_url:
        print(f"  公网访问: {public_url}")
    else:
        print(f"  (公网隧道未配置，仅局域网可访问)")

    server = ThreadingHTTPServer(("0.0.0.0", PORT), LogHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")


if __name__ == "__main__":
    main()
