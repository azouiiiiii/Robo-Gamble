"""
对手建模系统，分为两部分：

1. 读牌（单局）— 本局对手动作链数据，注入 DecisionAgent prompt，
   由同一次 LLM 调用完成手牌范围推断。不增加延迟。

2. 形象（全场）— 跨牌局对手画像，手牌结束后异步调用 LLM 更新，
   缓存后注入后续每手决策。零延迟影响。
"""

import json
import re
from collections import defaultdict


class GameMemory:
    def __init__(self):
        # ── 本局数据 ──
        self.current_hand: list[dict] = []  # 本轮对手动作链

        # ── 跨牌局数据 ──
        self.completed_hands: list[dict] = []  # 每手牌结束后存摘要
        self.profile: dict = {}                # LLM 生成的对手画像（缓存）
        self.hands_since_profile_update: int = 0
        self._actions_ingested: int = 0        # 已录入的对手动作数（防重复）

    # ═══════════════════════════════════════════════════════════
    # 数据录入
    # ═══════════════════════════════════════════════════════════

    def add_event(self, event: dict):
        """
        记录本局一次事件。event 来自 main.py 的 on_decide 回调或 /observe。
        格式: {"phase": GamePhase, "pot": int, "to_call": int, "my_chips": int,
               "public_cards": [...], "action_type": "raise", "amount": 500}
        """
        self.current_hand.append(event)

    def add_opponent_actions(self, actions: list):
        """批量录入对手动作（增量：只追加上次录入后新增的动作）"""
        new_actions = actions[self._actions_ingested:]
        for a in new_actions:
            self.current_hand.append({
                "phase": a.phase,
                "action_type": a.action_type,
                "amount": a.amount,
                "is_aggressor": a.is_aggressor,
            })
        self._actions_ingested = len(actions)

    def finalize_hand(self, showdown_info: dict = None):
        """
        手牌结束，将本局摘要归档，触发画像更新。
        showdown_info: {"winner": "hero"/"opponent", "opponent_showed": ["Ah","Kh"], ...}
        """
        if not self.current_hand:
            return

        summary = self._summarize_hand(self.current_hand, showdown_info)
        self.completed_hands.append(summary)

        # 限制总手牌存储（保留最近 50 手）
        if len(self.completed_hands) > 50:
            self.completed_hands = self.completed_hands[-50:]

        self.current_hand = []
        self._actions_ingested = 0
        self.hands_since_profile_update += 1

    def reset_round(self):
        """新一局开始（如果上一局未正常结束则归档）"""
        if self.current_hand:
            self.finalize_hand()
        else:
            self.current_hand = []

    # ═══════════════════════════════════════════════════════════
    # 读牌：本局对手动作链 → 注入 DecisionAgent prompt
    # ═══════════════════════════════════════════════════════════

    def get_hand_reading_context(self) -> str:
        """
        输出本局对手动作链的结构化文本，供 OpponentAgent/DecisionAgent 推断对手手牌范围。
        只包含有实际对手动作的阶段，不含状态快照。
        """
        if not self.current_hand:
            return ""

        phase_cn = {"PRE_FLOP": "翻前", "FLOP": "翻牌", "TURN": "转牌", "RIVER": "河牌"}

        # 按阶段分组，分离动作事件和状态事件
        by_phase: dict[str, list[dict]] = defaultdict(list)
        state_by_phase: dict[str, dict] = {}
        for e in self.current_hand:
            phase = e.get("phase", "")
            if hasattr(phase, "name"):
                phase = phase.name
            if e.get("action_type"):
                by_phase[phase].append(e)
            elif "pot" in e:
                state_by_phase[phase] = e

        lines = []
        prev_pot = 0

        for phase_key in ("PRE_FLOP", "FLOP", "TURN", "RIVER"):
            actions = by_phase.get(phase_key, [])
            if not actions:
                continue

            label = phase_cn.get(phase_key, phase_key)
            actions_desc = []
            for a in actions:
                atype = a.get("action_type", "")
                amt = a.get("amount", 0)
                if atype in ("raise", "bet"):
                    pot = state_by_phase.get(phase_key, {}).get("pot", prev_pot)
                    pct = f" ({amt / pot * 100:.0f}%底池)" if pot > 0 else ""
                    actions_desc.append(f"{atype} {amt}{pct}")
                else:
                    actions_desc.append(f"{atype} {amt}" if amt else atype)

            state = state_by_phase.get(phase_key, {})
            pot_now = state.get("pot", prev_pot)
            cards_now = state.get("public_cards", [])
            cards_str = ", ".join(cards_now) if cards_now else "无"

            lines.append(
                f"{label}: {' → '.join(actions_desc)} | "
                f"底池={pot_now} | 公共牌=[{cards_str}]"
            )
            prev_pot = pot_now

        return "\n".join(f"  {l}" for l in lines) if lines else ""

    # ═══════════════════════════════════════════════════════════
    # 形象：跨牌局对手画像 → 注入 DecisionAgent prompt
    # ═══════════════════════════════════════════════════════════

    def get_profile_text(self) -> str:
        """返回缓存的对手画像（LLM 生成），供 DecisionAgent 参考"""
        if not self.profile:
            return self._basic_profile_text()

        lines = [
            f"对手类型: {self.profile.get('archetype', '未知')}",
            f"特征: {', '.join(self.profile.get('traits', []))}",
            f"VPIP估算: {self.profile.get('vpip_estimate', 'N/A')}",
            f"PFR估算: {self.profile.get('pfr_estimate', 'N/A')}",
            f"弱点: {', '.join(self.profile.get('weaknesses', []))}",
            f"建议对策: {self.profile.get('counter_strategy', '')}",
            f"置信度: {self.profile.get('confidence', 0):.0%} (基于{self.profile.get('hands_observed', 0)}手)",
        ]
        return "\n".join(f"- {l}" for l in lines)

    def _basic_profile_text(self) -> str:
        """没有 LLM 画像时的兜底：基于简单统计"""
        if not self.completed_hands:
            return "- 暂无对手数据"

        total = len(self.completed_hands)
        # 统计各街侵略频率
        agg_count = sum(1 for h in self.completed_hands if h.get("was_aggressive"))
        agg_pct = agg_count / total * 100 if total > 0 else 0

        return f"- 已观察{total}手 | 侵略频率≈{agg_pct:.0f}% | 数据有限，待更多手牌后生成详细画像"

    # ═══════════════════════════════════════════════════════════
    # 画像更新：调 LLM 生成对手形象
    # ═══════════════════════════════════════════════════════════

    def needs_profile_update(self) -> bool:
        """是否需要更新画像（至少累积 3 手且有新数据）"""
        return (len(self.completed_hands) >= 3
                and self.hands_since_profile_update >= 3)

    def update_profile(self, client, model: str):
        """
        异步调用 LLM 更新对手画像。调用方（main.py）负责在合适时机调用。
        client: OpenAI client
        model: 模型名
        """
        if len(self.completed_hands) < 3:
            return

        summaries = self._format_hand_summaries()
        prompt = f"""你是一位顶级德州扑克对手分析专家。以下是同一对手在{len(self.completed_hands)}手牌中的行为摘要。

请你分析并输出 JSON，不要包含任何解释。

### 对手行为历史:
{summaries}

### 已有画像（如有，可在此基础上更新）:
{json.dumps(self.profile, ensure_ascii=False) if self.profile else "无"}

### 输出格式:
{{{{
  "archetype": "LAG/TAG/LP/TP/Manic/Unknown",
  "traits": ["aggressive", "passive", "bluffs", "tight", "loose", "station", "nit"],
  "vpip_estimate": "如 '25-30%'",
  "pfr_estimate": "如 '15-20%'",
  "weaknesses": ["可被利用的漏洞"],
  "counter_strategy": "针对此对手的最佳策略简述（2-3句话）",
  "confidence": 0.7,
  "hands_observed": {len(self.completed_hands)}
}}}}
"""

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是德州扑克对手分析专家。始终只输出纯 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                stream=False,
                timeout=15,
            )
            raw = response.choices[0].message.content
            new_profile = self._parse_json(raw)

            # 合并新旧画像（新数据权重更高）
            if new_profile:
                new_profile["hands_observed"] = len(self.completed_hands)
                new_profile["confidence"] = min(
                    new_profile.get("confidence", 0.5) + 0.05,
                    0.95
                )
                self.profile = new_profile
                self.hands_since_profile_update = 0

        except Exception as e:
            print(f"[Memory] 画像更新失败: {e}")

    def _summarize_hand(self, events: list[dict], showdown_info: dict = None) -> dict:
        """将一手牌的原始事件压缩为摘要"""
        phase_cn = {"PRE_FLOP": "翻前", "FLOP": "翻牌", "TURN": "转牌", "RIVER": "河牌"}
        by_phase = defaultdict(list)
        for e in events:
            phase = e.get("phase", "")
            if hasattr(phase, "name"):
                phase = phase.name
            by_phase[phase].append(e)

        summary_parts = []
        was_aggressive = False

        for pk in ("PRE_FLOP", "FLOP", "TURN", "RIVER"):
            evts = by_phase.get(pk, [])
            for e in evts:
                atype = e.get("action_type", "")
                amt = e.get("amount", 0)
                pot = e.get("pot", 0)
                pct = f"({amt / pot * 100:.0f}%pot)" if pot > 0 and amt > 0 else ""
                summary_parts.append(f"{phase_cn.get(pk, pk)}:{atype}{' ' + str(amt) if amt else ''}{pct}")
                if atype in ("raise", "bet"):
                    was_aggressive = True

        result = {
            "summary": " | ".join(summary_parts),
            "was_aggressive": was_aggressive,
            "street_count": len(by_phase),
        }

        if showdown_info:
            result["showdown"] = showdown_info

        return result

    def _format_hand_summaries(self) -> str:
        """格式化已完成手牌的摘要列表"""
        lines = []
        for i, h in enumerate(self.completed_hands[-20:], 1):  # 最近 20 手
            lines.append(f"手牌{i}: {h['summary']}")
            if h.get("showdown"):
                sd = h["showdown"]
                lines.append(f"  → 摊牌: {json.dumps(sd, ensure_ascii=False)}")
        return "\n".join(lines)

    def _parse_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            return {}

    # ═══════════════════════════════════════════════════════════
    # 兼容旧接口
    # ═══════════════════════════════════════════════════════════

    def get_semantic_history(self) -> str:
        """
        旧接口，保留兼容。
        返回本局对手动作链的文本描述。
        """
        return self.get_hand_reading_context()
