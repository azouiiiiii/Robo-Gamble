# server/prompt.py

def build_poker_prompt(data, semantic_history):
    """
    data: 状态机当前的实时数据 (dict)
    semantic_history: 从 memory.get_semantic_history() 获取的字符串
    """
    
    prompt = f"""
你是一位顶级的德州扑克策略专家，现在正在进行一场高额桌对局。
请根据【历史动作回顾】和【当前局势】给出最优决策。

### 1. 历史动作回顾 (本局记忆):
{semantic_history}
*(注：请根据底池激增情况判断对手是否在进行诈唬或持有强牌)*

### 2. 当前局势:
- **游戏阶段**: {data['current_phase'].name}
- **我的手牌**: {data['hand']}
- **公共牌**: {data['public_cards']}
- **当前总底池**: {data['pot']}
- **我的剩余筹码**: {data['my_chips']}

### 3. 决策约束:
- 如果你选择 Raise（加注），请确保金额逻辑合理（通常为底池的 0.5x 到 1x）。
- 如果【历史动作回顾】显示底池在上一轮有巨大激增，而你只有弱对子，请倾向于 Fold。
- 只输出 JSON 格式。

### 你的决策输出 (JSON):
{{
  "action": "fold/check/call/raise",
  "amount": 0,
  "analysis": "简短分析为什么这么做",
  "confidence": "0-100% 信心值"
}}
"""
    return prompt