"""socket.io 线协议解析，兼容 v2 和 v4。"""

import json
from typing import Optional


def parse_socketio_frame(payload: str) -> Optional[dict]:
    """
    解析 socket.io 线协议帧，兼容 v2 和 v4，支持 namespace 前缀。

    格式说明:
      v2:  2["eventName", {...data}]
      v4:  42["eventName", {...data}]
      v4 namespace: 42/ns,["eventName", {...data}]

      Engine.IO types: 0=open 1=close 2=ping 3=pong 4=message
      Socket.IO types (inside message): 0=CONNECT 1=DISCONNECT 2=EVENT 3=ACK
    """
    if not payload or len(payload) < 2:
        return None

    si_type = payload[0]
    body = payload[1:]

    is_v4 = (len(payload) >= 2 and payload[1].isdigit() and payload[0] == '4')

    if is_v4:
        si_type = payload[1]
        body = payload[2:]

    # namespace 前缀: "/ns,<json>" → 剥离 namespace，只留 JSON 部分
    if body.startswith('/'):
        comma_idx = body.find(',')
        json_body = body[comma_idx + 1:] if comma_idx >= 0 else ''
    else:
        json_body = body

    # Socket.IO CONNECT / Engine.IO open
    if si_type == '0':
        if not json_body:
            return {"_type": "connect"}
        try:
            data = json.loads(json_body)
            return {"_type": "connect", **(data if isinstance(data, dict) else {})}
        except json.JSONDecodeError:
            return {"_type": "connect", "_raw": body}

    # Socket.IO EVENT
    if si_type == '2':
        if not json_body:
            return None
        try:
            parsed = json.loads(json_body)
            if isinstance(parsed, list) and len(parsed) >= 2:
                return {"_type": "event", "name": parsed[0], "data": parsed[1]}
            return {"_type": "event", "_raw": parsed}
        except json.JSONDecodeError:
            return None

    # Socket.IO ACK
    if si_type == '3':
        if not json_body:
            return {"_type": "ack", "data": {}}
        try:
            return {"_type": "ack", "data": json.loads(json_body)}
        except json.JSONDecodeError:
            return {"_type": "ack", "_raw": body}

    # Engine.IO ping/pong (v4 only, ignore silently)
    if payload[0] in ('2', '3'):
        return None

    if si_type == '1':
        return {"_type": "disconnect"}

    return None


def decode_socketio_frame_event(frame_event: dict) -> dict:
    """把原始 WebSocket 帧解码为带来源引用的 Socket.IO 事件。

    ``parse_socketio_frame`` 仍是唯一的线协议解析器。此函数只包装其结果，
    让实时处理和会话回放得到相同结构，并保留回到原始帧的 ID 与顺序信息。
    ``packet`` 为 None 时，原始帧仍可由 ``source_frame_id`` 在会话记录中查回。
    """
    if not isinstance(frame_event, dict):
        raise TypeError("frame_event must be an object")
    if (
        frame_event.get("event_type") != "raw_network_event"
        or frame_event.get("stream_type") != "websocket"
        or frame_event.get("kind") != "frame"
    ):
        raise ValueError("frame_event must describe a raw WebSocket frame")

    payload = frame_event.get("payload")
    packet = parse_socketio_frame(payload) if isinstance(payload, str) else None
    frame_id = str(frame_event.get("event_id", ""))
    return {
        "event_type": "decoded_socketio_event",
        "schema_version": 1,
        "event_id": frame_id,
        "source_frame_id": frame_id,
        "connection_id": frame_event.get("connection_id", ""),
        "stream_id": frame_event.get("stream_id", ""),
        "stream_sequence": frame_event.get("stream_sequence", 0),
        "ingest_sequence": frame_event.get("ingest_sequence", 0),
        "direction": frame_event.get("direction", ""),
        "observed_at": frame_event.get("observed_at", ""),
        "decode_status": "decoded" if packet is not None else "no_packet",
        "packet": packet,
    }
