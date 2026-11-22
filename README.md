# Robo Gamble AI Agent

基于 LLM 的德州扑克决策引擎。游戏数据可以通过 HTTP API 输入，也可以由 Playwright Bridge 从浏览器的 Socket.IO WebSocket 中采集。

## 数据链路

### 当前 Bridge

```text
WebSocket 帧
  → Socket.IO 协议解码
  → 现有站点聚合器
  → GameDataSnapshot / GameState
  → 决策层

聚合器产生的对手动作增量 ──→ /observe ──→ 对手记忆
结算信息             ──→ /observe ──→ 对手记忆
```

当前站点聚合器仍按 Pokernow 的 Socket.IO 事件格式维护牌局状态。它是现有可运行实现，尚未拆成平台专属单事件解释器与独立状态聚合器。

### 目标数据链路

```text
原始 Socket.IO 帧
  → 平台专属解释器（直接读取原始帧，提取单帧牌局事实 / 状态补丁）
      ├─→ 状态聚合器（累计为当前 GameState，供决策层读取）
      └─→ 记忆层（消费对手动作、摊牌和结算等增量事件）
```

Learning prompt 只输入原始 WebSocket 帧，不接入通用 Socket.IO 解码器，也不输入 HTTP 事件。`learning_input.yaml` 定义帧筛选、顺序、prompt 字段和来源回查规则；人工采集分段只限定样本范围，不作为牌局边界标签。解释器只说明当前帧表达了什么，不持有整局累计状态。状态聚合器负责把单条事实应用到当前状态；记忆层按增量消费动作与结算信息。平台专属解释器的学习、验证和运行时尚待实现。

## 对手建模与决策

| 层 | 触发时机 | 职责 |
|---|---|---|
| L1 对手画像 | 对手动作与手牌结束 | 更新开枪频率、松紧度和侵略性等统计 |
| L2 读牌 | 对手动作 | 结合画像推断对手当前手牌范围 |
| L3 决策 | 己方回合 | 汇总当前 GameState 与 L1/L2 结果，生成动作 |

L1 和 L2 并行消费对手动作。每个对手拥有独立的缓冲队列和画像缓存；结算时会把摊牌信息交给记忆层校准。

## Bridge 模式

```bash
python bridge.py --mode learn --url http://localhost:7001
python bridge.py --mode verify --url http://localhost:7001
python bridge.py --mode auto --url http://localhost:7001
```

- `learn`：保存 WebSocket 原始帧和可选的 HTTP 网络事件；反引号键 `` ` `` / ``~`` 可标记牌局边界，并按 overlap 窗口生成分段引用。此模式只采集，不调用学习 Agent。
- `verify`：用当前内置 Socket.IO 聚合器解析事件，在页面浮层显示当前状态，不调用决策 Agent。
- `auto`：用同一聚合器生成决策状态，将对手动作和结算信息发送给 `/observe`，并自动执行决策。

采集配置位于 `config.json` 的 `bridge.capture`：`session_dir`、`boundary_key` 和 `overlap_ms`。原始网络事件以 JSONL 写入 `learning_sessions/`，WebSocket 帧与网络事件带有事件 ID 和流内序号。`bridge.replay.replay_socketio_session(path, hand_id=...)` 可用同一解码入口重放整份会话或指定分段。

Learning 输入契约位于 `learning_input.yaml`：学习 prompt 直接使用原始 WebSocket 帧；HTTP 仍可留在采集会话中，但不会进入该输入。当前 `verify` / `auto` 使用的 Socket.IO 解码和聚合路径与新 Learning 输入链路分开。

离线生成 prompt 输入时调用 `bridge.learning_input.build_learning_input(session_path, capture_segment_id=...)`。返回对象的 `prompt_text` 可交给 Learning Agent，`source_map` 在 prompt 外保留每行对应的原始事件 ID。

页面 HTTP 采集配置位于 `bridge.network_capture`。learn 模式会记录请求事件以保留请求与响应之间的关联；敏感请求头按配置脱敏。

## HTTP API 模式

```bash
python main.py
```

外部平台通过 `POST /decide` 提交游戏状态。字段名由 `config.json` 中的 `platform.field_map` 配置。`POST /observe` 接收对手动作、结算信息和网络上下文。

## 策略引擎

`strategy.yaml` 将扑克策略编码为两层：

- **硬约束**：不可违反的 guardrail，例如 `to_call=0` 时禁止 fold。
- **战术引导**：底池控制、价值尺度、翻牌/转牌策略和诈唬条件。

翻前策略按位置与手牌分数随机化边缘动作：

| 位置 | 强牌线 | 垃圾线 | Fold% | Call% | Raise% |
|---|---:|---:|---:|---:|---:|
| 前位 (UTG) | ≥7 | ≤3 | 55 | 35 | 10 |
| 中位 (MP) | ≥6 | ≤2 | 40 | 40 | 20 |
| 关煞 (CO) | ≥5 | ≤1 | 25 | 45 | 30 |
| 庄位 (BTN) | ≥4 | ≤0 | 15 | 45 | 40 |
| 小盲 (SB) | ≥6 | ≤2 | 40 | 30 | 30 |
| 大盲 (BB) | ≥4 | ≤0 | 25 | 50 | 25 |

## 快速开始

```bash
pip install -r requirements.txt
cp config.example.json config.json
# 在 config.json 中填写 AI API key
python main.py
python bridge.py --mode auto --url http://localhost:7001
```

Bridge 依赖 Playwright：`pip install playwright && playwright install chromium`。

## 主要目录

```text
main.py                   HTTP 决策服务入口
bridge.py                 CDP WebSocket Bridge 入口
core/                     GameState 与 HTTP 输入适配器
bridge/protocol.py        Socket.IO 帧解码
bridge/replay.py          原始会话到结构化 Socket.IO 事件的回放
bridge/aggregator.py      当前 Pokernow 事件聚合器
bridge/data_provider.py   Socket.IO 聚合器到 GameDataSnapshot 的适配
bridge/interceptor.py     WebSocket 拦截、采集和决策路由
bridge/session_recorder.py 原始网络事件持久化
bridge/hand_segments.py  人工边界与 overlap 分段
learning_input.yaml      Learning Agent 的原始 WebSocket 输入契约
bridge/learning_input.py 按契约筛选会话并渲染原始帧 prompt
server/                   对手记忆、读牌与决策
```
