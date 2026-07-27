# Robo Gamble AI Agent

![cover](cover.jpg)

基于 LLM 的德州扑克 AI 决策引擎，通过 WebSocket 拦截或 HTTP API 获取游戏状态，三层并行架构进行对手建模与决策。

---

## 架构

```
                    ┌──────────────────────────┐
                    │       Game Platform       │
                    │  (TexasPokerGame-master)  │
                    └─────┬──────────┬─────────┘
                          │          │
              WebSocket 帧 │          │ HTTP API
                          ▼          ▼
                    ┌──────────┐  ┌──────────┐
                    │ bridge.py│  │ main.py  │
                    │ (CDP拦截) │  │ (HTTP服务)│
                    └────┬─────┘  └────┬─────┘
                         │             │
                         └──────┬──────┘
                                ▼
              ┌─────────────────────────────────┐
              │     MultiOpponentMemory         │
              │                                  │
              │  L1: Profile Workers (×3)        │
              │  L2: Hand Reader Workers (×3)    │
              │  L3: Decision Agent  (×1)        │
              └─────────────────────────────────┘
```

### 三层并行流水线

| 层 | 工作线程 | 触发时机 | 职责 |
|---|---------|---------|------|
| L1 | Profile Workers ×3 | 对手每次动作 + 手牌结束 | 综合开枪频率/松紧度/侵略性更新对手画像 |
| L2 | Hand Reader Workers ×3 | 对手每次动作 | 结合画像缓存推断对手当前手牌范围 |
| L3 | Decision Agent ×1 | 己方回合 | 等 L1+L2 完成后整合 prompt，开 thinking 决策 |

L1 和 L2 并行消费对手队列。每批事件有双标签（`profile_written` + `hand_read`），两者都为 True 时移出缓冲区。

### 多对手建模

每个对手拥有独立的缓冲队列和画像缓存。`/observe` 端点按 `opp_id` 路由到对应队列，L1/L2 workers 并行处理。L2 直接读 L1 缓存画像做个性化读牌，不等 L1 当次任务完成。手牌结束时 L2 预测与摊牌实际对比反馈给 L1 校准。

---

## 部署方式

### 方式 1：HTTP API 模式

```bash
python main.py  # 启动 AI 引擎，监听 :8765
```

外部平台通过 `POST /decide` 推送游戏状态，引擎返回决策 JSON。字段名通过 `config.json` 的 `field_map` 映射。

### 方式 2：CDP WebSocket 桥接模式

```bash
python bridge.py --auto --url http://localhost:7001
```

通过 Playwright CDP 拦截浏览器 WebSocket 帧，自动提取游戏状态、上报对手动作、执行 AI 决策。支持两种模式：

- `--auto`：自动执行决策
- 不加 `--auto`：仅页面浮层显示建议

---

## 策略引擎

`strategy.yaml` 将扑克策略编码为两层：

- **硬约束**：不可违反的 guardrail（`to_call=0` 禁止 fold、强牌分数门槛等）
- **战术引导**：软建议（底池控制、价值尺度、翻牌/转牌策略、诈唬条件）

### 翻前 GTO 混合策略

基于位置 + 手牌分数的随机化入池决策，避免打法被对手摸透：

| 位置 | 强牌线 | 垃圾线 | Fold% | Call% | Raise% |
|------|--------|--------|-------|-------|--------|
| 前位(UTG) | ≥7 | ≤3 | 55 | 35 | 10 |
| 中位(MP) | ≥6 | ≤2 | 40 | 40 | 20 |
| 关煞(CO) | ≥5 | ≤1 | 25 | 45 | 30 |
| 庄位(BTN) | ≥4 | ≤0 | 15 | 45 | 40 |
| 小盲(SB) | ≥6 | ≤2 | 40 | 30 | 30 |
| 大盲(BB) | ≥4 | ≤0 | 25 | 50 | 25 |

边缘手牌按权重随机分配动作，强牌不受干预、垃圾牌强制弃牌。

---

## 快速开始

```bash
pip install -r requirements.txt

# 配置
cp config.example.json config.json
# 编辑 config.json，填入 AI API key

# 先启动 AI 引擎
python main.py

# 再启动桥接器（连接游戏浏览器）
python bridge.py --auto --url http://localhost:7001
```

---

## 目录结构

```
texas/
├── main.py                  # HTTP API 模式入口
├── bridge.py                # CDP WebSocket 桥接器入口
├── config.json              # 运行配置
├── strategy.yaml            # 扑克策略 DSL
├── requirements.txt
│
├── server/                  # AI 决策核心
│   ├── decision.py          # DecisionAgent LLM 调用
│   ├── evaluator.py         # 确定性计算（手牌评估/赔率/outs）
│   ├── prompt.py            # Prompt 构建 + YAML 规则引擎
│   ├── memory.py            # 旧单对手记忆（已弃用）
│   └── multi_memory.py      # 多对手记忆系统（三层并行）
│
├── core/                    # 数据模型
│   ├── gamestate.py         # GameState / OpponentAction / GamePhase
│   └── adapter.py           # 平台适配器（HTTP 服务器 + field_map）
│
├── bridge/                  # CDP WebSocket 桥接器
│   ├── protocol.py          # Socket.IO 线协议解析
│   ├── aggregator.py        # 事件流 → GameState 聚合
│   ├── interceptor.py       # Playwright CDP 拦截 + 决策路由
│   └── browser.py           # 浏览器生命周期管理
│
└── utils/
    ├── config_manager.py    # JSON 配置加载
    └── logger.py            # 日志
```

---

## 设计原则

- **LLM 做策略推理 + 确定性代码做数学计算**：赔率、outs、成牌判定由 `evaluator.py` 精确计算，LLM 不做数学
- **三层并行解耦**：画像更新、读牌、决策互不阻塞，超时缓存兜底
- **硬约束优先**：`strategy.yaml` 的 guardrail 不可违反，LLM 在边界内自由发挥
- **平台无关**：换平台只改 `config.json` 的 `field_map`，不改代码