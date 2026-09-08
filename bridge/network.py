"""Playwright 页面级网络采集与站点档案支持。"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from playwright.async_api import Page, Request, Response
from .network_events import NetworkEventSequencer


_DEFAULT_REDACT_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
}


def _content_type(headers: dict[str, str]) -> str:
    return headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _matches_url(url: str, patterns: list[str]) -> bool:
    return not patterns or any(re.search(pattern, url) for pattern in patterns)


def _redact_headers(headers: dict[str, str], names: set[str]) -> dict[str, str]:
    return {
        key.lower(): "[REDACTED]" if key.lower() in names else value
        for key, value in headers.items()
    }


def _decode_body(raw: bytes, content_type: str, max_bytes: int) -> tuple[Any, int, bool, str]:
    size = len(raw)
    truncated = size > max_bytes
    data = raw[:max_bytes]
    encoding = "utf-8"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        return None, size, truncated, "binary"

    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            return json.loads(text), size, truncated, encoding
        except json.JSONDecodeError:
            return text, size, truncated, encoding
    return text, size, truncated, encoding


class PageNetworkCollector:
    """采集页面 HTTP、Fetch/XHR 和失败请求，并批量发送原始事件。"""

    def __init__(
        self,
        agent_url: str,
        config: Optional[dict] = None,
        verbose: bool = False,
        on_event: Optional[Callable[[dict], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        send_enabled: bool = True,
    ):
        self.agent_url = agent_url.rstrip("/")
        self.cfg = config or {}
        self.verbose = verbose
        self.on_event = on_event
        self.send_enabled = send_enabled
        self._page: Optional[Page] = None
        self._queue: Optional[asyncio.Queue] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._closed = False
        self._sequencer = NetworkEventSequencer()
        self._request_events: dict[str, dict] = {}
        self.dropped = 0

    @property
    def enabled(self) -> bool:
        return self.cfg.get("enabled", False)

    def attach(self, page: Page):
        if not self.enabled:
            return
        self._page = page
        self._queue = asyncio.Queue(maxsize=int(self.cfg.get("max_queue_size", 500)))
        page.on("request", self._on_request)
        page.on("response", lambda response: asyncio.create_task(self._on_response(response)))
        page.on("requestfailed", self._on_request_failed)
        if self.send_enabled:
            self._sender_task = asyncio.create_task(self._sender_loop())

    def _allowed(self, request: Request) -> bool:
        types = self.cfg.get("resource_types", ["fetch", "xhr"])
        if types and request.resource_type not in types:
            return False
        url = request.url
        allowlist = self.cfg.get("url_allowlist", [])
        blocklist = self.cfg.get("url_blocklist", [])

        # 空白名单表示不限制；空黑名单表示不屏蔽任何 URL。
        if allowlist and not _matches_url(url, allowlist):
            return False
        if blocklist and _matches_url(url, blocklist):
            return False
        return url.startswith(("http://", "https://"))

    def _base(self, request: Request, kind: str) -> dict:
        request_id = f"request-{id(request)}"
        exchange_id = request_id
        stream_id = f"http:{exchange_id}"
        headers = _redact_headers(
            request.headers if self.cfg.get("capture_request_headers", True) else {},
            set(self.cfg.get("redact_headers", _DEFAULT_REDACT_HEADERS)),
        )
        event = self._sequencer.emit(
            kind=kind,
            stream_id=stream_id,
            stream_type="http",
            request_id=request_id,
            exchange_id=exchange_id,
            payload=None,
            metadata={
                "page_url": self._page.url if self._page else "",
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "request_headers": headers,
            },
        )
        if kind == "request":
            self._request_events[request_id] = event
        return event

    def _on_request(self, request: Request):
        if not self.enabled or not self._allowed(request) or not self.cfg.get("capture_request", False):
            return
        event = self._base(request, "request")
        event["phase"] = "request_started"
        event["complete"] = True
        event["payload"] = {"post_data": None}
        if self.cfg.get("capture_request_body", False):
            post_data = request.post_data
            if post_data:
                event["payload"]["post_data"] = post_data[: int(self.cfg.get("max_body_bytes", 65536))]
        self._enqueue(event)

    async def _on_response(self, response: Response):
        request = response.request
        if not self.enabled or not self._allowed(request) or not self.cfg.get("capture_response", True):
            return
        headers_event = self._base(request, "response_headers")
        headers_event["phase"] = "response_headers"
        headers_event["payload"] = {
            "status": response.status,
            "status_text": response.status_text,
            "ok": response.ok,
        }
        response_headers = _redact_headers(
            await response.all_headers() if self.cfg.get("capture_response_headers", True) else {},
            set(self.cfg.get("redact_headers", _DEFAULT_REDACT_HEADERS)),
        )
        headers_event["payload"]["response_headers"] = response_headers
        content_type = _content_type(response_headers)
        headers_event["payload"]["content_type"] = content_type
        request_event = self._request_events.get(headers_event.get("request_id"))
        headers_event["parent_event_id"] = request_event.get("event_id", "") if request_event else ""

        # 先记录响应头；读取完整 body 的耗时不能改变响应头的观测位置。
        self._enqueue(headers_event)

        if content_type == "text/event-stream":
            headers_event["complete"] = False
            return
        elif self._body_allowed(content_type):
            try:
                body, size, truncated, encoding = _decode_body(
                    await response.body(), content_type, int(self.cfg.get("max_body_bytes", 65536))
                )
                body_event = self._base(request, "response_body")
                body_event["phase"] = "response_body"
                body_event["parent_event_id"] = headers_event.get("event_id", "")
                body_event["payload"] = {
                    "body": body,
                    "body_size": size,
                    "body_truncated": truncated,
                    "body_encoding": encoding,
                }
                self._enqueue(body_event)
                return
            except Exception as exc:
                body_event = self._base(request, "response_body")
                body_event["phase"] = "response_body"
                body_event["parent_event_id"] = headers_event.get("event_id", "")
                body_event["payload"] = {"body_error": str(exc)}
                self._enqueue(body_event)
                return
        else:
            headers_event["payload"]["body_skipped_reason"] = "content_type"

    def _body_allowed(self, content_type: str) -> bool:
        allowed = self.cfg.get("content_types", ["application/json", "text/"])
        return any(content_type == item or content_type.startswith(item) for item in allowed)

    def _on_request_failed(self, request: Request):
        if not self.enabled or not self._allowed(request) or not self.cfg.get("capture_failed", True):
            return
        event = self._base(request, "requestfailed")
        event["phase"] = "request_failed"
        event["payload"] = {"failure_text": request.failure}
        parent = self._request_events.get(event.get("request_id"))
        event["parent_event_id"] = parent.get("event_id", "") if parent else ""
        self._enqueue(event)

    def _enqueue(self, event: dict):
        if self.on_event:
            self.on_event(event)
        if not self._queue or self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.verbose:
                print(f"[Network] 队列已满，丢弃事件 (dropped={self.dropped})")

    async def _sender_loop(self):
        batch_size = int(self.cfg.get("batch_size", 20))
        interval = int(self.cfg.get("flush_interval_ms", 200)) / 1000
        while not self._closed:
            batch = []
            try:
                batch.append(await asyncio.wait_for(self._queue.get(), timeout=interval))
            except asyncio.TimeoutError:
                continue
            while len(batch) < batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            payload = {"event_type": "network_batch", "schema_version": 1, "events": batch}
            await asyncio.to_thread(self._send, payload)

    def _send(self, payload: dict):
        try:
            response = requests.post(
                f"{self.agent_url}/observe",
                json=payload,
                timeout=float(self.cfg.get("send_timeout_seconds", 5)),
            )
            if not response.ok and self.verbose:
                print(f"[Network] Agent 返回 {response.status_code}")
        except requests.RequestException as exc:
            if self.verbose:
                print(f"[Network] 上报失败: {exc}")

    async def close(self):
        self._closed = True
        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
            self._sender_task = None


class SiteProfileStore:
    """保存每个站点的简化字段和事件映射。"""

    def __init__(self, directory: str = "profiles"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, host: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", host.lower()) or "unknown"
        return self.directory / f"{safe}.json"

    def load(self, host: str) -> dict:
        path = self._path(host)
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, host: str, profile: dict):
        payload = dict(profile)
        payload["host"] = host
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = self._path(host)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        temporary.replace(path)
