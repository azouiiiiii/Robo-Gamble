# Robo Gamble AI Agent

![cover](cover.jpg)

基于计算机视觉 + 大语言模型的德州扑克 AI 智能体，通过屏幕截图感知游戏状态、LLM 进行战略决策、鼠标自动化执行动作，实现实时对局。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3 |
| LLM | OpenAI兼容API调用 |
| 计算机视觉 | OpenCV |
| 图像处理 | Pillow、NumPy |

---

## 架构

```
┌─────────────────────────────────────────────────────┐
│                     main.py                         │
│                   (游戏主循环 ~10Hz)                  │
└────┬──────────┬──────────┬──────────┬───────────────┘
     │          │          │          │
     ▼          ▼          ▼          ▼
┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐
│perception│ │ server  │ │controller│ │   utils     │
│  感知层  │ │  决策层  │ │  执行层  │ │   工具层    │
└─────────┘ └─────────┘ └─────────┘ └─────────────┘

感知 ──▶ 状态机 ──▶ 评估器 ──▶ LLM 决策 ──▶ 执行器 ──▶ 循环
```

### 感知层 (`perception/`)

- **`capture.py`**：截屏 + EasyOCR + 模板匹配识别牌面、底池、筹码等数字信息
- **`detect.py`**：像素检测 + 模板匹配判断游戏状态（到自己回合 / 结算 / 公共牌数量）

### 决策层 (`server/`)

- **`statemachine.py`**：管理游戏阶段和智能体状态，维护对局数据
- **`evaluator.py`**：确定性手牌评估、底池赔率、公牌结构分析（不依赖 LLM）
- **`decision.py`**：调用 LLM API，解析决策响应
- **`prompt.py`**：构建 LLM 提示词，注入游戏状态 + 手牌评估 + YAML 策略规则
- **`memory.py`**：记录对局事件，生成对手行为语义分析

### 执行层 (`controller/`)

- **`executor.py`**：将决策转化为鼠标点击操作

### 工具层 (`utils/`)

- **`config_manager.py`**：配置加载，支持 DPI 缩放
- **`logger.py`**：文件 + 控制台日志
- **`log_server.py`**：SSE 实时日志 Web 看板，支持 ngrok 公网隧道

---

## 🌟🌟🌟

### 1. LLM + YAML 规则引擎的双层决策架构

不是纯规则引擎（太死板），也不是裸调 LLM（太不可控）。`strategy.yaml` 将扑克策略编码为两层：

- **硬约束**（不可违反的 guardrail）：如 "AA/KK/QQ 翻前永不弃牌"、"to_call=0 时禁止 fold"、"翻前起手评分 ≥6 且所需权益 ≤45% 时必须继续"
- **战术引导**（软建议，影响"怎么打"而非"能不能打"）：底池控制、保护下注、价值尺度、翻牌/转牌策略、check-raise、诈唬条件

LLM 在硬约束的边界内自由发挥，既有创造力又不会犯低级错误。

### 2. 纯视觉感知，无需游戏 API

整套系统通过屏幕截图 + 像素读取 + 鼠标模拟完成交互，不注入内存、不 hook API、不修改游戏客户端。理论上可适配任何渲染布局可预测的扑克客户端（通过 `get_coords.py` 重新标定坐标）。

### 3. 鲁棒的多层 OCR 流水线

- 主路径：EasyOCR + CLAHE 对比度增强
- 降级路径：EasyOCR + OTSU 二值化
- 最终降级：OpenCV 模板匹配（`rank_template/` 中的 2-A 图片模板）
- 预处理：模板匹配移除筹码图标，防止被误读为数字
- 语义修复：`k` 后缀解析（1.5k → 1500）、易混淆字符替换（I→1, O→0, l→1）

### 4. 对手行为语义分析

`GameMemory` 不是简单记录原始数据，而是生成语义描述并分析跨街行为矛盾：

- "翻前激进翻后被动" → 暗示 AK/AQ 未中牌
- "翻前被动翻后激进" → 暗示击中强牌或听牌成形
- 这些洞察直接注入 LLM prompt，辅助读牌判断

### 5. 实时 Web 日志看板

内置 SSE 日志服务器，启动后在浏览器中实时查看彩色日志流（行动=绿色、AI 决策=金色、错误=红色）。支持 pyngrok 隧道远程访问，方便无人值守运行时监控。

## 目录结构

```
texas/
├── main.py                 # 入口：游戏主循环
├── config.json             # 屏幕坐标、颜色、AI 配置
├── strategy.yaml           # 扑克策略规则集（翻前/翻后/战术）
├── get_coords.py           # 可视化坐标标定工具
├── log_server.py           # SSE 日志 Web 服务器
│
├── server/                 # 核心逻辑
│   ├── statemachine.py     # 游戏阶段 + 智能体状态机
│   ├── evaluator.py        # 确定性手牌评估与胜率计算
│   ├── decision.py         # LLM API 调用与决策解析
│   ├── prompt.py           # Prompt 构建 + YAML 规则引擎
│   └── memory.py           # 对手行为记忆与分析
│
├── perception/             # 计算机视觉
│   ├── capture.py          # 截屏、OCR 数字、牌面识别
│   └── detect.py           # 游戏状态检测（回合/结算）
│
├── controller/             # 动作执行
│   └── executor.py         # 鼠标自动化（点击/滑块加注）
│
├── utils/                  # 工具
│   ├── config_manager.py   # JSON 配置加载（DPI 感知）
│   └── logger.py           # 日志配置
│
├── rank_template/          # 牌面 rank 模板图片（2-10, A, J, Q, K）
├── templates/              # 游戏状态检测模板
├── debug/                  # 调试截图（自动生成）
├── logs/                   # 运行时日志
└── icon.png / icon_call.png # 筹码/按钮图标（OCR 预处理用）
```

---

## 快速开始

### 环境依赖

```bash
pip install openai opencv-python easyocr pyautogui pillow numpy pyyaml pyngrok
```

### 配置

1. **标定屏幕坐标**：运行 `python get_coords.py` 在目标扑克客户端上标定按钮位置、牌面区域、像素采样点
2. **配置 AI**：在 `config.json` 中设置 `ai.api_key` 和 `ai.model`
3. **调整策略**（可选）：编辑 `strategy.yaml` 定制翻前/翻后/战术规则

### 运行

```bash
python main.py
```

程序将自动：
- 启动日志 Web 服务器（默认 8080 端口）
- 持续扫描游戏窗口，检测到自己回合时自动感知、决策、执行

### 日志看板

浏览器打开 `http://<本机IP>:8080` 查看实时彩色日志流。若配置了 ngrok token，将自动创建公网隧道。

---

## 设计哲学

- **LLM 做它擅长的（策略推理、对手建模），确定性代码做它擅长的（赔率计算、规则约束）**
- **YAML 即策略 DSL**：修改 `strategy.yaml` 即可调整 AI 打法风格，无需改代码
- **硬约束优先于战术引导**：安全边界不可逾越，确保 AI 不会犯基本扑克错误