# TODOS

## v1 未完成




### 优先完成agent孵化，完善agent数量



### 补全核心模块单元测试（已批准）

**什么：** 为 world_state.py / fc_handlers.py / orchestrator.py 补充单元测试，重点覆盖 search_entities 误命中、handle_create_timer fire_at 计算、MAX_HOPS 循环检测、save_state 状态边界。测试计划见 `~/.gstack/projects/xiaoyi198-AgentEra/xiaoyi-main-eng-review-test-plan-20260410-214002.md`。
**为什么：** 当前覆盖率 27%，核心模块（fc_handlers / orchestrator / world_state）完全无测试。
**来源：** eng review 2026-04-10
**添加时间：** 2026-04-10


### SQLite WAL 模式（已批准）

**什么：** `world_state.py` 和 `timer_system.py` 初始化时执行 `PRAGMA journal_mode=WAL`。
**为什么：** 多个并发 asyncio Task 同时写 SQLite，默认日志模式会产生 `database is locked` 异常。
**来源：** eng review outside voice 2026-04-10
**添加时间：** 2026-04-10






### 将 Agent 关联到实体（list_entities / list_agents 筛选前置）

**什么：** 建立 agent↔entity 的映射关系，使 `list_entities` 的 `entity_type` 筛选和 `list_agents` 的 `location_id` 筛选真正生效。当前两个工具的可选筛选参数已在 schema 中定义，但 handler 忽略筛选参数直接返回全量数据。
**依赖：** `fc_tools.py` 和 `fc_handlers.py` 中的 `list_entities` / `list_agents` 工具已就绪。
**添加时间：** 2026-04-11

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


