# server/decision.py
import requests
import json

class PokerDecision:
    def __init__(self, model_name="qwen2.5:7b"): # 或者你本地的 qwen 3.5
        self.url = "http://localhost:11434/api/generate"
        self.model = model_name

    def ask_ai(self, game_context):
        """
        game_context: 传入 statemachine.data
        """
        from server.prompt import build_poker_prompt
        
        # 1. 构建提示词
        full_prompt = build_poker_prompt(game_context)
        
        # 2. 调用 Ollama 接口
        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "format": "json" # 强制模型输出 JSON，方便我们解析动作
        }
        
        try:
            response = requests.post(self.url, json=payload)
            result = response.json()
            return self._parse_action(result['response'])
        except Exception as e:
            print(f"Ollama 调用失败: {e}")
            return {"action": "check", "amount": 0}

    def _parse_action(self, ai_response):
        """解析 AI 返回的 JSON 字符串"""
        try:
            return json.loads(ai_response)
        except:
            # 备选方案：如果模型没按 JSON 输出，用正则提取
            return {"action": "call", "amount": 0}