"""德州扑克 AI Bridge — CDP WebSocket 桥接器。

从浏览器拦截 socket.io 游戏数据，转发给 AI Agent 决策引擎。
"""

from .protocol import parse_socketio_frame
from .aggregator import GamePhase, GameStateAggregator
from .interceptor import WebSocketInterceptor
from .browser import (
    try_connect_existing,
    launch_new,
    recover_state,
    CDP_PORT,
    DEFAULT_GAME_URL,
)
