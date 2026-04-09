# 设计稿对照清单

对照文件：`xiaoyi-unknown-design-20260407-215001.md.old`

目的：快速审核当前项目还有哪些设计项未完成、哪些已部分落地、哪些已经在实现中变更。

## 已完成

- 皇帝视角前端已完成
- FastAPI 后端骨架已完成
- WebSocket 实时推送已完成
- `types.py` 数据结构已完成
- `timer_system.py` 已完成
- `chain_log.py` 已完成
- `court_graph.py` 已完成
- `orchestrator.py` 主循环已完成
- `llm_client.py` 已完成
- `router.py` 已完成
- `action_agent.py` 已完成，承担了原设计中的世界模拟职责
- `world_state.py` 已完成
- `daming_court` DLC 加载已完成
- 初始事件注入已完成
- 上帝视角页面已完成
- Prompt 在线编辑已完成
- Agent 编辑器已完成
- 存档 / 读档已完成
- 时间倍率调节已完成
- 一键启动脚本已完成
- 基础单元测试已完成
- Token 统计已完成

## 部分完成

- 6 类 Agent 架构
说明：当前已完成官员 Agent、机构 Agent、Router、ActionAgent；随机事件生成器和时间模拟器尚未实现。

- 信息失真机制
说明：`content`、`original_intent`、`is_distorting` 已落地；但 `distortion_note` 的稳定生成与利用不完整。

- Router 自由路由
说明：LLM 驱动路由已落地；但设计稿里提到的更完整广播/自由分发能力还未完全实现。

- LLM 调试日志
说明：已有 `debug_llm.jsonl`，但当前记录的是摘要，不是设计稿里说的完整 input/output。

- 存档机制
说明：已支持暂停后存档和读档；但实现已经超出早期设计稿，实际连 Agent 记忆也一起保存了。

- 世界状态行动模拟
说明：`ActionAgent` 已能查询和更新实体/库存，但更细的财政、物资、灾害扩散规则仍较轻量。

- 多 Agent 并发 + Router 批处理
说明：实现里已有 `asyncio.gather` 和 Router 批处理；但缺少专门的集成级验证。

## 未完成

- 随机事件生成器
- 时间模拟器 `time_sim.py`
- 设计稿中的 `broadcast` Router 工具完整实现
- 前端测试体系（设计稿中的 `Vitest`）
- 集成测试：mock LLM 的端到端链路测试
- `orchestrator` 重点测试
- `router` 批处理重点测试
- 多事件并发场景验证
- 更丰富的事件类型（当前基本只有蝗灾样例）
- 更完整的制度玩法扩展，如早朝机制
- 完整回放/复盘型工具链
- 更细的成本与日志分析能力

## 设计已变更

- 官员 Agent 工具设计
设计稿：只有一个 `submit_action`
现状：已经扩展为 `submit_action + request_action`

- 世界模拟模块命名
设计稿：`world_sim.py`
现状：实际实现为 `action_agent.py`

- 皇帝批复链路
设计稿：更偏向 Router 统一接管
现状：玩家动作主要由 `orchestrator` 确定性处理，再下发给太监等角色

- Agent 记忆
设计稿里一度写“暂时不做”或“不保存”
现状：不仅有内存记忆，还有存档恢复

- 存档内容
设计稿：偏向保存定时器和日志，不保存记忆
现状：会保存 Agent 记忆

- LLM 调试日志深度
设计稿：完整输入输出
现状：摘要日志

## 当前最值得补的事项

- 补一条完整集成测试链路：事件注入 -> 上报 -> 皇帝收折 -> 批复 -> 下行执行
- 实现随机事件生成器
- 决定是否真的需要独立时间模拟器
- 补前端测试和关键交互测试
- 把 LLM 调试日志升级为可选的完整日志模式
- 决定是否补回 `broadcast` 与更自由的 Router 工具能力

## 审核建议

审核时可重点判断这几件事：

- “随机事件生成器”和“时间模拟器”是不是 v1 必需
- `request_action` 是否接受，还是要回归单 `submit_action`
- `ActionAgent` 是否继续作为世界模拟核心，还是要改名/拆分
- 调试日志是否需要上升到完整可回放级别
- 下一阶段优先做“玩法扩展”还是“测试补齐”
