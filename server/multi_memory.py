"""
多对手记忆系统 — 三层并行架构

  L1: Profile Workers (×3)
      对手每次动作后触发 + 手牌结束时触发。
      批处理所有未消费事件，综合开枪频率/松紧度/侵略性更新画像。
      产出: profile dict → 存入 OpponentBuffer.profile

  L2: Hand Reader Workers (×3)
      对手每次动作后触发。
      批处理所有未消费事件，结合 L1 缓存画像做个性化手牌范围推断。
      产出: hand_reading dict → 存入 OpponentBuffer.hand_reading

  L3: Decision Maker
      flush_and_wait(5s) 等待 L1+L2 完成 → 整合上下文 → DecisionAgent.think()

缓冲区事件双标签:
  - profile_written: L1 已消化此 batch
  - hand_read:      L2 已消化此 batch
  两个都为 True → batch 移出缓冲区
"""

import json
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, Future


# ═══════════════════════════════════════════════════════════════
# EventBatch
# ═══════════════════════════════════════════════════════════════

class EventBatch:
    __slots__ = ('events', 'profile_written', 'hand_read', 'batch_id')
    _counter = 0

    def __init__(self, events: list[dict]):
        EventBatch._counter += 1
        self.events = events
        self.profile_written = False
        self.hand_read = False
        self.batch_id = EventBatch._counter

    @property
    def ready_to_remove(self) -> bool:
        return self.profile_written and self.hand_read


# ═══════════════════════════════════════════════════════════════
# OpponentBuffer — 单对手缓冲区
# ═══════════════════════════════════════════════════════════════

class OpponentBuffer:
    def __init__(self, opp_id: str):
        self.opp_id = opp_id
        self.batches: list[EventBatch] = []

        # L1 缓存
        self.profile: dict = {}
        self._profile_lock = threading.Lock()

        # L2 缓存
        self.hand_reading: dict = {}
        self._hand_lock = threading.Lock()

        # 防重复提交
        self._profile_task_pending = False
        self._hand_task_pending = False

        # 手牌归档
        self.completed_hands: list[dict] = []
        self.max_history = 50

    # ── 写入 ──

    def feed(self, events: list[dict]) -> EventBatch | None:
        if not events:
            return None
        batch = EventBatch(events)
        self.batches.append(batch)
        return batch

    def finalize_hand(self, summary: dict):
        self.completed_hands.append(summary)
        if len(self.completed_hands) > self.max_history:
            self.completed_hands = self.completed_hands[-self.max_history:]

    # ── L1 接口 ──

    def try_acquire_profile(self) -> bool:
        """尝试获取 profile 处理权。返回 True 表示抢到了。"""
        with self._profile_lock:
            if self._profile_task_pending:
                return False
            if not any(not b.profile_written for b in self.batches):
                return False
            self._profile_task_pending = True
            return True

    def collect_pending_events(self) -> list[dict]:
        """收集所有未被 L1 消费的事件"""
        events = []
        for b in self.batches:
            if not b.profile_written:
                events.extend(b.events)
        return events

    def mark_profile_done(self, profile: dict):
        with self._profile_lock:
            for b in self.batches:
                b.profile_written = True
            self._profile_task_pending = False
            if profile:
                self.profile = profile
        self._cleanup()

    # ── L2 接口 ──

    def try_acquire_hand(self) -> bool:
        """尝试获取 hand reader 处理权"""
        with self._hand_lock:
            if self._hand_task_pending:
                return False
            if not any(not b.hand_read for b in self.batches):
                return False
            self._hand_task_pending = True
            return True

    def collect_all_events(self) -> list[dict]:
        """收集所有 batch 的事件（L2 读牌用全量数据）"""
        events = []
        for b in self.batches:
            events.extend(b.events)
        return events

    def mark_hand_done(self, hand_reading: dict):
        with self._hand_lock:
            for b in self.batches:
                b.hand_read = True
            self._hand_task_pending = False
            if hand_reading:
                self.hand_reading = hand_reading
        self._cleanup()

    # ── 清理与查询 ──

    def _cleanup(self):
        while self.batches and self.batches[0].ready_to_remove:
            self.batches.pop(0)

    def force_flush(self):
        """超时兜底：强制标记所有 batch 完成"""
        with self._profile_lock:
            for b in self.batches:
                b.profile_written = True
            self._profile_task_pending = False
        with self._hand_lock:
            for b in self.batches:
                b.hand_read = True
            self._hand_task_pending = False
        self._cleanup()

    @property
    def pending_count(self) -> int:
        return len(self.batches)

    @property
    def is_ready(self) -> bool:
        return all(b.ready_to_remove for b in self.batches)

    @property
    def has_pending_profile(self) -> bool:
        return any(not b.profile_written for b in self.batches)

    @property
    def has_pending_hand(self) -> bool:
        return any(not b.hand_read for b in self.batches)

    @property
    def summary(self) -> str:
        parts = [self.opp_id[-6:]]
        if self.profile:
            parts.append(f"画:{self.profile.get('archetype','?')}")
        if self.hand_reading:
            parts.append(f"牌:{self.hand_reading.get('likely_strength','?')}")
        parts.append(f"待处理:{self.pending_count}")
        return " ".join(parts)


# ═══════════════════════════════════════════════════════════════
# MultiOpponentMemory
# ═══════════════════════════════════════════════════════════════

class MultiOpponentMemory:
    def __init__(self,
                 llm_client,
                 opponent_model: str,
                 profile_workers: int = 3,
                 hand_reader_workers: int = 3):
        self._client = llm_client
        self._model = opponent_model

        self._opponents: dict[str, OpponentBuffer] = {}
        self._lock = threading.Lock()

        self._profile_pool = ThreadPoolExecutor(
            max_workers=profile_workers, thread_name_prefix="L1-profile")
        self._hand_pool = ThreadPoolExecutor(
            max_workers=hand_reader_workers, thread_name_prefix="L2-hand")

        self._futures: list[Future] = []
        self._futures_lock = threading.Lock()

    # ═══════════════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════════════

    def get_or_create(self, opp_id) -> OpponentBuffer:
        opp_id = str(opp_id)
        with self._lock:
            if opp_id not in self._opponents:
                self._opponents[opp_id] = OpponentBuffer(opp_id)
            return self._opponents[opp_id]

    def on_opponent_action(self, opp_id: str, events: list[dict]):
        """
        对手动作到达 → 写入 buffer → 同时触发 L1 画像更新 + L2 读牌。
        L1 和 L2 并行执行，互不等待。
        """
        buf = self.get_or_create(opp_id)
        batch = buf.feed(events)
        if batch is None:
            return
        self._submit_profile_task(buf)
        self._submit_hand_task(buf)

    def on_hand_end(self, opp_id: str, summary: dict = None):
        """手牌结束 → 附带 L2 预测 → 归档 → 触发 L1 终局画像更新"""
        buf = self.get_or_create(opp_id)
        if summary:
            # 附带 L2 读牌预测，供 L1 对比摊牌校准画像
            if buf.hand_reading:
                summary["_l2_prediction"] = {
                    "hand_range": buf.hand_reading.get("hand_range", ""),
                    "likely_strength": buf.hand_reading.get("likely_strength", ""),
                    "bluff_probability": buf.hand_reading.get("bluff_probability", 0),
                }
            buf.finalize_hand(summary)
        # 手牌结束强制触发 L1（即使没有新 batch）
        if buf.has_pending_profile:
            self._submit_profile_task(buf)
        elif buf.completed_hands:
            buf.feed([{"_type": "hand_end"}])
            self._submit_profile_task(buf)

    def flush_and_wait(self, timeout: float = 5.0) -> str:
        """
        /decide 到达时调用。等待所有 L1+L2 任务完成，返回对手上下文文本。
        超时则强制 flush，用旧缓存兜底。
        """
        deadline = time.time() + timeout

        with self._futures_lock:
            pending = list(self._futures)

        for f in pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                f.result(timeout=max(remaining, 0.1))
            except Exception:
                pass

        with self._futures_lock:
            self._futures = [f for f in self._futures if not f.done()]

        # 超时兜底
        with self._lock:
            for buf in self._opponents.values():
                if not buf.is_ready:
                    buf.force_flush()

        return self.build_opponent_prompt_section()

    def get_opponent_ids(self) -> list[str]:
        with self._lock:
            return list(self._opponents.keys())

    def status(self) -> str:
        with self._lock:
            lines = [f"{len(self._opponents)} 个对手:"]
            for buf in self._opponents.values():
                lines.append(f"  {buf.summary}")
            return "\n".join(lines)

    def shutdown(self):
        self._profile_pool.shutdown(wait=False)
        self._hand_pool.shutdown(wait=False)

    # ═══════════════════════════════════════════════════════════
    # 任务提交
    # ═══════════════════════════════════════════════════════════

    def _submit_profile_task(self, buf: OpponentBuffer):
        if not buf.try_acquire_profile():
            return
        f = self._profile_pool.submit(self._do_profile_work, buf)
        self._track_future(f)

    def _submit_hand_task(self, buf: OpponentBuffer):
        if not buf.try_acquire_hand():
            return
        f = self._hand_pool.submit(self._do_hand_work, buf)
        self._track_future(f)

    def _track_future(self, f: Future):
        with self._futures_lock:
            self._futures.append(f)

    # ═══════════════════════════════════════════════════════════
    # L1: Profile Worker — 画像更新
    # ═══════════════════════════════════════════════════════════

    def _do_profile_work(self, buf: OpponentBuffer):
        try:
            pending = buf.collect_pending_events()
            if not pending:
                buf.mark_profile_done({})
                return

            real_events = [e for e in pending if e.get("_type") != "hand_end"]

            events_text = self._format_events(real_events) if real_events else "手牌结束，综合更新"
            existing = json.dumps(buf.profile, ensure_ascii=False) if buf.profile else "无"
            hand_summaries = self._format_hand_summaries_with_calibration(buf)

            prompt = (
                "你是德州扑克对手建模专家。根据累积数据更新对手画像。只输出纯 JSON。\n\n"
                "分析: 开枪频率(bet/raise占比)、打法松紧(入池倾向)、各阶段侵略性变化。\n"
                "如有摊牌历史，对比 L2 读牌预测 vs 实际亮牌来校准画像模型。\n\n"
                f"### 新动作 ({len(real_events)}条):\n{events_text}\n\n"
                f"### 历史手牌 ({len(buf.completed_hands)}手):\n{hand_summaries}\n\n"
                f"### 已有画像:\n{existing}\n\n"
                "### 输出格式:\n"
                '{"archetype":"LAG/TAG/LP/TP/Manic/Unknown",'
                '"traits":["aggressive","tight","loose","bluffs","station","nit"],'
                '"vpip_estimate":"25-30%",'
                '"pfr_estimate":"15-20%",'
                '"aggression_by_street":{"preflop":"high/medium/low","flop":"high/medium/low",'
                '"turn":"high/medium/low","river":"high/medium/low"},'
                '"weaknesses":["漏洞"],'
                '"counter_strategy":"对策(2-3句)",'
                '"confidence":0.7,'
                f'"hands_observed":{len(buf.completed_hands)}}}'
            )

            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "只输出纯 JSON。基于累积数据持续迭代画像。"},
                    {"role": "user", "content": prompt},
                ],
                stream=False, timeout=15,
            )
            result = self._parse_json(resp.choices[0].message.content)
            if result:
                result["hands_observed"] = len(buf.completed_hands)
                result["confidence"] = min(result.get("confidence", 0.5) + 0.03, 0.95)
            buf.mark_profile_done(result or {})
            print(f"[L1] 画像 opp={buf.opp_id[-6:]} "
                  f"events={len(real_events)} arch={result.get('archetype','?') if result else 'fail'}")

        except Exception as e:
            print(f"[L1] 画像失败 opp={buf.opp_id[-6:]}: {e}")
            buf.mark_profile_done({})

    # ═══════════════════════════════════════════════════════════
    # L2: Hand Reader Worker — 读牌
    # ═══════════════════════════════════════════════════════════

    def _do_hand_work(self, buf: OpponentBuffer):
        try:
            all_events = buf.collect_all_events()
            real_events = [e for e in all_events if e.get("_type") != "hand_end"]
            if not real_events:
                buf.mark_hand_done({})
                return

            # 直接读 L1 缓存画像（不等 L1 当前任务，画像是慢变量）
            profile_text = json.dumps(buf.profile, ensure_ascii=False) if buf.profile else "暂无画像"

            prompt = (
                "你是德州扑克读牌专家。结合对手画像 + 本局动作链推断当前手牌范围。只输出纯 JSON。\n\n"
                f"### 对手画像(缓存,可能略旧):\n{profile_text}\n\n"
                f"### 本局动作链:\n{self._format_events(real_events)}\n\n"
                "### 输出格式:\n"
                '{"hand_range":"对手可能的手牌范围(1-2句)",'
                '"likely_strength":"weak/medium/strong/very_strong",'
                '"aggression_level":"passive/neutral/aggressive/very_aggressive",'
                '"bluff_probability":0.0,'
                '"exploitable_tendency":"当前可被利用的倾向(1句)",'
                '"recommended_approach":"针对当前局面的策略建议(1-2句)"}'
            )

            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": "只输出纯 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                stream=False, timeout=15,
            )
            result = self._parse_json(resp.choices[0].message.content)
            buf.mark_hand_done(result or {})
            print(f"[L2] 读牌 opp={buf.opp_id[-6:]} "
                  f"range={result.get('hand_range','?')[:50] if result else 'fail'}")

        except Exception as e:
            print(f"[L2] 读牌失败 opp={buf.opp_id[-6:]}: {e}")
            buf.mark_hand_done({})

    # ═══════════════════════════════════════════════════════════
    # 上下文构建 (L3 用)
    # ═══════════════════════════════════════════════════════════

    def build_opponent_prompt_section(self) -> str:
        with self._lock:
            profiles = {oid: dict(buf.profile) for oid, buf in self._opponents.items() if buf.profile}
            readings = {oid: dict(buf.hand_reading) for oid, buf in self._opponents.items() if buf.hand_reading}

        if not profiles and not readings:
            return "- 暂无对手数据。"

        lines = []
        all_ids = sorted(set(profiles.keys()) | set(readings.keys()))
        for opp_id in all_ids:
            label = f"对手 {opp_id[-4:]}"
            parts = []

            p = profiles.get(opp_id, {})
            if p:
                streets = p.get("aggression_by_street", {})
                street_str = "/".join(f"{k}={v}" for k, v in streets.items()) if streets else ""
                parts.append(
                    f"类型={p.get('archetype','?')} | "
                    f"VPIP={p.get('vpip_estimate','?')} | "
                    f"PFR={p.get('pfr_estimate','?')}"
                    + (f" | 侵略={street_str}" if street_str else "")
                    + f" | 弱点={','.join(p.get('weaknesses',[])) or '无'}"
                )

            r = readings.get(opp_id, {})
            if r:
                parts.append(
                    f"范围={r.get('hand_range','?')} | "
                    f"牌力={r.get('likely_strength','?')} | "
                    f"侵略={r.get('aggression_level','?')} | "
                    f"诈唬={r.get('bluff_probability',0):.0%}"
                )

            if parts:
                lines.append(f"- {label}: {' || '.join(parts)}")

            approach = (r or {}).get("recommended_approach") or (p or {}).get("counter_strategy", "")
            if approach:
                lines.append(f"  对策: {approach}")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 工具
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _format_hand_summaries_with_calibration(buf) -> str:
        """格式化历史手牌摘要，附带 L2 预测 vs 实际摊牌对比"""
        lines = []
        for i, h in enumerate(buf.completed_hands[-20:], 1):
            summary = h.get("summary", str(h))
            lines.append(f"手牌{i}: {summary}")

            # L2 预测 vs 实际摊牌
            l2_pred = h.get("_l2_prediction", {})
            opponent_showed = h.get("opponent_showed", [])

            if l2_pred:
                lines.append(f"  L2预测: 范围={l2_pred.get('hand_range','?')} | "
                             f"牌力={l2_pred.get('likely_strength','?')} | "
                             f"诈唬概率={l2_pred.get('bluff_probability',0):.0%}")

            if opponent_showed:
                cards_str = ", ".join(opponent_showed)
                lines.append(f"  实际亮牌: {cards_str}")
                # 对比提示
                if l2_pred:
                    lines.append("  → 请对比预测 vs 实际,校准画像")

            # 胜负信息
            winner = h.get("winner", "")
            if winner:
                lines.append(f"  结果: {'对手赢' if winner == 'opponent' else 'Hero赢'}")

            if l2_pred or opponent_showed:
                lines.append("")  # 空行分隔有摊牌的手牌

        return "\n".join(lines) if lines else "暂无历史"

    @staticmethod
    def _format_events(events: list[dict]) -> str:
        phase_cn = {"PRE_FLOP": "翻前", "FLOP": "翻牌", "TURN": "转牌", "RIVER": "河牌"}
        lines = []
        for e in events[-30:]:
            atype = e.get("action_type", e.get("actionCommand", "?"))
            amt = e.get("amount", e.get("actionSize", 0))
            phase = e.get("phase", "?")
            cn = phase_cn.get(phase, phase)
            lines.append(f"- {cn}: {atype} {amt}" if amt else f"- {cn}: {atype}")
        return "\n".join(lines) if lines else "暂无动作"

    @staticmethod
    def _parse_json(text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return {}
