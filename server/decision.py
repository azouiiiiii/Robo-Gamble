import re
import json
from openai import OpenAI
from server.prompt import build_poker_prompt, build_prompt_from_state, build_opponent_prompt
from server.evaluator import preflop_hand_strength, evaluate_hand, pot_odds_analysis


class PokerDecision:
    def __init__(self, config):
        self.cfg = config
        self.model = self.cfg.get("ai.model")
        self.opponent_model = self.cfg.get("ai.opponent_model", self.model)
        self.client = OpenAI(
            api_key=self.cfg.get("ai.api_key"),
            base_url=self.cfg.get("ai.base_url")
        )
        self.reasoning_effort = self.cfg.get("ai.reasoning_effort")

    # ═══════════════════════════════════════════════════════════
    # Agent 1: OpponentAgent — 对手行为分析（快模型，无深度思考）
    # ═══════════════════════════════════════════════════════════

    def analyze_opponent(self, hand_context: str, profile_text: str,
                         opponent_actions: list = None) -> dict:
        """
        OpponentAgent: 分析对手当前手牌范围和长期形象。
        使用轻量模型，不启用 thinking，快速返回结构化分析。
        """
        prompt = build_opponent_prompt(hand_context, profile_text, opponent_actions)

        try:
            response = self.client.chat.completions.create(
                model=self.opponent_model,
                messages=[
                    {"role": "system", "content": "你是对手行为分析专家。始终只输出纯 JSON。"},
                    {"role": "user", "content": prompt}
                ],
                stream=False,
                timeout=15,
            )
            raw = response.choices[0].message.content
            return self._parse_json_safely(raw)

        except Exception as e:
            print(f"[OpponentAgent] 调用失败: {e}")
            return {}

    # ═══════════════════════════════════════════════════════════
    # Agent 2: DecisionAgent — 综合决策（深模型，启用思考）
    # ═══════════════════════════════════════════════════════════

    def decide(self, gs, semantic_history="", opponent_analysis=None):
        """
        DecisionAgent: 综合游戏状态 + 对手分析 + 策略约束，输出最终决策。
        使用深度模型，启用 thinking。
        """
        eval_data = evaluate_hand(gs.hand, gs.public_cards)
        preflop = preflop_hand_strength(gs.hand)
        odds = pot_odds_analysis(gs.pot, gs.to_call)

        full_prompt = build_prompt_from_state(
            gs, eval_data, preflop, odds, semantic_history,
            opponent_analysis=opponent_analysis or "",
        )

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是德州扑克策略专家。结合对手分析报告调整策略。始终只输出纯 JSON。"},
                {"role": "user", "content": full_prompt}
            ],
            "stream": False,
            "timeout": 45,
            "reasoning_effort": self.reasoning_effort,
            "extra_body": {"thinking": {"type": "enabled"}}
        }

        try:
            response = self.client.chat.completions.create(**kwargs)
            raw_res = response.choices[0].message.content
            return self._parse_json_safely(raw_res)

        except Exception as e:
            print(f"[DecisionAgent] AI 调用失败: {e}")
            return {"action": "check", "amount": 0, "analysis": "error fallback"}

    # ═══════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════

    def ask_ai(self, game_context, semantic_history):
        """旧接口"""
        full_prompt = build_poker_prompt(game_context, semantic_history)
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是德州扑克策略专家。始终只输出纯 JSON。"},
                {"role": "user", "content": full_prompt}
            ],
            "stream": False,
            "reasoning_effort": self.reasoning_effort,
            "extra_body": {"thinking": {"type": "enabled"}}
        }
        try:
            response = self.client.chat.completions.create(**kwargs)
            return self._parse_json_safely(response.choices[0].message.content)
        except Exception as e:
            print(f"[DECISION] AI 调用失败: {e}")
            return {"action": "check", "amount": 0, "analysis": "error fallback"}

    def _parse_json_safely(self, text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
            return {"action": "call", "amount": 0}
