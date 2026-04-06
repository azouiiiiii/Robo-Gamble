# prompt.py
def build_poker_prompt(data, semantic_history):
    # 处理空手牌和公共牌的显示逻辑
    hand_str = ", ".join(data['hand']) if data['hand'] else "尚未看牌或已弃牌"
    public_str = ", ".join(data['public_cards']) if data['public_cards'] else "桌面无公共牌"
    
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

### 3. 强制要求:
- 如果选择 Raise，金额必须为整数，通常为底池的 0.5x 到 1.2x。
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