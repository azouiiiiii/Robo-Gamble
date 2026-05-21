"""
本地确定性计算：起手牌强度、当前牌型、outs 数、底池赔率。
不依赖 LLM，保证计算结果精确一致。
"""

RANK_ORDER = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7,
              "8": 8, "9": 9, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}


def parse_card(card_str):
    """'Ah' → (14, 'h')"""
    if not card_str or len(card_str) < 2:
        return None
    rank = card_str[0].upper()
    suit = card_str[1].lower()
    return (RANK_ORDER.get(rank, 0), suit)


def preflop_hand_strength(hand):
    """返回 (类别, 分数) — 分数 0~10"""
    if len(hand) < 2:
        return "未知", 0

    c1, c2 = parse_card(hand[0]), parse_card(hand[1])
    if not c1 or not c2:
        return "未知", 0

    r1, s1 = c1
    r2, s2 = c2
    suited = s1 == s2
    high, low = max(r1, r2), min(r1, r2)
    gap = high - low

    # 口袋对子
    if high == low:
        if high >= 14: return "超强对子(AA)", 10
        if high >= 13: return "强对子(KK)", 9
        if high >= 12: return "强对子(QQ)", 8
        if high >= 11: return "中对子(JJ)", 7
        if high >= 9:  return "中对子(TT/99)", 6
        if high >= 7:  return "小对子(88-77)", 5
        return "小对子(66-)", 4

    # 非对子
    if high == 14:
        if low >= 13: return "AK", 9 if suited else 8
        if low >= 12: return "AQ", 8 if suited else 7
        if low >= 11: return "AJ", 7 if suited else 6
        if low >= 10: return "AT", 6 if suited else 5
        return "A带小", 5 if suited else 3

    if high == 13:
        if low >= 12: return "KQ", 7 if suited else 6
        if low >= 11: return "KJ", 6 if suited else 5
        return "K带小", 4 if suited else 2

    if high == 12 and low >= 11: return "QJ", 5 if suited else 4

    # 同花连牌
    if suited and gap <= 2 and high >= 9:
        return "同花连牌", 6 if gap == 1 else 5
    if suited and gap <= 3 and high >= 8:
        return "同花准连牌", 4

    return "边缘牌", 2 if suited else 1


def evaluate_hand(hand, public_cards):
    """
    返回当前成牌信息: {
        "made_hand": "高牌/一对/两对/三条/顺子/同花/葫芦/四条/同花顺",
        "draws": ["同花听牌", "两头顺听牌", ...],
        "outs": 9,
        "outs_detail": "同花:9张"
    }
    """
    if not hand or len(hand) < 2:
        return {"made_hand": "未知", "draws": [], "outs": 0, "outs_detail": ""}

    all_cards = [parse_card(c) for c in hand + public_cards if parse_card(c)]
    if len(all_cards) < 2:
        return {"made_hand": "高牌", "draws": [], "outs": 0, "outs_detail": ""}

    ranks = [c[0] for c in all_cards]
    suits = [c[1] for c in all_cards]
    my_ranks = [parse_card(c)[0] for c in hand if parse_card(c)]

    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1

    suit_counts = {}
    for s in suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1

    # 成牌判定
    counts = sorted(rank_counts.values(), reverse=True)
    is_flush = max(suit_counts.values()) >= 5
    is_straight = _has_straight(list(rank_counts.keys()))

    made = "高牌"
    if counts == [4, 1, 1, 1, 1, 1, 1] or counts[0] == 4:
        made = "四条"
    elif counts in ([3, 3, 1], [3, 3, 1, 1, 1]) or (counts[0] == 3 and counts[1] >= 2):
        made = "葫芦"
    elif is_flush:
        made = "同花"
    elif is_straight:
        made = "顺子"
    elif counts[0] == 3:
        made = "三条"
    elif counts.count(2) >= 2:
        made = "两对"
    elif counts[0] == 2:
        made = "一对"

    if is_flush and is_straight:
        made = "同花顺"

    # 听牌 + outs 计算
    draws = []
    outs = 0
    details = []

    # 同花听牌
    for s, cnt in suit_counts.items():
        if cnt == 4:
            draws.append("同花听牌")
            flush_outs = 9
            outs += flush_outs
            details.append(f"同花:{flush_outs}张")

    # 顺子听牌
    if not is_straight:
        straight_outs = _count_straight_outs(list(rank_counts.keys()))
        if straight_outs >= 8:
            draws.append("两头顺听牌")
            outs += straight_outs
            details.append(f"两头顺:{straight_outs}张")
        elif straight_outs >= 4:
            draws.append("卡顺听牌")
            outs += straight_outs
            details.append(f"卡顺:{straight_outs}张")

    # 成牌提升 outs
    if made == "一对" and len(public_cards) >= 3:
        # 三条 / 两对 outs
        pair_rank = [r for r, c in rank_counts.items() if c >= 2]
        if pair_rank:
            # 三条 outs
            trip_outs = 4 - rank_counts.get(pair_rank[0], 0)
            outs += trip_outs
            details.append(f"三条:{trip_outs}张")
            # 两对 outs (if we hit another pair)
            for r, c in rank_counts.items():
                if c == 1 and r != pair_rank[0]:
                    outs += 3  # 3 more cards of this rank
                    break  # just count one potential second pair

    if made == "两对":
        fh_outs = 4  # 两种点数各剩 2 张
        outs += fh_outs
        details.append(f"葫芦:{fh_outs}张")

    if made == "三条":
        outs += 3  # 1 for quads + need more for boat
        if len(public_cards) >= 4:
            outs += 3  # 公牌中配对
        details.append(f"提升:{outs}张")

    # 去重 (同花和顺子 outs 可能有重叠)
    outs = min(outs, 25)

    return {
        "made_hand": made,
        "draws": draws,
        "outs": outs,
        "outs_detail": " | ".join(details) if details else "无"
    }


def pot_odds_analysis(pot, to_call):
    """返回 (赔率比, 所需胜率%)"""
    if to_call <= 0:
        return "无限", 0.0
    total = pot + to_call
    required = to_call / total * 100
    return f"{pot}:{to_call}", round(required, 1)


def _has_straight(ranks):
    uniq = sorted(set(ranks))
    if len(uniq) < 5:
        return False
    # 普通顺子
    for i in range(len(uniq) - 4):
        if uniq[i + 4] - uniq[i] == 4:
            return True
    # A-2-3-4-5 (wheel)
    if {14, 2, 3, 4, 5}.issubset(set(uniq)):
        return True
    return False


def _count_straight_outs(ranks):
    uniq = sorted(set(ranks))
    max_outs = 0
    for i in range(len(uniq)):
        window = set(uniq[i:i + 5])
        if len(window) >= 4:
            # 差一张成顺
            needed = 5 - len(window)
            if needed == 1:
                # 检查缺口能不能补
                start, end = min(window), max(window)
                if end - start <= 4:
                    max_outs = max(max_outs, 4)
    return max_outs


if __name__ == "__main__":
    print("Evaluator 测试")
    print("=" * 40)

    # preflop
    assert preflop_hand_strength(["Ah", "As"])[0] == "超强对子(AA)"
    assert preflop_hand_strength(["Ah", "Kh"])[0] == "AK"
    assert preflop_hand_strength(["7s", "2d"])[0] == "边缘牌"
    print("[PASS] preflop_hand_strength")

    # made hand
    assert evaluate_hand(["Ah", "Kh"], ["Qh", "Jh", "Th"])["made_hand"] == "同花顺"
    assert evaluate_hand(["Ah", "Ad"], ["Ac", "Kd", "Qs"])["made_hand"] == "三条"
    assert evaluate_hand(["Ah", "Kh"], ["Qh", "Jh", "2h"])["made_hand"] == "同花"
    print("[PASS] evaluate_hand 成牌判定")

    # draws
    result = evaluate_hand(["Ah", "Kh"], ["Qh", "Jh", "2d"])
    assert "同花听牌" in result["draws"]
    assert result["outs"] >= 9
    print(f"[PASS] 听牌 + outs: {result['draws']}, outs={result['outs']}")

    # pot odds
    ratio, pct = pot_odds_analysis(1000, 500)
    assert pct == 33.3
    print(f"[PASS] pot_odds: 底池1000 跟注500 → 需{pct}%胜率")

    # edge cases
    assert parse_card("??") is None or parse_card("??")[0] == 0
    print("[PASS] 边界情况")

    print("\n全部通过")
