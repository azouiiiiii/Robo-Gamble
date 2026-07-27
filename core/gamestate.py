from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum


class GamePhase(Enum):
    INIT = "INIT"
    PRE_FLOP = "PRE_FLOP"
    FLOP = "FLOP"
    TURN = "TURN"
    RIVER = "RIVER"
    SETTLEMENT = "SETTLEMENT"


class AgentState(Enum):
    IDLE = "IDLE"
    MY_TURN = "MY_TURN"
    THINKING = "THINKING"
    ACTING = "ACTING"
    FOLDED = "FOLDED"


@dataclass
class OpponentAction:
    """对手的一次动作"""
    action_type: str       # "fold", "check", "call", "raise", "bet"
    amount: int = 0        # 下注/加注金额
    phase: str = ""        # 发生在哪个阶段
    is_aggressor: bool = False  # 是否是主动下注/加注方
    opponent_id: str = ""  # 对手 ID (多人局分对手用)


@dataclass
class GameState:
    """统一的游戏状态，所有平台适配器都输出此格式"""
    phase: GamePhase
    hand: List[str] = field(default_factory=list)       # ["Ah", "Kh"]
    public_cards: List[str] = field(default_factory=list)  # ["Qh", "Jh", "2d"]
    pot: int = 0
    my_chips: int = 0
    to_call: int = 0                                     # 跟注需要支付的金额，0 表示 check
    my_position: str = ""                                # "BTN", "BB", "SB", "UTG", etc.
    opponent_actions: List[OpponentAction] = field(default_factory=list)
    opponent_chips: int = 0                              # 对手剩余筹码
    showdown: Optional[Dict] = None                      # 结算时的摊牌信息
    table_id: str = ""                                   # 牌桌标识，用于跨牌局记忆
    round_id: str = ""                                   # 当前局标识

    @property
    def is_check(self) -> bool:
        return self.to_call == 0

    @property
    def phase_name(self) -> str:
        return self.phase.value

    def to_context_dict(self) -> dict:
        """转换为 prompt.py / decision.py 所需的上下文 dict（向后兼容）"""
        return {
            "current_phase": self.phase,
            "hand": self.hand,
            "public_cards": self.public_cards,
            "pot": self.pot,
            "my_chips": self.my_chips,
            "to_call": self.to_call,
            "my_position": self.my_position,
        }
