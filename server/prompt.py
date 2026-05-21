# prompt.py
def build_poker_prompt(data, semantic_history):
    # 处理空手牌和公共牌的显示逻辑
    hand_str = ", ".join(data['hand']) if data['hand'] else "尚未看牌或已弃牌"
    public_str = ", ".join(data['public_cards']) if data['public_cards'] else "桌面无公共牌"
    
    hand_eval = data.get("hand_strength", "")
    made = data.get("made_hand", "")
    draws = ", ".join(data.get("draws", [])) or "无"
    outs = data.get("outs", 0)
    outs_detail = data.get("outs_detail", "")
    odds = data.get("pot_odds", "")
    equity = data.get("required_equity", 0)

    prompt = f"""
你是一位顶级的德州扑克策略专家。

### 1. 历史动作回顾:
{semantic_history}

### 2. 当前局势:
- 阶段: {data['current_phase'].name}
- 手牌: {hand_str}
- 公共牌: {public_str}
- 底池: {data['pot']}
- 我的筹码: {data['my_chips']}

### 3. 本地手牌评估 (确定数据，直接使用):
- 起手牌评级: {hand_eval}
- 当前成牌: {made}
- 听牌: {draws}
- Outs 数: {outs} 张 ({outs_detail})
- 底池赔率: {odds} (需 {equity}% 胜率)

### 4. 强制要求:
- 基于上述确定性的手牌评估数据做决策，不要自行计算赔率。
- 如果选择 Raise，金额必须为整数，通常为底池的 0.5x 到 1.2x。
- 必须只输出纯 JSON 格式，不得包含任何解释文字。

### 输出格式:
{{
  "action": "fold/check/call/raise",
  "amount": 0,
  "analysis": "原因",
  "confidence": 0.9
}}
"""
    return prompt


if __name__ == "__main__":
    from enum import Enum

    class FakePhase(Enum):
        PRE_FLOP = 1
        FLOP = 2

    data = {
        "current_phase": FakePhase.PRE_FLOP,
        "hand": ["Ah", "Kh"],
        "public_cards": [],
        "pot": 1000,
        "my_chips": 5000,
        "hand_strength": "AK",
        "made_hand": "高牌",
        "draws": [],
        "outs": 0,
        "outs_detail": "无",
        "pot_odds": "1000:500",
        "required_equity": 33.3,
    }

    prompt = build_poker_prompt(data, "本局刚开始。")
    assert "PRE_FLOP" in prompt
    assert "Ah, Kh" in prompt
    assert "AK" in prompt
    assert "高牌" in prompt
    assert "33.3" in prompt
    assert "1000" in prompt
    assert "5000" in prompt
    print("[PASS] prompt contains all fields")

    # 空手牌
    data2 = data.copy()
    data2["hand"] = []
    prompt2 = build_poker_prompt(data2, "")
    assert "尚未看牌" in prompt2
    print("[PASS] empty hand handled")

    # 有听牌
    data3 = data.copy()
    data3["current_phase"] = FakePhase.FLOP
    data3["public_cards"] = ["Qh", "Jh", "2d"]
    data3["made_hand"] = "高牌"
    data3["draws"] = ["同花听牌", "两头顺听牌"]
    data3["outs"] = 13
    data3["outs_detail"] = "同花:9张 | 两头顺:4张"
    prompt3 = build_poker_prompt(data3, "")
    assert "同花听牌" in prompt3
    assert "13" in prompt3
    print("[PASS] draws and outs included")

    print("\n全部通过")