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
        将底池数字序列转化为 AI 易于理解的博弈描述
        """
        if len(self.history) < 2:
            return "本局刚开始，尚无显著底池波动。"

        narrative = []
        for i in range(1, len(self.history)):
            prev = self.history[i-1]
            curr = self.history[i]
            
            # 计算底池增量
            diff = curr['pot'] - prev['pot']
            phase_name = curr['phase'].name if hasattr(curr['phase'], 'name') else str(curr['phase'])

            if diff == 0:
                msg = f"在 {phase_name} 阶段，底池维持 {curr['pot']}，局势相对平稳。"
            elif diff > prev['pot'] * 0.5:
                msg = f"在 {phase_name} 阶段，底池从 {prev['pot']} 激增至 {curr['pot']}，显示有玩家进行了重注（Raise）。"
            else:
                msg = f"在 {phase_name} 阶段，底池小幅增至 {curr['pot']}，有人进行了跟注（Call）或试探性下注。"
            
            narrative.append(msg)

        return "\n".join(narrative)

    def get_history(self):
        return self.history