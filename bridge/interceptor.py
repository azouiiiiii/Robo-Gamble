"""CDP WebSocket 拦截器：截获浏览器 WebSocket 帧，路由决策。"""

import asyncio
import json
import threading
import time
from collections import defaultdict
from typing import Optional

import requests
from playwright.async_api import Page, WebSocket

from .protocol import decode_socketio_frame_event
from .aggregator import GamePhase
from .data_provider import SocketIOGameDataProvider
from .network import PageNetworkCollector
from .network_context import NetworkEventContext
from .network_events import NetworkEventSequencer
from .session_recorder import SessionRecorder
from .hand_segments import HandBoundary, HandSegment, HandSegmenter
from .boundary_listener import BoundaryListener
from .modes import BridgeMode, normalize_mode


class WebSocketInterceptor:
    """
    Playwright CDP WebSocket 拦截器。
    拦截页面所有 WebSocket 连接的收发帧，解析 socket.io 协议。
    """

    def __init__(self, agent_url: str = "http://localhost:8765", verbose: bool = False,
                 network_config: Optional[dict] = None,
                 mode: str | BridgeMode = BridgeMode.VERIFY,
                 session_dir: str = "learning_sessions",
                 capture_config: Optional[dict] = None):
        self.agent_url = agent_url
        self.mode = normalize_mode(mode)
        self.session_dir = session_dir
        self.capture_config = dict(capture_config or {})
        self.data_provider: Optional[SocketIOGameDataProvider] = None
        self.auto_mode: bool = self.mode == BridgeMode.AUTO
        self.verbose: bool = verbose
        self._page: Optional[Page] = None
        self._loop = asyncio.get_event_loop()
        self.recorder = None
        self.segmenter = None
        self.boundary_listener = None
        if self.mode == BridgeMode.LEARN:
            self.segmenter = HandSegmenter(
                overlap_ms=int(self.capture_config.get("overlap_ms", 1000)),
                on_boundary=self._on_capture_boundary,
                on_segment=self._on_captured_segment,
            )
            self.recorder = SessionRecorder(
                session_dir, on_event=self.segmenter.add_event
            )
            self.boundary_listener = BoundaryListener(
                self._mark_capture_boundary,
                key=str(self.capture_config.get("boundary_key", "backquote")),
                verbose=verbose,
            )
        effective_network_config = dict(network_config or {})
        if self.mode == BridgeMode.LEARN:
            # 采集阶段需要请求与响应的关联；不改变调用方传入的原 dict。
            effective_network_config["capture_request"] = True
            effective_network_config["capture_request_body"] = bool(
                effective_network_config.get("capture_request_body", False)
            )
        self.network_context = NetworkEventContext(recorder=self.recorder)
        self._network_sequencer = NetworkEventSequencer()
        self._network = PageNetworkCollector(
            agent_url, effective_network_config, verbose=verbose,
            on_event=self.network_context.observe,
            send_enabled=self.mode == BridgeMode.AUTO,
            sequencer=self._network_sequencer,
        )
        self._last_decision: Optional[dict] = None
        self._decision_count: int = 0
        self._ws_connected: bool = False
        self._frame_count: int = 0
        self._settlement_sent: bool = False   # 结算只发一次
        self._deciding: bool = False          # 决策进行中锁
        self._last_observed_count: int = 0    # 已上报的 action_history 数量
        self._hand_id: int = 0                # 当前局号, 新局+1, 防旧决策打到新局
        self._last_verify_key: Optional[str] = None
        self._last_capture_boundary_ns = 0

    async def attach(self, page: Page, auto_mode: Optional[bool] = None):
        self._page = page
        if auto_mode is True:
            self.mode = BridgeMode.AUTO
            self.auto_mode = True

        def on_websocket(ws: WebSocket):
            url = ws.url
            connection_id = f"ws-{id(ws)}"
            print(f"[Bridge] WebSocket 连接: {url[:80]}...")
            self._ws_connected = True

            ws.on('framereceived', lambda frame: self._on_frame("RECV", frame, ws, connection_id))
            ws.on('framesent', lambda frame: self._on_frame("SENT", frame, ws, connection_id))
            ws.on('close', lambda: self._on_close())

        page.on('websocket', on_websocket)
        self._network.attach(page)
        if self.mode == BridgeMode.LEARN:
            try:
                await page.expose_binding(
                    "__pokerCaptureBoundary", self._on_page_boundary
                )
                await page.add_init_script("""
                    (() => {
                        window.addEventListener('keydown', (event) => {
                            if (event.code !== 'Backquote' || event.repeat) return;
                            event.preventDefault();
                            event.stopPropagation();
                            if (typeof window.__pokerCaptureBoundary === 'function') {
                                window.__pokerCaptureBoundary('page_backquote');
                            }
                        }, true);
                    })();
                """)
                print("[Capture] 已监听页面反引号键 ` / ~（页面聚焦时可用）")
            except Exception as exc:
                print(f"[Capture] 页面反引号监听不可用: {exc}")
        if self.boundary_listener and self.boundary_listener.start():
            print("[Capture] 已监听反引号键 ` / ~，按键切分牌局")

    async def close(self):
        if self.boundary_listener:
            self.boundary_listener.stop()
        if self.segmenter:
            final_segment = self.segmenter.close()
            self._on_captured_segment(final_segment)
        await self._network.close()
        if self.recorder:
            self.recorder.close()

    def _on_frame(self, _direction: str, frame, _ws: WebSocket, connection_id: str = ""):
        if isinstance(frame, (str, bytes)):
            payload = frame
        else:
            payload = getattr(frame, 'payload', str(frame))
        if not payload:
            return

        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        elif not isinstance(payload, str):
            payload = str(payload)

        frame_event = self._network_sequencer.emit(
            kind="frame",
            stream_id=f"websocket:{connection_id or 'unknown'}",
            stream_type="websocket",
            connection_id=connection_id or "unknown",
            direction=_direction,
            payload=payload,
            metadata={"url": getattr(_ws, "url", "")},
        )
        if self.recorder:
            self.recorder.record_event(frame_event)

        # learn 模式只记录原始帧，不运行解释器或触发决策。
        if self.mode == BridgeMode.LEARN:
            if self.verbose:
                print(f"[Capture] 已记录 WebSocket {_direction} 帧 ({len(payload)} bytes)")
            return

        decoded_event = decode_socketio_frame_event(frame_event)
        parsed = decoded_event["packet"]
        if parsed is None:
            return

        etype = parsed.get("_type", "?")
        ename = parsed.get("name", "")
        edata = parsed.get("data", {})

        # 检测 parseMsg 包装格式：事件名是 socket ID，真正 action 在 data.data.action 里
        _has_wrapped = (
            isinstance(edata, dict)
            and "data" in edata
            and isinstance(edata["data"], dict)
            and "action" in edata["data"]
        )

        if etype == "event":
            self._frame_count += 1
            if self.verbose:
                data_preview = str(edata)[:120]
                tag = "📦" if _has_wrapped else "←"
                print(f"[Bridge] {tag} {ename} | {data_preview}")

        if etype == "connect":
            sid = parsed.get("sid", "?")
            print(f"[Bridge] 🔌 socket.io 已连接 (sid={sid})")

        # newGame → 重置结算标记
        if etype == "event" and ename == "online":
            action = edata.get("action", "") if isinstance(edata, dict) else ""
            if action == "newGame":
                self._settlement_sent = False
                self._last_observed_count = 0
                self._deciding = False
                self._last_state_key = None
                self._hand_id += 1
                print(f"[Bridge] 🆕 新对局 (hand={self._hand_id})")

        if etype == "event":
            if self.data_provider:
                was_settlement = self.data_provider.phase == GamePhase.SETTLEMENT
                self.data_provider.feed(decoded_event)
                if self.mode == BridgeMode.VERIFY:
                    self._show_verification_snapshot()
                else:
                    self._check_turn()
                    self._flush_opponent_actions()  # 持续上报对手动作给 agent
                    # 结算：gameOver 事件后立刻上报，让 agent 归档手牌
                    if not was_settlement and self.data_provider.phase == GamePhase.SETTLEMENT:
                        if not self._settlement_sent:
                            self._settlement_sent = True
                            print("[Bridge] 🏁 检测到结算，上报归档...")
                            self._trigger_settlement()
            else:
                uid = self._extract_user_id(parsed)
                if uid:
                    print(f"[Bridge] 👤 识别玩家 userId={uid}")
                    self.data_provider = SocketIOGameDataProvider(
                        uid, verbose=self.verbose
                    )
                    self.data_provider.feed(decoded_event)
                    if self.mode == BridgeMode.VERIFY:
                        self._show_verification_snapshot()
                    else:
                        self._check_turn()
                        self._flush_opponent_actions()  # 首次创建也要上报已有动作
                elif self.verbose and ename in ("online", "gameInfo"):
                    print("[Bridge] ⚠️ 收到游戏数据但尚未识别用户，等待 userInfo...")

    def _on_page_boundary(self, _source, key: str = "page_backquote"):
        self._mark_capture_boundary(key)

    def _mark_capture_boundary(self, key: str = "backquote"):
        if not self.segmenter:
            return
        now = time.monotonic_ns()
        if now - self._last_capture_boundary_ns < 300_000_000:
            return
        self._last_capture_boundary_ns = now
        try:
            boundary = self.segmenter.mark_boundary(key)
            if self.verbose:
                print(f"[Capture] 🏁 {boundary.boundary_id}, overlap={boundary.overlap_ms}ms")
        except Exception as exc:
            if self.verbose:
                print(f"[Capture] 边界处理失败: {exc}")

    def _on_capture_boundary(self, boundary: HandBoundary):
        if self.recorder:
            self.recorder.record_boundary(boundary.to_dict())

    def _on_captured_segment(self, segment: HandSegment):
        if self.recorder:
            self.recorder.record_segment(segment.to_dict())
        if self.verbose:
            print(f"[Capture] 已封存 {segment.hand_id}: {len(segment.event_refs)} 个事件引用")

    def _extract_user_id(self, parsed: dict) -> Optional[str]:
        """从多种消息格式中提取 userId"""
        data = parsed.get("data", {})
        name = parsed.get("name", "")

        # 新格式: online 事件 → action=userInfo, data.userInfo.userId
        if name == "online" and data.get("action") == "userInfo":
            inner = data.get("data", {})
            user = inner.get("userInfo", {})
            uid = user.get("userId", "")
            if uid:
                return uid

        # parseMsg 包装格式: data.data.payload.userInfo.userId
        for path in [
            lambda d: d.get("userInfo", {}).get("userId"),
            lambda d: d.get("data", {}).get("payload", {}).get("userInfo", {}).get("userId"),
            lambda d: d.get("payload", {}).get("userInfo", {}).get("userId"),
        ]:
            try:
                result = path(data)
                if result:
                    return result
            except Exception:
                continue
        return None

    def _flush_opponent_actions(self):
        """检测新的对手动作并上报 /observe，触发 L1+L2 持续更新"""
        if self.mode != BridgeMode.AUTO:
            return
        if self.data_provider is None:
            return
        all_actions = self.data_provider.action_history
        new_actions = all_actions[self._last_observed_count:]
        if not new_actions:
            return
        self._last_observed_count = len(all_actions)

        # 按对手分组，排除 AI 自己的动作
        my_id = self.data_provider.my_user_id
        by_opponent: dict[str, list[dict]] = defaultdict(list)
        for a in new_actions:
            uid = a.get("userId", "")
            if uid and uid != my_id:
                by_opponent[uid].append({
                    "action_type": a.get("actionCommand", ""),
                    "amount": a.get("actionSize", 0),
                    "phase": a.get("phase", ""),
                })

        if not by_opponent:
            return

        # 异步 POST /observe（每个对手一次，不阻塞 WebSocket 回调线程）
        for opp_id, events in by_opponent.items():
            payload = {"opp_id": opp_id, "events": events}
            try:
                asyncio.run_coroutine_threadsafe(
                    self._do_observe_opponent(payload), self._loop
                )
            except Exception:
                threading.Thread(
                    target=self._do_observe_opponent_sync,
                    args=(payload,), daemon=True
                ).start()

    async def _do_observe_opponent(self, payload: dict):
        try:
            await asyncio.to_thread(self._call_observe, payload)
        except Exception:
            pass

    def _do_observe_opponent_sync(self, payload: dict):
        self._call_observe(payload)

    def _check_turn(self):
        if self.mode != BridgeMode.AUTO:
            return
        if self.data_provider is None:
            return
        if not self.data_provider.is_my_turn:
            return
        if self._deciding:
            # 上一轮决策还在进行中，跳过（防止并发请求打架）
            if self.verbose:
                print("[Bridge] ⏳ 决策进行中，跳过重复触发")
            return

        snapshot = self.data_provider.current_snapshot()
        if snapshot is None:
            return
        current_state = snapshot.raw
        if self.network_context.context.snapshot():
            current_state["network_context"] = self.network_context.context.snapshot()
        if len(current_state.get("my_cards", [])) == 0:
            # 手牌数据未到，不触发决策
            return

        cards_str = ','.join(sorted(current_state.get('community_cards', [])))
        state_key = f"{current_state['current_stage']}_{current_state['total_pot']}_{current_state['required_call']}_{cards_str}"
        if hasattr(self, '_last_state_key') and self._last_state_key == state_key:
            return
        self._last_state_key = state_key

        if self.verbose:
            print(f"[Bridge] 🎯 检测到我的回合! phase={current_state['current_stage']} pot={current_state['total_pot']} to_call={current_state['required_call']}")

        try:
            asyncio.run_coroutine_threadsafe(self._do_decide_async(self._hand_id), self._loop)
        except Exception:
            threading.Thread(target=self._do_decide, daemon=True).start()

    def _trigger_settlement(self):
        """结算时上报 /observe，带对手ID和摊牌信息。"""
        if self.data_provider is None:
            return
        snapshot = self.data_provider.current_snapshot()
        if snapshot is None:
            return
        gs = dict(snapshot.raw)
        showdown = gs.pop("showdown", None)

        # 收集所有对手 ID
        opponent_ids = [
            uid for uid in self.data_provider.players
            if uid != self.data_provider.my_user_id
        ]

        payload = {
            "phase": "SETTLEMENT",
            "showdown": showdown,
            "opponent_ids": opponent_ids,
        }
        try:
            asyncio.run_coroutine_threadsafe(self._do_observe_async(payload), self._loop)
        except Exception:
            threading.Thread(target=self._do_observe_sync, args=(payload,), daemon=True).start()

    async def _do_observe_async(self, payload: dict):
        ok = await asyncio.to_thread(self._call_observe, payload)
        if ok:
            sd = payload.get("showdown") or {}
            opp_showed = sd.get("opponent_showed", [])
            extra = f"，对手摊牌={opp_showed}" if opp_showed else ""
            print(f"[Bridge] 结算已归档{extra}")

    def _do_observe_sync(self, payload: dict):
        self._call_observe(payload)

    def _call_observe(self, payload: dict) -> bool:
        try:
            resp = requests.post(f"{self.agent_url}/observe", json=payload, timeout=10)
            return resp.ok
        except Exception:
            return False

    async def _do_decide_async(self, hand_id: int = 0):
        """异步决策流程"""
        if self.mode != BridgeMode.AUTO:
            return
        if self.data_provider is None:
            return

        self._deciding = True
        try:
            snapshot = self.data_provider.current_snapshot()
            if snapshot is None:
                return
            gs = dict(snapshot.raw)
            network_events = self.network_context.context.snapshot()
            if network_events:
                gs["network_context"] = network_events
            self._decision_count += 1
            self._print_decision_request(gs)

            decision = await asyncio.to_thread(self._call_agent, gs)

            # 校验: 决策返回时对局是否已结束/切换
            if hand_id != self._hand_id:
                print(f"[Bridge] ⏭ 旧决策作废 (hand={hand_id} → {self._hand_id})")
                return

            if decision:
                if self.auto_mode:
                    await self._execute_decision_async(decision)
                else:
                    await self._show_suggestion_async(decision)
        finally:
            self._deciding = False

    def _print_decision_request(self, gs: dict):
        print(f"\n{'='*55}")
        print(f"[Bridge] 第 {self._decision_count} 次决策请求")
        print(f"  阶段: {gs['current_stage']}")
        print(f"  手牌: {gs['my_cards']}")
        print(f"  公共牌: {gs['community_cards']}")
        print(f"  底池: {gs['total_pot']}  |  跟注: {gs['required_call']}")
        print(f"  筹码: {gs['player']['stack']}  |  位置: {gs['player']['position']}")
        print(f"{'='*55}")

    def _call_agent(self, gs: dict) -> Optional[dict]:
        """同步 HTTP 调用 agent（在线程中执行）"""
        try:
            resp = requests.post(f"{self.agent_url}/decide", json=gs, timeout=55)
            if not resp.ok:
                body = resp.text[:300]
                print(f"[Bridge] Agent 返回 {resp.status_code}: {body}")
                return None
            decision = resp.json()
            self._last_decision = decision
            self._print_decision(decision)
            return decision
        except requests.ConnectionError:
            print("[Bridge] Agent 引擎未启动 (python main.py)")
            return None
        except Exception as e:
            print(f"[Bridge] 决策错误: {e}")
            return None

    def _print_decision(self, decision: dict):
        action = decision.get("action", "?")
        amount = decision.get("amount", 0)
        confidence = decision.get("confidence", 0)
        reason = decision.get('reasoning', '')
        print(f"[Bridge] Agent 决策: {action.upper()}"
              + (f" {amount}" if amount else "")
              + f" (置信度: {confidence:.0%})"
              + (f" | {reason[:60]}..." if reason else ""))

    def _do_decide(self):
        """同步版决策（fallback，仅打印不执行页面操作）"""
        if self.mode != BridgeMode.AUTO:
            return
        if self.data_provider is None:
            return
        self._deciding = True
        try:
            snapshot = self.data_provider.current_snapshot()
            if snapshot is None:
                return
            gs = dict(snapshot.raw)
            self._decision_count += 1
            self._print_decision_request(gs)
            self._call_agent(gs)
        finally:
            self._deciding = False

    def _show_verification_snapshot(self):
        """验证模式：将当前 Socket.IO 聚合结果显示到页面。"""
        if self.mode != BridgeMode.VERIFY or self.data_provider is None:
            return
        snapshot = self.data_provider.current_snapshot()
        if snapshot is None:
            return
        state = snapshot.state
        key = "|".join([
            state.phase.value, ",".join(state.hand), ",".join(state.public_cards),
            str(state.pot), str(state.to_call), state.my_position,
        ])
        if key == self._last_verify_key:
            return
        self._last_verify_key = key
        payload = {
            "provider": snapshot.provider_version,
            "phase": state.phase.value,
            "hand": state.hand,
            "public_cards": state.public_cards,
            "pot": state.pot,
            "to_call": state.to_call,
            "position": state.my_position,
            "actions": len(state.opponent_actions),
            "source": snapshot.source_kind,
        }
        try:
            asyncio.run_coroutine_threadsafe(
                self._show_verification_overlay(payload), self._loop
            )
        except Exception:
            pass

    async def _show_verification_overlay(self, data: dict):
        if not self._page:
            return
        text = (
            f"解析入口: {data['provider']}\\n"
            f"阶段: {data['phase']}\\n"
            f"手牌: {', '.join(data['hand']) or '-'}\\n"
            f"公共牌: {', '.join(data['public_cards']) or '-'}\\n"
            f"底池: {data['pot']}  跟注: {data['to_call']}\\n"
            f"位置: {data['position'] or '-'}  动作数: {data['actions']}\\n"
            "[验证模式：当前 Socket.IO 聚合器；未调用决策 Agent]"
        )
        js_text = json.dumps(text, ensure_ascii=False)
        js = f"""
        (() => {{
            const old = document.getElementById('socketio-verification');
            if (old) old.remove();
            const div = document.createElement('pre');
            div.id = 'socketio-verification';
            div.textContent = {js_text};
            div.style.cssText = 'position:fixed;top:10px;left:10px;z-index:99999;'
                + 'background:rgba(20,20,20,0.9);color:#7dd3fc;padding:12px 16px;'
                + 'border:1px solid #38bdf8;border-radius:8px;font:13px monospace;'
                + 'line-height:1.5;white-space:pre;pointer-events:none;';
            document.body.appendChild(div);
        }})()
        """
        try:
            await self._page.evaluate(js)
        except Exception:
            pass

    async def _execute_decision_async(self, decision: dict):
        action = decision.get("action", "check")
        amount = decision.get("amount", 0)
        cmd_map = {"fold": "fold", "call": "call", "check": "check",
                    "raise": f"raise:{amount}", "bet": f"raise:{amount}", "allin": "allin"}
        cmd = cmd_map.get(action, "check")

        # 递归遍历 Vue 2 组件树找到 socket.io 实例
        # 协议: socket.emit('action', { target: '', payload: { command: '...' } }) on /socket nsp
        js = f"""
        (() => {{
            try {{
                const app = document.querySelector('#app');
                if (!app || !app.__vue__) return 'vue root not found';

                function findSocket(vm) {{
                    if (vm.socket && typeof vm.socket.emit === 'function') return vm.socket;
                    if (vm.$children) {{
                        for (const child of vm.$children) {{
                            const found = findSocket(child);
                            if (found) return found;
                        }}
                    }}
                    return null;
                }}

                const sock = findSocket(app.__vue__);
                if (sock) {{
                    sock.emit('action', {{ target: '', payload: {{ command: '{cmd}' }} }});
                    return 'sent: {cmd}';
                }}
                return 'socket not found';
            }} catch(e) {{ return 'error: ' + e.message; }}
        }})()
        """
        try:
            result = await self._page.evaluate(js) if self._page else "no page"
            print(f"[Bridge] 自动执行: {cmd} ({result})")
        except Exception as e:
            print(f"[Bridge] 执行失败: {e}")

    async def _show_suggestion_async(self, decision: dict):
        action = decision.get("action", "check")
        amount = decision.get("amount", 0)
        confidence = decision.get("confidence", 0)
        text = f"{action.upper()}" + (f" {amount}" if amount else "")
        js = f"""
        (() => {{
            const existing = document.getElementById('agent-suggestion');
            if (existing) existing.remove();
            const div = document.createElement('div');
            div.id = 'agent-suggestion';
            div.style.cssText = 'position:fixed;top:10px;right:10px;z-index:99999;'
                + 'background:rgba(0,0,0,0.85);color:#0f0;padding:12px 18px;'
                + 'border-radius:10px;font-family:monospace;font-size:16px;'
                + 'border:2px solid #0f0;pointer-events:none;';
            div.textContent = 'AI: {text} ({int(confidence*100)}%)';
            document.body.appendChild(div);
            setTimeout(() => div.remove(), 5000);
        }})()
        """
        try:
            if self._page:
                await self._page.evaluate(js)
        except Exception:
            pass

    def _on_close(self):
        self._ws_connected = False
        print("[Bridge] WebSocket 已断开")
