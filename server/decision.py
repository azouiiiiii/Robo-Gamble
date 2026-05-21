import re
import json
from openai import OpenAI
from server.prompt import build_poker_prompt


class PokerDecision:
    def __init__(self, config):
        self.cfg = config
        self.model = self.cfg.get("ai.model")
        self.client = OpenAI(
            api_key=self.cfg.get("ai.api_key"),
            base_url=self.cfg.get("ai.base_url")
        )
        self.thinking = self.cfg.get("ai.thinking")
        self.reasoning_effort = self.cfg.get("ai.reasoning_effort")

    def ask_ai(self, game_context, semantic_history):
        full_prompt = build_poker_prompt(game_context, semantic_history)

        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是德州扑克策略专家。始终只输出纯 JSON，不要包含任何解释文字。"},
                {"role": "user", "content": full_prompt}
            ],
            "stream": False,
            "reasoning_effort": self.reasoning_effort,
            "extra_body": {"thinking": {"type": "enabled"}}
        }

        try:
            response = self.client.chat.completions.create(**kwargs)
            raw_res = response.choices[0].message.content
            return self._parse_json_safely(raw_res)

        except Exception as e:
            print(f"[DECISION] AI 调用失败: {e}")
            return {"action": "check", "amount": 0, "analysis": "error fallback"}

    def _parse_json_safely(self, text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
            return {"action": "call", "amount": 0}
