# TODOS

## v1 未完成


### Agent 编辑器

**什么：** 系统agent编辑未做。另：原上帝视角中间栏的 Prompt 管理（官员模板 / Router / Personas JSON 编辑器）已随 2026-04-10 的中间栏改造（改为系统 Agent 日志栏）被移除，需要迁移到 Agent 编辑器页面承载。
**依赖：** 已有
**添加时间：** 2026-04-09

### 优先完成agent孵化，完善agent数量

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


