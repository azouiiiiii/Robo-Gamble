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
    """遍历所有可能的顺子组合，统计需要多少 outs"""
    uniq = set(ranks)
    total_outs = 0

    # 所有可能的顺子：2-3-4-5-6 到 10-J-Q-K-A，加上 A-2-3-4-5
    straights = []
    for low in range(2, 11):
        straights.append({low, low + 1, low + 2, low + 3, low + 4})
    straights.append({14, 2, 3, 4, 5})  # wheel

    for straight in straights:
        have = len(straight & uniq)
        if have == 4:
            total_outs += 4  # 差一张，4 个 outs

    return min(total_outs, 25)


def analyze_board_texture(public_cards):
    """返回牌面客观事实，不做主观判断"""
    if not public_cards:
        return {"paired": False, "suited_count": 0, "connected_gaps": 0,
                "high_cards": 0, "board_ranks": []}

    cards = [parse_card(c) for c in public_cards if parse_card(c)]
    if not cards:
        return {"paired": False, "suited_count": 0, "connected_gaps": 0,
                "high_cards": 0, "board_ranks": []}

    ranks = [c[0] for c in cards]
    suits = [c[1] for c in cards]

    # 对子
    paired = len(ranks) != len(set(ranks))
    pair_rank = None
    if paired:
        for r, cnt in {r: ranks.count(r) for r in ranks}.items():
            if cnt >= 2:
                pair_rank = r
                break

    # 同色
    suit_counts = {}
    for s in suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1
    suited_count = max(suit_counts.values())
    dominant_suit = max(suit_counts, key=suit_counts.get) if suit_counts else None

    # 连接度
    uniq = sorted(set(ranks))
    gaps = sum(1 for i in range(len(uniq) - 1) if uniq[i + 1] - uniq[i] <= 2)

    # 高牌 T+
    high_cards = sum(1 for r in ranks if r >= 10)

    return {
        "paired": paired,
        "pair_rank": pair_rank,
        "suited_count": suited_count,
        "dominant_suit": dominant_suit,
        "connected_gaps": gaps,
        "high_cards": high_cards,
        "board_ranks": sorted(ranks, reverse=True)
    }


_RANK_NAME = {14: "A", 13: "K", 12: "Q", 11: "J", 10: "T",
               9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2"}


def hand_strength_facts(hand, public_cards):
    """
    返回手牌质量的客观事实，不包含任何主观判断或策略标签。
    所有判断性逻辑由 YAML 定义。
    """
    if not hand or len(hand) < 2:
        return {"exists": False}

    ev = evaluate_hand(hand, public_cards)
    my_cards = [c for c in hand if parse_card(c)]
    my_parsed = [parse_card(c) for c in my_cards]
    all_parsed = [parse_card(c) for c in hand + public_cards if parse_card(c)]
    ranks = [c[0] for c in all_parsed]
    suits = [c[1] for c in all_parsed]

    rank_counts = {}
    for r in ranks:
        rank_counts[r] = rank_counts.get(r, 0) + 1

    suit_counts = {}
    for s in suits:
        suit_counts[s] = suit_counts.get(s, 0) + 1

    uniq = sorted(set(ranks))

    facts = {
        "exists": True,
        "made_hand": ev["made_hand"],
        "draws": ev["draws"],
        "outs": ev["outs"],
        "outs_detail": ev["outs_detail"],
        "my_ranks": sorted([c[0] for c in my_parsed], reverse=True),
        "my_rank_names": [_RANK_NAME.get(r, str(r)) for r in sorted([c[0] for c in my_parsed], reverse=True)],
    }

    made = ev["made_hand"]

    if made == "同花顺":
        facts["hand_detail"] = "同花顺"

    elif made == "四条":
        quad_r = max((r for r, c in rank_counts.items() if c >= 4), default=0)
        facts["quads_rank"] = quad_r
        facts["quads_rank_name"] = _RANK_NAME.get(quad_r, str(quad_r))

    elif made == "葫芦":
        trips_r = max((r for r, c in rank_counts.items() if c >= 3), default=0)
        pair_r = max((r for r, c in rank_counts.items() if c >= 2 and r != trips_r), default=0)
        facts["trips_rank"] = trips_r
        facts["trips_rank_name"] = _RANK_NAME.get(trips_r, str(trips_r))
        facts["pair_rank"] = pair_r
        facts["pair_rank_name"] = _RANK_NAME.get(pair_r, str(pair_r))
        # 比我大的葫芦有多少种
        better = sum(1 for r in set(ranks) if r > trips_r and rank_counts.get(r, 0) >= 2)
        facts["better_boats_possible"] = better

    elif made == "同花":
        flush_suit = max(suit_counts, key=suit_counts.get)
        my_flush_cards = [c for c in my_parsed if c[1] == flush_suit]
        flush_ranks = sorted([c[0] for c in all_parsed if c[1] == flush_suit], reverse=True)
        my_high = max((c[0] for c in my_flush_cards), default=0)
        facts["flush_high_rank"] = my_high
        facts["flush_high_name"] = _RANK_NAME.get(my_high, str(my_high))
        # 坚果同花? (我有没有该花色的A)
        facts["has_nut_flush"] = any(c[0] == 14 and c[1] == flush_suit for c in my_parsed)
        # 比我大的同花有多少种 (board 上有多少张该花色比我大)
        higher_flush_boards = [r for r in flush_ranks if r > my_high]
        facts["better_flush_cards_on_board"] = len(higher_flush_boards)

    elif made == "顺子":
        top = 0
        for i in range(len(uniq) - 4):
            if uniq[i + 4] - uniq[i] == 4:
                top = max(top, uniq[i + 4])
        if {14, 2, 3, 4, 5}.issubset(set(uniq)):
            top = max(top, 5)
        facts["straight_top"] = top
        facts["straight_top_name"] = _RANK_NAME.get(top, str(top))
        # 比我大的顺子: 任何顺子顶 > top
        better = 0
        for i in range(len(uniq) - 4):
            if uniq[i + 4] - uniq[i] == 4 and uniq[i + 4] > top:
                better += 1
        facts["better_straights_possible"] = better

    elif made == "三条":
        trips_r = max((r for r, c in rank_counts.items() if c >= 3))
        facts["trips_rank"] = trips_r
        facts["trips_rank_name"] = _RANK_NAME.get(trips_r, str(trips_r))
        my_kickers = sorted([r for r in ranks if r != trips_r], reverse=True)[:2]
        facts["kickers"] = my_kickers
        facts["kicker_names"] = [_RANK_NAME.get(r, str(r)) for r in my_kickers]
        better = sum(1 for r in set(ranks) if r > trips_r and rank_counts.get(r, 0) >= 2)
        facts["better_trips_possible"] = better

    elif made == "两对":
        pair_ranks = sorted([r for r, c in rank_counts.items() if c >= 2], reverse=True)
        facts["top_pair_rank"] = pair_ranks[0] if len(pair_ranks) > 0 else 0
        facts["top_pair_name"] = _RANK_NAME.get(pair_ranks[0], "?") if len(pair_ranks) > 0 else "?"
        facts["bottom_pair_rank"] = pair_ranks[1] if len(pair_ranks) > 1 else 0
        facts["bottom_pair_name"] = _RANK_NAME.get(pair_ranks[1], "?") if len(pair_ranks) > 1 else "?"
        # 更大的两对有多少
        better = sum(1 for r in set(ranks) if r > pair_ranks[0] and rank_counts.get(r, 0) >= 2) if pair_ranks else 0
        facts["better_two_pairs_possible"] = better

    elif made == "一对":
        pair_r = max((r for r, c in rank_counts.items() if c >= 2))
        facts["pair_rank"] = pair_r
        facts["pair_rank_name"] = _RANK_NAME.get(pair_r, str(pair_r))
        my_kickers = sorted([r for r in facts["my_ranks"] if r != pair_r], reverse=True)
        facts["kicker"] = my_kickers[0] if my_kickers else 0
        facts["kicker_name"] = _RANK_NAME.get(my_kickers[0], "?") if my_kickers else "?"
        # 超对?
        pub_ranks = [parse_card(c)[0] for c in public_cards if parse_card(c)]
        facts["is_overpair"] = all(r < pair_r for r in pub_ranks) if pub_ranks else True
        # 顶对?
        facts["is_top_pair"] = pair_r == max(pub_ranks) if pub_ranks else False
        # 更大的对子数量
        facts["better_pairs_on_board"] = sum(1 for r in set(pub_ranks) if r > pair_r) if pub_ranks else 0

    else:  # 高牌
        facts["high_card_rank"] = facts["my_ranks"][0]
        facts["high_card_name"] = facts["my_rank_names"][0]
        # 听牌细节
        if "同花听牌" in ev["draws"]:
            fd_suit = max(suit_counts, key=suit_counts.get)
            facts["is_nut_flush_draw"] = any(c[0] == 14 and c[1] == fd_suit for c in my_parsed)
        # 两张超张? (手牌都比公牌大)
        pub_ranks = [parse_card(c)[0] for c in public_cards if parse_card(c)]
        if pub_ranks:
            facts["both_overcards"] = all(r > max(pub_ranks) for r in facts["my_ranks"])

    return facts


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

    # ── board texture ──
    t = analyze_board_texture([])
    assert t["paired"] is False and t["board_ranks"] == []
    # K72r → 彩虹干燥
    t = analyze_board_texture(["Kh", "7d", "2c"])
    assert not t["paired"] and t["connected_gaps"] == 0
    # 88K → 对子面
    t = analyze_board_texture(["8s", "8h", "Kd"])
    assert t["paired"] and t["pair_rank"] == 8
    # 三张红桃
    t = analyze_board_texture(["Ah", "Kh", "3h"])
    assert t["suited_count"] == 3
    # 连接面
    t = analyze_board_texture(["Jd", "Ts", "9h"])
    assert t["connected_gaps"] >= 2
    print("[PASS] board_texture 客观事实")

    # ── hand_strength_facts ──
    f = hand_strength_facts(["Ah", "Kh"], ["Qh", "Jh", "Th"])
    assert f["made_hand"] == "同花顺"
    f = hand_strength_facts(["Ah", "5h"], ["Kh", "Qh", "Jh"])
    assert f["has_nut_flush"] is True
    f = hand_strength_facts(["Ah", "Ad"], ["Kd", "7s", "2c"])
    assert f["is_overpair"] is True and f["pair_rank_name"] == "A"
    f = hand_strength_facts(["Ah", "Kh"], ["Qh", "Jh", "2d"])
    assert "同花听牌" in f["draws"] and f["exists"]
    f = hand_strength_facts(["7h", "2d"], ["Kd", "Qs", "Jh"])
    assert f["made_hand"] == "高牌"
    # 葫芦
    f = hand_strength_facts(["Ah", "Ad"], ["Ac", "Kd", "Ks"])
    assert f["trips_rank_name"] == "A"
    print(f"[PASS] hand_strength_facts: 同花顺 | 坚果同花 | 超对A | 高牌 | 葫芦A")

    # edge cases
    assert parse_card("??") is None or parse_card("??")[0] == 0
    print("[PASS] 边界情况")

    print("\n全部通过")
