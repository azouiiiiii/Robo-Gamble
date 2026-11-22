"""从原始会话重建与实时处理相同的 Socket.IO 解码事件。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .network_events import NetworkSequenceReconstructor
from .protocol import decode_socketio_frame_event
from .session_recorder import SessionRecorder


def replay_socketio_events(frame_events: Iterable[dict]) -> list[dict]:
    """按采集顺序重放 WebSocket 帧，并使用实时路径的同一解码入口。"""
    frames = [
        event for event in frame_events
        if isinstance(event, dict)
        and event.get("event_type") == "raw_network_event"
        and event.get("stream_type") == "websocket"
        and event.get("kind") == "frame"
    ]
    ordered = NetworkSequenceReconstructor.reconstruct(frames)
    return [decode_socketio_frame_event(frame) for frame in ordered]


def replay_socketio_session(
    path: str | Path, hand_id: str | None = None
) -> list[dict]:
    """读取一份 JSONL 会话，可选按手牌分段 ID 取出 Socket.IO 事件。"""
    records = SessionRecorder.load_records(path)
    frame_events = [
        event for event in records
        if event.get("event_type") == "raw_network_event"
        and event.get("stream_type") == "websocket"
        and event.get("kind") == "frame"
    ]

    if hand_id is not None:
        segment = next((
            event for event in records
            if event.get("event_type") == "hand_segment"
            and str(event.get("hand_id")) == str(hand_id)
        ), None)
        if segment is None:
            raise KeyError(f"hand segment not found: {hand_id}")
        frame_ids = {
            str(ref.get("event_id", ""))
            for ref in segment.get("events", [])
            if isinstance(ref, dict)
        }
        frame_events = [
            event for event in frame_events
            if str(event.get("event_id", "")) in frame_ids
        ]

    return replay_socketio_events(frame_events)
