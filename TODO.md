# 待办

## 三模式网络映射流程

- [x] 建立统一 `GameDataProvider` / `GameDataSnapshot` 数据入口
- [x] 将旧 WebSocket 聚合器和 HTTP `field_map` 接入统一 Provider
- [x] 统一 L1 对手画像、L2 读牌、L3 决策的快照编排入口
- [x] 设计统一的站点 Profile 格式，区分 `draft`、`candidate`、`verified` 状态
- [x] 增加三模式入口：`learn`、`verify`、`auto`
- [x] 完善网络事件排序、请求/响应关联、WebSocket 连接与帧序号记录
- [x] 增加原始网络流持久化和回放能力
- [x] 学习模式支持反引号人工牌局边界与 overlap 双角色事件引用
- [x] 学习牌局通过有界队列异步提交，采集与分析解耦
- [x] 实现单个学习 Agent：根据连续网络事件推断字段映射、事件规则和转换规则
- [x] 在原始网络事件与学习 Agent 之间加入可回溯输入压缩层
- [ ] 实现无 LLM 的 ProfileMapper，根据 Profile 重建统一对局状态
- [x] 将当前硬编码聚合器登记为 `legacy_socketio_v1` 固定 Profile 和验证样例
- [x] 实现验证模式：加载固定 Profile，在浏览器浮层展示映射结果和置信度
- [ ] 增加映射结果的字段级、事件级、整局级验证
- [x] 自动模式只允许加载 `builtin/verified` Profile
- [ ] 将已验证 Profile 接入现有决策 Agent 和自动动作执行流程
- [ ] 为学习、验证、自动模式补充回放测试和异常恢复测试
