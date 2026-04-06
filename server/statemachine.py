# statemachine.py
from enum import Enum, auto

class GamePhase(Enum):
    INIT = auto()
    PRE_FLOP = auto()
    FLOP = auto()
    TURN = auto()
    RIVER = auto()
    SETTLEMENT = auto()

class AgentState(Enum):
    IDLE = auto()
    MY_TURN = auto()
    THINKING = auto()
    ACTING = auto()
    FOLDED = auto()

class PokerStateMachine:
    def __init__(self):
        self.current_phase = GamePhase.INIT
        self.agent_state = AgentState.IDLE
        self.data = {
            "hand": [],
            "public_cards": [],
            "pot": 0,
            "my_chips": 0,
            "history_pots": []  # 新增：用于 memory 模块对比
        }

    def update_pot(self, new_val):
        """更新底池，并记录增量"""
        if new_val > self.data["pot"]:
            self.data["history_pots"].append(new_val)
            self.data["pot"] = new_val
        elif self.current_phase == GamePhase.SETTLEMENT:
            self.data["pot"] = 0
            self.data["history_pots"] = []

    def reset_hand(self):
        """一局结束后的清理"""
        print("[SM] 状态重置：开启新一局")
        self.current_phase = GamePhase.INIT
        self.agent_state = AgentState.IDLE
        self.data["hand"] = []
        self.data["public_cards"] = []
        self.data["pot"] = 0
        self.data["history_pots"] = []

    def derive_phase(self):
        """根据公共牌数量自动修正阶段（双重保险）"""
        count = len(self.data["public_cards"])
        if count == 0 and self.current_phase != GamePhase.PRE_FLOP:
            pass # 保持当前或依赖 detector
        elif count == 3: self.current_phase = GamePhase.FLOP
        elif count == 4: self.current_phase = GamePhase.TURN
        elif count == 5: self.current_phase = GamePhase.RIVER