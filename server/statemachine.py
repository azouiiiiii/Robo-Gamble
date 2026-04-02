from enum import Enum, auto

class GamePhase(Enum):
    """游戏物理阶段"""
    INIT = auto()         
    PRE_FLOP = auto()     
    FLOP = auto()         
    TURN = auto()         
    RIVER = auto()        
    SETTLEMENT = auto()   
    WAITING = auto()      

class AgentState(Enum):
    """智能体决策状态"""
    IDLE = auto()         
    MY_TURN = auto()      
    THINKING = auto()     
    ACTING = auto()       
    FOLDED = auto()      

class PokerStateMachine:
    def __init__(self):
        self.current_phase = GamePhase.INIT
        self.agent_state = AgentState.IDLE
        
        # 实时数据存储
        self.data = {
            "hand": None,        
            "public_cards": [],  
            "pot": 0,            # <--- 存储右上角识别到的底池总额
            "my_chips": 0,       
            "is_my_turn": False,
            "history_pots": []   # 选填：记录本局每一轮的底池变化，方便 AI 分析对手力度
        }

    def reset_hand(self):
        """新一局开始时重置状态"""
        self.current_phase = GamePhase.INIT
        self.agent_state = AgentState.IDLE
        self.data["pot"] = 0
        self.data["public_cards"] = []
        self.data["hand"] = None
        self.data["history_pots"] = []

    def update_pot(self, new_pot_value):
        """
        专门更新底池的方法
        加入简单的逻辑判断：底池只应增加不应减少（除非新一局开始）
        """
        if new_pot_value >= self.data["pot"]:
            self.data["pot"] = new_pot_value
        elif self.current_phase == GamePhase.SETTLEMENT:
            # 结算阶段允许底池归零
            self.data["pot"] = 0

    def update_by_perception(self, perception_results):
        """
        根据感知层的识别结果更新状态机
        """
        # 提取识别到的 pot 值
        if "pot" in perception_results:
            self.update_pot(perception_results["pot"])
            
        # 更新其他基础数据
        self.data.update({k: v for k, v in perception_results.items() if k != "pot"})
        
        # 逻辑判断阶段
        self.derive_phase()
        
        # 更新动作状态
        if self.data.get("is_my_turn"):
            # 如果不是正在思考或动作中，切换到 MY_TURN
            if self.agent_state not in [AgentState.THINKING, AgentState.ACTING, AgentState.FOLDED]:
                self.agent_state = AgentState.MY_TURN
        else:
            if self.agent_state != AgentState.FOLDED:
                self.agent_state = AgentState.IDLE

    def derive_phase(self):
        pc_count = len(self.data.get("public_cards", []))
        if pc_count == 0 and self.data.get("hand"):
            self.current_phase = GamePhase.PRE_FLOP
        elif pc_count == 3:
            self.current_phase = GamePhase.FLOP
        elif pc_count == 4:
            self.current_phase = GamePhase.TURN
        elif pc_count == 5:
            self.current_phase = GamePhase.RIVER