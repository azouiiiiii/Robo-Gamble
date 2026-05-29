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

        # 翻前 equity 覆写：底池为0时底池赔率=100%，用筹码占比替代
        eq_override = pf.get("equity_override", {})
        if eq_override.get("when_pot_zero") == "stack_pct" and data.get("pot", 0) == 0:
            chips = data.get("my_chips", 0)
            equity = to_call / (chips + to_call) * 100 if (chips + to_call) > 0 else 0

        # hard_continue_rules: 强牌不弃
        for rule in pf.get("hard_continue_rules", []):
            if score >= rule["min_score"] and equity <= rule["max_equity"]:
                rules.append(f"手牌分数={score} >= {rule['min_score']} 且 所需胜率={equity:.1f}% <= {rule['max_equity']}% → {rule['desc']}，禁止 fold")

        # playable_hand_overrides: 可玩牌类别
        rules.extend(_check_playable_overrides(data))

        if pf.get("no_fold_on_check") and to_call == 0:
            rules.append("当前为 check (免费看牌)，严禁选择 fold")

    # ── postflop 规则 ──
    else:
        pf = strategy.get("postflop", {})

        # 听牌 equity 约束（按阶段读取 flop/turn/river）
        phase_lower = phase.lower()
        phase_cfg = pf.get(phase_lower, {})
        draw_cfg = phase_cfg.get("draw_by_equity", {})
        river_cfg = pf.get("river", {})
        use_outs = river_cfg.get("use_outs", True) if phase == "RIVER" else True
        if draw_cfg and use_outs:
            if outs >= draw_cfg.get("min_outs", 4):
                mult = draw_cfg.get("multiplier", 4)
                estimated_eq = outs * mult
                if estimated_eq > equity:
                    rules.append(f"outs={outs} 估算胜率≈{estimated_eq}% > 所需{equity}%，禁止 fold")

        # 已成牌保护
        protect = pf.get("made_hand_protect", {})
        min_made = protect.get("min_made", "")
        if made and _made_rank(made) >= _made_rank(min_made):
            if to_call == 0:
                rules.append(f"已成牌={made}，免费看牌，禁止 fold")

        # 半诈唬规则
        semi = pf.get("semi_bluff", {})
        if semi.get("allow_raise") and outs >= semi.get("min_outs", 8):
            rules.append(f"outs={outs} >= {semi.get('min_outs', 8)}，允许半诈唬加注")

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


def _check_playable_overrides(data):
    """根据 YAML playable_hand_overrides 检查手牌是否属于可玩类别"""
    strategy = _load_strategy()
    overrides = strategy.get("preflop", {}).get("playable_hand_overrides", {})
    if not overrides:
        return []

    hand = data.get("hand", [])
    if len(hand) < 2:
        return []

    # 内联牌面解析，避免跨模块导入问题
    _RANK_ORDER = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
                   "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}

    def _parse(card_str):
        if not card_str or len(card_str) < 2:
            return None
        rank = card_str[0].upper()
        suit = card_str[1].lower()
        return (_RANK_ORDER.get(rank, 0), suit)

    c1, c2 = _parse(hand[0]), _parse(hand[1])
    if not c1 or not c2:
        return []

    r1, s1 = c1
    r2, s2 = c2
    suited = s1 == s2
    high, low = max(r1, r2), min(r1, r2)
    gap = high - low
    equity = data.get("required_equity", 0)

    # 翻前底池为0时用筹码占比替代底池赔率
    eq_override = strategy.get("preflop", {}).get("equity_override", {})
    if eq_override.get("when_pot_zero") == "stack_pct" and data.get("pot", 0) == 0:
        chips = data.get("my_chips", 0)
        to_call = data.get("to_call", 0)
        equity = to_call / (chips + to_call) * 100 if (chips + to_call) > 0 else 0

    results = []
    rank_names = {10: "T", 11: "J", 12: "Q", 13: "K", 14: "A"}

    # suited_ace: A2s ~ A9s
    sa = overrides.get("suited_ace", {})
    if sa.get("enabled") and suited and high == 14:
        min_rank = sa.get("min_rank", 2)
        if low >= min_rank and low < 13:  # exclude AK/AQ which are already covered by hard_continue_rules
            max_eq = sa.get("max_equity", 35)
            if equity <= max_eq:
                lc = rank_names.get(low, str(low))
                results.append(f"A{lc}s 属于 suited_ace，所需胜率{equity}% <= {max_eq}% → {sa.get('action_if_reasonable_price', 'call_or_raise')}，禁止 fold")

    # suited_broadway: JTs+
    sb = overrides.get("suited_broadway", {})
    if sb.get("enabled") and suited and high >= 10 and low >= 10:
        max_eq = sb.get("max_equity", 35)
        if equity <= max_eq:
            hc = rank_names.get(high, str(high))
            lc = rank_names.get(low, str(low))
            results.append(f"{hc}{lc}s 属于 suited_broadway，所需胜率{equity}% <= {max_eq}% → {sb.get('action_if_reasonable_price', 'call_or_raise')}，禁止 fold")

    # suited_connector: 同花连牌/准连牌
    sc = overrides.get("suited_connector", {})
    if sc.get("enabled") and suited:
        min_gap = sc.get("min_gap", 0)
        max_gap = sc.get("max_gap", 1)
        if min_gap <= gap <= max_gap and high >= 5:
            max_eq = sc.get("max_equity", 25)
            if equity <= max_eq:
                hc = rank_names.get(high, str(high))
                lc = rank_names.get(low, str(low))
                results.append(f"{hc}{lc}s(gap={gap}) 属于 suited_connector，所需胜率{equity}% <= {max_eq}% → {sc.get('action_if_reasonable_price', 'call')}，禁止 fold")

    # offsuit_broadway: ATo+, KQo, QJo
    ob = overrides.get("offsuit_broadway", {})
    if ob.get("enabled") and not suited and high >= 11 and low >= 10:
        max_eq = ob.get("max_equity", 28)
        if equity <= max_eq:
            hc = rank_names.get(high, str(high))
            lc = rank_names.get(low, str(low))
            results.append(f"{hc}{lc}o 属于 offsuit_broadway，所需胜率{equity}% <= {max_eq}% → {ob.get('action_if_reasonable_price', 'call')}，禁止 fold")

    return results


def _build_strategy_guidance(data=None):
    """从 YAML 生成软性策略引导（翻前 + 翻后 + 战术模块）"""
    strategy = _load_strategy()
    lines = []

    # ── 翻前引导 ──
    up = strategy.get("preflop", {}).get("uncertainty_policy", {})
    if up.get("prefer_call_over_fold_when_playable"):
        lines.append("- 当手牌有一定可玩性但不确定时，倾向于跟注（call）而非弃牌（fold）")
    if up.get("do_not_require_dominating_equity_preflop"):
        lines.append("- 翻前不需要压倒性胜率即可入池，赔率合适就可以继续")
    if up.get("avoid_overfolding"):
        lines.append("- 避免过度弃牌，特别是在有位置优势或底池赔率合适的情况下")

    # ── 翻后阶段特定引导 ──
    phase_name = ""
    if data and data.get("current_phase", None):
        phase_name = data["current_phase"].name if hasattr(data["current_phase"], "name") else str(data["current_phase"])
        pf = strategy.get("postflop", {})

        if phase_name == "RIVER":
            river = pf.get("river", {})
            priority = river.get("evaluate_priority", [])
            if priority:
                lines.append(f"- 河牌决策优先级: {' → '.join(priority)}")
            vb = river.get("value_bet", {})
            if vb.get("min_made"):
                lines.append(f"- 价值下注门槛: 成牌 >= {vb['min_made']}")
            bluff = river.get("bluff", {})
            if bluff.get("allow_missed_draw_bluff"):
                requires = bluff.get("require_one_of", [])
                if requires:
                    lines.append(f"- 允许诈唬（满足其一即可）: {'、'.join(requires)}")
            if river.get("thin_value", {}).get("allow"):
                lines.append("- 允许薄价值下注")
            if river.get("bluff_catch", {}).get("enabled"):
                lines.append("- 启用抓诈模式：评估对手是否可能在诈唬")
        else:
            # 翻牌/转牌：半诈唬
            semi = pf.get("semi_bluff", {})
            if semi.get("allow_raise"):
                lines.append(f"- 听牌 outs >= {semi.get('min_outs', 8)} 时可考虑半诈唬加注")

    # ── 通用战术模块（顶层 tactics）──
    tactics = strategy.get("tactics", {})
    if tactics:
        disclaimer = tactics.get("disclaimer", "")
        if disclaimer:
            lines.append(f"- [原则] {disclaimer}")

        for module_name in ("pot_control", "protection_bet", "value_sizing", "check_raise", "bluffing"):
            mod = tactics.get(module_name, {})
            if mod.get("enabled", True):
                for g in mod.get("guidance", []):
                    lines.append(f"- [{module_name}] {g}")

        # 阶段特定的战术模块
        phase_tactic_map = {"FLOP": "flop_strategy", "TURN": "turn_strategy"}
        if phase_name in phase_tactic_map:
            pmod = tactics.get(phase_tactic_map[phase_name], {})
            if pmod.get("enabled", True):
                for g in pmod.get("guidance", []):
                    lines.append(f"- [{phase_tactic_map[phase_name]}] {g}")

    return "\n".join(lines) if lines else ""


_SUIT_NAMES = {"h": "红桃", "d": "方块", "s": "黑桃", "c": "草花"}
_RANK_NAME = {14: "A", 13: "K", 12: "Q", 11: "J", 10: "T",
               9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2"}


def _build_hand_label(facts, labels, phase=""):
    """根据客观事实 + YAML 标签模板 → 生成手牌描述"""
    made = facts["made_hand"]
    outs = facts.get("outs", 0)
    draws = facts.get("draws", [])

    if made == "同花顺":
        return labels["straight_flush"]

    if made == "四条":
        return labels["quads"].format(rank=facts.get("quads_rank_name", "?"))

    if made == "葫芦":
        return labels["boat"].format(
            trips=facts.get("trips_rank_name", "?"),
            better=facts.get("better_boats_possible", "?"))

    if made == "同花":
        if facts.get("has_nut_flush"):
            return labels["nut_flush"]
        return labels["flush"].format(
            high=facts.get("flush_high_name", "?"),
            better=facts.get("better_flush_cards_on_board", "?"))

    if made == "顺子":
        return labels["straight"].format(
            top=facts.get("straight_top_name", "?"),
            better=facts.get("better_straights_possible", "?"))

    if made == "三条":
        return labels["trips"].format(
            rank=facts.get("trips_rank_name", "?"),
            better=facts.get("better_trips_possible", "?"))

    if made == "两对":
        return labels["two_pair"].format(
            top=facts.get("top_pair_name", "?"),
            bottom=facts.get("bottom_pair_name", "?"),
            better=facts.get("better_two_pairs_possible", "?"))

    if made == "一对":
        if facts.get("is_overpair"):
            return labels["overpair"].format(rank=facts.get("pair_rank_name", "?"))
        if facts.get("is_top_pair"):
            return labels["top_pair"].format(
                rank=facts.get("pair_rank_name", "?"),
                kicker=facts.get("kicker_name", "?"))
        better = facts.get("better_pairs_on_board", 0)
        if better <= 1:
            return labels["middle_pair"].format(
                rank=facts.get("pair_rank_name", "?"), better=better)
        return labels["bottom_pair"].format(
            rank=facts.get("pair_rank_name", "?"), better=better)

    # 高牌
    has_fd = "同花听牌" in draws
    has_oesd = "两头顺听牌" in draws
    has_gs = "卡顺听牌" in draws
    high = facts.get("high_card_name", "?")

    # 高牌 / 听牌未中（river）
    if has_fd and has_oesd:
        return labels["combo_draw"].format(outs=outs)
    if has_fd:
        if facts.get("is_nut_flush_draw"):
            if facts["my_ranks"][0] >= 13:
                return labels["fd_overs"].format(outs=outs)
            return labels["nut_fd"].format(outs=outs)
        if facts["my_ranks"][0] >= 13:
            return labels["fd_overs"].format(outs=outs)
        return labels["fd"].format(outs=outs)
    if has_oesd:
        if facts["my_ranks"][0] >= 13:
            return labels["oesd_overs"].format(outs=outs)
        return labels["oesd"].format(outs=outs)
    if has_gs:
        return labels["gutshot"].format(outs=outs)

    # 河牌：高牌无听牌 → 错失听牌 / 摊牌价值 / 空气
    if phase == "RIVER":
        missed = facts.get("had_draws", [])
        if "同花听牌" in missed:
            return labels.get("missed_fd", labels["air"])
        if "两头顺听牌" in missed:
            return labels.get("missed_oesd", labels["air"])
        if "卡顺听牌" in missed:
            return labels.get("missed_gutshot", labels["air"])
        if high in ("A", "K"):
            return labels.get("weak_showdown", labels["air"])
        if facts.get("pair_rank_name") or facts.get("has_showdown_value"):
            return labels.get("weak_showdown", labels["air"])
        return labels.get("no_showdown", labels["air"])

    if high == "A":
        return labels["high_card_a"]
    if high == "K":
        return labels["high_card_k"]
    return labels["air"]


def _build_board_label(board, labels):
    """根据牌面事实 + YAML 标签模板 → 生成牌面描述"""
    parts = []

    if board["paired"]:
        rank = _RANK_NAME.get(board["pair_rank"], "?")
        parts.append(labels["paired"].format(rank=rank))

    sc = board["suited_count"]
    if sc >= 3:
        ds = board.get("dominant_suit", "?")
        suit_name = _SUIT_NAMES.get(ds, ds)
        parts.append(labels["monotone"].format(count=sc, suit=suit_name))

    if board["connected_gaps"] >= 2:
        parts.append(labels["connected"])

    if not parts:
        if not board["paired"] and sc < 3 and board["connected_gaps"] < 1:
            parts.append(labels["dry"])

    if not parts:
        parts.append(labels["default"])

    return "；".join(parts)


def _build_board_and_hand_text(data):
    """读 YAML 模板 + 调用 evaluator → 生成完整的手牌/牌面分析段落"""
    try:
        from server.evaluator import analyze_board_texture, hand_strength_facts
    except ModuleNotFoundError:
        from evaluator import analyze_board_texture, hand_strength_facts

    strategy = _load_strategy()
    pf = strategy.get("postflop", {})
    board_labels = pf.get("board_labels", {})
    hand_labels = pf.get("hand_labels", {})

    if not board_labels or not hand_labels:
        return ""

    hand = data.get("hand", [])
    public = data.get("public_cards", [])
    phase = data["current_phase"].name

    parts = []

    # 翻前：用预评级
    if phase == "PRE_FLOP":
        parts.append(f"起手牌: {data.get('hand_strength', '?')} (分数: {data.get('hand_score', 0)}/10)")
    else:
        # 牌面纹理
        if public:
            board = analyze_board_texture(public)
            parts.append(f"牌面: {_build_board_label(board, board_labels)}")

        # 手牌质量
        hf = hand_strength_facts(hand, public)
        parts.append(f"手牌: {_build_hand_label(hf, hand_labels, phase)}")
        parts.append(f"听牌: {', '.join(hf.get('draws', [])) or '无'} ({hf.get('outs_detail', '0 outs')})")

    return "\n".join(f"- {p}" for p in parts)


def build_poker_prompt(data, semantic_history):
    hand_str = ", ".join(data['hand']) if data['hand'] else "尚未看牌或已弃牌"
    public_str = ", ".join(data['public_cards']) if data['public_cards'] else "桌面无公共牌"

    odds = data.get("pot_odds", "")
    equity = data.get("required_equity", 0)
    to_call = data.get("to_call", 0)

    # 翻前底池为0时，向 LLM 展示筹码占比（而非 100% 的底池赔率）
    strategy = _load_strategy()
    eq_override = strategy.get("preflop", {}).get("equity_override", {})
    equity_label = "所需胜率"
    if (eq_override.get("when_pot_zero") == "stack_pct"
            and data['current_phase'].name == "PRE_FLOP"
            and data.get("pot", 0) == 0
            and to_call > 0):
        equity = to_call / (data['my_chips'] + to_call) * 100
        odds = f"跟注{to_call}/筹码{data['my_chips']}"
        equity_label = "筹码占比"

    hand_assessment = _build_board_and_hand_text(data)
    hard_rules = _build_hard_rules(data)
    strategy_guidance = _build_strategy_guidance(data)

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
- {equity_label}: {equity:.1f}%

### 3. 手牌与牌面分析:
{hand_assessment}

### 4. 硬性约束 (绝对不可违反):
{hard_rules if hard_rules else "无特殊约束，自由决策。"}

### 5. 策略引导 (遵循这些原则):
{strategy_guidance if strategy_guidance else "无特殊引导，按标准德州扑克策略决策。"}

### 6. 强制要求:
- 第4节硬性约束是绝对底线，必须无条件遵守。第5节策略引导仅为参考，当两者冲突时硬约束优先。
- 例如: 硬约束说"禁止fold"则绝对不能fold，策略引导的任何相反建议均无效。
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
    assert "起手牌" in prompt
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

    # 有听牌 (翻后)
    data3 = data.copy()
    data3["current_phase"] = FakePhase.FLOP
    data3["public_cards"] = ["Qh", "Jh", "2d"]
    data3["made_hand"] = "高牌"
    data3["draws"] = ["同花听牌", "两头顺听牌"]
    data3["outs"] = 13
    data3["outs_detail"] = "同花:9张 | 两头顺:4张"
    prompt3 = build_poker_prompt(data3, "")
    # 新格式用 YAML 模板填充，不再是原始字段
    assert "花顺双抽" in prompt3 or "同花听牌" in prompt3
    assert "13" in prompt3
    print("[PASS] draws and outs included")

    print("\n全部通过")