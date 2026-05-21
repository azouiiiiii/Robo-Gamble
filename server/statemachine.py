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


if __name__ == "__main__":
    sm = PokerStateMachine()
    assert sm.current_phase == GamePhase.INIT
    assert sm.agent_state == AgentState.IDLE
    print("[PASS] init state")

    # derive_phase
    sm.data["public_cards"] = ["Ah", "Kh", "Qh"]
    sm.derive_phase()
    assert sm.current_phase == GamePhase.FLOP
    print("[PASS] 3 cards -> FLOP")

    sm.data["public_cards"].append("Jh")
    sm.derive_phase()
    assert sm.current_phase == GamePhase.TURN
    print("[PASS] 4 cards -> TURN")

    sm.data["public_cards"].append("Th")
    sm.derive_phase()
    assert sm.current_phase == GamePhase.RIVER
    print("[PASS] 5 cards -> RIVER")

    # update_pot
    sm.update_pot(100)
    assert sm.data["pot"] == 100
    sm.update_pot(200)
    assert sm.data["pot"] == 200
    assert len(sm.data["history_pots"]) == 2
    sm.update_pot(100)  # lower val ignored
    assert sm.data["pot"] == 200
    print("[PASS] update_pot")

    # reset
    sm.reset_hand()
    assert sm.current_phase == GamePhase.INIT
    assert sm.data["hand"] == []
    assert sm.data["pot"] == 0
    assert len(sm.data["history_pots"]) == 0
    print("[PASS] reset_hand")

    # agent state transitions
    sm.agent_state = AgentState.MY_TURN
    assert sm.agent_state == AgentState.MY_TURN
    sm.agent_state = AgentState.THINKING
    sm.agent_state = AgentState.ACTING
    sm.agent_state = AgentState.FOLDED
    print("[PASS] agent states")

    print("\n全部通过")