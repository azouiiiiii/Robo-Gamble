"""德州扑克 AI Bridge — Socket.IO 网络采集、解析与决策路由。"""

from core.data_provider import GameDataProvider, GameDataSnapshot, HttpGameDataProvider

from .aggregator import GamePhase, GameStateAggregator
from .browser import (
    CDP_PORT,
    DEFAULT_GAME_URL,
    launch_new,
    recover_state,
    try_connect_existing,
)
from .data_provider import SocketIOGameDataProvider
from .hand_segments import HandBoundary, HandSegment, HandSegmenter, SegmentEventRef
from .interceptor import WebSocketInterceptor
from .learning_input import LearningInputSource, LearningPromptInput, build_learning_input
from .network_context import NetworkContext, NetworkEventContext
from .protocol import decode_socketio_frame_event, parse_socketio_frame
from .replay import replay_socketio_events, replay_socketio_session
from .session_recorder import SessionRecorder

__all__ = [
    "GameDataProvider", "GameDataSnapshot", "HttpGameDataProvider",
    "GamePhase", "GameStateAggregator", "SocketIOGameDataProvider",
    "WebSocketInterceptor", "try_connect_existing", "launch_new",
    "LearningInputSource", "LearningPromptInput", "build_learning_input",
    "recover_state", "CDP_PORT", "DEFAULT_GAME_URL",
    "HandBoundary", "HandSegment", "HandSegmenter", "SegmentEventRef",
    "NetworkContext", "NetworkEventContext", "SessionRecorder",
    "parse_socketio_frame", "decode_socketio_frame_event",
    "replay_socketio_events", "replay_socketio_session",
]
