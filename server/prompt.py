# prompt.py
import os
import yaml

_STRATEGY = None


def _load_strategy():
    global _STRATEGY
    if _STRATEGY is not None:
        return _STRATEGY
    path = "strategy.yaml"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            _STRATEGY = yaml.safe_load(f)
    return _STRATEGY or {}


def _build_hard_rules(data):
    """根据当前局面 + 策略 YAML 生成不可违反的硬性约束"""
    strategy = _load_strategy()
    if not strategy:
        return ""

    phase = data["current_phase"].name
    score = data.get("hand_score", 0)
    made = data.get("made_hand", "")
    outs = data.get("outs", 0)
    equity = data.get("required_equity", 0)
    to_call = data.get("to_call", 0)
    rules = []

    # ── preflop 规则 ──
    if phase == "PRE_FLOP":
        pf = strategy.get("preflop", {})
        for rule in pf.get("rules", []):
            if score >= rule["min_score"] and equity <= rule["max_equity"]:
                rules.append(f"手牌分数={score} >= {rule['min_score']} 且 所需胜率={equity}% <= {rule['max_equity']}% → {rule['desc']}，禁止 fold")
        if pf.get("no_fold_on_check") and to_call == 0:
            rules.append("当前为 check (免费看牌)，严禁选择 fold")

    # ── postflop 规则 ──
    else:
        pf = strategy.get("postflop", {})

        # 听牌 equity 约束
        draw_cfg = pf.get("draw_by_equity", {})
        if outs >= draw_cfg.get("min_outs", 4):
            mult = draw_cfg.get("turn_multiplier", 4) if phase in ("FLOP", "TURN") else draw_cfg.get("river_multiplier", 2)
            estimated_eq = outs * mult
            if estimated_eq > equity:
                rules.append(f"outs={outs} 估算胜率≈{estimated_eq}% > 所需{equity}%，禁止 fold")

        # 已成牌保护
        protect = pf.get("made_hand_protect", {})
        min_made = protect.get("min_made", "")
        if made and _made_rank(made) >= _made_rank(min_made):
            if to_call == 0:
                rules.append(f"已成牌={made}，免费看牌，禁止 fold")

    # ── 通用规则 ──
    general = strategy.get("general", {})
    if general.get("no_fold_on_check") and to_call == 0:
        if not any("严禁选择 fold" in r or "禁止 fold" in r for r in rules):
            rules.append("to_call=0 (免费看牌)，禁止 fold")

    if not rules:
        return ""

    return "\n".join(f"- [硬约束] {r}" for r in rules)


def _made_rank(made):
    """成牌强度排序: 高牌=0, 一对=1, 两对=2, 三条=3, 顺子=4, 同花=5, 葫芦=6, 四条=7, 同花顺=8"""
    order = {"高牌": 0, "一对": 1, "两对": 2, "三条": 3, "顺子": 4, "同花": 5, "葫芦": 6, "四条": 7, "同花顺": 8}
    return order.get(made, -1)


def build_poker_prompt(data, semantic_history):
    hand_str = ", ".join(data['hand']) if data['hand'] else "尚未看牌或已弃牌"
    public_str = ", ".join(data['public_cards']) if data['public_cards'] else "桌面无公共牌"

    hand_eval = data.get("hand_strength", "")
    score = data.get("hand_score", 0)
    made = data.get("made_hand", "")
    draws = ", ".join(data.get("draws", [])) or "无"
    outs = data.get("outs", 0)
    outs_detail = data.get("outs_detail", "")
    odds = data.get("pot_odds", "")
    equity = data.get("required_equity", 0)
    to_call = data.get("to_call", 0)

    hard_rules = _build_hard_rules(data)

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
- 跟注需支付: {to_call} ({'check' if to_call == 0 else 'call'})

### 3. 本地手牌评估 (确定数据，直接使用):
- 起手牌评级: {hand_eval} (分数: {score}/10)
- 当前成牌: {made}
- 听牌: {draws}
- Outs 数: {outs} 张 ({outs_detail})
- 底池赔率: {odds} (需 {equity}% 胜率)

### 4. 硬性约束 (绝对不可违反):
{hard_rules if hard_rules else "无特殊约束，自由决策。"}

### 5. 强制要求:
- 上述硬性约束是绝对底线，决策不得违反。
- 如果选择 Raise，金额为整数，底池的 0.5x ~ 1.2x。
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
        "to_call": 500,
        "hand_strength": "AK",
        "hand_score": 9,
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