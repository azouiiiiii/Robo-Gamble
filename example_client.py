"""
示例：外部平台如何对接德州扑克 AI 决策引擎。

启动引擎:
  python main.py
  → HTTP 服务器监听 http://localhost:8765

══════════════════════════════════════════════
方式 1：平台直接 POST /decide（字段名与 field_map 匹配）
══════════════════════════════════════════════

  平台发送的 JSON 字段名由 config.json → platform.field_map 定义。
  示例 field_map:
    "hand" → "my_cards"      → 平台发 "my_cards": ["Ah", "Kh"]
    "pot"  → "total_pot"     → 平台发 "total_pot": 1500
    "phase" → "current_stage" → 平台发 "current_stage": "PRE_FLOP"

  如果平台发的字段名与内部 GameState 一致（hand, pot, phase 等），
  field_map 可以留空或配成 1:1 映射。

══════════════════════════════════════════════
方式 2：平台随时 POST /observe（积累对手记忆）
══════════════════════════════════════════════

  在非决策时刻，平台可以推送事件来积累对手行为分析：
  POST /observe  {"phase": "PRE_FLOP", "type": "opponent_raised", "amount": 500}
"""

import requests
import json


SERVER = "http://localhost:8765"


def decide(game_state: dict) -> dict:
    """向决策引擎发送游戏状态，获取决策"""
    resp = requests.post(f"{SERVER}/decide", json=game_state, timeout=15)
    resp.raise_for_status()
    return resp.json()


def observe(event: dict):
    """推送观察事件（非决策时刻）"""
    requests.post(f"{SERVER}/observe", json=event, timeout=5)


if __name__ == "__main__":
    # ─── 场景 1: 平台字段名 = 内部字段名（field_map 配成 1:1）───
    print("=" * 50)
    print("场景 1: 翻前 AKs，对手加注 500")
    print("平台发送的 JSON 使用 field_map 映射后的字段名")
    print("=" * 50)

    state_1 = {
        # 以下字段名来自 config.json → platform.field_map
        "current_stage": "PRE_FLOP",
        "my_cards": ["Ah", "Kh"],
        "community_cards": [],
        "total_pot": 1500,
        "player": {"stack": 5000, "position": "BTN"},
        "required_call": 500,
        "action_history": [
            {"action_type": "raise", "amount": 500, "phase": "PRE_FLOP", "is_aggressor": True}
        ],
        "opponent": {"stack": 6000},
        "table": {"id": "table_01"},
        "round": {"id": "round_003"},
    }

    print(json.dumps(state_1, ensure_ascii=False, indent=2))
    print()

    try:
        result = decide(state_1)
        print(f"决策: {json.dumps(result, ensure_ascii=False)}")
    except requests.ConnectionError:
        print("[跳过] 引擎未启动，请先运行 python main.py")
    except Exception as e:
        print(f"错误: {e}")

    # ─── 场景 2: 翻牌圈顶对顶踢 ───
    print()
    print("=" * 50)
    print("场景 2: 翻牌圈，顶对顶踢，对手持续下注")
    print("=" * 50)

    state_2 = {
        "current_stage": "FLOP",
        "my_cards": ["Ah", "Kh"],
        "community_cards": ["Kd", "7s", "2c"],
        "total_pot": 3000,
        "player": {"stack": 4500, "position": "BTN"},
        "required_call": 1000,
        "action_history": [
            {"action_type": "raise", "amount": 500, "phase": "PRE_FLOP", "is_aggressor": True},
            {"action_type": "bet", "amount": 1000, "phase": "FLOP", "is_aggressor": True},
        ],
        "opponent": {"stack": 5500},
        "table": {"id": "table_01"},
        "round": {"id": "round_003"},
    }

    print(json.dumps(state_2, ensure_ascii=False, indent=2))
    print()

    try:
        result = decide(state_2)
        print(f"决策: {json.dumps(result, ensure_ascii=False)}")
    except requests.ConnectionError:
        print("[跳过] 引擎未启动")
    except Exception as e:
        print(f"错误: {e}")
