# decision.py
import requests
import json
import re

class PokerDecision:
    def __init__(self, config):
        self.cfg = config
        # 从 config.json 获取模型和 URL
        self.url = self.cfg.get("ai.ollama_url")
        self.model = self.cfg.get("ai.model")

    def ask_ai(self, game_context, semantic_history):
        """
        修正：现在正确接收两个参数
        """
        from server.prompt import build_poker_prompt
        full_prompt = build_poker_prompt(game_context, semantic_history)
        
        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "format": "json"
        }
        
        try:
            # 增加超时，防止 Ollama 响应慢导致游戏卡死
            response = requests.post(self.url, json=payload, timeout=30)
            response.raise_for_status()
            
            raw_res = response.json()['response']
            return self._parse_json_safely(raw_res)
            
        except Exception as e:
            print(f"[DECISION] AI 调用失败: {e}")
            return {"action": "check", "amount": 0, "analysis": "error fallback"}

    def _parse_json_safely(self, text):
        """
        鲁棒性解析：处理 Markdown 代码块干扰
        """
        try:
            # 尝试直接解析
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试用正则提取第一个 { ... }
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
            # 最终保底
            return {"action": "call", "amount": 0}