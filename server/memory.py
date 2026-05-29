# server/memory.py

class GameMemory:
    def __init__(self):
        self.history = []  # 建议存储格式: {"phase": GamePhase.FLOP, "pot": 5300, "my_turn": True}
        self.current_round_id = None

    def reset(self):
        print("[MEMORY] New round, reset history")
        self.history = []

    def add_event(self, event):
        """
        event 格式建议: 
        {
            "phase": current_phase, 
            "pot": current_pot, 
            "action": "detect" # 或者是执行器的动作如 "raise"
        }
        """
        self.history.append(event)

    def get_semantic_history(self):
        """
        将底池轨迹 + 跟注额转化为对手行动的博弈描述。
        每次记录是我的回合状态 → 反推对手在两次回合之间做了什么。
        核心: 对比翻前翻后行为一致性，发现矛盾 → 试探机会。
        """
        if not self.history:
            return "本局刚开始。"

        phase_cn = {"PRE_FLOP": "翻前", "FLOP": "翻牌圈", "TURN": "转牌圈", "RIVER": "河牌圈"}

        def _phase_name(e):
            p = e.get("phase")
            name = p.name if hasattr(p, "name") else str(p)
            return phase_cn.get(name, name)

        def _bet_size_desc(to_call, pot):
            if to_call <= 0 or pot <= 0:
                return ""
            pct = to_call / pot * 100
            if pct > 80:
                return f"重注({pct:.0f}%底池)"
            elif pct > 50:
                return f"中等偏大({pct:.0f}%底池)"
            elif pct > 25:
                return f"标准下注({pct:.0f}%底池)"
            else:
                return f"小注({pct:.0f}%底池)"

        def _preflop_aggression(e):
            """判断翻前对手的加注力度，模糊描述"""
            to_call = e.get("to_call", 0)
            chips = e.get("my_chips", 0)
            if to_call <= 0 or chips <= 0:
                return "passive"
            pct = to_call / (chips + to_call) * 100
            if pct > 12:
                return "heavy"   # 大额加注，显示强牌
            elif pct > 5:
                return "medium"  # 中等加注，有一定牌力
            else:
                return "light"   # 小额/平跟，范围宽泛

        _aggression_label = {
            "heavy": "对手翻前大额加注，显示较强牌力",
            "medium": "对手翻前中等加注，有一定牌力",
            "light": "对手翻前小额加注/平跟，范围较宽",
            "passive": "翻前无人加注，整体被动",
        }

        narrative = []
        prev_pot = 0
        preflop_aggression = None  # 记录翻前力度，用于后续对比

        for i, e in enumerate(self.history):
            label = _phase_name(e)
            pot = e.get("pot", 0)
            to_call = e.get("to_call", 0)

            if label == "翻前":
                preflop_aggression = _preflop_aggression(e)

                # 统计翻前事件数量（多轮 = re-raise）
                pf_events = [ev for ev in self.history
                             if _phase_name(ev) == "翻前"]
                pf_idx = pf_events.index(e) if e in pf_events else 0

                if pf_idx == 0:
                    # 翻前首次行动
                    if to_call > 0:
                        narrative.append(
                            f"翻前: 需跟注{to_call}，"
                            f"{_aggression_label.get(preflop_aggression, '')}"
                        )
                    else:
                        narrative.append("翻前: 免费看牌(check)，无人加注")
                else:
                    # 翻前再次轮到 → 有人 re-raise
                    narrative.append(
                        f"!!! 翻前再遇加注: 有人 re-raise，需再跟{to_call}。"
                        f"对手范围进一步收窄，极可能持有强牌。"
                    )
            else:
                # 翻后
                pot_growth = pot - prev_pot
                if to_call == 0:
                    narrative.append(f"{label}: 对手check，底池维持{pot}")
                else:
                    sz = _bet_size_desc(to_call, pot)
                    growth_note = f"底池从{prev_pot}→{pot}" if pot_growth > 0 else ""
                    narrative.append(f"{label}: 对手下注{to_call}{' (' + sz + ')' if sz else ''}，{growth_note}")

            prev_pot = pot

        # ── 翻前 vs 翻后 一致性分析 ──
        postflop_events = [e for e in self.history if _phase_name(e) != "翻前"]
        if preflop_aggression in ("heavy", "medium") and postflop_events:
            # 翻前强势 → 检查翻后是否一致
            post_tc = [e.get("to_call", 0) for e in postflop_events]
            post_aggressive = sum(1 for t in post_tc if t > 0)
            if post_aggressive == 0:
                narrative.append(
                    "→ 行为矛盾: 翻前加注强势，但翻后连续check示弱。"
                    "对手可能持AK/AQ等高张未击中，或中对/小对子控池。"
                    "若胜率允许，可考虑下注试探——对手无击中时很可能弃牌。"
                )
            elif post_aggressive < len(post_tc):
                narrative.append(
                    "→ 翻前强势但翻后有停顿，对手可能在某一街未击中后转为控池。"
                )
        elif preflop_aggression in ("light", "passive") and postflop_events:
            # 翻前被动 → 翻后突然发力
            post_tc = [e.get("to_call", 0) for e in postflop_events]
            post_aggressive = sum(1 for t in post_tc if t > 0)
            if post_aggressive >= len(post_tc) * 0.66:
                narrative.append(
                    "→ 行为矛盾: 翻前被动但翻后持续发力。"
                    "对手可能翻牌击中强成牌(两对/三条/顺子/同花)，或利用牌面诈唬。"
                    "需结合牌面纹理判断是真实击中还是诈唬。"
                )

        # ── 整体翻后趋势 ──
        if len(postflop_events) >= 2:
            to_calls = [e.get("to_call", 0) for e in postflop_events]
            non_zero = [t for t in to_calls if t > 0]
            if len(non_zero) >= len(to_calls) * 0.66:
                narrative.append("→ 翻后整体: 对手持续施压，侵略性强")
            elif len(non_zero) == 0:
                narrative.append("→ 翻后整体: 对手连续check示弱，整体偏被动")
            else:
                half = max(len(to_calls) // 2, 1)
                first = sum(1 for t in to_calls[:half] if t > 0)
                second = sum(1 for t in to_calls[half:] if t > 0)
                if second > first:
                    narrative.append("→ 翻后整体: 后期发力，可能在转牌/河牌击中")
                elif first > second:
                    narrative.append("→ 翻后整体: 前期积极、后期收力，可能未击中或控池")

        return "\n".join(narrative)

    def get_history(self):
        return self.history


if __name__ == "__main__":
    from enum import Enum

    class FakePhase(Enum):
        PRE_FLOP = 1
        FLOP = 2
        TURN = 3
        RIVER = 4

    mem = GameMemory()
    assert "刚开始" in mem.get_semantic_history()
    print("[PASS] empty history")

    # ── 场景1: 翻前加注 → 翻后 check 示弱（矛盾）──
    print("\n=== 场景1: 翻前强翻后弱（AK未击中）===")
    mem.add_event({"phase": FakePhase.PRE_FLOP, "pot": 0, "to_call": 30, "my_chips": 200})
    mem.add_event({"phase": FakePhase.FLOP, "pot": 90, "to_call": 0, "my_chips": 170})
    mem.add_event({"phase": FakePhase.TURN, "pot": 90, "to_call": 0, "my_chips": 170})
    sem = mem.get_semantic_history()
    assert "翻前" in sem
    assert "check" in sem or "无人加注" in sem
    assert "矛盾" in sem
    print(sem)

    mem.reset()
    print("\n=== 场景2: 翻前 re-raise（多轮翻前）===")
    mem.add_event({"phase": FakePhase.PRE_FLOP, "pot": 0, "to_call": 15, "my_chips": 200})
    mem.add_event({"phase": FakePhase.PRE_FLOP, "pot": 45, "to_call": 40, "my_chips": 185})
    mem.add_event({"phase": FakePhase.FLOP, "pot": 150, "to_call": 50, "my_chips": 145})
    sem = mem.get_semantic_history()
    assert "re-raise" in sem
    print(sem)

    mem.reset()
    print("\n=== 场景3: 翻前被动翻后发力（可能击中）===")
    mem.add_event({"phase": FakePhase.PRE_FLOP, "pot": 0, "to_call": 0, "my_chips": 200})
    mem.add_event({"phase": FakePhase.FLOP, "pot": 10, "to_call": 30, "my_chips": 200})
    mem.add_event({"phase": FakePhase.TURN, "pot": 100, "to_call": 50, "my_chips": 170})
    sem = mem.get_semantic_history()
    assert "矛盾" in sem
    print(sem)

    print("\n全部通过")