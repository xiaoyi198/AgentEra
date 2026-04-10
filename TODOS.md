# TODOS

## v1 未完成


### Agent 编辑器

**什么：** 系统agent编辑未做。另：原上帝视角中间栏的 Prompt 管理（官员模板 / Router / Personas JSON 编辑器）已随 2026-04-10 的中间栏改造（改为系统 Agent 日志栏）被移除，需要迁移到 Agent 编辑器页面承载。
**依赖：** 已有
**添加时间：** 2026-04-09

### 优先完成agent孵化，完善agent数量

### main.py 拆分为 FastAPI Router 模块（已批准）

**什么：** 将 1149 行 55 个 endpoint 的 main.py 拆分为多个 APIRouter 模块（routers/simulation.py / editor.py / worldstate.py / debug.py / saves.py）。
**为什么：** 单文件过大，局部改动难以定位，无法为单独功能写独立测试。
**来源：** eng review 2026-04-10
**添加时间：** 2026-04-10

### 补全核心模块单元测试（已批准）

**什么：** 为 world_state.py / fc_handlers.py / orchestrator.py 补充单元测试，重点覆盖 search_entities 误命中、handle_create_timer fire_at 计算、MAX_HOPS 循环检测、save_state 状态边界。测试计划见 `~/.gstack/projects/xiaoyi198-AgentEra/xiaoyi-main-eng-review-test-plan-20260410-214002.md`。
**为什么：** 当前覆盖率 27%，核心模块（fc_handlers / orchestrator / world_state）完全无测试。
**来源：** eng review 2026-04-10
**添加时间：** 2026-04-10

### 修复消息合并丢失 chain_id（已批准）

**什么：** `orchestrator.py _tick()` 的 `merged_agent_wakeups` 合并同目标消息时只保留首个 chain_id，后续链路 ID 丢失。修复方向：保留各条消息各自的 chain_id；或改为不合并，分别调用 agent。
**为什么：** 上帝视角的信息失真追踪依赖完整链路 ID，合并后调试能力受损。
**来源：** eng review outside voice 2026-04-10
**添加时间：** 2026-04-10

### SQLite WAL 模式（已批准）

**什么：** `world_state.py` 和 `timer_system.py` 初始化时执行 `PRAGMA journal_mode=WAL`。
**为什么：** 多个并发 asyncio Task 同时写 SQLite，默认日志模式会产生 `database is locked` 异常。
**来源：** eng review outside voice 2026-04-10
**添加时间：** 2026-04-10

### chain_log + 调试日志 maxlen 限制（已批准）

**什么：** `orchestrator.router_log / action_log / fired_timers` 和 `chain_log._entries` 改用 `collections.deque(maxlen=N)` 替换普通 list。
**为什么：** 长时间运行高频交互后内存常驻增长，最终 OOM。
**来源：** eng review 2026-04-10
**添加时间：** 2026-04-10

## v1.1+

### WorldState 模糊匹配误命中 description 字段

**什么：** `_resolve_entity_id` 的模糊匹配用 `search_entities`，后者在 id/name/description 三个字段做 LIKE 搜索。由于 description 中可能包含官员姓名（如"知县张三治下"），若 ActionAgent 传入人名作为 entity_id，可能误命中地点实体而非官员本人。
**为什么暂不处理：** 目前尚无"差人/官员作为 WorldState 实体"的功能，该场景不会出现。
**修复方向：** 将 `search_entities` 的 SQL 范围缩窄为只匹配 `id` 和 `name`，不搜 `description`（[world_state.py:156-163](multiagent_framework/world_state.py#L156-L163)）。
**添加时间：** 2026-04-09



### Agent 孵化机制

**什么：** 政府机构 Agent 在某些条件下（如某个员工反复被提及、参与重大事件）自动孵化出独立的官员 Agent。
**为什么：** 让世界更动态，重要人物自然涌现而不是全部预设。机构 Agent 目前模拟除一把手外的所有员工，孵化后个别重要员工获得独立人格和记忆。
**依赖：** v1 政府机构 Agent 跑通后。
**添加时间：** 2026-04-07
**附注（eng review）：** Agent 孵化上线时需一并设计记忆压缩方案（max_memory=20 无摘要，长期游戏中官员会忘记早期事件）。

### Router 批量大小上限（低优先级）

**什么：** `router.py _build_batch_description` 加 batch size 上限（如 20 action/批），超出的放到下次 tick 处理。
**为什么：** Agent 孵化后并发 Agent 数可能大幅增加，批描述过大会超出 Router LLM context window，导致静默丢指令。
**依赖：** Agent 孵化机制上线后再评估是否需要。
**添加时间：** 2026-04-10


