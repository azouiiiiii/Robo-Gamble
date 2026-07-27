"""游戏状态聚合器：从 socket.io 事件流中增量重建完整游戏状态。"""

from enum import Enum


class GamePhase(Enum):
    INIT = "INIT"
    PRE_FLOP = "PRE_FLOP"
    FLOP = "FLOP"
    TURN = "TURN"
    RIVER = "RIVER"
    SETTLEMENT = "SETTLEMENT"


def _derive_phase(common_cards: list) -> GamePhase:
    n = len(common_cards)
    if n == 0:
        return GamePhase.PRE_FLOP
    elif n == 3:
        return GamePhase.FLOP
    elif n == 4:
        return GamePhase.TURN
    elif n >= 5:
        return GamePhase.RIVER
    return GamePhase.INIT


def _position_label(player_type: str) -> str:
    mapping = {"d": "BTN", "sb": "SB", "bb": "BB"}
    return mapping.get(player_type, "")


class GameStateAggregator:
    """
    从 socket.io 事件流中增量重建完整游戏状态。

    输入：一系列已解析的 socket.io 事件
    输出：合并后的 GameState dict，可直接 POST 给 agent /decide
    """

    def __init__(self, my_user_id: str, verbose: bool = False):
        self.my_user_id = my_user_id
        self.verbose = verbose
        self.reset()

    def reset(self):
        self.pot: int = 0
        self.prev_size: int = 0
        self.common_cards: list = []
        self.hand_cards: list = []
        self.players: dict = {}           # userId → player info
        self.small_blind: int = 0
        self.curr_player_id: str = ""
        self.phase: GamePhase = GamePhase.INIT
        self.table_id: str = ""
        self.round_id: str = ""
        self._action_history: list = []   # [{userId, actionCommand, actionSize, phase}]
        self._game_over_data: dict | None = None  # 结算原始数据（含摊牌）

    def feed(self, event: dict):
        """喂入一个已解析的 socket.io 事件，增量更新状态"""
        if event.get("_type") != "event":
            return

        name = event.get("name", "")
        data = event.get("data", {})

        # 兼容 parseMsg 包装格式: {data: {action: 'xxx', payload: {...}}}
        wrapped_action = ""
        wrapped_payload = {}
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            inner = data["data"]
            wrapped_action = inner.get("action", "")
            wrapped_payload = inner.get("payload", {})

        # ── online 广播/单播事件 ──
        if name == "online":
            action = data.get("action", "")
            payload = data.get("data", {})

            if action == "gameInfo":
                self._handle_game_info(payload)

            elif action == "actionComplete":
                cards = payload.get("commonCard", [])
                if isinstance(cards, list) and len(cards) > 0:
                    self.common_cards = [c for c in cards if c]
                    self.phase = _derive_phase(self.common_cards)
                self.curr_player_id = ""  # 清空, 等下一个 gameInfo 确定谁先动

            elif action == "gameOver":
                cards = payload.get("commonCard", [])
                if isinstance(cards, list):
                    self.common_cards = [c for c in cards if c]
                self.phase = GamePhase.SETTLEMENT
                self._game_over_data = payload
                for ap in payload.get("allPlayers", []):
                    pid = ap.get("userId", "")
                    if pid in self.players:
                        self.players[pid]["counter"] = ap.get("counter", 0)

            elif action == "newGame":
                self.reset()

            elif action == "players":
                for p in payload.get("players", []):
                    pid = p.get("userId", "")
                    if pid in self.players:
                        self.players[pid].update(p)
                    else:
                        self.players[pid] = p

            elif action == "userInfo":
                user = payload.get("userInfo", {})
                uid = user.get("userId", "")
                if uid:
                    self.my_user_id = uid
                    self.players[uid] = {**self.players.get(uid, {}), **user}
                    if self.verbose:
                        print(f"[Bridge] 👤 在线识别玩家: {user.get('nickName', uid)}")

            elif action == "handCard":
                cards = payload.get("handCard", [])
                if isinstance(cards, list) and len(cards) > 0:
                    self.hand_cards = [c for c in cards if c]

            elif action == "sitList":
                sl = payload.get("sitList", [])
                if isinstance(sl, list):
                    self.table_id = self.table_id or f"table_{id(sl)}"

        # ── parseMsg 包装格式：事件名是 socket ID，action 在 data.data.action 里 ──
        elif wrapped_action:
            if wrapped_action == "handCard":
                cards = wrapped_payload.get("handCard", [])
                if isinstance(cards, list) and len(cards) > 0:
                    self.hand_cards = [c for c in cards if c]
            elif wrapped_action == "gameInfo":
                gi = wrapped_payload.get("data", wrapped_payload)
                self._handle_game_info(gi)
            elif wrapped_action == "userInfo":
                user = wrapped_payload.get("userInfo", {})
                uid = user.get("userId", "")
                if uid:
                    self.my_user_id = uid
                    self.players[uid] = {**self.players.get(uid, {}), **user}

        # ── 兼容旧格式：事件名 = 真实 action ──
        elif name == "handCard":
            cards = (data if isinstance(data, list)
                     else wrapped_payload.get("handCard", [])
                     if wrapped_payload
                     else data.get("handCard", []))
            if isinstance(cards, list) and len(cards) > 0:
                self.hand_cards = [c for c in cards if c]

        elif name == "gameInfo":
            gi = wrapped_payload.get("data", wrapped_payload) if wrapped_payload else data
            self._handle_game_info(gi)

        elif name == "sitList":
            sl = wrapped_payload.get("sitList", wrapped_payload) if wrapped_payload else data
            if isinstance(sl, list):
                self.table_id = self.table_id or f"table_{id(sl)}"

        elif name == "userInfo":
            user = (wrapped_payload.get("userInfo", {}) if wrapped_payload
                    else data.get("userInfo", {}))
            uid = user.get("userId", "")
            if uid:
                self.my_user_id = uid
                self.players[uid] = {**self.players.get(uid, {}), **user}

    def _handle_game_info(self, data: dict):
        """处理 gameInfo（online 广播 + join 直接发送 两种格式）"""
        self.pot = data.get("pot", self.pot)
        self.prev_size = data.get("prevSize", self.prev_size)
        self.small_blind = data.get("smallBlind", self.small_blind)

        cards = data.get("commonCard", [])
        if isinstance(cards, list) and len(cards) > 0:
            self.common_cards = [c for c in cards if c]
            self.phase = _derive_phase(self.common_cards)

        if self.phase == GamePhase.INIT and self.pot > 0:
            self.phase = GamePhase.PRE_FLOP

        curr = data.get("currPlayer", {})
        self.curr_player_id = curr.get("userId", self.curr_player_id)

        for p in data.get("players", []):
            pid = p.get("userId", "")
            if pid:
                existing = self.players.get(pid, {})
                existing.update(p)
                self.players[pid] = existing
                cmd = p.get("actionCommand", "")
                if cmd:
                    phase_val = self.phase.value
                    prev_cmd = self._last_action_cmd(pid, phase_val)
                    if prev_cmd != cmd:
                        self._action_history.append({
                            "userId": pid,
                            "actionCommand": cmd,
                            "actionSize": p.get("actionSize", 0),
                            "phase": self.phase.value,
                        })

        sit_list = data.get("sitList", [])
        if sit_list:
            self.table_id = self.table_id or f"table_{id(sit_list)}"

    def _last_action_cmd(self, user_id: str, phase: str) -> str:
        """返回该玩家在当前阶段最近一次记录的动作命令"""
        for a in reversed(self._action_history):
            if a["userId"] == user_id and a.get("phase") == phase:
                return a["actionCommand"]
        return ""

    @staticmethod
    def _convert_card(card: str) -> str:
        """游戏格式 → 标准格式: l4→Ks, m3→Ah, j2→Jc"""
        if not card or len(card) < 2:
            return card
        rank_map = {'a': '2', 'b': '3', 'c': '4', 'd': '5', 'e': '6',
                    'f': '7', 'g': '8', 'h': '9', 'i': 'T', 'j': 'J',
                    'k': 'Q', 'l': 'K', 'm': 'A'}
        suit_map = {'1': 'd', '2': 'c', '3': 'h', '4': 's'}
        r = rank_map.get(card[0].lower(), card[0])
        s = suit_map.get(card[1], card[1])
        return r + s

    @staticmethod
    def _convert_cards(cards: list) -> list:
        return [GameStateAggregator._convert_card(c) for c in cards]

    def build_game_state(self) -> dict:
        """构建可直接 POST 给 agent /decide 的 GameState dict"""
        me = self.players.get(self.my_user_id, {})
        opponent = self._find_opponent()

        my_action_size = me.get("actionSize", 0)
        if my_action_size < 0:
            my_action_size = 0
        to_call = max(0, self.prev_size - my_action_size)

        result = {
            "current_stage": self.phase.value,
            "my_cards": self._convert_cards(self.hand_cards),
            "community_cards": self._convert_cards(self.common_cards),
            "total_pot": self.pot,
            "player": {
                "stack": me.get("counter", 0),
                "position": _position_label(me.get("type", "")),
            },
            "required_call": to_call,
            "action_history": [
                {
                    "action_type": a["actionCommand"],
                    "amount": a.get("actionSize", 0),
                    "phase": a.get("phase", self.phase.value),
                    "is_aggressor": a["actionCommand"] in ("raise", "bet"),
                    "opponent_id": a.get("userId", ""),
                }
                for a in self._action_history
                if a.get("userId") != self.my_user_id
            ],
            "opponent": {
                "stack": opponent.get("counter", 0),
            },
            "table": {"id": self.table_id},
            "round": {"id": self.round_id},
        }

        # 结算时附加摊牌信息
        if self.phase == GamePhase.SETTLEMENT and self._game_over_data:
            showdown = self._build_showdown(self._game_over_data)
            if showdown:
                result["showdown"] = showdown

        return result

    def _build_showdown(self, go: dict) -> dict | None:
        """从 gameOver 数据中提取摊牌信息"""
        winners = go.get("winner", [])
        all_players = go.get("allPlayers", [])

        # 找出对手 show 出的手牌
        opponent_showed = []
        hero_result = None
        my_id = self.my_user_id

        for ap in all_players:
            uid = ap.get("userId", "")
            cards = ap.get("handCard", [])
            income = ap.get("income", 0) or 0
            if uid != my_id:
                if isinstance(cards, list) and len(cards) > 0:
                    opponent_showed.extend(self._convert_cards(cards))
            else:
                hero_result = {"income": income, "hand_shown": self._convert_cards(cards) if isinstance(cards, list) else cards}

        # 判断谁赢了
        hero_won = False
        for tier in winners:
            for w in (tier if isinstance(tier, list) else [tier]):
                if w.get("userId", "") == my_id:
                    hero_won = True
                    break

        if not opponent_showed and hero_result is None:
            return None

        return {
            "winner": "hero" if hero_won else "opponent",
            "opponent_showed": opponent_showed,
            "hero_income": hero_result["income"] if hero_result else 0,
            "hero_showed": hero_result["hand_shown"] if hero_result else [],
        }

    def _find_opponent(self) -> dict:
        """找到对手（非自己的第一个玩家）"""
        for pid, p in self.players.items():
            if pid != self.my_user_id:
                return p
        return {}

    @property
    def is_my_turn(self) -> bool:
        return self.curr_player_id == self.my_user_id and self.phase not in (
            GamePhase.INIT, GamePhase.SETTLEMENT)
