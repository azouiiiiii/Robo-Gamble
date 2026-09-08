"""
平台适配器层：通过 config.json 的 platform 配置节，将任意平台的 API 格式映射为
内部统一的 GameState，并调用平台 API 执行决策。

核心思想：换平台只改 config.json，不改任何 Python 代码。
"""

import json
import queue
import re
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Optional, Callable

import requests

from core.gamestate import GameState, GamePhase, OpponentAction
from core.data_provider import HttpGameDataProvider, GameDataSnapshot


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _extract_field(data: dict, path: str, default=None):
    """按点号路径从嵌套 dict 提取值。不存在时返回 default。"""
    if not path:
        return default
    for key in path.split("."):
        if isinstance(data, dict) and key in data:
            data = data[key]
        elif isinstance(data, list):
            try:
                data = data[int(key)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return data


def _render_template(template, variables: dict):
    """将模板中的 {{var}} 替换为实际值。纯 {{var}} 保持原类型。"""
    if isinstance(template, str):
        m = re.fullmatch(r"\{\{(\w+)\}\}", template)
        if m:
            return variables.get(m.group(1), template)
        def _replacer(m):
            var = m.group(1)
            return str(variables.get(var, m.group(0)))
        return re.sub(r"\{\{(\w+)\}\}", _replacer, template)
    elif isinstance(template, dict):
        return {k: _render_template(v, variables) for k, v in template.items()}
    elif isinstance(template, list):
        return [_render_template(v, variables) for v in template]
    return template


# ═══════════════════════════════════════════════════════════════
# GameState 解析器：通过 field_map 将平台 JSON → GameState
# ═══════════════════════════════════════════════════════════════

class GameStateParser:
    """通过 config field_map 将任意平台的 JSON 映射为统一的 GameState"""

    def __init__(self, field_map: dict):
        self.fm = field_map

    def parse(self, raw: dict) -> GameState:
        phase_str = self._get("phase", raw, "PRE_FLOP")
        try:
            phase = GamePhase(phase_str)
        except ValueError:
            phase = GamePhase.PRE_FLOP

        actions = []
        for a in self._get("opponent_actions", raw, []):
            actions.append(OpponentAction(
                action_type=a.get("action_type", a.get("type", "call")),
                amount=a.get("amount", 0),
                phase=a.get("phase", ""),
                is_aggressor=a.get("is_aggressor", False),
                opponent_id=a.get("opponent_id", ""),
            ))

        return GameState(
            phase=phase,
            hand=self._get("hand", raw, []),
            public_cards=self._get("public_cards", raw, []),
            pot=self._get("pot", raw, 0),
            my_chips=self._get("my_chips", raw, 0),
            to_call=self._get("to_call", raw, 0),
            my_position=self._get("my_position", raw, ""),
            opponent_actions=actions,
            opponent_chips=self._get("opponent_chips", raw, 0),
            showdown=self._get("showdown", raw, None),
            table_id=str(self._get("table_id", raw, "")),
            round_id=str(self._get("round_id", raw, "")),
            network_context=self._get("network_context", raw, []),
        )

    def _get(self, internal_name: str, raw: dict, default):
        mapping = self.fm.get(internal_name, "")
        if not mapping:
            return raw.get(internal_name, default)
        return _extract_field(raw, mapping, default)


# ═══════════════════════════════════════════════════════════════
# 动作执行器：通过平台 API 执行决策
# ═══════════════════════════════════════════════════════════════

class ActionExecutor:
    """根据 config action_api 调用平台 API 执行动作"""

    def __init__(self, action_config: dict):
        self.cfg = action_config

    def execute(self, decision: dict) -> bool:
        if not self.cfg:
            return False

        url = self.cfg.get("url", "")
        if not url:
            return False

        method = self.cfg.get("method", "POST").upper()
        headers = self.cfg.get("headers", {})
        body_template = self.cfg.get("body_template", {})

        body = _render_template(body_template, {
            "action": decision.get("action", "check"),
            "amount": decision.get("amount", 0),
        })

        try:
            if method == "POST":
                resp = requests.post(url, json=body, headers=headers, timeout=5)
            elif method == "PUT":
                resp = requests.put(url, json=body, headers=headers, timeout=5)
            else:
                resp = requests.get(url, headers=headers, timeout=5)
            return resp.ok
        except requests.RequestException:
            return False


# ═══════════════════════════════════════════════════════════════
# HTTP 服务器适配器
# ═══════════════════════════════════════════════════════════════

class HttpServerAdapter:
    """
    HTTP 服务器模式。平台通过 POST 推送游戏状态，bot 返回决策。

    端点:
      POST /decide   — 推送当前游戏状态，返回决策 JSON
      POST /observe  — 推送事件（仅积累记忆，不触发决策）
      GET  /health   — 健康检查
    """

    def __init__(self, config):
        sc = config.get("platform.server", {})
        self.host = sc.get("host", "0.0.0.0")
        self.port = sc.get("port", 8765)

        field_map = config.get("platform.field_map", {})
        self.parser = GameStateParser(field_map)
        self.data_provider = HttpGameDataProvider(self.parser)

        action_cfg = config.get("platform.action_api", {})
        self.executor = ActionExecutor(action_cfg)

        self._on_decide: Optional[Callable] = None
        self._on_observe: Optional[Callable] = None
        self._on_network: Optional[Callable] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._observe_queue = queue.Queue(maxsize=int(sc.get("observe_queue_size", 500)))
        self._observe_worker: Optional[threading.Thread] = None
        self._stopping = threading.Event()

    def set_observe_handler(self, handler: Callable[[dict], None]):
        self._on_observe = handler

    def set_network_handler(self, handler: Callable[[dict], None]):
        self._on_network = handler

    def _start_observe_worker(self):
        def work():
            while not self._stopping.is_set():
                try:
                    event = self._observe_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    handler = self._on_network if event.get("event_type") in ("network", "network_batch") else self._on_observe
                    if handler:
                        handler(event)
                except Exception:
                    pass
                finally:
                    self._observe_queue.task_done()

        self._observe_worker = threading.Thread(target=work, daemon=True)
        self._observe_worker.start()

    def _enqueue_observe(self, event: dict) -> bool:
        try:
            self._observe_queue.put_nowait(event)
            return True
        except queue.Full:
            return False

    def run(self, on_decide: Callable[[GameDataSnapshot], dict]):
        """启动 HTTP 服务器（阻塞）"""
        self._on_decide = on_decide
        adapter = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/decide":
                    self._handle_decide()
                elif self.path == "/observe":
                    self._handle_observe()
                elif self.path == "/health":
                    self._send_json(200, {"status": "ok"})
                else:
                    self._send_json(404, {"error": "not found"})

            def do_GET(self):
                if self.path == "/health":
                    self._send_json(200, {"status": "ok"})
                else:
                    self._send_json(404, {"error": "not found"})

            def _handle_decide(self):
                try:
                    body = self._read_body()
                    raw = json.loads(body)
                    snapshots = adapter.data_provider.feed(raw)
                    snapshot = snapshots[-1]

                    if adapter._on_decide is None:
                        self._send_json(500, {"error": "engine not ready"})
                        return

                    decision = adapter._on_decide(snapshot)
                    adapter.executor.execute(decision)
                    self._send_json(200, decision)

                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid JSON"})
                except Exception as e:
                    self._send_json(500, {"error": str(e)})

            def _handle_observe(self):
                try:
                    body = self._read_body()
                    event = json.loads(body)
                    if adapter._enqueue_observe(event):
                        self._send_json(202, {"status": "queued"})
                    else:
                        self._send_json(503, {"error": "observe queue full"})
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "invalid JSON"})

            def _read_body(self):
                content_length = int(self.headers.get("Content-Length", 0))
                return self.rfile.read(content_length)

            def _send_json(self, code: int, data: dict):
                try:
                    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def log_message(self, format, *args):
                pass

        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._start_observe_worker()
        print(f"[Adapter] HTTP 服务器启动: http://{self.host}:{self.port}")
        print(f"[Adapter] 决策端点: POST /decide")
        print(f"[Adapter] 观察端点: POST /observe")

        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass

    def stop(self):
        self._stopping.set()
        if self._server:
            self._server.shutdown()


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

def create_adapter(config):
    """根据 config 创建平台适配器"""
    return HttpServerAdapter(config)
