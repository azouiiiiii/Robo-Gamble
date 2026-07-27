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
