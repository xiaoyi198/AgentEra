# 开发指南 — 大明王朝·朝堂风云

> 项目目录结构、数据库 schema、API 接口、前端组件详见 [PROJECT_DOC.md](PROJECT_DOC.md)。

## 架构核心概念

### 时间系统

- `sim_time` 是**游戏世界时间**，单位为游戏秒，1 游戏天 = 86400 游戏秒
- `seconds_per_day` 是速度倍率：多少真实秒 = 1 游戏天
- 推进公式：`sim_time += real_elapsed * (86400 / seconds_per_day)`
- 定时器延迟固定用游戏时间：`delay_days * 86400`，改速度自然影响触发快慢
- 修改速度只能在暂停时进行

### 7 类 Agent

| 类型 | 实现状态 | 说明 |
|------|----------|------|
| 官员 Agent (OfficialAgent) | ✅ 已实现 | 双 FC: submit_action + request_action，表达意图或触发行动 |
| 机构 Agent (InstitutionAgent) | ✅ 已实现 | 模拟机构除一把手外所有员工，同样支持双 FC |
| 世界运行法则 (RouterAgent) | ✅ 已实现 | LLM 驱动中枢，解析意图→路由→定时器 |
| 行动模拟 Agent (ActionAgent) | ✅ 已实现 | 模拟行动过程和结果，读写世界状态，多轮 FC 循环 |
| 天玑调试 Agent (TianJiAgent) | ✅ 已实现 | 注入全工具集，与用户对话，独立于 Orchestrator 主循环 |
| 随机事件生成器 | ✅ 已实现 | DLC 驱动、确定性调度、热重载、存档恢复 |
| 时间模拟器 | ❌ 未实现 | 科学计算过程耗时 |

### 正在发生的事情（OngoingEvent）

ActionAgent 可以通过 `add_ongoing_event` 创建延迟生效的事件，到期后由 Orchestrator 自动应用：

- **执行者侧**：立即用 `update_inventory` 扣除发出的资源
- **目标侧**：用 `add_ongoing_event` 排队一个延迟事件，N 天后系统自动落账（更新 current_status 和库存变化）
- 事件可叠加（如赈灾进行中又收到拨款）：用 `modify_ongoing_event` 修改 `duration_days` 或 `expected_result`
- 事件可取消：用 `delete_ongoing_event`

```python
@dataclass
class OngoingEvent:
    event_id: str           # 唯一 ID
    entity_id: str          # 归属实体
    event_type: str         # "state_change" | "inventory_arrival"
    description: str        # 事件简述
    start_time: float       # 开始时间 (sim_time)
    end_time: float         # 到期时间 (sim_time)
    expected_result: dict   # 到期时应用的变化 {"new_status": ..., "inventory_deltas": [...]}
    source_action_id: str   # 来源 chain_id
```

### 资源目录、实体数值与价值锚

内置资源定义由 `resource_catalog` 注册，`kind` 是权威分类：

| 名称 | 单位 | 默认值 | 范围 | kind | 存储 | 价值锚 |
|------|------|--------|------|------|------|--------|
| 金钱 | 两 | 0 | 0 ~ ∞ | asset | inventory | 1.0 |
| 粮草 | 石 | 0 | 0 ~ ∞ | asset | inventory | 1.0 |
| 工料 | 工日 | 0 | 0 ~ ∞ | asset | inventory | 1.0 |
| 兵力 | 人 | 0 | 0 ~ ∞ | asset | inventory | 1.0 |
| 科技点 | 点 | 0 | 0 ~ 200 | metric | entity_values | — |
| 满意度 | — | 50 | -1 ~ 100 | metric | entity_values | — |
| 人口 | 人 | 0 | -1 ~ ∞ | metric | entity_values | — |
| 生活质量 | — | 1.0 | 0.2 ~ 1.5 | parameter | entity_values | — |
| 税率 | % | 0.08 | 0 ~ 0.5 | parameter | entity_values | — |
| 生产系数 | — | 1.0 | 0.5 ~ 2.5 | parameter | entity_values | — |

- `set_inventory` / `update_inventory` 是兼容名称，会根据目录自动写入 inventory 或 entity_values。
- 资产可支出并写 `inventory_ledger`；统一价值为 `quantity × anchor_rate`。
- metric 与 parameter 不可作为库存消费或转移，按 min/max 约束。
- 机构类型实体的不适用值为 `-1`，不可修改。
- DLC 可使用 `upsert_resource_definition()` 注册额外资源，不得修改框架硬编码白名单。

### 实体关系系统

WorldState 支持实体之间的有向关系边（`entity_relations` 表）：

- `add_relation(parent_id, child_id, rel_type, note)` — 创建关系
- `remove_relation(parent_id, child_id, rel_type)` — 删除关系
- `get_parents(child_id)` — 查询父节点
- `get_children(parent_id)` — 查询子节点
- `get_siblings(entity_id)` — 查询兄弟节点
- `get_all_relations()` — 导出所有关系
- 关系类型限定为 `RELATION_TYPES = {"subordinate", "affiliated"}`

### 两套 FC 工具集

> 所有工具定义集中在 `fc_tools.py`，处理逻辑集中在 `fc_handlers.py`。
> 添加新工具只需在这两个文件各加一处，Agent 文件无需改动。

**Agent 工具**（官员/机构 Agent 使用）：

| 工具 | 作用 |
|------|------|
| `submit_action(action, content, original_intent, is_distorting, urgency, metadata?)` | 表达意图：do_nothing / report_message / do_something；`metadata` 为可选字典 |
| `request_action(action_type, target_location, description, resources?)` | 触发实际行动（调查/赈灾/抓捕等），绕过 Router 直接给 ActionAgent |

**Router 工具**（世界运行法则使用）：

| 工具 | 作用 |
|------|------|
| `list_agents(location_id?)` | 列出所有可唤醒 Agent 的 ID 和基本信息，确认合法目标 |
| `wake_agent(target, message, from_agent, urgency?)` | 向目标 Agent 投递消息；延迟由代码按双方坐标与 urgency（urgent/express/normal，速度倍率 1.8/1.5/1）自动计算，同城立即送达 |
| `execute_policy(policy, location, executor?, from_agent?, description?)` | 调用 ActionAgent 执行政策效果；可选 `from_agent` 和 `description` |
| `relay_decree(decree, targets, urgency?)` | 传递皇帝圣旨给指定官员；`urgency` 为可选传递系数 |

**ActionAgent 工具**（行动模拟使用）：

| 工具 | 作用 |
|------|------|
| `list_entities(entity_type?)` | 列出所有实体的 ID、类型和名称，确认合法实体 ID；`entity_type` 可选筛选（location/institution/organization） |
| `query_entity(entity_id)` | 查询实体的完整状态：基本情况 + 当前状况 + 库存 + 正在发生的事情 |
| `update_entity_status(entity_id, new_status)` | 更新实体状况 |
| `update_inventory(entity_id, resource_name, quantity_delta, reason?)` | 增减资源代币；`resource_name` 为 10 种标准代币之一 |
| `add_ongoing_event(entity_id, description, duration_days, expected_result, event_type?)` | 在目标实体上排队延迟事件，到期自动落账 |
| `modify_ongoing_event(event_id, description?, duration_days?, expected_result?)` | 修改已存在的进行中事件（叠加/缩短/增强） |
| `delete_ongoing_event(event_id)` | 取消进行中事件 |
| `list_agents(location_id?)` | 列出所有可唤醒 Agent |
| `complete_action(result_description, duration_days, success?)` | 结束行动，返回结果 |

**工具分组**：
- `ACTION_AGENT_TOOLS` — 9 个（上述 ActionAgent 工具全部）
- `ROUTER_TOOLS` — 4 个（list_agents / wake_agent / execute_policy / relay_decree）
- `AGENT_TOOLS` — 2 个（submit_action / request_action）
- `ALL_TOOLS` — 14 个（天玑用，全量工具）

### 信息流

```
事件注入 → 知县 Agent (submit_action / request_action)
         │
         ├─ submit_action 路径：
         │   → Router (解析意图, 查层级, 算延迟)
         │   → Timer (定时器等待)
         │   → 知府 Agent (submit_action)
         │   → Router → Timer → ... → 皇帝收到奏折
         │
         └─ request_action 路径：
             → 绕过 Router，直接给 ActionAgent
             → ActionAgent 模拟行动（查询实体/更新状态/排队事件/库存增减）
             → 结果通过 Timer 送回发起者

皇帝批复 → 太监 Agent (传达旨意)
         → Router → Timer → 逐级传达 → 知县执行
```

### 信息失真

- Agent 在 submit_action 中声明 `is_distorting: bool`
- `original_intent`（真实想法）vs `content`（实际传递）由 Agent LLM 自主产生
- chain_log 记录两个版本，上帝视角可对比

### 世界状态 vs 层级图

| 组件 | 管什么 | 变化频率 |
|------|--------|----------|
| CourtGraph | "这个官是谁、在哪、听谁的"（结构层） | 启动时加载，运行中不变 |
| WorldState | "这个地方/机构发生了什么、有多少粮、有什么事件在排队"（动态层） | ActionAgent 和系统持续更新 |
| 两者通过相同 ID 关联（如 `location_id="吴县"` 在两边都有） | | |
| 坐标信息 | 存储在 WorldState 的 Entity metadata 中（x, y, region） | 可通过 API 编辑 |

## 关键文件说明

### multiagent_framework/types.py

所有数据结构定义。修改字段时注意同步：

- `TimerEntry` — timer_system.py 的序列化/反序列化；完整字段：timer_id, fire_at, target_agent, payload, chain_id, parent_timer_id, created_at, cancelled
- `ChainLogEntry` — chain_log.py 的序列化/反序列化；完整字段：chain_id, entry_id, event_id, sim_time, caller, caller_agent_type, fc_called, original_text, transmitted_text, is_distorted, distortion_note, hop_count, routed_to, timer_id, trigger_at, wakeup_type, status
- `AgentAction` — agent_runtime.py 的 FC 解析；完整字段：agent_id, action, content, original_intent, is_distorting, urgency, metadata
- `RouterCommand` — router.py 的输出 + orchestrator.py 的执行；完整字段：command_type, target, targets, payload, delay_seconds, chain_id, source_action
- `ActionResult` — action_agent.py 的输出；完整字段：action_type, executor_id, target_location, result_description, duration_days, changes_made, success, error, fc_trace
- `EventObject` — daming_court/loader.py 的事件加载；完整字段：event_id, event_type, severity, target_agents, initial_payload, generated_at
- `PlayerAction` — 皇帝操作；完整字段：action_type, memorial_id, content, note
- `TickResult` — 前端 WebSocket 接收的数据格式；有 `to_dict()` 方法
- `OngoingEvent` — 延迟生效事件；完整字段：event_id, entity_id, event_type, description, start_time, end_time, expected_result, source_action_id

**关键枚举**：
- `RouterCommandType`: WAKE_AGENT, EXECUTE_POLICY, RELAY_DECREE, BROADCAST, NO_OP
- `PlayerActionType`: APPROVE, REJECT, HOLD, ISSUE_DECREE

> **注意**：`BROADCAST` 和 `NO_OP` 目前没有对应的 FC handler，如果 Router 返回这两种类型，`dispatch_fc` 会返回 `{"ok": False, "error": "未知 FC"}`。

### multiagent_framework/fc_tools.py

FC 工具定义的中央注册表：

- 每个工具以具名常量定义（`WAKE_AGENT_TOOL`, `LIST_ENTITIES_TOOL` 等），共 14 个
- 预组合工具集：
  - `ACTION_AGENT_TOOLS` — `list_entities / query_entity / update_entity_status / update_inventory / add_ongoing_event / modify_ongoing_event / delete_ongoing_event / list_agents / complete_action`（9 个）
  - `ROUTER_TOOLS` — `list_agents / wake_agent / execute_policy / relay_decree`
  - `AGENT_TOOLS` — `submit_action / request_action`
  - `ALL_TOOLS` — 天玑用，全量 14 个工具
- **添加新工具**：在此文件定义工具 schema，加入对应分组；Agent 代码无需改动

> `query_inventory` 已合并进 `query_entity`（一次性返回实体状态 + 库存 + 正在发生的事情）。

### multiagent_framework/fc_handlers.py

FC 处理函数的中央注册表：

- `FCContext` — 执行上下文数据类，打包所有依赖：
  - `world_state`, `court`, `timers`, `chain_log`, `action_agent`
  - `pending_wakeups`, `run_action`, `agent_tasks` — Orchestrator 共享引用
  - `sim_time`, `seconds_per_day`（传入速度倍率值）, `caller_id`, `chain_id`, `source_action`
  - `changes_made` — ActionAgent 收集世界状态变更
- **每个 FC 工具一个 handler**，全部注册在 `FC_HANDLERS: dict[str, (handler, is_async)]`：
  - 世界状态组：`handle_list_entities / handle_query_entity / handle_update_entity_status / handle_update_inventory`
  - 正在发生的事情组：`handle_add_ongoing_event / handle_modify_ongoing_event / handle_delete_ongoing_event`
  - `handle_complete_action` — 纯解析，返回结构化结果，调用方决定收尾
  - Router 组（async）：`handle_wake_agent / handle_execute_policy / handle_relay_decree`
  - Agent 组：`handle_list_agents`
- `dispatch_fc(name, ctx, args)` — 统一分发入口，async-aware
- `command_type_to_fc_name(cmd_type)` — RouterCommandType 枚举 → FC 工具名
- `command_to_fc_args(cmd)` — RouterCommand → FC 参数 dict（Orchestrator 桥接用）
- `parse_submit_action / parse_request_action` — FC args → AgentAction
- `parse_agent_fc_response(response, agent_id)` — LLM 响应 → `list[AgentAction]`（支持双 FC 同时返回 submit_action + request_action）
- `_resolve_entity_id(world_state, entity_id)` — 模糊实体 ID 匹配（精确匹配 → search_entities 模糊搜索 id/name/description）

### multiagent_framework/orchestrator.py

主循环关键逻辑：
- 属性：`sim_time`（游戏时间）、`sim_day`（游戏天数）、`is_paused`、`is_running` — 运行状态查询
- `memorials` — 当前奏折列表 `list[dict]`
- `persistor` — `RuntimePersistor | None`，后台定期持久化状态
- `router_log` — `deque(maxlen=2000)`，Router 决策日志
- `action_log` — `deque(maxlen=2000)`，ActionAgent FC 执行追踪
- `fired_timers` — `deque(maxlen=2000)`，已触发的定时器记录
- `__init__(court_graph, timer_system, chain_log, router, seconds_per_day, on_tick, action_agent, memory_compressor)` — 构造参数
- `run(tick_interval=0.25)` — tick 循环，用 `time.monotonic()` 推进游戏时间；启动 persistor 后台任务
- `_tick()` — claim 到期 Timer → 发布持久 Wakeup Outbox → 弹出到期 OngoingEvent → claim/派发唤醒（玩家 Issue vs Agent 调用）→ 处理玩家操作
  - 同一 Agent 的多个唤醒消息会自动合并（拼接为一条 payload）
- `_apply_matured_event(ev)` — 应用到期 OngoingEvent 的 expected_result（更新实体状态 + 库存变化）
- `_process_agents_background()` — **后台 asyncio.Task**，不阻塞时间流动
  - 并行调用所有被唤醒的 Agent
  - Router 批量处理
  - 执行 Router 指令
- `_call_agent_safe()` — 调用单个 Agent，记录 chain log，异常安全
  - 检测 `request_action` → 绕过 Router，直接分发给 ActionAgent
  - 检测 hop 超限 → 标记 loop_detected
- `_run_action()` — 运行 ActionAgent 模拟行动，完成后创建 Timer 把结果送回发起者；`request_action` 在后台 wakeup 任务内等待该分发完成后才 ACK
- `_build_fc_context()` — 组装 `FCContext`，注入 sim_time、pending_wakeups、_run_action 绑定等
- `_execute_commands(commands)` — 遍历 RouterCommand 列表，调用 `dispatch_fc` 统一执行；不再有 if/elif 分支
- `_handle_player_action(action, result)` — 确定性逻辑，皇帝→太监→逐级传达（第二个参数 `result: TickResult`）
- `_fuzzy_find_agent()` — Router 返回模糊 ID 时的容错匹配（ID包含关键词 → 地点匹配 → 名字匹配）
- `inject_event()` — 注入事件，为每个 target_agent 创建唤醒
- `save_state()` — 返回 `dict` 包含 `sim_time, seconds_per_day, timer_count, agent_memories`；数据持久化由 RuntimePersistor 负责
- `load_state(state)` — 恢复 `sim_time, seconds_per_day`；调用 `timers.load_from_db()`, `chain_log.load_from_file()`, `router.update_time_scale()`
- `register_agent(agent)` / `get_agent(agent_id)` — Agent 注册与查询
- `inject_player_action(action)` — async，注入玩家操作到队列
- `set_time_scale(seconds_per_day)` — 设置时间倍率（需暂停）
- `pause()` / `resume()` / `stop()` — 模拟控制
- `drain_agent_tasks(timeout)` — async，等待所有后台 Agent 任务完成；完成后调用 `await self.persistor.flush()`
- `world_state` — 属性，委托到 `action_agent.world_state`

### multiagent_framework/router.py

Router 是 LLM 驱动的，不是规则引擎：
- `process_batch(actions, context=None)` — 批量处理多个 AgentAction，返回 RouterCommand 列表；`context` 为可选额外上下文字典
- `_build_batch_description(actions, context)` — 构建给 LLM 的描述，包含：
  - 可用官员 ID 列表
  - 每个行动的官员信息（品级、驻地、上级、直属下级、距离约 X.X 天）
  - 距离由 `court.get_distance(from_id, superior_id)` 动态计算
  - 行动内容和紧急程度
- `_parse_commands()` — 解析 LLM 的 function call 响应为 RouterCommand 列表
- `_fc_to_command()` — 将单个 FC 调用转换为 RouterCommand
- `update_time_scale(seconds_per_day)` — 更新时间倍率（影响距离计算中的时间缩放）
- `_default_prompt()` — 默认 Router system prompt
- `last_response_text` — 记录 LLM 原始响应供调试
- `MAX_HOPS = 10` — 最大跳数，防止无限循环

### multiagent_framework/agent_runtime.py

Agent 运行时：
- `AgentConfig` — Agent 配置数据类：
  - `agent_id`, `agent_type`, `display_name` — Agent 标识
  - `system_prompt`, `tools`, `personality_seed` — 运行参数
  - `max_memory` — 记忆 deque 最大长度（默认 20）
  - `compress_threshold` — 记忆压缩阈值（默认 40 条）
  - `keep_recent` — 压缩时保留最近 N 条（默认 20）
  - **注意**：`llm_client` 不是 AgentConfig 字段，而是 `BaseAgent.__init__()` 的单独参数
- `BaseAgent` — 抽象基类，管理 memory（deque）、system prompt、LLM 调用
  - `serialize_memory()` — 将记忆序列化为可持久化的 v2 dict（`{version: 2, summary, messages}`），兼容 v1 list 格式反序列化
  - `deserialize_memory(data)` — 从 v2 dict 或 v1 list 恢复 deque（读档用）
  - `add_memory(role, content)` — 添加一条记忆
  - `get_messages()` — 构建消息列表（含摘要压缩后拼接）
  - `set_compressor(compressor)` — 注入记忆压缩器
  - `_maybe_compress()` — 自动压缩过长记忆（超过阈值时触发 LLM 摘要）
  - `_record_response_to_memory()` — 格式化 LLM 响应并写入记忆
  - `_summary` — 内部摘要缓存
  - `_compress_failures` — 压缩失败计数器，超过 3 次退化为截断
- `OfficialAgent.process()` — 官员 Agent 主入口，返回 `list[AgentAction]`（支持同时 submit_action + request_action）
- `InstitutionAgent.process()` — 机构 Agent，逻辑类似，同样返回 `list[AgentAction]`

### multiagent_framework/action_agent.py

行动模拟 Agent（独立于 BaseAgent 类层次）：
- `agent_id` 属性 — 固定返回 `"action_agent"`
- `agent_type` 属性 — 固定返回 `"world_sim"`
- `process(payload)` — 模拟一次行动：
  - 构建动态 system prompt（注入世界状态上下文）
  - 多轮 FC 循环（默认 max_rounds=7）：
    - 组装 `FCContext(world_state=..., changes_made=..., caller_id=...)` 后调用 `dispatch_fc`
    - 直到 `complete_action` 或达到最大轮次
  - 返回 ActionResult
- `_build_system_prompt()` — 加载 prompt 模板，填充字段并注入世界上下文
- `_load_template()` — 加载 action_agent.txt prompt 模板

### multiagent_framework/tianji_agent.py

天玑调试 Agent（独立于 Orchestrator）：
- `TianJiAgent` — 与用户对话，注入全部工具集（ALL_TOOLS，14 个）
- 构造参数：`llm_client / world_state / court_graph / timer_system / chain_log / action_agent / router / orchestrator（可选）/ system_prompt_path`
- `attach_orchestrator(orb)` — 反向注入 Orchestrator，共享 `sim_time / pending_wakeups / _agent_tasks`；使天玑 `wake_agent / relay_decree` 等产生与主模拟一致的真实副作用
- `reset()` — 清空 messages，重新注入 system prompt
- `chat(user_text)` — 主入口，async generator，yield SSE 事件流：
  - `{"type": "assistant_delta", "text": "..."}` — LLM 文本
  - `{"type": "tool_call", "name": "...", "arguments": {...}, "call_id": "..."}` — 工具调用
  - `{"type": "tool_result", "name": "...", "result": {...}, "call_id": "..."}` — 工具结果
  - `{"type": "tool_round_start", "round": N}` — 工具轮开始
  - `{"type": "error", "text": "..."}` — LLM 调用错误
  - `{"type": "done"}` — 本轮结束
- `_build_ctx()` — 组装 `FCContext`，当挂载了 Orchestrator 时共享其运行时引用
- `_dispatch_tool(name, args)` — 统一分发：`submit_action` → Router；`request_action` → `execute_policy` 路径；其余 → `dispatch_fc`
- `_summarize_result(result)` — 模块级辅助函数，用于 chain_log 展示
- `_CHAIN_PREFIX = "tianji_"` — 天玑 chain_log 前缀常量
- `_load_system_prompt()` — 加载 system prompt，缺失时使用 `_DEFAULT_SYSTEM_PROMPT`
- `session_id` — 会话 ID 属性
- `get_history()` — 返回当前 messages 列表
- 所有工具调用以 `tianji_` 前缀写入 chain_log，上帝视角可见且不污染主模拟

### multiagent_framework/world_state.py

世界状态数据库（与 CourtGraph 并列）：
- `Entity` — 世界中的实体（id/type/name/description/current_status/updated_at/metadata）
- `InventoryItem` — 实体的库存（entity_id/category/item_name/quantity/description）
- `OngoingEvent` — 正在进行的事件（event_id/entity_id/event_type/description/start_time/end_time/expected_result/source_action_id）
- 资源代币常量：`RESOURCE_TOKENS`（10 种）、`RESOURCE_NAMES`、`RESOURCE_META`
- 关系类型常量：`RELATION_TYPES = {"subordinate", "affiliated"}`
- `WorldState` 类：
  - `get_entity()`, `upsert_entity()`, `update_status()` — 实体 CRUD
  - `delete_entity()` — 删除实体（级联删除库存、关系、正在发生的事件）
  - `get_entities_by_type(entity_type)` — 按类型筛选实体
  - `get_all_entities()` — 获取所有实体
  - `search_entities(keyword)` — 模糊搜索实体（id/name/description LIKE 搜索）
  - `get_coordinates(entity_id)` — 从实体 metadata 提取 (x, y, region)
  - `get_inventory()`, `set_inventory()`, `update_inventory()` — 库存 CRUD（仅允许标准资源代币）
  - `delete_inventory_item()` — 删除单条库存记录（标准资源代币不可删除）
  - `init_default_inventory(entity_id, entity_type)` — 补齐 7 个标准资源代币
  - `_clamp_resource(name, value)` — 按 RESOURCE_META 的 min/max 裁剪数值
  - `add_relation()`, `remove_relation()` — 关系 CRUD
  - `get_parents()`, `get_children()`, `get_siblings()` — 关系查询
  - `get_all_relations()` — 导出所有关系
  - `add_ongoing_event()`, `get_ongoing_event()`, `list_ongoing_events()` — 正在进行的事件 CRUD
  - `update_ongoing_event()` — 部分更新事件字段
  - `delete_ongoing_event()` — 删除事件
  - `pop_matured_events(sim_time)` — 弹出到期事件
  - `get_context_for_action(location_id, executor_id, sim_time, seconds_per_day)` — 为 ActionAgent 构建上下文（含正在发生的事件）
  - `export_all()` — 导出全部数据（实体 + 库存 + 关系 + 事件）
  - `bulk_load(entities, inventory, relations, ongoing_events)` — 批量导入，返回 (实体数, 库存数, 关系数)
  - `restore_all(data)` — 从导出数据完整恢复

### multiagent_framework/court_graph.py

层级图：
- `Official` — 官员数据（id/name/rank/location_id/role/agent_type/superior_id/personality_seed/enabled/extra_prompt）
- **注意**：`Location` 数据类已移除，坐标信息存储在 WorldState 的 Entity metadata 中
- `TravelCoeff` — 速度倍率（越大越快）：URGENT=1.8（急递），EXPRESS=1.5（快马），NORMAL=1.0（普通公文）
- `TRAVEL_SPEED_LI_PER_DAY` = 300（基准速度）
- `MIN_TRAVEL_DAYS` = 0.1（同城传递最短耗时）
- `CourtGraph` 类：
  - `get_official()`, `get_superior()`, `get_subordinates()` — 层级查询
  - `get_chain_to_emperor()` — 完整上报链
  - `get_distance(from_id, to_id, speed_coeff)` — 通信耗时（天），通过 WorldState.get_coordinates() 获取坐标；无坐标时回退 5.0/speed_coeff 天
  - `get_officials_at_location()` — 按地点查询
  - `get_all_officials()` — 获取所有官员
  - `get_officials_by_rank(rank)` — 按品级查询
  - `set_world_state(world_state)` — 注入 WorldState 引用（用于坐标查询）
  - `add_official(official)` — 添加官员
  - `bulk_load_officials(officials)` — 批量加载官员
- **已移除的方法**：`get_all_locations()`, `get_location()`, `add_location()`, `distance_li()` — 这些功能已迁移到 WorldState
- 旧数据库自动迁移：检测缺失列并 `ALTER TABLE ADD COLUMN`（`enabled` 和 `extra_prompt`）

### multiagent_framework/timer_system.py

定时器系统：
- `TimerEntry` — 定时器条目（heapq 按 fire_at 排序）；完整字段：timer_id, fire_at, target_agent, payload, chain_id, parent_timer_id, created_at, cancelled
- `schedule()`, `cancel()`, `pop_due()` — 核心操作
- `get_in_transit()` — 获取在途公文
- `peek_next_time()` — 查看下一个定时器的触发时间
- `count()` — 活跃定时器数量
- `save_all()`, `load_from_db()` — SQLite 持久化
- `export_all()`, `import_all(timers)` — 导出/导入（存档用）

### multiagent_framework/chain_log.py

调用链日志：
- 内存条目上限 `_ENTRIES_MAXLEN = 5000`
- `append(entry)` — 追加日志（内存 + JSONL 文件）
- `get_chain(chain_id)` — 按链路查询
- `get_distortions(chain_id=None)` — 查询失真记录（可选按 chain_id 过滤）
- `get_withheld()` — 查询被压下的消息
- `get_event_chains(event_id)` — 按事件 ID 查询相关链路
- `get_max_hop(chain_id)` — 获取某条链的最大跳数（用于循环检测）
- `all_entries()` — 获取所有条目（调试 API 用）
- `to_god_view(chain_id)` — 上帝视角对比
- `clear()` — 清空所有日志
- `load_from_file()` — 从 JSONL 文件恢复

### multiagent_framework/llm_client.py

LLM 客户端：
- `LLMConfig` — 配置（base_url/api_key/model/timeout_s/max_concurrent/retry_delay_s）
：
  - `LLMConfig.from_file(path)` — 从 JSON 文件加载配置
  - `LLMConfig.save_to_file(path)` — 保存配置到文件
  - `LLMConfig.to_display_dict()` — 返回脱敏字典（api_key 打码显示）
- `LLMResponse` — LLM 响应数据类（content, tool_calls, input_tokens, output_tokens, latency_ms, raw）
- `LLMClient.chat()` — 异步调用（非流式），自动重试，并发控制；每次失败到下一次重试之间按 `retry_delay_s` 固定等待（默认 5 秒，设为 0 关闭等待）
- `LLMClient.chat_stream()` — 流式调用，yield delta/tool_call_delta/done 事件；出错时退化为非流式
- OpenAI 兼容响应可返回 `tool_calls: null`，解析层必须将其视为无工具调用；不要直接遍历可空字段。缺失 ID 与 `arguments: null` 也应在协议边界归一化。
- HTTP 错误必须通过脱敏诊断保留 Provider 的 `message/type/code/param` 以及请求结构摘要，但不得记录 Prompt、消息正文、工具结果正文或 API Key。
- `LLMClient.close()` — 关闭 HTTP 客户端
- `LLMClient.config` — 属性，当前配置
- `TokenCounter` — 累计 token 统计
- `async update_config(new_config)` — 热更新配置（**注意：该方法是 async**）

### multiagent_framework/memory_compressor.py

LLM 记忆压缩器：
- `MemoryCompressor` — Protocol 协议类，定义 `compress(messages, existing_summary, agent_context)` 接口
- `LLMMemoryCompressor` — 基于协议的默认实现：
  - `compress(messages, existing_summary, agent_context)` — 压缩记忆：合并已有摘要 + 旧消息生成新摘要
  - `existing_summary` — 上一次压缩产生的摘要字典（需合并），或 None
  - `agent_context` — Agent 身份描述字符串（如"况钟，苏州知府"）
  - 返回 `dict`（摘要消息，含 `_meta` 标记）
  - 内部有 `_compress_failures` 回退机制
- 辅助函数：
  - `is_summary_message(msg)` — 检测消息是否为压缩摘要
  - `find_safe_split_point(messages, target)` — 找到安全分割点（不切断 tool_calls 组）
  - `format_messages_for_compression(messages)` — 将消息列表格式化为可读文本
- 常量：`SUMMARY_META_KEY = "_meta"`, `SUMMARY_TYPE_VALUE = "memory_summary"`
- 阈值由 `AgentConfig.compress_threshold` 控制（默认 40 条）
- 压缩后的记忆格式（v2）：`{version: 2, summary: "...", messages: [...]}`
- Orchestrator 初始化时创建并注入到各 Agent

### multiagent_framework/runtime_paths.py

运行时路径常量：
- `RUNTIME_DIR` — `runtime/` 目录
- `WORLD_DB` — `runtime/world.db`（SQLite 数据库）
- `CHAIN_LOG` — `runtime/chain_log.jsonl`
- `STATE_FILE` — `runtime/state.json`（sim_time + seconds_per_day + memorials）
- `AGENTS_DIR` — `runtime/agents/`（各 Agent 记忆 JSON）
- `ensure_runtime_dir()` — 确保 runtime/ 及子目录存在
- `runtime_exists()` — 判断 runtime 是否已初始化

### multiagent_framework/runtime_persistor.py

运行时状态持久化：
- `RuntimePersistor` — 负责 Orchestrator 的 in-memory 状态落盘到 `runtime/` 目录
  - `mark_agent_dirty(agent_id)` — 标记 Agent 记忆需要落盘
  - `mark_state_dirty()` — 标记 state.json 需要落盘
  - `async flush()` — 批量序列化并原子写入（state.json + agents/*.json）
  - `async run_flush_loop(interval=2.0)` — 后台定时 flush
  - `start()` / `stop()` — 启停 flush loop
- 设计要点：mark_*_dirty 只标记，不做 IO；flush() 批量序列化并原子写入

### daming_court/loader.py

DLC 数据加载：
- `load_officials()` — JSON → Official 列表
- `load_personas()` — 加载人格种子配置
- `load_world_state()` — JSON → 实体 + 库存 + 关系，批量导入 WorldState；返回 `(entity_count, inventory_count, relation_count)`
- `load_events()` — JSON → EventObject 列表
- `create_agents()` — 为每个官员创建 Agent 实例
- `_build_system_prompt()` — Prompt 优先级：专用文件 > 模板 > 默认
- **注意**：`load_locations()` 已移除，坐标信息现在通过 `load_world_state()` 从 world_state.json 的实体 metadata 加载

### main.py

FastAPI 后端入口：
- `init_orchestrator(load_from_save=False, force_fresh=False)` — 从配置文件初始化 Orchestrator + TianJiAgent
  - 创建 `LLMMemoryCompressor` 并注入各 Agent
  - CourtGraph、TimerSystem、WorldState 共用同一个 `runtime/world.db` SQLite 文件
  - 创建 `RuntimePersistor` 并注入 Orchestrator
  - `force_fresh=True`：清空 runtime/ 目录并重新初始化
- `_load_dlcs(orch, court, llm, world_state, registry, load_from_save=False)` — 按依赖顺序加载所有启用且已发布 DLC；无可用 manifest 时调用 legacy fallback
  - `load_from_save=False`：完整加载（世界状态 JSON + 官员/Agent + 初始事件注入）
  - `load_from_save=True`：读档模式，从 RuntimePersistor 恢复状态
  - 不再调用 `load_locations()`，坐标数据已在 world_state.json 实体 metadata 中
- `broadcast_tick()` — WebSocket 广播 tick 结果
- 新增 API 端点：`GET /api/memorials`（返回持久化的奏折列表）、`POST /api/new-game`（清空 runtime 并重新初始化）
- REST API 见 [PROJECT_DOC.md](PROJECT_DOC.md) 第五节
- WebSocket `/ws` — 实时 tick 推送 + 接收玩家操作

> 前端组件（App.jsx、AppNav.jsx、GodView.jsx、TianJiView.jsx、AgentEditor.jsx、WorldStateView.jsx、RelationsView.jsx）的详细说明见 [PROJECT_DOC.md](PROJECT_DOC.md) 第八节。

## 数据库结构

> SQLite 表结构和字段说明详见 [PROJECT_DOC.md](PROJECT_DOC.md) 第六节。

## 如何添加新官员

1. 编辑 `daming_court/config/officials.json`，添加条目：
```json
{
  "id": "松江知府_陈五",
  "name": "陈五",
  "rank": 5,
  "location_id": "松江",
  "role": "official",
  "agent_type": "official",
  "superior_id": "江南布政使_周忱",
  "personality_seed": "忠直",
  "superior_distance": 2,
  "enabled": true,
  "extra_prompt": "你特别关注水利问题"
}
```

字段说明：
- `id` — 唯一标识，建议格式：`职位_姓名`
- `rank` — 品级（1=知县, 5=知府, 8=布政使, 10=内阁, 12=六部, 15=皇帝）
- `role` — `official`（官员）/ `eunuch`（太监）/ `player`（皇帝）
- `agent_type` — `official`（个人）/ `institution`（机构，模拟多人）
- `superior_id` — 直属上级 ID（null 表示最高级）
- `personality_seed` — 人格种子（对应 personas.json 的 key）
- `enabled` — 是否参与模拟（false 则不创建 Agent）
- `extra_prompt` — 附加个性化提示词（有则标记为"活跃"）

2. 如果是新地点，还需在 `daming_court/config/world_state.json` 的 `entities` 数组添加：
```json
{
  "id": "松江",
  "type": "location",
  "name": "松江府",
  "description": "松江府，江南重镇",
  "current_status": "太平",
  "metadata": {"x": 350, "y": -1600, "region": "江南"}
}
```

> 坐标信息现在存储在实体的 `metadata` 字段中（x, y, region），不再使用单独的 `locations` 数组。

3. 如需添加实体关系，在 `world_state.json` 的 `relations` 数组添加：
```json
{
  "parent_id": "江南布政使司",
  "child_id": "松江",
  "rel_type": "subordinate",
  "note": "松江属江南布政使司"
}
```

4. （可选）如需自定义 prompt，创建 `daming_court/prompts/松江知府_陈五.txt`

5. 重启后端，自动加载

## 如何添加新事件

编辑 `daming_court/config/events.json`：
```json
{
  "initial_events": [
    {
      "event_id": "evt_002",
      "event_type": "drought",
      "severity": 6,
      "target_agents": ["松江知府_陈五"],
      "initial_payload": {
        "area": "松江府",
        "description": "松江府大旱，农田绝收。"
      }
    }
  ]
}
```

## 如何添加世界状态实体

编辑 `daming_court/config/world_state.json`：
```json
{
  "entities": [
    {
      "id": "吴县",
      "type": "location",
      "name": "吴县",
      "description": "苏州府下辖县，鱼米之乡",
      "current_status": "蝗灾肆虐，庄稼受损",
      "metadata": {"x": 448, "y": -1732, "region": "江南"}
    }
  ],
  "inventory": [
    {
      "entity_id": "吴县",
      "category": "粮草",
      "item_name": "粮草",
      "quantity": 5000,
      "description": "石"
    }
  ],
  "relations": [
    {
      "parent_id": "苏州府",
      "child_id": "吴县",
      "rel_type": "subordinate",
      "note": ""
    }
  ]
}
```

> 注意：库存现在使用标准资源代币（`category` 和 `item_name` 相同，如 "粮草"/"粮草"）。实体坐标存储在 `metadata` 字段中。

## 如何调整 Prompt

三种方式（不需要改代码）：

1. **前端上帝视角** — 时间线、Router 日志、ActionAgent 执行日志、定时器状态（Prompt 编辑器已移至独立管理页面）
2. **Agent 编辑器** — 可视化编辑人格种子，预览 prompt 渲染结果，管理系统 Agent 配置
3. **文件编辑** — 修改 `daming_court/prompts/*.txt` 或 `personas.json`
4. **专用 Prompt** — 创建 `daming_court/prompts/{官员ID}.txt`，优先级最高

优先级：专用文件 > 模板 > 默认

## 如何运行测试

```bash
python -m pytest tests/unit/ -v
```

覆盖：TimerSystem（内存+持久化）、ChainLog（追加+查询+持久化）、CourtGraph（层级查询+距离计算）

## API 一览

> 完整 API 接口列表详见 [PROJECT_DOC.md](PROJECT_DOC.md) 第五节。

## 核心流程详解

### Agent 调用流程

```
Orchestrator._tick()
  │
  ├─ 检查到期定时器 → 加入 _pending_wakeups
  │
  ├─ 弹出到期的 OngoingEvent → _apply_matured_event()
  │   └─ 更新实体 current_status + 增减库存
  │
  ├─ 遍历 _pending_wakeups：
  │   ├─ 目标是皇帝 (role=player) → 添加到 TickResult.new_memorials
  │   ├─ 模糊匹配失败 → 记录 agent_errors
  │   └─ 匹配成功 → 收集 agent_wakeups
  │
  └─ asyncio.create_task(_process_agents_background(agent_wakeups))
        │
        ├─ 并行调用所有 Agent：_call_agent_safe()
        │   ├─ agent.process(payload) → list[AgentAction]（支持双 FC 同时返回）
        │   ├─ 如果含 request_action → 绕过 Router，直接分发给 ActionAgent
        │   └─ 如果含 submit_action → 记录 chain log，返回 action
        │
        ├─ router.process_batch(actions) → RouterCommand 列表
        │
        └─ _execute_commands(commands)
            ├─ WAKE_AGENT → 按坐标+urgency 算延迟 → 同城走 _pending_wakeups / 异地走 timers.schedule()
            ├─ EXECUTE_POLICY → _run_action()（后台 Task）
            └─ RELAY_DECREE → 为每个 target 加入 _pending_wakeups
```

### ActionAgent 执行流程

```
Orchestrator._run_action(payload, source_agent_id, chain_id)
  │
  ├─ action_agent.process(payload) → ActionResult
  │   ├─ 构建 system prompt（注入世界状态上下文）
  │   ├─ 多轮 FC 循环（最多 7 轮）：
  │   │   ├─ LLM 调用 → 解析工具调用
  │   │   ├─ query_entity → 返回实体全部状态（含库存+进行中的事件）
  │   │   ├─ update_entity_status / update_inventory → 修改世界状态
  │   │   ├─ add_ongoing_event → 在目标实体上排队延迟事件
  │   │   ├─ modify_ongoing_event → 修改进行中的事件
  │   │   ├─ delete_ongoing_event → 取消进行中的事件
  │   │   └─ complete_action → 结束循环
  │   └─ 返回 ActionResult（结果描述/耗时/成功与否/变更记录）
  │
  ├─ 记录 chain log（fc_called=complete_action）
  │
  └─ 创建 TimerEntry，delay = duration_days * 86400 游戏秒
      → 结果在 N 天后送达发起者
```

### 皇帝操作流程

```
Orchestrator._handle_player_action(action, result)
  │
  ├─ HOLD → 记录 chain log，结束
  │
  ├─ 找太监：court.get_subordinates("皇帝") 中 role=eunuch 的
  │   回退：找不到就用皇帝的任何直属下级
  │
  ├─ APPROVE / REJECT：
  │   └─ 唤醒太监 Agent，payload 含 "皇帝{verb}了奏折：{response_text}。传达下去。"
  │
  └─ ISSUE_DECREE：
      └─ 唤醒太监 Agent，payload 含 "皇帝下旨：{decree_text}"
```

## 当前数据配置

> 官员层级、地点配置、人格模板详见 [PROJECT_DOC.md](PROJECT_DOC.md) 第七节。

---

## 前端视觉开发规范（2026 重构）

### 文件职责

| 文件 | 职责 |
|---|---|
| `frontend/src/design-system.css` | 全局设计令牌、字体栈、页面底色、通用输入控件、焦点态、滚动条、品牌印章与共享动效 |
| `frontend/src/AppNav.css` | 朝堂 / 编修 / 天玑之间的可拖拽浮动导航 |
| `frontend/src/App.css` | 朝堂、上帝视角、世界状态、关系图与通用弹窗的页面级样式 |
| `frontend/src/editor.css` | 百官编修录的树、表单、人格卡片与系统 Agent 编辑样式 |
| `frontend/src/tianji.css` | 天玑三栏工作台、对话气泡、工具调用块与输入区样式 |

三个入口文件都应在组件与页面 CSS 之后引入 `design-system.css`，让共享令牌和基础控件规则保持一致：

```jsx
import AppNav from './AppNav.jsx'
import './design-system.css'
```

### 视觉约定

1. **色彩**：底色使用墨黑与暖灰；主强调色使用鎏金；危险操作使用朱砂红；成功状态使用低饱和松绿色。
2. **层级**：一级页面使用品牌印章 + 主标题 + 副标题；内容区域使用细金边、弱阴影和局部高光，不使用大面积纯亮色。
3. **排版**：标题优先使用楷体/宋体字体栈，操作控件使用系统无衬线字体，日志与工具名可使用等宽字体。
4. **动效**：仅使用短距离淡入、悬停位移和状态光晕；必须尊重 `prefers-reduced-motion`。
5. **响应式**：桌面端优先保留信息密度；`900px` 左右开始转为纵向布局；移动端不得依赖 hover 才能访问功能。
6. **可访问性**：新增交互控件必须保留 `:focus-visible`，不要只用颜色表达在线、危险或选中状态。

### 修改页面时的检查清单

```bash
cd frontend
npm run build
npm run lint
```

- 检查 `/`、`/editor.html`、`/tianji.html`、`/dlc.html` 四个入口。
- 至少检查 1440px 桌面宽度与 390px 移动端宽度。
- 检查浮动导航不会遮挡关键按钮；默认位置为底部居中，拖动位置存于 `appNav.position.v2`。
- 新增颜色、圆角或阴影前，优先复用 `design-system.css` 中的变量。
- 修改前端结构或设计约定后，同步维护 `PROJECT_DOC.md` 与本节。

> 当前 `npm test`、`npm run lint` 与 `npm run build` 均可通过。WebSocket tick 镜像和延迟重连处保留窄范围 ESLint 注释，因为它们分别由外部消息事件和异步 close 回调触发；不要扩大规则禁用范围。

---

## 架构优化计划（2026-07）

后续核心改造必须按 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md) 的依赖顺序实施。新增模块时同步维护本指南中的文件职责、配置格式、迁移步骤和测试命令；DLC、模型、Prompt、记忆或世界状态 schema 变更必须附带兼容与迁移说明。


---

## Secure LLM configuration (Phase 0)

1. Copy `config/llm.example.json` to the ignored local file `config/llm.json`.
2. Keep `api_key` as an environment reference, for example `${WORLD_SIM_API_KEY}`.
3. Set the variable before starting the backend:

```powershell
$env:WORLD_SIM_API_KEY = "your-api-key"
python main.py
```

Do not place a real secret in committed files, examples, logs, tests, traces, or artifacts. Provider keys are intentionally stored in plaintext only in the Git-ignored local `config/models.json`; do not share or upload that file.

Windows 开发环境可直接运行 `start.bat`。该脚本以 `python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload` 启动后端，因此 Python 源码修改会自动重载；修改启动脚本本身或更换依赖后仍需重新运行脚本。

Validation commands:

```powershell
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m pytest -q
python -m compileall -q multiagent_framework routers
cd frontend
npm run build
```

`tests/unit/test_llm_client.py` covers configuration loading, limit clamping, display redaction, the `max_concurrent=1` stream-fallback regression, `tool_calls: null` compatibility, Provider 400 diagnostics, and configurable retry cooldown.


## Task-aware model configuration (Phase 1)

Copy `config/models.example.json` to ignored local `config/models.json`, then set each Provider `base_url`, plaintext `api_key`, `timeout_s`, and optional `retry_delay_s` (default `5.0`). The loader still accepts legacy environment references for migration, but new saves remove `api_key_env`.

Core task policy names:

| Task | Current caller |
|---|---|
| `router.simple` | reserved for deterministic/hybrid routing phase |
| `router.ambiguous` | Router semantic arbitration |
| `agent.turn` | official and institution Agents |
| `action.simulation` | ActionAgent world simulation |
| `memory.compress` | long-memory summarization |
| `tianji.chat` | TianJi interactive debugging |

Each task maps to an ordered list of model profile names. The first compatible profile is tried first; each profile performs `1 + max_retries` attempts, waiting for its Provider `retry_delay_s` between failed attempts, before Provider/model errors fall through to later profiles. Tool calls skip profiles that explicitly lack the `tools` or `function_calling` capability. Each profile owns an independent concurrency semaphore.

Read-only diagnostics:

```text
GET /api/debug/model-config
GET /api/debug/llm-metrics
GET /api/debug/llm-traces?limit=100&task_type=action.simulation
```

`GET /api/debug/model-config` is safe for DLC Studio profile dropdowns. It intentionally omits Provider URLs, environment-variable names, local source paths, credentials, and Authorization metadata.

Provider `base_url` and plaintext `api_key` may be changed directly in the local settings page. Saving uses atomic file replacement and immediately rebuilds live model clients; clearing the field clears the active key. Use “测试连接” before saving when diagnosing a new endpoint.

To preflight and atomically activate edits to the existing local `config/models.json`, explicitly enable the administration boundary and provide a process-local token:

```powershell
$env:WORLD_SIM_ENABLE_ADMIN_API = "1"
$env:WORLD_SIM_ADMIN_TOKEN = "generate-a-long-random-token"
$headers = @{ "X-Admin-Token" = $env:WORLD_SIM_ADMIN_TOKEN }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/debug/model-config/preview -Headers $headers
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/debug/model-config/reload -Headers $headers
```

When disabled, both administration endpoints return 404. Missing or incorrect tokens return 403. The endpoints accept no remote Provider URL, key, environment-variable name, or configuration body; they only read the already configured local file. Invalid files and client-construction failures preserve the current generation. In-flight ordinary and streaming requests finish on their captured generation, then retired clients close.

Relevant tests:

```powershell
python -m pytest -q tests/unit/test_model_gateway.py
python -m pytest -q tests/unit/test_settings_security.py tests/unit/test_model_reload_api.py
```

## Phase 2 开发指南：TurnEngine、Agent 多轮协议与锁

### 通用多轮引擎

新 Agent 不应自行编写 `for round in ...` 循环。使用：

```python
from multiagent_framework.turn_engine import TurnBudget, TurnEngine

outcome = await TurnEngine().run(
    llm=llm,
    messages=messages,
    tools=tools,
    execute_tool=execute_tool,
    terminal_predicate=lambda name, result: name == "finish_turn",
    trace_context={
        "trace_id": trace_id,
        "turn_id": turn_id,
        "chain_id": chain_id,
        "agent_id": agent_id,
    },
    budget=TurnBudget(
        max_rounds=4,
        max_tool_calls=12,
        max_tokens=12000,
        max_seconds=60,
        no_progress_rounds=2,
    ),
)
```

`execute_tool(name, args, tool_call)` 必须返回可 JSON 序列化的字典。终止工具由调用方通过 `terminal_predicate` 指定。不要在工具 handler 中重新生成 trace/turn/chain ID。

### 普通 Agent 工具协议

普通 Agent 默认工具集合位于 `multiagent_framework/fc_tools.py::AGENT_TOOLS`：

- 只读：`list_entities`、`query_entity`、`list_agents`
- 意图：`submit_action`、`request_action`
- 终止：`finish_turn`

标准流程是：

```text
query（可多次） → submit/request（可多个） → finish_turn
```

意图工具返回 `accepted_pending_execution` 只是接收确认，不得解释为世界状态已经改变。Router/FC 的调度结果由 Orchestrator 写入 `【系统执行反馈】`；耗时行动完成后写入 `【真实行动结果】`。新增工具时必须区分：只读查询、意图接受、真实执行、终止工具。

### 并发安全约束

- 不要绕过 `BaseAgent.process()` 直接调用 `_process_locked()`。
- 不要从 Agent 外部直接修改 `memory`、`_summary` 或调用压缩器。
- 需要异步写入反馈时调用 `await agent.record_execution_feedback(...)`。
- 持久化运行中记忆时调用 `await agent.snapshot_memory()`；只有应用启动/完全暂停后才可使用同步 `serialize_memory()` / `deserialize_memory()`。
- Orchestrator 可并行调用不同 Agent；同一 Agent 由实例锁和 inbox sequence 保证公平串行。

### 调试与测试

```powershell
python -m pytest -q tests/unit/test_turn_engine.py
python -m pytest -q
python -m compileall -q multiagent_framework routers daming_court
```

调试接口：

```text
GET /api/debug/agent-turns
GET /api/debug/llm-traces?task_type=agent.turn
GET /api/debug/action-log
```

Fake LLM 测试至少应验证：各轮共享 trace/turn/chain、round 递增、真实 tool result 被追加、预算能停止循环、同 Agent 最大并发为 1。

## Phase 3 development guide: Hybrid Router

### Routing decision order

1. Normalize the accepted Agent action into a versioned intent/command envelope.
2. Prefer deterministic routing for direct reports, explicit target hints, policies, and decrees.
3. Verify target enablement and `CourtGraph` reachability.
4. Use `router.ambiguous` only for unresolved legacy/free-text intent, with bounded batch and candidate lists.
5. Preserve the incoming `chain_id`; append parent chains instead of generating a replacement chain.
6. Reject invalid, duplicate, over-hop, self-loop, or repeated-edge commands into the dead-letter buffer.
7. Recheck idempotency and persisted route edges in Orchestrator before FC dispatch.

Do not let an LLM invent arbitrary target IDs. Candidate IDs returned by the model must be members of the bounded candidates supplied by `CourtGraph.get_routing_candidates()`. Do not call private Router internals from API routes; use the public batch-routing and rejection interfaces.

### Compatibility and persistence

`ChainLog.has_route_edge()` and `RouterAgent.reject_command()` are optional when using lightweight test doubles. Production implementations provide both, while Orchestrator checks them with `getattr(..., None)` before calling. Route-edge indexes must be updated on append, cleared on reset, and rebuilt on JSONL reload.

### Router verification

```powershell
python -m pytest -q tests/unit/test_router.py
python -m pytest -q tests/unit/test_chain_log.py
python -m pytest -q tests/unit/test_turn_engine.py
python -m pytest -q
python -m compileall -q multiagent_framework routers daming_court
cd frontend
npm run build
cd ..
git -c safe.directory='F:/world-simulator' diff --check
```

The last command may require replacing the example safe-directory path with the actual workspace path on Windows. `.pytest_cache` permission warnings do not indicate a product failure when all tests pass.


## Phase 4 开发指南：WorldCommandBus、资源目录与价值锚

### 世界写入规则

- 新代码不得直接对 `entities`、`inventory`、`entity_values` 或 `ongoing_events` 执行分步写入；使用 `WorldState` 兼容方法或显式 `WorldCommandBus.execute()`。
- 来自 LLM/FC 的写操作必须传稳定 command ID。优先使用 `{chain_id}:{tool_call_id}:{operation}`；API 使用 `Idempotency-Key`。
- 重试必须复用相同 command ID 和相同 canonical payload。不要在重试时重新生成事件 ID；创建事件使用稳定 ID 或当前 `uuid5` 规则。
- 修改已有记录时传 `expected_version`。遇到 `version_conflict` 时回读最新状态后再由调用方决定是否重试，禁止静默覆盖。
- 不要捕获后忽略 `WorldCommandError`；错误码（如 `insufficient_balance`、`idempotency_conflict`）是协议的一部分。

### 资源建模

```python
from multiagent_framework.world_state import ResourceDefinition

world_state.upsert_resource_definition(ResourceDefinition(
    name="战马",
    unit="匹",
    kind="asset",
    default_quantity=0,
    min_quantity=0,
    anchor_rate=25.0,
    metadata={"dlc_id": "example"},
))
```

- `asset` 存入 inventory，可支出并写 ledger；`anchor_rate` 必须是有限正数。
- `metric` 与 `parameter` 存入 `entity_values`，不进入库存、不参与资产价值。
- `metric` / `parameter` 使用资源定义的 min/max clamp；`-1` 表示不适用且禁止修改。
- 比较不同资产时使用 `base_value`，不要把不同单位的 quantity 直接相加。
- DLC Loader 后续必须先注册 `ResourceDefinition`，再载入实体初始值。

### 成熟事件与恢复

- Orchestrator 只能调用 `apply_matured_events(sim_time)`；不要恢复“弹出事件 → 分步更新状态/库存”的旧路径。
- `pop_matured_events()` 是兼容只读方法，不再删除事件。
- `bulk_load()` 与 `restore_all()` 是单事务操作。扩展导出 schema 时必须同时补充迁移和 rollback 测试。
- 资产变化必须能在 `get_inventory_ledger()` 中追溯到 command、chain、reason 和模拟时间。

### 前端/API 协议

```http
PUT /api/world-state/{entity_id}/inventory
Idempotency-Key: <stable-uuid>
Content-Type: application/json

{
  "resource_name": "粮草",
  "quantity": 1200,
  "expected_version": 3,
  "reason": "world_state_editor"
}
```

实体详情中的 `inventory` 仅包含资产；指标读取 `metrics`，参数读取 `parameters`，总价值读取 `inventory_valuation.base_value`。DLC 自定义资产不得被前端内置名称白名单过滤。

### 验证命令

```powershell
python -m pytest -q tests/unit/test_world_commands.py
python -m pytest -q
python -m compileall -q multiagent_framework routers daming_court
cd frontend
npm test
npm run lint
npm run build
cd ..
git -c safe.directory='F:/世界模拟器' diff --check
```

`test_world_commands.py` 必须持续覆盖幂等重放、payload 冲突、余额不足回滚、实体/资源版本冲突、资源分层、价值锚、成熟事件原子性、并发无丢失更新与恢复回滚。


## Phase 5 开发指南：DLC Registry、Runtime 与 Studio

### 文件职责

- `multiagent_framework/dlc_registry.py`：磁盘包边界、manifest discovery、schema/引用校验、SemVer 依赖范围、Content migration、原子写入、发布生命周期、依赖排序与 keyed CRUD。
- `multiagent_framework/dlc_runtime.py`：将规范化 bundle 转成 `ResourceDefinition`、`Entity`、`InventoryItem`、`EntityValue`、`OngoingEvent`、`Official`、普通 Agent 与 `EventObject`。
- `routers/dlc.py`：DLC Studio HTTP API，不直接拼接任意文件路径。
- `daming_court/dlc.json`：内置 legacy-v1 兼容 manifest。
- `dlcs/example_frontier/`：最小 bundle-v1 示例，默认 `enabled=false`。
- `frontend/src/DLCStudio.jsx`：草稿、manifest、依赖版本编辑、Content migration preview、模型策略、校验/发布、核心内容 tab、Content 工具栏、保存前 Diff、Undo/Redo 和 raw content 编辑。
- `frontend/src/DLCContentEditors.jsx`：库存、实体数值、关系、初始/持续事件、Persona 与 Prompt blocks 的结构化表单。
- `frontend/src/DLCStudioTools.js`：无副作用的 Content envelope 导入/导出、schema 规范化、稳定键 Diff 与历史栈；对应测试位于 `DLCStudioTools.test.js`。

### bundle-v1 目录和 content schema

```text
dlcs/<dlc_id>/
├── dlc.json
├── content.json
└── prompts/
```

`content.json` 顶层字段固定为：

```json
{
  "schema_version": "1.0",
  "resources": [],
  "entities": [],
  "inventory": [],
  "entity_values": [],
  "relations": [],
  "agents": [],
  "initial_events": [],
  "ongoing_events": [],
  "personas": {},
  "prompts": {}
}
```

资源 `kind` 只能是 `asset | metric | parameter`。`asset.anchor_rate` 必须是有限正数；inventory 只能引用 asset，且库存条目不得用自己的 `anchor_rate` 覆盖资源目录价值锚；entity_values 只能引用 metric/parameter。运行时 materialization 始终以 `resources[].anchor_rate` 为唯一价值锚来源。DLC ID 必须匹配 `^[a-z][a-z0-9_-]{1,63}$`，并与包目录名一致。

关系必须使用受支持的 `rel_type` 且禁止 self-reference。初始事件必须有唯一 ID、1–10 整数 severity、有效 Agent 目标和 object payload；持续事件必须引用有效实体、使用 object result、具有有限时间且结束时间不早于开始时间。Persona 值必须是非空字符串。所有 Prompt definition 会在 publish/load 前由 Framework Prompt Registry 解析和 lint；Agent 专属 Prompt 只能引用该 Agent 模板允许的变量。

Manifest 依赖继续使用有序 ID 列表，并通过独立 map 声明版本范围：

```json
{
  "version": "1.3.0",
  "dependencies": ["base_world"],
  "dependency_constraints": {
    "base_world": ">=1.2.0,<2.0.0"
  }
}
```

版本必须是完整 `major.minor.patch` SemVer。范围支持精确值、`> >= < <=`、逗号或空格 AND、`^`、`~`、`1.x`/`1.2.*` 以及 `||`。Constraint key 必须同时出现在 `dependencies`；不要在运行时自行解析或跳过 Registry 的 `version_satisfies`/validation。

Content 当前 schema 为 `1.0`。Registry 的迁移图支持 `0.9 -> 1.0`，会规范化 `officials -> agents`、resource `type -> kind` 与 inventory `resource_name/name -> item_name`。任何新迁移必须保持纯预览先行、确定性、幂等，转换成功后才允许原子写入，并接受 `expected_source_schema_version` 防止基于过期预览覆盖。
### 生命周期

```text
create draft → edit content → validate → publish → enable
```

- draft 不能启用。
- publish 前所有 error 级 validation issue 必须清零，包括跨引用、事件时间、Persona、Prompt lint 和 catalog-only anchor invariant。
- active package 按 dependency 拓扑顺序加载；缺失/未启用依赖、版本不满足或依赖环会阻止启动。依赖包必须先发布，启用 dependent 前还必须先启用依赖。
- legacy-v1 内容只读；需要全面编辑时应在 Studio 新建 bundle-v1 并迁移内容。
- seed 数据仅在新世界加载；读档时仍注册结构和 Agent，但不重复 seed 世界实体或初始事件。

### API

```text
GET/POST                 /api/dlcs
GET                      /api/dlcs/{id}
PUT                      /api/dlcs/{id}/manifest
PUT                      /api/dlcs/{id}/content
POST                     /api/dlcs/{id}/validate
POST                     /api/dlcs/{id}/migration/preview
POST                     /api/dlcs/{id}/migration/apply
POST                     /api/dlcs/{id}/publish
POST                     /api/dlcs/{id}/enabled
PUT/DELETE               /api/dlcs/{id}/entities/{entity_id}
PUT/DELETE               /api/dlcs/{id}/agents/{agent_id}
PUT/DELETE               /api/dlcs/{id}/resources/{resource_name}
```

### 开发与测试

```powershell
python -m pytest -q tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_dlc_api.py
python -m pytest -q
python -m compileall -q main.py multiagent_framework routers daming_court
cd frontend
npm test
npm run lint
npm run build
```

访问 `/dlc.html` 打开 Studio。修改已启用 DLC 的 seed 内容通常只影响下一次新游戏；manifest 启停和运行中热重载是两个不同概念，当前版本不会把 seed 变更直接覆盖当前存档。



## Phase 6/7 development guide: model bindings, Prompt Registry, and Memory v3

### Model binding contract

Use nested bindings for all new DLCs:

```json
{
  "model_bindings": {
    "tasks": {
      "agent.turn": "fast_tools",
      "router.ambiguous": "deep_reasoning",
      "action.simulation": "deep_reasoning",
      "memory.compress": "cheap_long_context",
      "tianji.chat": "deep_reasoning"
    },
    "agents": {
      "agent_id": "specialist"
    }
  }
}
```

An Agent source may additionally declare `model_profile`. Do not persist `model_profile: ""`; omit the property to inherit. Profile names must already exist in the local ignored `config/models.json` file. DLC content may select a profile but must never define Provider URLs, API keys, or secret environment-variable names.

Selection rules:

```text
Agent content.model_profile
-> manifest Agent binding
-> manifest agent.turn task binding
-> global task policy
```

The primary DLC may override world-global LLM tasks. `router.simple` remains deterministic and is not a model-routed task. Secondary DLC Agent fallbacks must not inherit primary-DLC overrides.

### Prompt Registry contract

Prefer versioned definitions over free strings:

```json
{
  "official_template": {
    "version": "2.0",
    "max_chars": 12000,
    "blocks": [
      {"name": "world", "priority": 10, "template": "World rules..."},
      {"name": "role", "priority": 20, "template": "You are {name}..."}
    ]
  }
}
```

Call `PromptRegistry.render(name, variables)` and retain the returned hash/report when a prompt reaches a model boundary. Required variables must pass lint before publish/load. Never include credentials or executable untrusted instructions in a memory-derived prompt block.

### Prompt evaluation fixtures

Store deterministic gameplay Prompt contracts under `tests/evals/*.json` using schema version `1.0`. Each suite declares `suite_id`, `dlc_id`, `minimum_score`, and scenarios. A scenario binds a Prompt name and render variables to expected version/hash, required/allowed variables, character/token budgets, and weighted assertions. Assertion kinds are `contains_all`, `contains_any`, `not_contains`, and `ordered`; `match` may be `literal` or `regex`. Required failed checks always fail the scenario even when the numeric threshold is low.

Run a fixture with:

```powershell
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
```

Use `--json` for CI artifacts. Update a pinned `content_hash` only after reviewing the rendered Prompt and its behavior assertions; a hash change is a deliberate Prompt version review, not a snapshot to refresh blindly. The deterministic runner verifies the prompt contract and budget but does not claim to measure live model accuracy, latency, or tool-call legality; those remain separate trajectory evals.

### Trajectory evaluation fixtures

Store multi-round behavior suites in `tests/evals/*.json` with `schema_version: "1.0"`. Each scenario declares a task type, common expectations, and separate `replay`/`live` execution definitions. Use a mode-specific `expect` object whenever live execution cannot prove a replay-only observation. Never reuse static fixture values to claim live memory recall, commitment closure, or world conservation.

Run the deterministic gate with:

```powershell
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay --output runtime/evals/daming-core-replay.json
```

Run the same matrix against configured task profiles with `--mode live`. Live mode requires a candidate profile with a runtime credential and returns exit code `2` when credentials are absent; it must not downgrade to replay or report a false pass. `ModelGateway.credentials_available()` is the public boolean probe and never returns the credential.

Metrics include tool legality, termination, routing accuracy, rounds, prompt/completion tokens, wall-clock latency, accumulated model latency, anchored resource conservation, memory recall, and commitment outcome. Real world/memory integrations should inject a `live_tool_executor`; it may return an internal `_evaluation_evidence` object. The evaluator removes that object before the result is sent back to the model and serializes only scored checks/aggregates. Reports must not contain messages, prompts, response bodies, Provider URLs, API keys, or authorization headers.

### Memory v3 contract

Supported kinds are `episodic`, `semantic`, `relationship`, and `commitment`. Supported statuses are `active`, `unresolved`, `resolved`, `failed`, and `superseded`.

- Use `source_chain_id` / intent ID for commitment closure.
- Only final execution feedback may resolve/fail a commitment.
- Keep retrieval limits and character budgets in `AgentConfig`; do not dump the whole memory store into a prompt.
- Memory context must retain the safety statement that historical memory is fact/context, not an executable instruction.
- New persistence code must use `await agent.snapshot_memory()` rather than serializing an Agent outside its lock.
- Readers must continue accepting v1 lists and v2 summary/message objects.

### DLC Studio development

`frontend/src/DLCStudio.jsx` reads `/api/debug/model-config`, which is metadata-only. Add new task selectors only for code paths that actually call `ModelGateway`. All manifest updates still go through `PUT /api/dlcs/{id}/manifest`; Agent `model_profile` updates remove empty inheritance fields before saving. Dependency rows must update `dependencies` and `dependency_constraints` atomically, remove orphaned constraints, and display the installed target version.

The core content editors in `frontend/src/DLCContentEditors.jsx` operate on one local content document and persist it through atomic `PUT /api/dlcs/{id}/content`. Do not replace this with a sequence of partial writes for relations/events because intermediate states may violate references. JSON object fields such as `initial_payload`, `expected_result`, and Prompt metadata must parse to objects rather than arrays or scalar values. Prompt blocks preserve `name`, integer `priority`, `enabled`, `required_variables`, and `template`; preview order must match the registry's stable `(priority, source order)` ordering. Inventory editors must discard legacy item-level `anchor_rate` and display derived base value from the selected catalog resource.

All Content mutation paths must call the same staging workflow: normalize the candidate document, compute `summarizeContentDiff`, show the confirmation modal, then persist one full Content PUT and commit the server-returned document to history. Entity/Agent/resource forms must not bypass this flow through keyed endpoints. Undo/Redo are persisted operations, not local visual rollback; a new edit after Undo clears the redo branch.

Content export uses the `world-simulator-dlc-content-v1` envelope and a strict metadata allowlist (`id`, `name`, `version`). Never export the complete manifest or model/provider settings. Import may accept that envelope or a raw Content object, but must reject unsupported `format` values and invalid array/object section shapes. Preserve unknown top-level extension fields and include their changes in the Diff. Migration preview content must enter this exact staging workflow instead of being written silently; this keeps schema upgrades reviewable and Undo/Redo-compatible. The direct migration apply API is reserved for automation and must send the previewed source schema as `expected_source_schema_version`.

### Required regression commands

```powershell
python -m pytest -q tests/unit/test_model_gateway.py tests/unit/test_model_reload_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_dlc_api.py tests/unit/test_prompt_registry.py tests/unit/test_prompt_evaluation.py
python -m pytest -q tests/unit/test_memory_store.py tests/unit/test_turn_engine.py tests/unit/test_runtime_persistor.py
python -m pytest -q
python -m compileall -q main.py multiagent_framework routers daming_court
cd frontend
npm run build
cd ..
git -c safe.directory='F:/世界模拟器' diff --check
```

## Scale profiling and batch world commands (2026-07-11)

Run the deterministic smoke profile:

```powershell
python -m multiagent_framework.scale_evaluation `
  --config tests/evals/daming_scale_smoke.json `
  --output runtime/evals/daming-scale-smoke.json
```

The report contains operations, elapsed time, operations/second, traced peak memory, phase thresholds, and invariant evidence. Do not commit `runtime/evals/` reports.

Run the deterministic 100-cycle soak profile:

```powershell
python -m multiagent_framework.scale_evaluation `
  --config tests/evals/daming_scale_soak.json `
  --output runtime/evals/daming-scale-soak.json
```

`soak_cycles > 1` enables the additional `soak` phase. It repeatedly saves and rediscovers a published DLC, creates and matures one world event per tick, runs a non-atomic batch containing both valid and deliberately rejected commands, and overfills a bounded memory store. A passing report proves reload visibility, isolated rejection, event cleanup, unresolved-commitment retention, memory compaction, and anchored base-value conservation for every configured cycle. Keep `soak_commands_per_cycle >= 2`.

### CourtGraph cache rules

- Query methods return detached `Official` values; mutating a returned value does not mutate the index.
- Use `add_official()` or `bulk_load_officials()` for normal writes; both invalidate all indexes after commit.
- If another process or direct SQLite statement changes `officials`, call `court.refresh_cache()` before expecting this process to observe it.
- Keep graph writes behind the graph API. A refresh is explicit by design so Router reads stay O(1) between content reloads.

### WorldCommandBus batch API

```python
batch = world.command_bus.execute_batch(commands, atomic=True)
if not batch.ok:
    for result in batch.results:
        print(result.command_id, result.status, result.error_code)
```

- `atomic=True`: any rejection aborts the transaction. Commands already reduced in this batch return `batch_rolled_back`; commands after the failure return `batch_aborted`. Successful receipts that existed before the batch remain valid replays.
- `atomic=False`: each command uses a savepoint. Valid commands commit together; failed commands are rolled back individually and persist rejected receipts.
- Do not split a logical all-or-nothing transfer across non-atomic batches. Use one atomic batch or one reducer command that validates the complete transfer.
- Reuse stable command IDs for retries. Never reuse one command ID with changed payload, chain, intent, metadata, version, or simulation time because all are part of the canonical payload hash.

Targeted regression gate:

```powershell
python -m pytest `
  tests/unit/test_court_graph.py `
  tests/unit/test_router.py `
  tests/unit/test_world_commands.py `
  tests/unit/test_scale_evaluation.py `
  -q -p no:cacheprovider
```

## DLC Runtime hot reload operations (2026-07-11)

DLC publish and runtime activation are separate operations. After saving and publishing a candidate bundle, inspect the runtime plan before activation:

```powershell
$headers = @{ 'X-Admin-Token' = $env:WORLD_SIM_ADMIN_TOKEN }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/debug/dlc-runtime/preview -Headers $headers
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/debug/dlc-runtime/reload -Headers $headers
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8000/api/debug/dlc-runtime
$rollback = @{ source_generation = 1 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/debug/dlc-runtime/rollback/preview -Headers $headers -ContentType application/json -Body $rollback
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/debug/dlc-runtime/rollback -Headers $headers -ContentType application/json -Body $rollback
```

Enable the mutating administration boundary only in a trusted environment:

```text
WORLD_SIM_ENABLE_ADMIN_API=1
WORLD_SIM_ADMIN_TOKEN=<strong local secret>
```

Operational rules:

- Always preview first. A plan with `safe: false` requires an explicit migration; do not bypass the rejection by editing the SQLite database directly.
- Hot reload may add resources/entities/inventory/relations/ongoing events, but it preserves current entity status, inventory quantity, dynamic value, event progress, and version data.
- Initial events are seed-time behavior and are never injected by hot reload.
- Changing a resource kind or an asset `anchor_rate` is intentionally rejected. Implement and review a ledger-aware value migration before changing either invariant.
- Candidate Agents and model profiles must build successfully before activation. Unknown profiles leave the old generation active.
- Existing turns are allowed to retire on their captured Agent. Reload waits for safe memory snapshots, publishes a new Agent map, archives removed-Agent memory, and lets pending wakeups resolve the new map on the next tick.
- World rows and managed Court officials commit in one SQLite transaction. Refresh CourtGraph only after a successful commit.
- Tianji keeps its current session and message history; runtime activation replaces its system message and model client without reconstructing the session.
- Runtime status and events are deliberately aggregate-only. Never add Prompt/response bodies, Provider URLs, API environment names, Authorization, secrets, or configuration paths to API responses or reload logs.
- Monitor `metrics.reload_in_progress`, `current_waiting_inflight_agents`, reload success/failure totals, duration totals/maxima, memory-migration duration, migration rejection counters, and `world_rows_added_total`. Alerting thresholds remain environment-specific and should be calibrated from repeated soak samples.
- Runtime generations are monotonic. Rolling back source generation `N` creates a new generation; it never changes the active generation counter back to `N`.
- Rollback restores retained runtime configuration but deliberately does not delete later world additions or rewind live inventory, status, value, version, timestamp, or event-progress data. Always inspect rollback preview warnings.
- Generation history is bounded to the latest 20 activations and stores configuration-only, memory-empty Agent blueprints. A restart clears it; do not treat it as a memory backup or persistent disaster-recovery archive.

Targeted regression gate:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_dlc_runtime.py tests/unit/test_dlc_reload_api.py
python -m pytest -q -p no:cacheprovider
python -m compileall -q main.py multiagent_framework routers daming_court
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
git -c safe.directory='F:/世界模拟器' diff --check
```

## Atomic runtime binding development guide (2026-07-12)

Runtime model-backed components must not read Prompt and client fields independently during a call. Use the shared hub instead:

```python
binding = self._runtime_bindings.capture(TASK_ID)
response = await binding.llm.chat(
    messages=[{"role": "system", "content": binding.prompt}],
    trace_context={"runtime_generation": binding.generation},
)
```

Rules:

1. Capture once at the beginning of the logical call. Multi-round turns must reuse that binding for every round.
2. Build a complete candidate with `Orchestrator.prepare_runtime_bindings()` before changing live state.
3. Publish only with `Orchestrator.activate_runtime_bindings()`. Do not sequentially assign Router, ActionAgent, MemoryCompressor, and Tianji Prompt/client fields in production reload code.
4. Legacy attributes (`llm`, `_llm`, `_system_prompt`, `_prompt_template`) are compatibility mirrors only after a component is bound to the hub.
5. Generation blueprints must contain the fully resolved binding set so rollback does not inherit values from the generation being replaced.
6. Public APIs, traces, tests, and operations events may expose generation and profile identifiers, but never Prompt bodies, client objects, provider URLs, credentials, or local configuration paths.

Focused verification:

```powershell
python -m pytest tests/unit/test_runtime_bindings.py -q
python -m pytest tests/unit/test_dlc_runtime.py tests/unit/test_dlc_reload_api.py -q
```

The current full Python baseline is **244 passed**.

## Encrypted generation archives and operational metrics

### Generation archive key

`DLCBundleLoader` uses an encrypted cross-restart archive under the runtime directory. For production, inject a stable externally managed key rather than relying on the local generated key:

```powershell
$env:WORLD_SIM_DLC_ARCHIVE_KEY = '<32-byte URL-safe Base64 key>'
```

Never commit this value. Rotating it makes older archives unreadable; unreadable archives are isolated and reported as aggregate issues while current DLC startup continues.

### Enable the metrics API

The metrics API is opt-in and has a token independent from the administration and model credentials:

```powershell
$env:WORLD_SIM_ENABLE_METRICS_API = '1'
$env:WORLD_SIM_METRICS_TOKEN = '<long random metrics token>'
```

Requests use `X-Metrics-Token`:

```powershell
Invoke-RestMethod -Headers @{'X-Metrics-Token'=$env:WORLD_SIM_METRICS_TOKEN} `
  http://127.0.0.1:8000/api/metrics

Invoke-WebRequest -Headers @{'X-Metrics-Token'=$env:WORLD_SIM_METRICS_TOKEN} `
  http://127.0.0.1:8000/api/metrics/prometheus
```

When disabled the endpoint returns 404. A missing or incorrect token returns 403, and an enabled endpoint returns 503 until the simulator is initialized. Do not place the token in query strings, logs, dashboards, or checked-in scrape configuration.

### Configure alert thresholds

```powershell
$env:WORLD_SIM_ALERT_LLM_ERROR_RATE = '0.10'
$env:WORLD_SIM_ALERT_LLM_FALLBACK_RATE = '0.25'
$env:WORLD_SIM_ALERT_DLC_RELOAD_FAILURE_RATE = '0.10'
$env:WORLD_SIM_ALERT_DLC_RELOAD_MAX_MS = '5000'
$env:WORLD_SIM_ALERT_WORLD_COMMAND_REJECTION_RATE = '0.25'
$env:WORLD_SIM_ALERT_AGENT_TURN_ERROR_RATE = '0.10'
$env:WORLD_SIM_ALERT_ARCHIVE_ISSUES = '0'
$env:WORLD_SIM_ALERT_BUSY_AGENT_RATIO = '0.90'
```

Ratio thresholds are clamped to `[0, 1]`. Negative, non-finite, and malformed values fall back to defaults. Calibrate these values from repeated samples in each deployment environment instead of copying local development timings directly into production enforcement.

### Metrics safety rules

When adding a metric or label, export aggregate numbers only. Never export Agent IDs, Prompt or persona bodies, message/memory content, command or chain IDs, API keys, Authorization values, Provider URLs, secret environment variable names/values, local configuration paths, or raw errors. Keep labels bounded; per-Agent and per-command labels create unsafe cardinality even when they are not secret.

### Metrics verification

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_operational_metrics.py tests/unit/test_metrics_api.py tests/unit/test_turn_engine.py tests/unit/test_world_commands.py
python -m pytest -q -p no:cacheprovider
```

The current full Python baseline is **244 passed**.

## Calibrating environment-specific scale thresholds

Run the deterministic profile repeatedly on one stable target environment. Do not mix machines, Python builds, workload configs, or phase sets in one baseline:

```powershell
1..5 | ForEach-Object {
  python -m multiagent_framework.scale_evaluation `
    --config tests/evals/daming_scale_soak.json `
    --output "runtime/evals/calibration/local-windows-$_.json"
}
```

Create the aggregate baseline:

```powershell
python -m multiagent_framework.scale_calibration calibrate `
  runtime/evals/calibration/local-windows-1.json `
  runtime/evals/calibration/local-windows-2.json `
  runtime/evals/calibration/local-windows-3.json `
  runtime/evals/calibration/local-windows-4.json `
  runtime/evals/calibration/local-windows-5.json `
  --environment-id local-windows `
  --output runtime/evals/daming-scale-baseline-local.json
```

Compare a candidate run:

```powershell
python -m multiagent_framework.scale_calibration compare `
  --baseline runtime/evals/daming-scale-baseline-local.json `
  --candidate runtime/evals/calibration/local-windows-5.json `
  --output runtime/evals/daming-scale-comparison-local.json
```

CLI exit codes are `0` for success/pass, `1` for a valid candidate with a detected regression, and `2` for malformed or incompatible input. Calibration requires at least three reports and rejects unknown `ScaleConfig` fields, invalid environment IDs, non-finite/negative measurements, mismatched suites/workloads, duplicate phases, and phase-order drift.

Each new scale report exposes:

- `invariants_passed`: deterministic world/DLC/router/memory correctness.
- `performance_passed`: compliance with the threshold embedded in that source config.
- `passed`: both conditions combined.

Calibration requires `invariants_passed`; it intentionally does not require the old `performance_passed`. Comparison then enforces the newly calibrated elapsed and peak-memory envelope. Legacy reports lacking the split fields use `passed` as a conservative invariant fallback.

The local five-sample baseline currently recommends: DLC 258.253 ms, Router 147.314 ms, World 387.191 ms, Memory 240.637 ms, and Soak 4,297.548 ms. Treat these as local evidence only; generate a separate baseline for each deployment/CI environment.

## Calibrating operational alert thresholds

Capture at least three independent `/api/metrics` JSON snapshots from the same deployment class and comparable workload window. Do not duplicate one cumulative snapshot, mix environments, or use snapshots without actual LLM/DLC/world/Agent exposure.

```powershell
python -m multiagent_framework.operational_calibration calibrate `
  runtime/evals/ops/staging-1.json `
  runtime/evals/ops/staging-2.json `
  runtime/evals/ops/staging-3.json `
  runtime/evals/ops/staging-4.json `
  runtime/evals/ops/staging-5.json `
  --environment-id staging-win-x64 `
  --output runtime/evals/operational-baseline-staging.json
```

Compare a later snapshot:

```powershell
python -m multiagent_framework.operational_calibration compare `
  --baseline runtime/evals/operational-baseline-staging.json `
  --snapshot runtime/evals/ops/staging-candidate.json `
  --output runtime/evals/operational-comparison-staging.json
```

Exit codes:

- `0`: baseline is ready, or candidate passes.
- `1`: baseline contains a policy-ceiling incident requiring review, or candidate regressed.
- `2`: malformed snapshot, missing exposure, duplicate timestamp, unsafe identity, unreviewed baseline, or incompatible schema.

The calibrator reads only the eight bounded alert values and their exposure denominators. It ignores existing snapshot thresholds, active-alert lists, status text, detailed model entries, Prompt/message content, paths, IDs, and errors. Reports contain no API credential, Provider URL, environment-variable name, raw error, or source path.

Recommendations can tighten but cannot exceed the built-in safety ceilings. In particular, `archive_issues` is permanently capped at zero. If any raw sample exceeds a safety ceiling, the report is still produced for diagnosis but has `ready=false`; do not enforce or copy its recommendations until the underlying incident is resolved and clean independent samples are collected.

The files under `runtime/evals/*local-fixture*` are deterministic synthetic toolchain checks only. Generate separate reviewed baselines from actual CI, staging, and production hardware/workload windows before enforcement.

## Signed baseline registry operations

Use the baseline registry only with reviewed, secret-free aggregate calibration artifacts. Do not import raw prompts, messages, errors, Provider URLs, credentials, local paths, or trace identifiers.

Typical lifecycle:

```powershell
python -m multiagent_framework.baseline_registry --database <registry.db> approve --baseline <baseline.json> --approval-ref <review-id>
python -m multiagent_framework.baseline_registry --database <registry.db> activate --version <version>
python -m multiagent_framework.baseline_registry --database <registry.db> status --output <status.json>
python -m multiagent_framework.baseline_registry --database <registry.db> gate-scale --environment-id <environment> --candidate <scale-report.json> --output <comparison.json>
python -m multiagent_framework.baseline_registry --database <registry.db> gate-operational --environment-id <environment> --snapshot <snapshot.json> --output <comparison.json>
python -m multiagent_framework.baseline_registry --database <registry.db> rollback --baseline-type scale --environment-id <environment>
```

Operational rules:

1. Supply the 32-byte Base64 master key through the protected deployment environment, or preserve the generated sidecar key with the registry database. Losing or replacing it makes existing signatures unverifiable.
2. Back up the database and key as one unit, while keeping their access controls separate from public artifacts.
3. Approval is immutable and idempotent. To supersede a baseline, approve a new Baseline ID and activate its returned version.
4. Activation and rollback are scoped to one baseline type and environment. Cross-environment rollback is rejected.
5. A rollback without `--target-version` reverses the latest activation transition. Use an explicit target for deterministic incident procedures.
6. Treat exit `1` as a measured regression and exit `2` as invalid input, missing approval, signature failure, or registry integrity failure.
7. `status` is metadata-only. Do not extend it with stored baseline payloads, database paths, signing material, or raw failure details.
8. The local operational fixture is suitable for toolchain smoke tests only. Production enforcement requires independently collected, reviewed deployment snapshots.

Current verification commands remain:

```powershell
python -m compileall -q main.py multiagent_framework routers daming_court
python -m pytest -q -p no:cacheprovider
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay --output <replay.json>
cd frontend
npm test
npm run lint
npm run build
```

Verified on 2026-07-12: **262 Python tests**, **18 registry-focused tests**, **6 frontend tests**, Prompt and trajectory suites at **100%**, and both calibrated comparison gates passing.

## Random-event development workflow (2026-07-12)

1. Add templates through DLC Studio or the DLC content `random_events` array. IDs and event types must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
2. `target_agents` must be a non-empty unique list referencing Agents in the same normalized DLC content. Payloads must be JSON objects and cannot define `random_event_template_id` or `occurrence`.
3. Severity is an integer range within 1..10. Interval days are positive finite numbers, probability is 0..1, and `max_occurrences` is a non-negative integer (`0` means unlimited). Boolean values are never accepted as numbers.
4. Runtime reload uses `preserve_state=True`: unchanged fingerprints retain `next_due`, attempts, and emitted counts; changed or newly added templates schedule from current `sim_time`; removed templates stop immediately. Rollback republishes the archived template blueprint.
5. Polling occurs at the start of `Orchestrator._tick()`. Keep the catch-up budget bounded when changing scheduler behavior. `TickResult.generated_events` must remain metadata-only.
6. Persist only scheduler fingerprints, due time, attempts, and emitted counts. Never add Prompt text, event payloads, model credentials, file paths, raw errors, Trace IDs, or Agent identifiers to scheduler status.
7. Required focused command: `python -m pytest -q -p no:cacheprovider tests/unit/test_event_generator.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_runtime_persistor.py`. Current focused result: **58 passed**. Current full result: **273 passed**.
## Action-time rule development workflow (2026-07-12)

### DLC schema

Add rules to the normalized DLC `time_rules` array:

```json
{
  "rule_id": "relief_operations",
  "action_types": ["relief", "grain_transport"],
  "base_days": 2,
  "min_days": 1,
  "max_days": 45,
  "workload_factor": 1,
  "severity_factor": 4,
  "distance_factor": 1,
  "resource_factor": 0.5,
  "success_factor": 1.25,
  "priority": 100,
  "enabled": true
}
```

Rules must use safe unique IDs, a non-empty unique action-type list, finite non-negative numeric fields, integer priority, Boolean enabled, and `min_days <= max_days`. Boolean values are not numbers. Validate the DLC through Registry or DLC Studio before publishing it.

### Engine ownership rules

1. Never schedule an action or ongoing-event timer directly from an LLM `duration_days` value. Call the active `TimeSimulator` and use `TimeEstimate.duration_days`.
2. Treat `proposed_days` as a workload hint. Add new deterministic inputs as explicit bounded parameters rather than parsing prose.
3. Resource discounts must use canonical anchored `InventoryItem.base_value`; never trust a per-action exchange rate supplied by an LLM or raw DLC inventory row.
4. Distance inputs must be derived by the engine from world coordinates and the shared travel-speed constant.
5. Public metadata may include duration, Rule ID, source, clamp flag, numeric components, invalid-input replacement flags, rule count, and fingerprint. It must not contain descriptions, entity/Agent IDs, resource rows, Prompt/message/memory bodies, credentials, Provider URLs, paths, Trace IDs, command IDs, or raw errors.
6. Reload and rollback must publish the complete rule set from the selected immutable generation blueprint. A restart must restore archived generations before current content is published as the next monotonic generation.

### Manual API behavior

For `POST /api/world-state/{entity_id}/events` and `PATCH /api/world-state/events/{event_id}`:

- Omit `duration_mode` or use `estimate` for the normal authoritative path.
- Use `override` only for an explicitly trusted operator value.
- Negative, NaN, and infinity durations are rejected in both modes.
- Successful duration changes return `duration_days`, `source`, and `rule_id` so clients can display whether the value was estimated or overridden.
- If a minimal test orchestrator has no published simulator, the router uses a deterministic safe fallback rather than failing with an attribute error.

### Verification commands

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_time_simulator.py tests/unit/test_world_commands.py tests/unit/test_worldstate_time_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_generation_archive.py tests/unit/test_runtime_persistor.py
python -m compileall -q multiagent_framework routers main.py
cd frontend
npm test -- --run
npm run lint
npm run build
```

When changing random-event polling, preserve the global `(next_due, template_id)` ordering. It is the fairness guarantee for bounded catch-up across multiple overdue templates.

Verified on 2026-07-12: **286 Python tests**, **6 frontend tests**, Prompt contracts **100%**, trajectory replay **100%**, local scale comparison passed, local operational fixture comparison passed, compile/lint/build passed, and repository diff/encoding/CRLF checks passed. The operational comparison remains a deterministic local fixture, not production evidence.

## Mock LLM world-pipeline integration test (2026-07-12)

Use `tests/integration/test_mock_world_scenario.py` when changing Orchestrator ticks, Agent turn handling, ActionAgent tools, timers, matured events, anchored inventory, or structured commitments.

The scripted scenario intentionally uses the production orchestration path:

1. a deterministic random event wakes an OfficialAgent;
2. the OfficialAgent queries world state, submits `request_action`, and finishes its turn;
3. `request_action` bypasses Router and starts ActionAgent in the background;
4. ActionAgent queries the target, spends the executor's inventory immediately, creates a delayed target event, and calls `complete_action`;
5. TimeSimulator replaces both LLM duration hints with authoritative rule results;
6. WorldState and TimerSystem are reopened from SQLite before delayed work fires;
7. the matured event applies its expected result atomically;
8. the receipt timer wakes the OfficialAgent and resolves the matching structured-memory commitment.

Run the focused pipeline gate with:

```powershell
python -m pytest -q -p no:cacheprovider tests/integration/test_mock_world_scenario.py tests/unit/test_turn_engine.py tests/unit/test_event_generator.py tests/unit/test_time_simulator.py tests/unit/test_world_commands.py tests/unit/test_generation_archive.py tests/unit/test_worldstate_time_api.py
```

Current focused result: **51 passed**. Current complete Python suite: **287 passed**; frontend tests, lint, build, Prompt contracts, trajectory replay, local scale comparison, and the deterministic operational-fixture comparison also pass.

Tick implementation invariant: never reuse the outer `TickResult` variable for timer, command, or matured-event loop items. A matured event must not change the response object later used for `in_transit`, token usage, memorials, generated events, or Agent errors.

## Generation-safe in-flight action development rules (2026-07-12)

Use `tests/integration/test_dlc_generation_inflight.py` whenever changing DLC reload/rollback, runtime bindings, ActionAgent, TimeSimulator publication, ongoing events, or generation archive restoration.

Required invariants:

1. Publish a new validated `TimeSimulator`; never mutate the engine currently visible to in-flight work.
2. Capture Prompt, client, runtime generation, and time-rule engine once at the start of an ActionAgent logical call.
3. Use the captured engine for every duration estimate in that call, including `add_ongoing_event`, `modify_ongoing_event`, and final `complete_action` adjudication.
4. Persist delayed work as resolved `end_time` plus `expected_result`. Reload, restart, and rollback must not recalculate either field from the currently active rules.
5. A restart may publish the registry's current content as the next monotonic generation. A rollback also creates a new monotonic generation and must never rewind dynamic world state.
6. Safe observability may report runtime generation, rule fingerprint, Rule ID, source, duration, and numeric components. Do not add Prompt/message/memory bodies, model/provider details, credentials, paths, command/Trace IDs, Agent IDs, payloads, or raw errors.

Focused gate:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_runtime_bindings.py tests/unit/test_dlc_runtime.py tests/unit/test_generation_archive.py tests/unit/test_time_simulator.py tests/unit/test_world_commands.py tests/integration/test_mock_world_scenario.py tests/integration/test_dlc_generation_inflight.py
```

Current focused result: **56 passed**. Current complete Python result: **289 passed**. Frontend tests/lint/build, Prompt contracts, deterministic trajectory replay, and the calibrated local 100-cycle scale comparison also pass.
## Live trajectory model-matrix workflow (2026-07-12)

The live matrix is intentionally separate from the default deterministic test gate. Never enable real provider calls in ordinary unit tests or untrusted pull requests.

### Files and task coverage

- Matrix definition: `tests/evals/daming_live_model_matrix.json`
- Controlled live scenarios: `tests/evals/daming_live_trajectory_scenarios.json`
- Runner: `multiagent_framework/trajectory_matrix.py`
- Focused tests: `tests/unit/test_trajectory_matrix.py`, `tests/unit/test_model_gateway.py`, and `tests/unit/test_trajectory_evaluation.py`

The repository suite covers `agent.turn`, `router.ambiguous`, `action.simulation`, `memory.compress`, and `tianji.chat`. Every comparison cell binds one explicit profile with strict fallback disabled.

### Generate a safe human-review template

This operation does not construct a gateway or require credentials:

```powershell
python -m multiagent_framework.trajectory_matrix tests/evals/daming_live_model_matrix.json `
  --review-template runtime/evals/daming-live-reviews.json `
  --template-only
```

Fill every target with a short safe reviewer identifier and all six numeric dimensions from 0 through 100. Do not add notes, model output, Prompt excerpts, URLs, paths, Agent/Trace/command IDs, or free-text evidence.

### Run the credentialed matrix

Provide credentials only through the protected runtime environment referenced by model configuration, then run:

```powershell
python -m multiagent_framework.trajectory_matrix tests/evals/daming_live_model_matrix.json `
  --reviews runtime/evals/daming-live-reviews.json `
  --output runtime/evals/daming-live-matrix.json `
  --policy-output runtime/evals/daming-live-policy-patch.json
```

Operational rules:

1. A profile comparison must use `strict_profile=True`; never permit task-policy fallback inside a measured cell.
2. Every profile must exist, support tool calling, and have its own credential available before the first call.
3. Keep tool executors deterministic or explicitly dry-run. Live evaluation must not mutate the production world.
4. Treat status `provisional` as evaluation evidence only. It is not a production recommendation.
5. Apply a generated policy patch only after independent review and change control. The runner never edits `config/models.json`.
6. Preserve report redaction. Do not add prompts, messages, responses, Provider URLs, credential metadata, paths, Agent IDs, Trace IDs, command/tool-call IDs, or raw exceptions to dataclasses or JSON output.
7. Exit `0` means reviewed approval, exit `1` means a valid regression or incomplete approval, and exit `2` means invalid input or unavailable credentials.

### Required regression commands

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_trajectory_matrix.py tests/unit/test_model_gateway.py tests/unit/test_trajectory_evaluation.py
python -m compileall -q main.py multiagent_framework routers daming_court
python -m pytest -q -p no:cacheprovider
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay --output runtime/evals/daming-core-replay.json
```

Verified on 2026-07-12: **51 focused tests**, **312 complete Python tests**, Prompt contracts **100%**, and deterministic trajectory replay **100%**. Credentialed live results are deliberately not claimed by this local verification.

## Review quorum, disagreement, and cohort workflow (2026-07-12)

The repository live matrix currently uses:

```json
{
  "cohorts": ["daming-core"],
  "minimum_reviewers_per_target": 2,
  "minimum_review_coverage": 1.0,
  "maximum_review_disagreement": 15
}
```

Operational requirements:

1. Each cell/scenario target must be scored by at least two distinct safe reviewer identifiers. Reusing the same reviewer for the same target is a schema error, not a second vote.
2. Coverage counts qualified targets, not individual review rows. One completed row in a two-reviewer matrix leaves that target uncovered.
3. The disagreement gate uses the maximum per-target population standard deviation of reviewer composite scores. A value above the configured threshold leaves the cell `provisional`; resolve the review discrepancy rather than increasing the threshold casually.
4. Every automated-passing profile for the task/cohort must reach `reviewed` before the ranking is approved. Do not review only the current leader.
5. Explicit cohort scenarios must declare matching `cohorts` labels. Missing matches are configuration failures. Do not combine `all` with explicit cohorts or duplicate labels.
6. Keep cohort labels abstract and safe. Do not use Agent IDs, user identifiers, paths, URLs, credentials, Trace IDs, command IDs, or other operational identifiers as cohort names.
7. Global advice appears in `task_policies`; cohort-specific advice appears only in `cohort_task_policies`. Promotion remains a separate reviewed configuration change.

The template-only command now generates **18 rows for 9 unique targets** because every target has two empty reviewer slots. Preserve both rows, assign different reviewer identifiers, and fill only the six numeric dimensions. Do not add free text or model transcript evidence.

Strict schema boundaries reject reviewer quorum outside 1 through 20, booleans/floats/strings in place of the integer quorum, non-finite or out-of-range disagreement thresholds, duplicate cohorts, `all` mixed with explicit cohorts, and malformed or duplicate scenario cohort labels.

Latest verified local gate on 2026-07-12: **69 focused tests**, **330 complete Python tests**, **6 frontend tests**, frontend lint/build passed, Prompt contracts **100%**, deterministic trajectory replay **100%**, and Diff check passed. This remains local toolchain evidence; it does not replace credentialed model runs or independent human review.

## Model-policy promotion gate workflow (2026-07-12)

Use the promotion gate only after the credentialed matrix has completed and its generated policy patch has been independently reviewed. The repository staging policy is defined in `tests/evals/daming_model_promotion_gate.json`.

```powershell
python -m multiagent_framework.model_promotion_gate `
  --config tests/evals/daming_model_promotion_gate.json `
  --matrix-report runtime/evals/daming-live-matrix.json `
  --policy-patch runtime/evals/daming-live-policy-patch.json `
  --scale-gate runtime/evals/staging-scale-gate.json `
  --operational-gate runtime/evals/staging-operational-gate.json `
  --output runtime/evals/staging-model-promotion.json
```

Promotion invariants:

1. The matrix report must be schema v1, `mode: live`, and match the configured matrix and suite IDs.
2. Repetition count, reviewer quorum, review coverage, and disagreement policy must be at least as strict as the promotion configuration.
3. Every matrix cell must pass every repetition, satisfy review coverage/disagreement limits, and be `reviewed`.
4. Ranking profiles must exactly match the corresponding cell profiles. Every required cohort/task target must exist, with no extra target unless explicitly allowed.
5. The policy patch must be byte-structure equivalent after JSON parsing to policy advice derived from the reviewed rankings. Never hand-edit the patch between matrix generation and gating.
6. Required scale and operational evidence must both have passed and must target the same environment as the promotion configuration. Local fixture evidence cannot satisfy the staging gate.
7. Exit `0` means the artifacts may proceed to external change control; it does not apply configuration. Exit `1` is a valid blocked decision. Exit `2` means malformed, inconsistent, cross-environment, policy-substituted, or unsafe evidence.
8. Preserve secret-free artifacts. Do not add Prompt/message/response/memory text, credentials, URLs, paths, Agent/Trace/command/tool-call IDs, or raw errors.

The output records canonical matrix/policy fingerprints so downstream CI can bind approval to the exact reviewed evidence. Deployment still needs a separate authenticated model-config preview/reload and rollback plan.

Latest verified local gate on 2026-07-12: **16 promotion tests**, **84 focused promotion/matrix/model/trajectory tests**, **346 complete Python tests**, **6 frontend tests**, frontend lint/build passed, Prompt contracts **100%**, deterministic trajectory replay **100%**, and Diff check passed.

## Applying an approved model promotion (2026-07-12)

Model promotion is a two-request, generation-bound administrative operation. Keep the admin API disabled by default and provide its token only through the protected runtime environment.

A mapping document binds safe evaluation cohorts to real DLC and Agent targets:

```json
{
  "schema_version": "1.0",
  "promotion_id": "promotion-2026-07-12",
  "expected_gateway_generation": 1,
  "expected_policy_fingerprint": "<64 lowercase hex characters>",
  "cohort_targets": {
    "daming-core": {
      "dlcs": ["daming_court"],
      "agents": [
        {"dlc_id": "daming_court", "agent_id": "cabinet"}
      ]
    }
  }
}
```

Operational sequence:

1. Submit the approved Promotion Gate artifact and mapping to `POST /api/debug/model-promotion/preview`.
2. Review the safe change counts, current/next generation, target counts, policy fingerprint, and returned `plan_fingerprint`.
3. Submit the same promotion and mapping plus `expected_plan_fingerprint` to `POST /api/debug/model-promotion/apply`.
4. Confirm that the returned generation advanced exactly once and that the persisted `config/models.json` equals the active Gateway configuration.
5. Observe the new generation before proceeding to a wider rollout. Do not reuse a preview after any Gateway reload, mapping change, Promotion artifact change, or external model-config edit.

The apply path deliberately rejects stale generations, unknown Profiles, unknown DLC/Agent targets, incomplete/overlapping cohort mappings, policy-fingerprint substitution, plan-fingerprint substitution, and source-file changes between preview and apply. A failed Gateway activation restores the previous file. Calls already in progress continue with their captured generation and client set; later calls use the new policies.

Scoped central model configuration uses:

```json
{
  "task_policies": {
    "agent.turn": ["fast_tools"]
  },
  "dlc_task_policies": {
    "daming_court": {
      "agent.turn": ["deep_sim", "fast_tools"]
    }
  },
  "agent_task_policies": {
    "daming_court": {
      "cabinet": {
        "agent.turn": ["deep_sim", "fast_tools"]
      }
    }
  }
}
```

Never place API keys in promotion artifacts or other committed/generated review files. Local Provider configuration uses plaintext `api_key` only in ignored `config/models.json`; promotion artifacts, mappings, previews, logs, and error responses must not contain Provider URLs, credentials, Prompt/message/memory text, filesystem paths, Agent/Trace/command/tool-call IDs, or raw exceptions.

### Version synchronization

`VERSION` is the canonical project version. For every release, update all of the following in the same change:

- `VERSION`
- `multiagent_framework/version.py`
- `frontend/package.json` and its lockfile
- the current-version line in `README.md`

FastAPI imports the backend version for OpenAPI metadata. Run:

```powershell
python -m pytest -q tests/unit/test_version_consistency.py
```

The complete local release gate for version `0.11.0` is **360 Python tests**, **9 frontend tests**, frontend lint/build, Python compile, Prompt contracts at **100%**, deterministic trajectory replay at **100%**, and `git diff --check`.


## DLC Studio promotion workbench (2026-07-12)

Open `http://localhost:3000/dlc.html`, select a DLC, and choose the **晋级工作台** tab. The panel is an operator client for the existing authenticated Promotion APIs; it does not weaken or bypass backend validation.

Recommended workflow:

1. Generate an approved Promotion Gate artifact from reviewed, same-environment evidence.
2. Import or paste the artifact. Local validation requires schema `1.0`, `approved: true`, `decision: approved`, a lowercase 64-character policy fingerprint, and at least one cohort policy.
3. Enter a safe Promotion ID, explicitly select any Agent targets, and generate the mapping. Every cohort is mapped to the current DLC; no Agent is included unless checked.
4. Enter the Admin Token in the password field. It remains only in React state and is sent only as `X-Admin-Token`. Never place it in Promotion or Mapping JSON.
5. Run Preview and inspect current/next Generation, global/DLC/Agent change counts, target counts, and fingerprints.
6. Apply only while the Preview is current. Editing either JSON document, changing the selected DLC, or observing a new Gateway Generation invalidates the plan and disables Apply.
7. After Apply, the workbench reloads the public model configuration and reports the activated Generation. Continue with operational observation before wider rollout.

The pure helpers live in `frontend/src/DLCStudioTools.js`; their Node tests cover approval/fingerprint validation, safe-ID rejection, explicit-Agent mapping, cohort expansion, and stale input fingerprints. The API contract test verifies the exact safe Preview field set and generic invalid-artifact error response.

Do not add local/session storage for the token. Do not render raw backend payloads or errors. Do not expose Provider URLs, environment-variable names, credentials, Prompt/message/memory bodies, filesystem paths, or Trace/command/tool-call identifiers.

## Phase checkpoint: encrypted model-generation observation and guided rollback (2026-07-12)

Status: **implemented and locally verified; credentialed staging observation remains pending**.

- `ModelGenerationArchiveStore` persists a bounded model-configuration history with AES-GCM authenticated encryption and HKDF-derived keys. Runtime credential values, Prompt/message/response/memory bodies, Authorization data, and other forbidden sensitive fields are rejected before archival.
- Gateway startup validates retained records, safely reports corrupt or undecryptable records by code, restores a matching monotonic generation across process restarts, and creates a new generation when the on-disk configuration changed externally.
- Every successful `replace_config()` records the activated configuration in memory and attempts encrypted persistence without exposing archive failures or interrupting an otherwise valid activation.
- `generation_status()` aggregates the latest redacted traces by `config_generation`: calls, successes, errors, fallback attempts, token totals, mean/max latency, and active-call count. It exposes only configuration fingerprints and structural counts.
- `ModelGenerationController` recomputes rollback plans, verifies optimistic current-generation and plan fingerprints, rejects unavailable/current sources and external file edits, atomically writes the retained configuration, restores the original file if activation fails, and always activates rollback content as the next generation.
- Authenticated administration endpoints now include `GET /api/debug/model-generations`, `POST /api/debug/model-config/rollback/preview`, and `POST /api/debug/model-config/rollback/apply`; errors are generic and payloads never include decrypted configuration.
- DLC Studio reuses its ephemeral Admin Token to refresh a per-generation observation table, select a retained source, invalidate stale previews, and execute a confirmed Preview/Apply rollback.

### Version identity and acceptance evidence

- Release identity is `0.12.0`, synchronized across canonical, backend, frontend, lockfile, FastAPI, and README metadata.
- Complete Python suite: **371 passed**; encrypted archive and generation-control focused suite: **11 passed**.
- Frontend: **11 tests passed**, lint passed, and production build passed.
- Python compile and `git diff --check` passed. Prompt contracts and deterministic trajectory replay both scored **100%**.
- Production use still requires credentialed staging promotion, protected operational observation, rollback rehearsal, independent review quorum, and environment-specific alert thresholds.


## Deterministic causal event chain development (v0.13.0)

Add rules to the bundle Content section `causal_event_rules` or use the DLC Studio **Causal Event Chains** tab. A minimal rule is:

```json
{
  "rule_id": "drought_to_shortage",
  "source_event_types": ["drought"],
  "child_event_type": "food_shortage",
  "target_agents": [],
  "delay_days": 10,
  "probability": 1,
  "severity_delta": 1,
  "initial_payload": {},
  "inherit_payload_keys": ["region"],
  "conditions": [
    {
      "kind": "anchored_inventory",
      "entity_id": "capital",
      "name": "grain",
      "operator": "lt",
      "value": 1000
    }
  ],
  "max_depth": 4,
  "max_fires_per_chain": 1,
  "priority": 0,
  "enabled": true
}
```

Development rules:

1. `rule_id`, event types, and explicit Agent targets must use safe IDs; IDs must be unique across all active DLCs.
2. An empty `target_agents` array inherits the source event targets. Only keys listed by `inherit_payload_keys` are copied; causal metadata keys are reserved and cannot be authored.
3. `probability` is deterministic for a rule/source/chain tuple. Do not use it as a cryptographic random source.
4. `anchored_inventory` compares base value (`quantity * anchor_rate`), not raw quantity. The referenced resource must be an asset. `metric` and `parameter` conditions must reference matching resource kinds.
5. Missing world facts fail closed. Keep rules explicit and avoid wildcard cycles unless depth and fire limits are deliberately tested.
6. A rule fire is counted when its child is scheduled. Reloading a DLC changes only future observations; already materialized children preserve committed semantics.
7. Public/admin responses must never add event payloads, target Agent IDs, entity/resource IDs, chain IDs, paths, credentials, Prompt/message/memory bodies, or raw exceptions.
8. Any schema/runtime change must update Registry, generation blueprint/rollback, persistence, Studio import/export/Diff, tests, `README.md`, `PROJECT_DOC.md`, `DEV_GUIDE.md`, `OPTIMIZATION_PLAN.md`, and the synchronized project version.

Focused verification:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_causal_events.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_runtime_persistor.py tests/unit/test_event_generator.py
cd frontend
npm test
npm run lint
npm run build
```

## Causal rule workbench development (v0.14.0)

Use the DLC Studio causal-rule tab for structured conditions and isolated previews. The backend endpoint is:

```text
POST /api/dlcs/{dlc_id}/causal-events/dry-run
```

Request shape:

```json
{
  "content": { "causal_event_rules": [] },
  "source_event": {
    "event_id": "studio_preview_source",
    "event_type": "shortage",
    "severity": 5,
    "target_agents": [],
    "initial_payload": {},
    "generated_at": 0
  },
  "horizon_days": 365,
  "max_events": 128
}
```

Development invariants:

1. Preview must use `DLCRegistry.normalize_content_document()` and must never call `save_content()`, publish, reload, or mutate the live causal scheduler.
2. Reuse `CausalEventRule` and `CausalEventEngine`; do not implement a second probability, delay, depth, fire-limit, or anchored-value semantics in the API layer.
3. Dry-run source payloads cannot contain causal metadata. Source severity must be an integer from 1 to 10, target IDs are bounded, the horizon is at most 3650 days, and the event limit is at most 256.
4. Static diagnostics must remain deterministic and secret-free. Do not add world runtime payloads, credentials, Prompt/message/memory bodies, Provider metadata, filesystem paths, or raw exceptions.
5. Structured conditions must filter resources by kind: `anchored_inventory -> asset`, `metric -> metric`, and `parameter -> parameter`. Every row requires an entity, resource/value, operator, and finite threshold.
6. Preview must include the current unsaved rule form through `buildCausalDryRunDraft()` without committing Studio history or changing package content.
7. Runtime behavior remains owned by `causal_events.py`; analysis code is advisory and isolated. A warning must never silently alter or disable a production rule.

Focused verification:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_causal_analysis.py tests/unit/test_causal_events.py tests/unit/test_dlc_api.py
python -m compileall -q main.py routers multiagent_framework tests
cd frontend
npm test
npm run lint
npm run build
```


## Transactional causal world-effect development (v0.15.0)

Author world mutations through `world_effects`; never place internal commands in `initial_payload`. Example:

```json
{
  "rule_id": "shortage_relief",
  "source_event_types": ["shortage"],
  "child_event_type": "relief_dispatched",
  "delay_days": 1,
  "probability": 1,
  "world_effects": [
    {
      "effect_type": "transfer_anchored_value",
      "from_entity_id": "warehouse",
      "from_resource": "grain",
      "to_entity_id": "treasury",
      "to_resource": "coin",
      "base_value": 100,
      "reason": "emergency relief"
    },
    {
      "effect_type": "create_ongoing_event",
      "entity_id": "warehouse",
      "event_type": "replenishment",
      "duration_days": 5,
      "description": "Restock grain",
      "expected_result": {
        "inventory_deltas": [{"resource": "grain", "delta": 20}]
      }
    }
  ]
}
```

Development invariants:

1. Supported effect types are exactly `adjust_resource`, `set_resource`, `update_status`, `create_ongoing_event`, and `transfer_anchored_value`. Keep authored effects at or below 32 and materialized commands at or below 64.
2. Materialization happens in `CausalEventEngine.observe()`. Do not defer anchor lookup, quantity conversion, event IDs, or event times until polling/execution; committed delayed semantics must survive reloads and restarts unchanged.
3. Execution must go through `execute_causal_world_commands()` and `WorldCommandBus.execute_batch(..., atomic=True)`. Never loop over effects with independent database transactions.
4. Stable command IDs are `causal:{event_id}:{index}`. Changing this format requires a migration and replay-compatibility tests.
5. Anchored transfers may reference only asset resources with finite positive anchor rates. Debit and credit must share the same authored base value, and their ledger `base_value_delta` sum must remain zero.
6. A failed effect batch must not wake Agents or feed the child into downstream causal rules. The Orchestrator may publish only bounded status/count/error-code metadata.
7. Ongoing-event expected inventory deltas may reference only assets. Creation is transactional; maturity remains owned by `WorldState.apply_matured_events()`.
8. Dry Run may mutate only `_SeedWorldFacts`. It must restore all projected facts after a rejected batch and must never write SQLite, package content, Studio history, or runtime scheduler state.
9. Studio selectors and client validation are convenience gates, not security boundaries. Registry and runtime normalization remain authoritative.
10. Any effect-schema change must update snapshots, Registry validation, analysis previews, Studio serialization/UI, tests, README, project docs, optimization plan, and synchronized version metadata.

Focused verification:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_causal_world_effects.py tests/unit/test_causal_events.py tests/unit/test_causal_analysis.py tests/unit/test_dlc_registry.py
python -m compileall -q main.py routers multiagent_framework tests
cd frontend
npm test
npm run lint
npm run build
```


## Anchored economy invariant development (v0.16.0)

The economy monitor is an audit surface over authoritative inventory and ledger state, not a second economy implementation. Keep mutation semantics in `WorldCommandBus` and computation in `WorldState.get_economic_invariants()`.

Definitions:

1. Current base value is the sum of each applicable asset inventory quantity multiplied by the resource catalog's current positive anchor rate.
2. Transfer debit and credit entries are identified by `metadata.transfer_side`. Their absolute base-value deltas are removed from gross negative/positive ledger flows before cumulative destruction/issuance is calculated.
3. New causal transfers must share one deterministic `transfer_id` across adjacent debit and credit plans. The ID is internal audit metadata and must never be exposed through public economy APIs or metrics.
4. Snapshots created before `0.16.0` may omit `transfer_id`. Preserve restore compatibility and pair those entries by `causal_event_id` plus the debit effect index; the following credit index belongs to the same legacy pair.
5. A transfer is conserved only when both sides exist and the absolute debit/credit mismatch is at most `1e-6`. Do not silently drop malformed/incomplete groups from mismatch reporting.
6. Rejected atomic batches must leave inventory, ledger, ongoing events, and downstream causal propagation unchanged. Never synthesize economic corrections outside the transaction.

Public surfaces:

- `GET /api/world-state/economy` returns invariant aggregates plus causal-effect health and simulation time. Register named static routes before `GET /api/world-state/{entity_id}`.
- `/api/metrics/json` and Prometheus may expose totals, rates, resource names, units, and anchor rates, but not entity/command/event/chain/transfer identifiers, Prompt or message bodies, model credentials, Provider connection metadata, filesystem paths, or raw exceptions.
- New signals should not be added to deployment alert calibration until same-environment baselines and safety caps are approved. Dashboard status is observational unless a separately reviewed alert threshold exists.
- `WorldStateView` must refresh economy data on initial load, manual request, and successful asset mutation; failures should remain bounded and must not break entity/inventory management.

Required verification for economy changes:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_economic_invariants.py tests/unit/test_causal_world_effects.py tests/unit/test_operational_metrics.py tests/unit/test_metrics_api.py tests/integration/test_causal_economic_scenario.py
python -m pytest -q -p no:cacheprovider
python -m compileall -q main.py routers multiagent_framework tests
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay
cd frontend
npm test
npm run lint
npm run build
```

Verified on 2026-07-13 for `0.16.0`: **434 Python tests**, **14 frontend tests**, Python compile, frontend lint/build, Prompt contracts **100%**, and deterministic trajectory replay **100%**.


## Reusable causal authoring development (v0.17.0)

The causal engine must remain unaware of Studio authoring conveniences. Keep reusable definitions in DLC content and materialize them through `multiagent_framework.causal_templates.expand_causal_rule_document()` before calling `CausalEventRule.from_mapping()`.

Rules for contributors:

1. Treat `causal_condition_groups` and `causal_rule_templates` as package-local authoring collections. Do not resolve references across DLC packages.
2. Preserve precedence: engine defaults `<` `template.rule` `<` authored rule. Preserve condition order: template conditions, each `condition_group_ids` entry in authored order, then inline `conditions`.
3. Do not add recursive references. Template `rule` objects cannot contain `template_id` or `condition_group_ids`; the first version intentionally stays single-level and cycle-free.
4. The expansion helper must be pure, deterministic, deep-copy mutable values, and strip authoring-only reference keys from final runtime rules.
5. Registry validation, `causal_analysis.py`, draft Dry Run, and `DLCBundleLoader` must use the same helper. Never implement a second backend merge path.
6. Pending causal events are immutable materialized commitments. Template edits may affect future generation fingerprints and future source events, but must not rewrite queued events or restored scheduler snapshots.
7. Frontend `expandCausalRuleDraft()` is for responsive author feedback only. Backend validation/execution remains authoritative. Include both new collections in normalization, export/import, keyed Diff, undo/redo history, counts, and raw content.
8. Keep template/group IDs stable and safe (`1-64` letters, numbers, dot, underscore, or hyphen). Final active causal `rule_id` uniqueness remains global across materialized packages.

Focused verification:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_causal_templates.py tests/unit/test_causal_analysis.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_dlc_api.py
cd frontend
npm test
npm run lint
npm run build
```

Verified on 2026-07-13 for `0.17.0`: **446 Python tests** and **15 frontend tests** before final repository-wide gates.

## Causal graph replay development workflow (v0.18.0)

### Authoring and inspection flow

1. Open DLC Studio and select a causal rule.
2. Run **Analyze and preview**. The request uses `buildCausalDryRunDraft()` so the selected rule and other unsaved causal authoring data are analyzed without saving the package.
3. Inspect the resolved node strip and edge table. Filter by event type, rule ID, template ID, or condition-group ID.
4. Export the generated `agentera-causal-replay-v1` fixture.
5. Import the same fixture to verify a match, or change rules/seed facts and import it again to inspect drift.

The resolved-edge inspector is a table/workbench, not a graph-layout canvas. Do not describe it as ReactFlow visualization. Edge provenance is derived from authored references, while routing semantics and fingerprints are derived from the shared expansion/runtime model.

### Replay API

```http
POST /api/dlcs/{dlc_id}/causal-events/replay
Content-Type: application/json

{
  "content": { "...": "optional current unsaved Studio draft" },
  "fixture": { "format": "agentera-causal-replay-v1", "...": "..." }
}
```

If `content` is omitted, replay uses saved package content. If supplied, Registry normalization is applied in memory. The operation must remain read-only: never call save, publish, reload, rollback, generation activation, or world mutation from this endpoint.

### Fixture invariants

- Keep `format` and `schema_version` explicit.
- Keep source-event bounds aligned with `dry_run_causal_rules()`.
- Keep the result digest projection deterministic and secret-free.
- Never include `replay_fixture` inside its own digest projection.
- Never add full DLC content, Prompt text, API credentials, provider configuration, or raw exceptions to exported evidence.
- Treat the replay report's fixture as a fresh baseline for current content; imported evidence is comparison input only and is not persisted.
- Preserve the distinction between rule drift (`analysis_fingerprint`) and seed/execution drift (`result_digest`).

### Required verification

```powershell
python -m compileall -q main.py routers multiagent_framework tests
python -m pytest -q -p no:cacheprovider
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay

cd frontend
npm test
npm run lint
npm run build
cd ..

git -c safe.directory='F:/世界模拟器' diff --check
```

Verified for `0.18.0`: **451 Python tests**, **16 frontend tests**, Prompt contracts **100%**, trajectory replay **100%**, compile/lint/build/diff gates passed.

## Agent economic tool and causal replay workflow (v0.19.0)

### ActionAgent 工具选择

| 业务动作 | 必须使用 | 禁止做法 |
|---|---|---|
| 生产、采集、征收资产 | `produce_resource` | 用任意正 `quantity_delta` 模拟发行 |
| 消耗、损失、销毁资产 | `consume_resource` | 余额不足时静默 clamp 到零 |
| 跨实体拨款、调粮、兑换 | `transfer_anchored_value` | 用两次独立库存更新拼接转移 |
| 延迟效果 | `add_ongoing_event` | 在行动完成前假装未来结果已落账 |

贡献者必须保持以下约束：

1. `quantity`/`base_value` 只接受有限正数；拒绝零、负数、`NaN`、`Infinity` 和 bool。
2. 三个经济工具只接受 DLC 资源目录中 `kind == asset` 的资源。
3. `transfer_anchored_value` 的 API 输入是统一基础价值，不是双方库存数量。换算公式为 `debit = -(base_value / from_anchor)`、`credit = base_value / to_anchor`。
4. 借贷命令必须使用原子 `execute_batch`；任何余额或写入失败都不得留下半笔交易。
5. command ID、transfer ID 和 tool-call receipt 必须确定性稳定。相同 tool call 重放不能重复写库存、ledger 或 `changes_made`。
6. 旧 `update_inventory` 只保留给 `ALL_TOOLS` 兼容调用方，不得重新加入 `ACTION_AGENT_TOOLS`。

### 显式 ActionResult 因果事件

只有业务确实需要把行动结果作为后续因果源时，`request_action` 才设置 `causal_event_type`。省略字段必须保持旧行为。不要改成“所有 ActionResult 自动发布”，否则旧 DLC 的通配规则、循环和重放语义会被扩大。

`build_action_causal_event()` 的 payload 必须保持有界和脱敏。允许行动身份、成功状态、耗时和经济汇总；禁止 Prompt、FC 参数、完整 `fc_trace`、完整变化对象、结果叙事、Provider/API 配置、凭据和原始异常。新增字段时必须补充秘密隔离与确定性测试。

### 场景与门禁

经济/因果改动至少运行：

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_agent_economic_tools.py tests/integration/test_agent_economic_replay_scenario.py tests/integration/test_mock_world_scenario.py
python -m compileall -q multiagent_framework routers tests
python -m pytest -q -p no:cacheprovider
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay

cd frontend
npm test
npm run lint
npm run build
cd ..

git -c safe.directory='F:/世界模拟器' diff --check
```

长场景必须断言：多轮 FC 顺序、发行/销毁价值、转移净值为零、余额不足原子回滚、SQLite/因果快照恢复、ongoing event 成熟、完整 action replay 幂等，以及 Replay Fixture 对规则漂移与种子/执行漂移的不同分类。

Verified for `0.19.0`: **464 Python tests**, **16 frontend tests**, Prompt contracts **100%**, trajectory replay **100%**, compile/lint/build/diff gates passed.

## DLC gameplay contract development workflow (v0.20.0)

### Authoring schema

Add the optional `gameplay` object to the primary DLC's `content.json`:

```json
{
  "gameplay": {
    "player": {
      "identity_id": "station_commander",
      "display_name": "Station Commander",
      "actor_id": "commander_actor",
      "controlled_objects": [
        {"kind": "entity", "id": "orbital_station", "mode": "direct"},
        {"kind": "agent", "id": "operations_ai", "mode": "direct"},
        {"kind": "agent", "id": "science_ai", "mode": "command"}
      ]
    }
  }
}
```

Authoring rules:

1. Keep `identity_id` stable across display-name changes and localization updates.
2. Use `actor_id` for the world actor associated with the player; it is optional in v0.20.0 and is not yet a general authorization principal.
3. Every controlled object must reference an entity or Agent declared in the same normalized DLC content.
4. `kind` is exactly `entity` or `agent`; `mode` is exactly `direct` or `command`.
5. Do not duplicate the same `kind + id` pair. A player contract must control at least one object.
6. Only the primary DLC becomes the active player contract. Do not rely on gameplay metadata in a secondary package to override it.

### Direct versus command Agent semantics

- `agent/direct`: the record remains available as an official/actor node and can receive player-targeted output, but `AgentFactory` must not create an autonomous LLM Agent for it.
- `agent/command`: the Agent remains autonomous and may also be addressed through future player-command workflows.
- `entity/direct`: declares a directly controlled world entity. v0.20.0 exposed the declaration; v0.21.0 uses it as an authorization principal for DLC-authored generic actions.

Never use `role == "player"` to reinterpret an Agent when an explicit gameplay contract exists. `AgentFactory.create(..., legacy_role_player=False)` is required in both initial package load and active-generation materialization for explicit contracts.

### Compatibility and migration

Existing DLCs can omit `gameplay`. `GameplayContract.from_content()` then selects the first enabled legacy Agent with `role == "player"`, creates a direct Agent control object, and reports `source = legacy_role`. Explicit contracts report `source = explicit` and always win over legacy roles.

Keep the normalized content schema at `1.0` for this optional additive field. Do not force unrelated historical bundles through a schema migration solely to adopt v0.20.0. When an old generation archive lacks gameplay data, restore derives a compatibility contract from archived officials.

### Runtime invariants

Gameplay data must move with the atomic DLC generation:

- preview reports `gameplay_changed`;
- load/reload/rollback install the contract with the target generation;
- snapshot clone and generation archive blueprint retain the contract;
- rollback restores old gameplay content as a new monotonically increasing generation;
- `public_runtime()["gameplay"]` and `Orchestrator.gameplay_public_state()` expose the same active contract;
- directly controlled Agent IDs never appear in the autonomous runtime Agent map.

If adding fields, update `to_dict()`, `from_dict()`, normalization, validation, archive compatibility, public API tests, reload/rollback tests, and frontend fallback behavior together.

### API and frontend rules

`GET /api/gameplay` is the theme-neutral client contract. Keep it safe and stable: package identity, source, configured state, player identity, controlled objects, and active generation are allowed. Do not expose Prompt text, memories, model/provider configuration, credentials, filesystem paths, or raw archive payloads.

The main React UI must retain a safe fallback when the endpoint is unavailable, but new labels should prefer `package_name` and `player.display_name`. Avoid adding new “皇帝”, “大明”, “奏折”, or other theme assumptions to generic shell components.

### Required verification

```powershell
python -m py_compile multiagent_framework/gameplay_contract.py multiagent_framework/dlc_registry.py multiagent_framework/dlc_runtime.py multiagent_framework/orchestrator.py routers/simulation.py main.py
python -m pytest -q -p no:cacheprovider tests/unit/test_gameplay_contract.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py
python -m compileall -q multiagent_framework routers tests
python -m pytest -q -p no:cacheprovider
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay

cd frontend
npm test
npm run lint
npm run build
cd ..

git -c safe.directory='F:/世界模拟器' diff --check
```

The focused gameplay/Registry/Runtime suite for v0.20.0 contains 68 tests. Also inspect the real bundled packages: `daming_court` must produce a valid `legacy_role` contract, while `example_frontier` must validate its explicit non-imperial contract without errors.

Verified for `0.20.0`: **472 Python tests**, **16 frontend tests**, Prompt contracts **100%**, trajectory replay **100%**, compile/lint/build/diff gates passed.


## Generic DLC action development workflow (v0.21.0)

### Files and ownership

- `multiagent_framework/action_contract.py`: action schema parsing, parameter normalization, authorization, preconditions, anchored costs, duration, cooldown, result compilation, idempotency, and snapshots.
- `multiagent_framework/dlc_registry.py`: authoring-time syntax and cross-reference validation.
- `multiagent_framework/dlc_runtime.py`: active-package aggregation, duplicate detection, Generation archive/restore, and publication order.
- `routers/simulation.py`: stable catalog, preview, and execute API.
- `frontend/src/DLCContentEditors.jsx`: Actions editor; `frontend/src/DLCStudioTools.js`: normalization, import/export, history, and keyed diff.

### Authoring rules

1. Add actions to `content.json.actions`; keep the content schema version at `1.0` because the field is additive and defaults to an empty array.
2. Use safe, package-stable `action_id` and parameter names. An action ID must be unique across all simultaneously active DLC packages.
3. Declare `permissions.control_modes`, `control_kinds`, optional `control_ids`, and optional `actor_ids`. These must agree with the active primary DLC GameplayContract.
4. Declare asset costs in `base_value`, never in client-computed quantity. Runtime reads the resource catalog and calculates `quantity = base_value / anchor_rate`.
5. Use `$actor`, `$control`, `$target`, `$duration_days`, and `$param.<name>` only in supported template fields. Registry validation rejects malformed or unknown parameter references.
6. Use `TimeSimulator` rules for authoritative duration. An action's duration object supplies workload/severity/resource/distance inputs but does not bypass the selected rule.
7. Prefer `composite` when an action must combine world mutations, an ongoing event, an Agent wakeup, and/or a public source event.

### Runtime invariants

- Preview is side-effect free and may omit `invocation_id`; execute requires a non-empty client-generated `invocation_id`.
- Send `expected_generation` from the catalog response. A stale client receives `action.generation_conflict` instead of executing against a changed action definition.
- Never charge costs in callbacks. Costs and all world-command results must remain in the single atomic batch. Wake/event callbacks run only after commit and callback failure is reported without pretending the committed world transaction rolled back.
- Do not clear ActionCatalog receipts or cooldowns during configure/reload. They represent already committed gameplay history.
- The catalog uses an `RLock`; keep receipt lookup, preview, world commit, cooldown update, and receipt write in the serialized execute path to prevent concurrent duplicate charges.
- A failed atomic batch must leave both costs and result changes unapplied. Test this with a validly compiled command that violates a reducer constraint at commit time.

### API request

```json
{
  "invocation_id": "client-generated-stable-id",
  "expected_generation": 3,
  "actor_id": "",
  "control_object_id": "frontier_station",
  "target_id": "frontier_station",
  "parameters": {"alert_gain": 8}
}
```

Use `POST /api/actions/{action_id}/preview` before confirmation, then reuse the normalized choices with a fresh invocation ID for `execute`. Treat 409 as a refresh/reconcile path for generation, invocation, cooldown, or world-command conflicts.

### Required verification

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_action_contract.py tests/unit/test_action_api.py
python -m pytest -q -p no:cacheprovider tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_generation_archive.py tests/unit/test_dlc_api.py tests/unit/test_gameplay_contract.py
python -m compileall -q multiagent_framework routers main.py

Push-Location frontend
npm test -- --run
npm run lint
npm run build
Pop-Location
```

Also validate the real `example_frontier` package and execute its action against a temporary `WorldState` when changing effect materialization, anchored cost semantics, or persistence ordering.

## Generic Issue and visual settings development workflow (v0.22.0)

### Adding or changing a runtime setting

A runtime setting is not complete until the typed config, Chinese schema, runtime activation, visual editor, compatibility behavior, and tests change together:

1. Add the field to the appropriate dataclass in `multiagent_framework/runtime_settings.py` and define a conservative default.
2. Parse and bound the field in `RuntimeSettings.from_dict`; keep absent-field behavior compatible with older files.
3. Add Chinese `label`, `description`, input kind, range/options, unit, and operational warning to `settings_schema()`.
4. Wire the value into initial construction in `main.py` and hot activation in `routers/settings._apply_runtime_settings`.
5. Add the control to the matching `SettingsCenter` section. Do not duplicate bounds in ad-hoc frontend save logic when schema metadata can drive them.
6. Update `config/runtime.example.json`, README, `PROJECT_DOC.md`, this guide, and the optimization checkpoint when the field changes an operator-facing contract.
7. Add tests for minimum/maximum values, missing legacy values, validation, successful hot update, stale generation, and activation rollback.

`config/runtime.json` currently has four sections: `simulation`, `agent_defaults`, `action_agent`, and `memory`. Do not move model Provider/Profile/router data into this file; it stays in `config/models.json`.

### Settings API payload and two-stage save

Validation and save accept the same logical request:

```json
{
  "expected_generation": 12,
  "models": {
    "providers": {},
    "models": {},
    "task_policies": {},
    "dlc_task_policies": {},
    "agent_task_policies": {}
  },
  "runtime": {
    "simulation": {"seconds_per_day": 120, "tick_interval": 0.25},
    "agent_defaults": {},
    "action_agent": {},
    "memory": {}
  },
  "runtime_keys": {"primary": "temporary-key"},
  "clear_runtime_keys": [],
  "allow_secret_host_change": false
}
```

Development clients must call `POST /api/settings/validate` first, present its normalized document, warnings, and diff to the operator, then call `POST /api/settings` with the same generation and confirmed content. Never bypass the second server validation or rely only on frontend validation.

Security rules are mandatory:

- Provider API keys are allowed only in the local settings response and ignored local `config/models.json`. Never add them to public response models, Diffs, logs, exception details, committed fixtures, `runtime.json`, localStorage, sessionStorage, traces, evaluations, or archives.
- `runtime_keys` are write-only request data held in backend process memory. A GET response may expose only whether a key is configured.
- New Provider saves use plaintext `api_key`; `api_key_env` is migration-only. Environment status endpoints for admin/metrics/archive secrets still return booleans only.
- If a configured Provider's Base URL changes host, validation must warn and save must require `allow_secret_host_change=true`.
- The Advanced JSON editor may edit only `providers`, `models`, `task_policies`, `dlc_task_policies`, and `agent_task_policies`; strip read-only metadata such as `api_key_configured` before serialization.
- Preserve unknown model fields when applying known-section edits. Visual configuration must not become a lossy migration tool.

### Developing Issue types and options

- Add Issue schemas to `content.json.issue_types`; IDs must be stable and package-local references must resolve during Registry validation.
- Source restrictions, required evidence, urgency, deadlines, Case creation, and options belong in the Issue type, not in theme-specific router branches.
- Options that execute gameplay use existing generic `action_id` definitions. Parameter mappings must be deterministic and previewable; do not allow a client or LLM to inject an authoritative completion time or bypass Action permissions/costs.
- External producers must provide a stable `external_id` when retries are possible. Decision resolution must provide a stable `invocation_id`. Same-ID/same-payload is a replay; same-ID/different-payload is a conflict.
- Deadline transitions use authoritative simulation time. Do not use wall-clock time for Issue expiration.
- New lifecycle fields must be included in `IssueManager.snapshot/restore`, manual saves, runtime persistence, generation archives where applicable, and legacy migration tests.
- Keep `/api/memorials` as compatibility-only. New UI and DLC code should use `/api/issues`, `/api/cases`, and `/api/decision-requests`.
- DLC Studio edits must participate in validation, import/export, diff, history, and unsaved-draft behavior, with a frontend tool test for normalization.

### Required release gate

Run from the repository root:

```powershell
python -m pytest -q -p no:cacheprovider
python -m compileall -q multiagent_framework routers main.py

Push-Location frontend
npm test
npm run lint
npm run build
Pop-Location

git -c safe.directory='F:/世界模拟器' diff --check
```

Settings-focused work must additionally cover secret non-disclosure, Provider host-change confirmation, unknown-field preservation, generation conflicts, successful hot activation, and full three-file rollback when activation fails. Issue-focused work must cover Registry validation, dynamic options, Action preview/resolve, idempotency conflicts, deadlines, snapshot/manual-save restore, and Generation reload/archive behavior.


## DLC objective and ending authoring workflow (v0.23.0)

### Authoring rules

Add top-level `objectives` and `endings` arrays to the **primary** DLC's `content.json`. Keep IDs stable and ASCII-safe. Dependency DLC objective catalogs are validated but do not own the active Campaign. Omitting both arrays is valid sandbox mode and must not be replaced with a fake always-complete objective.

A minimal Campaign shape is:

```json
{
  "objectives": [
    {
      "objective_id": "secure_frontier",
      "display_name": "稳固边境防线",
      "description": "在期限前达到安全警戒线。",
      "category": "primary",
      "required": true,
      "milestone_mode": "ordered",
      "milestones": [
        {
          "milestone_id": "fund_patrols",
          "display_name": "准备巡逻经费",
          "description": "锚定价值至少为 50。",
          "condition": {
            "kind": "anchored_inventory",
            "entity_id": "边境哨站",
            "name": "边境银券",
            "operator": "gte",
            "value": 50
          }
        }
      ],
      "completion": {
        "kind": "metric",
        "entity_id": "边境哨站",
        "name": "警戒度",
        "operator": "gte",
        "value": 85
      },
      "enabled": true,
      "order": 0
    }
  ],
  "endings": [
    {
      "ending_id": "frontier_safe",
      "display_name": "边境安定",
      "description": "防线已稳定。",
      "outcome": "victory",
      "trigger": {"objective_ids": ["secure_frontier"], "operator": "all"},
      "priority": 50,
      "enabled": true
    }
  ]
}
```

Objective categories are `primary`, `optional`, and `failure`. Milestone modes are `ordered` and `independent`. Ending outcomes are `victory`, `defeat`, and `neutral`; ending trigger operators are `all` and `any`. Higher ending priority wins a same-Tick conflict, then lexical `ending_id` is the deterministic tie-breaker.

### Condition DSL

- Compose conditions with `{"op":"all|any|not","conditions":[...]}`; `not` requires exactly one child.
- Numeric operators are `lt`, `lte`, `eq`, `ne`, `gte`, `gt`. `entity_status` supports `eq` and `ne`.
- `anchored_inventory`, `metric`, and `parameter` require `entity_id` and resource/value `name`. Anchored inventory compares authoritative base value, not raw quantity.
- `sim_day` reads simulation time. `issue_count`, `case_count`, and `decision_count` may filter by `issue_type_id` and `status`. `action_count` requires `action_id` and may filter by actor, control object, and target.
- Do not add a client mutation endpoint for progress. New fact kinds must read durable framework state, provide exact validation paths, participate in snapshot/restore tests, and remain deterministic under reload and rollback.

### Studio and release workflow

1. Open DLC Studio and use the “目标” or “结局” tab. Prefer reference selectors and the visual condition tree; use condition-level or whole-document advanced JSON only for structures not yet exposed visually.
2. Validate the draft before publish. Fix the exact returned path for unknown entities/resources/Actions/Issue types/objective IDs and resource-kind mismatches.
3. Check Raw JSON, keyed Diff and history behavior. Publishing must include objectives/endings in the same atomic Content write and Generation archive as the rest of the DLC.
4. Exercise sandbox, ordered and independent milestones, failure conditions, disabled definitions, simultaneous endings, hot reload, rollback, runtime snapshot, manual save and restart recovery.
5. Verify `GET /api/objectives`, `GET /api/endings`, the main Objective panel, and WebSocket `objective_transitions` without hard-coded theme terminology.

The optional external Provider smoke test is `tests/integration/test_external_api_connection.py`. Supply `AGENTERA_TEST_BASE_URL` and `AGENTERA_TEST_API_KEY` only through a temporary process environment; `AGENTERA_TEST_MODEL`, `AGENTERA_TEST_TIMEOUT_S`, and `AGENTERA_TEST_MODEL_ATTEMPTS` are optional. The test must skip when URL/key are absent and must never print or persist credentials or raw provider responses.


## DLC UI Manifest authoring workflow (v0.24.0)

### Ownership and file location

Add an optional `ui_manifest` object to the primary DLC's `content.json`. Do not place campaign UI overrides in dependency DLCs: the loader intentionally ignores dependency ownership for this single-valued section. An omitted manifest means the framework's theme-neutral default.

### Use capabilities, not frontend implementation details

- Choose only documented `navigation[].view`, `dashboard.layout`, `dashboard.widgets[].kind/region`, `issue_page` fields, and `entity_detail` fields/sections.
- Never add URLs, `href`, React component names, HTML, CSS, or scripts. New executable UI behavior must first be implemented and reviewed as a framework capability, then added to both backend and frontend allowlists.
- Keep `nav_id` and `widget_id` stable and unique. At least one visible main navigation item and one visible shell navigation item are required.
- Treat authored labels and icons as short plain text. Backend validation is authoritative; frontend normalization is defense in depth, not a replacement for Registry validation.

### Studio workflow

1. Open DLC Studio and select the “UI Manifest（界面清单）” section.
2. Configure title/brand and the Chinese-described terminology fields.
3. Select fixed main/shell destinations, layout, widgets, Issue fields, and entity sections; use order and visibility rather than custom routes or components.
4. Resolve local validation errors. Use advanced JSON only for bulk editing and inspect its parse errors/security notice.
5. Review Content Diff, publish the DLC, then verify `GET /api/ui-manifest` and the gameplay shell after the new Generation activates.
6. Test hot reload and rollback when changing navigation or field visibility. Historical generations must restore their archived manifest.

### Adding a new framework UI capability

Update `multiagent_framework/ui_manifest.py` and `frontend/src/UIManifestTools.js` in the same change, then implement a fixed renderer/route mapping and expose it in `UIManifestEditor.jsx`. Add backend rejection/default/Generation tests plus frontend normalization/rendering/editor tests. Do not accept arbitrary component names as an extension mechanism.

### Required release gate

Run the complete Python suite and compile gate, all frontend tests, ESLint, production build, example DLC Registry validation, `git diff --check`, and a credential-pattern scan. The external OpenAI-compatible Smoke Test remains environment-gated and receives credentials only through `AGENTERA_TEST_*` process environment variables.


## DLC capability registry authoring workflow (v0.25.0)

### Authoritative location and example

能力写在 DLC 的 `content.json` 顶层 `capabilities` 数组中。玩家、普通 Agent 和 Router 必须引用同一个稳定 `capability_id`：

```json
{
  "capabilities": [
    {
      "capability_id": "launch_frontier_patrol",
      "display_name": "发起边境巡逻",
      "description": "选择边境哨站，通过通用行动契约执行巡逻。",
      "aliases": ["patrol_frontier"],
      "handler": "contract_action",
      "audiences": ["player"],
      "target": {
        "kind": "entity",
        "mode": "required",
        "allowed_ids": ["边境哨站"]
      },
      "parameters": [
        {
          "name": "alert_gain",
          "type": "number",
          "display_name": "警戒提升",
          "description": "巡逻完成后计划提升的警戒度，范围 1 到 20。",
          "required": false,
          "default": 8,
          "minimum": 1,
          "maximum": 20
        }
      ],
      "action_id": "frontier_patrol",
      "default_urgency": "urgent",
      "input": {
        "content_required": false,
        "content_label": "巡逻备注",
        "content_placeholder": "可选：补充本次巡逻重点。"
      },
      "enabled": true
    }
  ]
}
```

### Handler constraints

- `noop`：不需要目标或 Action 引用，适合等待、确认和纯记录能力。
- `message`：向单个 Agent 发消息；目标必须可用，参数用于模板化结构化内容。
- `broadcast`：向多个 Agent 广播；所有目标先校验，再整体入队。
- `world_action`：把经过能力契约校验的内容、目标和参数交给框架 `execute_policy` 世界行动链，不能在 DLC 中填写 Python 函数名。
- `contract_action`：顶层 `action_id` 必须引用同一当前 Generation 中可执行的 DLC Action。

任何未知 handler、代码实现名、URL、HTML/脚本或不安全字段都会被 Registry 拒绝。新增框架 handler 时必须先扩展后端白名单、执行器、Schema、API 脱敏、前端编辑器和完整测试，不能只改 DLC。

### Audiences, targets and parameters

- `audiences` 可包含 `player`、`agent`、`router`。玩家面板只显示 `player`，Agent 工具只包含 `agent`，Router 只路由 `router`。
- 当 Agent 需要 Router LLM 对自然语言目标做消歧时，应同时声明 `agent` 和 `router`；只声明 `agent` 表示调用者必须提供确定目标。
- `target.kind` 可选 `none`、`agent`、`entity`；`target.mode` 可选 `none`、`optional`、`required`、`multiple`，`allowed_ids` 用于限制合法目标。
- `parameters` 必须有稳定 ID、中文名称、中文说明、类型和 required 约束。不要把秘密、Provider Key 或实现细节放入默认值和说明。
- `aliases` 仅用于兼容输入，包内和所有已发布包之间必须唯一，也不能与另一能力 ID 冲突。

### Studio workflow

1. 打开 DLC Studio，选择“能力”页签。
2. 使用“新增能力”创建条目，逐项阅读中文说明并选择受众、handler、目标和参数。
3. `contract_action` 从当前草稿 Action 中选择引用；保存前处理所有即时校验错误。
4. 使用 Content Diff 检查能力 ID、目标、参数和 Action 引用变化；需要批量编辑时再使用高级 JSON。
5. Dry Run/保存后执行 Registry 校验，再进行 reload；确认玩家面板、Agent 工具 Schema 和 Router 路由均切换到同一 Generation。

### Required validation and release gate

```powershell
python -m pytest -q tests/unit/test_capability_contract.py tests/unit/test_capability_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_generation_archive.py tests/unit/test_router.py tests/unit/test_turn_engine.py
python -m pytest -q
python -m compileall multiagent_framework routers
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; print(DLCRegistry(Path('.')).validate('example_frontier'))"
```

可选外部 API 连通测试只读取 `AGENTERA_TEST_BASE_URL`、`AGENTERA_TEST_API_KEY`、`AGENTERA_TEST_MODEL`、`AGENTERA_TEST_TIMEOUT_S` 和 `AGENTERA_TEST_MODEL_ATTEMPTS`。未注入凭据时测试必须 skip；严禁把 URL 或 Key 写入命令、源码、日志、配置和提交。


## Generic Actor development workflow (v0.26.0)

### Authoritative schema and compatibility

Define reusable roles in `content.json.actors`; bind an LLM Agent with `agents[].actor_id`. Do not duplicate mutable role state inside Agent configuration. An omitted binding keeps legacy behavior by projecting a same-ID Actor, while an explicit unknown Actor ID fails Registry validation. Actor-only definitions are supported.

`actor_id`, component-local IDs and references must be stable package data, not translated display labels. Use `display_name` and `description` for localized text. Actor components are:

- `attributes`: bounded scalar values used by rules and presentation;
- `traits`: stable `trait_id`, intensity/value, tags and optional description;
- `needs`: stable `need_id`, current/target/min/max values and optional rate;
- `goals`: stable `goal_id`, priority, progress, status and optional deadline;
- `capability_ids`: references to enabled DLC capability IDs;
- `identities`: named identities/titles with optional entity and validity interval;
- `memberships`: one record per organization, with role, status and weight;
- `states`: bounded JSON-safe runtime state;
- `metadata`: authoring extensions that are persisted but not injected into prompts.

### Runtime invariants

1. Actor definitions are immutable within one Generation; only the `ActorManager` runtime overlay may change.
2. Reload, rollback and archive recovery must publish Actor, Agent, capability and prompt mappings from the same Generation snapshot.
3. Preserved state must be revalidated against the new Actor Schema. Never carry a need, goal or state value across reload merely because the field name matches.
4. Actor public state and persistence payloads must remain secret-free. Provider URLs, API keys and model-profile credentials never belong in Actor attributes, states or metadata.
5. Keep the compatibility projection to `Official/CourtGraph` isolated. New generic behavior should read Actor components rather than adding more court-specific fields.

### Prompt and API rules

The allowlisted prompt variables are `actor`, `actor_id`, `actor_type`, `actor_entity_id`, `actor_attributes`, `actor_traits`, `actor_needs`, `actor_goals`, `actor_capabilities`, `actor_identities`, `actor_memberships` and `actor_states`. They are serialized as bounded safe JSON. Do not add `actor_metadata` without a separate threat-model and disclosure review.

Use `GET /api/actors` for the catalog/runtime summary and `GET /api/actors/{actor_id}` for details. A missing Actor returns 404; an unavailable runtime returns 503. Do not expose internal model bindings through either endpoint.

### DLC Studio workflow

1. Open “Actor / 角色组件” and create the Actor before adding an explicit Agent binding.
2. Fill stable IDs first, then localized display names and component values. Every field includes Chinese guidance.
3. Use the Agent editor to select `actor_id`; leave it empty only when intentionally relying on legacy same-ID projection.
4. Add Actor prompt variables only where the Agent needs them. Prefer the smallest component set and avoid dumping the entire Actor document.
5. Review Content Diff, run Registry validation, then reload. Verify Actor-only entries, Agent-bound entries and state preservation separately.

### Required verification

```powershell
python -m pytest -q tests/unit/test_actor_contract.py tests/unit/test_actor_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_generation_archive.py
python -m pytest -q
python -m compileall multiagent_framework routers
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; print(DLCRegistry(Path('.')).validate('example_frontier'))"
git diff --check
```

The optional external API smoke test remains process-environment only. It pulls `/v1/models`, auto-selects bounded chat candidates and tests `/v1/chat/completions`; it must skip when the required environment variables are absent and must not print provider response bodies or persist secrets.

## Scrollable viewport-shell contract (2026-07-15)

When a page uses a fixed desktop shell (`height: 100vh; overflow: hidden`), apply the following rules:

1. Every flex/grid ancestor between the shell and the intended scroller must have `min-height: 0`.
2. Put `overflow-y: auto` on the element that actually owns the long information, not only on the page body.
3. Use `overflow: hidden` only for structural/card clipping. Do not include a long-form content column in a grouped selector that forces hidden overflow.
4. Prefer `overscroll-behavior: contain`, `scrollbar-gutter: stable`, and `touch-action: pan-y` on independent desktop columns.
5. Sticky controls inside a scroller need an opaque background and a positive `z-index` so content cannot paint over them.
6. At mobile breakpoints, either restore document flow (`height: auto; overflow: visible`) or keep an explicit bounded inner scroller; never leave a fixed-height, hidden-overflow column without a scroll owner.

Current reference implementations are `.editor-right` in `frontend/src/editor.css` and `.tj-tools-panel` in `frontend/src/tianji.css`. For manual regression checks, load real DLC data, select a registry node with a long detail form, expand all TianJi tool categories, then verify mouse-wheel/touch scrolling and confirm the bottom-most content is reachable at both desktop and mobile widths.

## DLC relationship contract authoring workflow (v0.27.0)

### Relationship type schema

在 `content.json.relationship_types` 中声明关系类型。`relationship_type_id` 是存档、API 和跨版本引用使用的稳定 ID，不要使用会随翻译变化的显示文本。推荐字段：

- `display_name` / `description`：面向作者与玩家的中文名称和说明；
- `direction`：`directed` 表示 source → target，`undirected` 表示双方对等；无向边会规范化端点顺序，禁止依赖输入顺序表达含义；
- `minimum_strength`、`maximum_strength`、`default_strength`：有限数值且默认值必须落在区间内；
- `default_visibility`：`public`、`limited`、`private` 或 `secret`；这是数据语义，不代表 API 自动完成身份鉴权，调用层仍需执行可见性策略；
- `allow_expiry`：为 false 时边不得填写 `expires_at`；
- `dimensions`：每项使用稳定 `dimension_id`、名称、说明、最小/最大/默认值；边上的 `values` 只能引用已声明维度并满足范围；
- `style`：建议使用 `label`、CSS 颜色 `color`、布尔 `dashed` 和 `marker`（如 `arrow`、`closed`、`none`）；它只控制安全展示，不允许脚本或组件名；
- `metadata`：仅存放 JSON-safe 作者扩展信息，不要放 Provider、模型凭据、Prompt、文件路径或可执行配置。

框架始终提供 `subordinate` 和 `affiliated` 两个 legacy 类型。新 DLC 可继续直接引用，也可用同 ID 覆盖展示/约束；其他自定义 ID 在全部启用 DLC 之间必须唯一。

### Relation edge schema

关系边至少填写 `source_entity_id`、`target_entity_id` 和 `relationship_type_id`，可选填写：

- 关系边使用 `(parent_id, child_id, rel_type)` 复合键；同一复合键再次写入表示覆盖更新；
- `strength`：省略时采用类型默认值；
- `visibility`：必须是四种白名单值之一；
- `starts_at` / `expires_at`：使用世界时间数值，要求 `expires_at > starts_at`；当前时间早于开始或不早于到期时边为 inactive；
- `values`：关系类型定义的多维数值映射；
- `note`：简短叙事说明；
- `metadata`：有界 JSON-safe 扩展数据。

不要在业务代码里按 `subordinate`/`affiliated` 写分支。需要方向、样式、范围或维度时，从当前 Generation 的 `RelationshipCatalog` 查询。对无向边执行查询、更新和删除时同样交给 Catalog 规范化端点。

### DLC Studio workflow

1. 在“关系类型”页签先创建类型，填写稳定 ID、中文说明、方向、强度、公开性、到期规则、维度和样式。
2. 再进入关系边编辑器，从当前草稿目录选择类型并填写强度、时间窗口、维度、备注和 Metadata。
3. 删除类型前先删除或迁移所有引用边；Studio 会提示引用位置，但最终仍以 Registry 校验为准。
4. 使用 Content Diff、撤销/重做和导入导出检查 keyed collection 变化；批量修改时才使用高级 JSON。
5. 保存后执行 Registry validate，再 reload；同时检查关系类型 API、关系列表和主界面动态图例是否属于同一 Generation。

### Runtime and migration rules

关系目录是 Generation-bound 权威数据。新增运行时入口必须通过 `WorldState`/`RelationshipCatalog`，不能绕过校验直接写 SQLite。Schema 迁移必须保留旧边；archive、rollback 和 restart recovery 必须先恢复目录再恢复边。修改类型范围、方向或维度时，应准备旧边兼容/迁移方案，不能假设热重载会静默接受不再合法的数据。

### Required verification

```powershell
python -m pytest -q tests/unit/test_relationship_contract.py tests/unit/test_relationship_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_generation_archive.py tests/unit/test_worldstate_time_api.py
python -m pytest -q
python -m compileall multiagent_framework routers
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

外部 API 连通测试只允许从 `AGENTERA_TEST_BASE_URL`、`AGENTERA_TEST_API_KEY`、`AGENTERA_TEST_MODEL`、`AGENTERA_TEST_TIMEOUT_S` 和 `AGENTERA_TEST_MODEL_ATTEMPTS` 读取临时配置；未注入时必须 skip。禁止把实际 URL、Key 或响应原文写入源码、命令记录、日志、文档和提交。
## Generic causal-condition authoring workflow (v0.28.0)

### Authoritative schema and shared evaluator

通用因果条件只允许通过 `multiagent_framework/causal_conditions.py` 扩展。新增来源或操作符时必须同时维护：

1. 后端 `FACT_KINDS` / `OPERATORS`、`CausalCondition.validate()`、`resolve_condition_value()` 与比较语义；
2. Registry 跨引用和资源 kind 校验；
3. `_SeedWorldFacts` 的 Dry Run 投影；
4. `frontend/src/CausalConditionTools.js` 的中文目录、默认值、序列化和测试；
5. `CausalConditionBuilder` 的动态字段与中文帮助；
6. Runtime、Dry Run 和 Orchestrator 的 focused 回归测试。

禁止在 `causal_events.py`、`causal_analysis.py` 或 React 编辑器里复制第二套条件求值逻辑。生产与预览必须构造 `CausalConditionContext` 并调用共享求值器。

### Condition shape and bounds

每个条件至少包含 `kind`、`operator`，除 `exists/not_exists` 外还必须包含 `value`。引用字段按来源使用：

- `entity_id`：实体、Actor、目标或历史目标；
- `target_entity_id`：关系另一端；
- `name`：资源/指标/参数/标签/Actor 组件/关系类型/事件类型；
- `field`：实体字段、成员字段、关系字段、历史字段、目标公开字段或聚合来源；
- `aggregate`：`sum`、`avg`、`min`、`max`、`count`；
- `window_days`：`0..36500` 的历史窗口；
- `filters`：最多 16 项的 JSON-safe 对象。

条件文本最多 512 字符，列表最多 64 项，规则或条件组最多 32 条。Studio 编辑态 `_value_type`、`_value_text`、`_filters_text` 等字段不得进入 `content.json`。

### Source semantics

- `entity_field` 使用 `field` 或 `name`；`metadata.xxx` 只读取单层 Metadata 键。`entity_tag` 返回布尔值，`entity_state` 读取 `current_status`。
- `actor_attribute` / `actor_state` 读取 Actor runtime；`actor_trait` 检查 `trait_id`；`actor_membership` 用 `name=organization_id`，可通过 `field` 读取 `role/status/weight` 等成员字段。
- `relationship` 必须提供两端和关系类型；无向类型允许反向端点匹配。字段可使用 `strength`、`active`、`values.xxx`、`metadata.xxx` 或其他安全公开字段。
- `ongoing_event` 默认返回数量，`field=exists|active` 返回布尔，`field=remaining_days` 返回最短剩余天数。`event_history` 默认返回数量，支持 `exists|seen`、`last_generated_at` 和 `window_days`。
- `objective_progress` 只读取 `ObjectiveManager.public_state()`。`player_knowledge` 只访问显式 Provider；没有 Provider 时为 missing，不能改成读取 WorldState 的便利回退。
- `aggregate_metric.field` 只能是 `entity_count`、`anchored_inventory`、`metric`、`parameter`、`actor_attribute`、`relation`、`ongoing_event`。实体过滤器支持 `entity_type/status/tag`，Actor 支持 `actor_type`，关系支持 `active/value_field`。

### History and snapshot invariants

事件历史必须保持有界和脱敏：不得保存 source Payload、Prompt、消息正文、模型配置、凭据或原始异常。更改历史形状时必须升级并迁移 causal snapshot Schema，同时验证大小、时间、ID、目标列表和 JSON 形状。旧快照必须安全恢复为空历史，而不是从日志猜测或回放过去事件。

### DLC Studio workflow

1. 优先从实体、Actor、资源、关系和目标下拉框选择稳定 ID；仅在兼容旧内容时手动输入。
2. 根据条件实际值选择数值、文本、布尔或 JSON 列表；`in/not_in` 通常使用列表目标值。
3. 关系和聚合字段使用中文说明中的白名单；Filter JSON 只写来源支持的键。
4. 条件组至少一条，模板/规则可组合条件组与 inline 条件；保存前统一调用 `serializeCausalConditions()`。
5. 用未保存草稿 Dry Run 验证多阶段链，确认后续节点能够观察前一节点的世界效果，但不会写入 live SQLite、运行时 scheduler 或包文件。

### Required verification

```powershell
python -m pytest -q tests/unit/test_causal_events.py tests/unit/test_causal_analysis.py tests/unit/test_causal_templates.py tests/unit/test_causal_world_effects.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_objective_orchestrator.py tests/unit/test_turn_engine.py tests/integration/test_causal_economic_scenario.py tests/integration/test_agent_economic_replay_scenario.py
python -m pytest -q
python -m compileall -q multiagent_framework main.py
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; assert not DLCRegistry(Path('.')).validate('example_frontier')"
cd frontend
npm test
npm run lint
npm run build
```

外部 API 连通测试只能读取 `AGENTERA_TEST_BASE_URL`、`AGENTERA_TEST_API_KEY`、`AGENTERA_TEST_MODEL`、`AGENTERA_TEST_TIMEOUT_S` 与 `AGENTERA_TEST_MODEL_ATTEMPTS`。未注入时必须 skip；不得把实际 URL、Key、模型响应或连接错误原文写入命令、测试、文档、日志或 Git。

## 通用因果效果开发流程（v0.29.0）

### 权威边界

1. 所有效果 Schema、边界和物化逻辑统一维护在 `multiagent_framework/gameplay_effects.py`；不得在 `causal_events.py`、`causal_analysis.py`、Orchestrator 或 React 中复制第二套效果解释器。
2. `GameplayEffectRuntime` 只保存题材无关的动态权威状态：Agent 启停、Actor/Membership、Issue、Knowledge、Objective、Action Unlock 和 Phase。WorldState SQLite 与该 Runtime 必须在同一因果批次中共同提交或共同恢复。
3. Manager 是投影视图，不是因果效果的第二权威来源。先执行 World Command，再投影到 Agent/Actor/Issue/Objective/Action Manager；投影失败必须导致整批事务回滚。
4. 每个子事件的命令 ID 固定为 `causal:{event_id}:{command_index}`。不要改成随机 UUID、时间戳或基于描述文本的 ID，否则重启恢复和重复提交会失去幂等性。

### DLC 编写规则

- 单条规则最多 32 个 authored effects，物化后最多 64 条命令。需要更长流程时拆为多个有界因果阶段。
- 先写 `create_entity`，同一规则后续效果即可引用该 ID；Registry 按顺序扩展临时实体目录。不得引用后面才创建的实体，也不得重复创建现有 ID。
- `create_issue` 必须引用当前 DLC 的 Issue Type，并使用其允许的来源 kind/id；`advance_objective`、`unlock_action`、关系类型、Actor、Agent、资源和 Scope 均必须通过 Registry 跨引用。
- `reveal_information` 写入玩家知识层，`switch_phase` 写入当前阶段；阶段条件只使用 `field=phase_id` 或 `field=metadata.xxx`。
- `create_ongoing_event.expected_result` 使用正式成熟结果字段，例如 `new_status` 和 `inventory_deltas`，不要继续写旧的任意 `status` 键。
- 每个效果的 `reason` 是安全审计说明，不应包含 Prompt、模型消息、凭据、Provider 地址或原始异常。

### DLC Studio 工作流

1. 在“因果事件规则”中添加效果，先根据六个中文分类选择类型，再按字段帮助选择实体、Agent、Actor、关系、Issue、Objective、Action 和 Scope。
2. JSON 字段必须是对应的对象、数组或任意安全 JSON 值；保存时统一调用 `serializeWorldEffects()`，不得把 `_metadata_text`、`_source_text` 等编辑态字段写入包。
3. 同规则创建实体后，确认后续实体引用控件已经出现新 ID；保存并重新打开，检查 roundtrip 后效果顺序不变。
4. 使用未保存草稿 Dry Run 验证命令数量、原子回滚、下游条件和重复事件幂等，再执行 Registry validate 与 Generation reload。
5. 条件编辑器当前支持 18 种来源；`phase` 字段只允许 `phase_id` 或 `metadata.xxx`，Studio 与 Runtime 均应覆盖测试。

### 必跑验证

```powershell
python -m pytest -q tests/unit/test_causal_generic_effects.py tests/unit/test_causal_analysis.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_objective_orchestrator.py tests/unit/test_turn_engine.py
python -m pytest -q
python -m compileall -q multiagent_framework main.py
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; assert not DLCRegistry(Path('.')).validate('example_frontier')"
cd frontend
npm test
npm run lint
npm run build
```

外部 API 连通测试仍只读取 `AGENTERA_TEST_BASE_URL`、`AGENTERA_TEST_API_KEY`、`AGENTERA_TEST_MODEL`、`AGENTERA_TEST_TIMEOUT_S` 与 `AGENTERA_TEST_MODEL_ATTEMPTS`。不得把值、模型响应或连接错误原文写入命令、源码、文档、日志或 Git。


## Phase checkpoint: three-layer knowledge and investigation (v0.30.0, 2026-07-15)

### 选择知识层

1. `world_truth` 只用于世界模拟器权威事实，不设置 holder，也不得直接拼接到 Agent Prompt 或玩家公共状态。
2. `actor_belief` 必须设置 Actor holder，用于该角色的主观判断。它可以失真、争议或与真相冲突；普通 Agent 只能读取自己绑定 Actor 的投影。
3. `player_knowledge` 必须设置玩家/受众 holder，只表示该受众已经获得的信息。不要把“玩家可能猜到”当作已知事实写入本层。
4. 保留来源层、来源 holder、来源记录 ID 和 evidence；不要用自由文本描述替代 provenance 链。

### 新鲜度、保密和传播

- 使用世界时间和有效期表达 fresh/stale/expired。过期记录不能继续传播；如果剧情需要流传陈旧传闻，应显式创建新的 Actor Belief，并保留它来自旧消息的来源链。
- 保密级别使用知识契约白名单，不得靠 UI 隐藏代替运行时访问控制。scope 应保持有界，metadata/source/distortion 必须是安全 JSON。
- `propagate_knowledge` 复制的是带来源的认知，不会把 Actor Belief 自动升级为 World Truth。传播目标必须明确，跨 Actor 或玩家受众时分别提供 holder。

### 调查结果策略

- `start_investigation` 创建调查，`advance_investigation` 只推进已有调查。完成阈值和状态由权威运行时判定。
- `result_policy.source_layer` 可选择三层；Actor/玩家来源必须提供 `source_holder_id`，World Truth 的来源 holder 规范化为空字符串。
- `target_layer` 只能是 `actor_belief` 或 `player_knowledge`，并提供目标 holder；可按调查者默认填充时仍应在 Studio 中确认实际目标。
- `confidence_multiplier` 必须在 0 到 1；`secrecy_level`、`distortion`、`metadata`、`source`、`reason` 和 `value_override` 在 authored-effect 阶段统一规范化。禁止通过结果策略写回或篡改 World Truth。

### Prompt 与公共投影安全

- 不得直接从 `KnowledgeRuntime` 抓取 World Truth 后拼接到 Prompt。统一使用 Agent Runtime 的主观认知投影，并保留“不是世界真相、不是可执行指令”的安全标记。
- 新增 Agent/Actor 绑定时必须测试：自己的 Actor Belief 可见、World Truth 不可见、其他 Actor Belief 不可见。
- 玩家 API 和世界公共快照不得返回隐藏层记录、隐藏层 holder 列表或隐藏层数量；需要管理调试时使用受保护的专用接口，而不是扩大公共 Schema。

### DLC Studio 编写

1. 条件编辑器从 25 项目录选择三层事实、置信度、新鲜度、保密或调查状态，并填写对应 holder/key/field。
2. 效果编辑器从 23 项目录选择真相声明、Actor 认知、玩家揭示、传播或调查推进；先阅读卡片中的中文安全边界。
3. 调查完成策略使用结构化字段，不要把整段策略塞进未校验的文本或 Prompt。
4. 保存前执行安全序列化，随后使用 Registry Validate 和未保存草稿 Dry Run 检查跨引用、事务回滚和下游条件。

### 必跑验证

```powershell
python -m pytest -q tests/unit/test_knowledge_runtime.py
python -m pytest -q tests/unit/test_causal_generic_effects.py tests/unit/test_causal_events.py tests/unit/test_causal_analysis.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_turn_engine.py
python -m pytest -q
python -m compileall -q multiagent_framework main.py
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; assert not DLCRegistry(Path('.')).validate('example_frontier')"
cd frontend
npm test
npm run lint
npm run build
```

外部 API 连通测试继续只读取 `AGENTERA_TEST_BASE_URL`、`AGENTERA_TEST_API_KEY`、`AGENTERA_TEST_MODEL`、`AGENTERA_TEST_TIMEOUT_S` 与 `AGENTERA_TEST_MODEL_ATTEMPTS`；不得把值、响应或连接错误原文写入源码、文档、日志或 Git。

## UI 中文编码约束（2026-07-15）

- 前端源码统一使用 UTF-8。新增 JSX 中文文案应优先直接写可读中文，例如 `<div>当前状态</div>`，不要把 JSXText 写成 `<div>\u5f53\u524d...</div>`。
- JSX 属性中的中文也应优先直接使用 UTF-8；如确需 Unicode 转义，必须放在 JavaScript 表达式中，例如 `placeholder={'\u8bf7\u8f93\u5165'}`，不要写成会按字面显示反斜杠的属性文本。
- JavaScript 字符串可以使用 `\uXXXX`，但可读的产品文案仍优先使用 UTF-8 中文；JSXText 不会自动把字面量转义序列转换成汉字。
- 修改 UI 文案后必须运行 `npm test`、`npm run lint` 和 `npm run build`，并扫描新增行中的替换字符、常见乱码片段及误显示的 `\uXXXX`。

## Phase checkpoint: DLC derived metrics and aggregate formulas (v0.31.0, 2026-07-15)

### 在 DLC 中声明派生指标

在任意 DLC 的 `content.json` 顶层添加 `derived_metrics` 数组。`metric_id` 是目标、规则、UI、Agent Prompt 和存档/Generation 引用使用的稳定 ID；发布后不要仅因显示文案变化而修改它。下面是合法的最小组合示例：

```json
{
  "derived_metrics": [
    {
      "metric_id": "frontier_readiness",
      "display_name": "边境综合战备度",
      "description": "组合警戒度、Actor 属性和统一锚定库存价值。",
      "unit": "%",
      "precision": 1,
      "minimum": 0,
      "maximum": 100,
      "enabled": true,
      "audiences": ["player", "ui", "agent", "rule", "objective"],
      "missing_policy": "fail",
      "formula": {
        "op": "round",
        "precision": 1,
        "value": {
          "op": "clamp",
          "min": 0,
          "max": 100,
          "value": {
            "op": "weighted_sum",
            "terms": [
              {
                "weight": 0.55,
                "value": {"source": "metric", "entity_id": "边境哨站", "name": "警戒度"}
              },
              {
                "weight": 0.25,
                "value": {"source": "actor_attribute", "entity_id": "frontier_guard", "name": "vigilance"}
              },
              {
                "weight": 0.20,
                "value": {
                  "op": "clamp", "min": 0, "max": 100,
                  "value": {"source": "anchored_inventory", "entity_id": "边境哨站", "name": "边境银券"}
                }
              }
            ]
          }
        }
      },
      "metadata": {"category": "readiness"}
    }
  ]
}
```

`formula` 可使用显式 `op`，也接受三种简写：`{"constant": 1}`、`{"derived_metric": "metric_id"}`、`{"source": "sim_day"}`。Studio 保存时会规范化为统一结构。

### 17 类公式节点

| `op` | 字段 | 语义 |
|---|---|---|
| `constant` | `value` | 有限数值常量 |
| `source` | `source` 与来源字段 | 直接读取单个世界值 |
| `derived_metric` | `metric_id` | 引用同一已解析 Catalog 中的另一指标 |
| `aggregate` | `source`, `aggregate`, 可选引用/过滤字段 | 聚合一组世界值 |
| `add` / `sum` | `values[]` | 子项求和 |
| `subtract` | `left`, `right` | 左值减右值 |
| `multiply` | `values[]` | 子项连乘 |
| `divide` | `left`, `right` | 左值除右值；除零返回稳定错误 |
| `min` / `max` / `avg` | `values[]` | 子项最小值、最大值或平均值 |
| `weighted_sum` | `terms[].weight`, `terms[].value` | 各项乘权重后求和；框架不会自动归一化权重 |
| `clamp` | `min`, `max`, `value` | 把结果限制在闭区间内 |
| `round` | `precision`, `value` | 四舍五入到 0-8 位小数 |
| `coalesce` | `values[]` | 返回首个不是“缺失”的子项；不吞掉除零等公式错误 |

每个 Catalog 最多 128 项；每棵公式最多 12 层、256 个节点，每个多子项节点最多 64 项。数值必须有限。跨指标依赖必须存在且不得形成自引用或循环。

### 数据来源与聚合

直接 `source` 支持：

- `anchored_inventory`：必须提供 `entity_id` 与资源 `name`，读取该实体对应库存的 `base_value` 总和，而不是原始数量；
- `metric` / `parameter`：必须提供 `entity_id` 与值 `name`，读取 `entity_values` 的数值；
- `actor_attribute`：必须提供 Actor `entity_id` 与属性 `name`；
- `sim_day`：读取当前模拟天数，不需要引用字段。

`aggregate` 支持 `entity_count`、`anchored_inventory`、`metric`、`parameter`、`actor_attribute`、`relation`、`ongoing_event`，并选择 `sum`、`avg`、`min`、`max` 或 `count`：

- 实体类来源可用 `filters.entity_type`、`filters.status`、`filters.tag`；可选 `entity_id` 进一步限定单一实体。
- Actor 属性可用 `filters.actor_type`；可选 `entity_id` 限定 Actor。
- 关系聚合中 `entity_id` 对应 `parent_id`，`target_entity_id` 对应 `child_id`，`name` 对应关系类型；`filters.active` 限制有效边，`filters.value_field` 指定数值路径，默认 `strength`。
- 持续事件用可选 `entity_id` 限定所属实体，用 `name` 限定事件类型。
- `count` 对空集合返回 `0`；其他聚合对空集合视为缺失。高级 `filters` 最多 16 项且值只能是安全 JSON 标量。

### 受众、缺失策略与消费端

- `player`：允许安全玩家 API 返回；
- `ui`：允许框架 UI 消费；
- `agent`：允许进入 Agent 每轮只读指标摘要；
- `rule`：允许因果条件 `kind: "derived_metric"` 求值；
- `objective`：允许 Objective 条件 `kind: "derived_metric"` 求值。

受众是运行时权限边界。不要只给 `player` 后再期待 Rule 或 Objective 能读取。`missing_policy: "fail"` 会保留 missing 状态；`"zero"` 只在来源缺失时回退为零，不会掩盖除零、非有限结果或非法公式。定义级 `minimum`、`maximum` 与 `precision` 在公式完成后统一应用。

Objective 示例：

```json
{
  "kind": "derived_metric",
  "name": "frontier_readiness",
  "operator": "gte",
  "value": 85
}
```

因果条件使用同样的 `kind/name/operator/value` 语义，但指标必须含 `rule` 受众。Agent 只收到含显示名、稳定 ID、值和单位的确定性摘要，不会收到公式或 Metadata。

### UI Manifest 与安全 API

Dashboard 可加入框架拥有的派生指标组件：

```json
{
  "widget_id": "readiness_metrics",
  "kind": "derived_metrics",
  "region": "top",
  "order": 5,
  "visible": true
}
```

`GET /api/derived-metrics` 返回当前 Generation 中授权给 `player` 的指标集合；`GET /api/derived-metrics/{metric_id}` 只返回单项安全结果。不要建立绕过 Catalog 的调试接口来公开公式、Metadata、隐藏受众指标或底层异常。

### 设置中心的 Prompt 预算

“多轮记忆”页包含四项热更新配置：

- `agent_defaults.knowledge_context_max_records`：每轮最多注入的当前 Agent 可见知识条数，范围 1-256；
- `agent_defaults.knowledge_context_char_budget`：知识/信念摘要字符预算，范围 128-200000；
- `agent_defaults.derived_metric_context_max_records`：每轮最多注入的 `agent` 受众指标条数，范围 0-64，设为 0 关闭；
- `agent_defaults.derived_metric_context_char_budget`：派生指标摘要字符预算，范围 0-16000，设为 0 关闭。

条数和字符预算同时生效，超出后按 Catalog 稳定顺序停止追加。增大预算会增加 Token 成本；不要通过预算扩大来绕过知识/指标受众隔离。

### DLC Studio 工作流

1. 在“派生指标”页新增稳定 ID，填写显示字段、单位、范围、精度、受众和缺失策略。
2. 使用可视化公式树选择 17 类节点；通过当前草稿 Catalog 选择实体、Actor、资源、Entity Value、关系类型、事件和已有派生指标。
3. 保存前处理自引用、缺失依赖、全图循环、重复 ID、非法来源字段、非有限值、Clamp 范围、Round 精度以及深度/节点上限错误。
4. 使用 Registry Validate 与未保存草稿 Dry Run 验证真实世界引用和运行值，再发布 Generation。
5. 热重载、归档恢复或回滚后，检查 API、Objective、Rule、Agent 和 UI 都报告同一 Generation。

### 必跑验证

```powershell
python -m pytest -q tests/unit/test_derived_metrics.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py tests/unit/test_causal_analysis.py tests/unit/test_objective_contract.py tests/unit/test_agent_runtime.py tests/unit/test_derived_metric_api.py
python -m pytest -q
python -m compileall multiagent_framework routers main.py
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; assert not DLCRegistry(Path('.').resolve()).validate('example_frontier')"
cd frontend
npm test
npm run lint
npm run build
```

外部 API 连通测试继续只读取 `AGENTERA_TEST_BASE_URL`、`AGENTERA_TEST_API_KEY`、`AGENTERA_TEST_MODEL`、`AGENTERA_TEST_TIMEOUT_S` 与 `AGENTERA_TEST_MODEL_ATTEMPTS`；未注入 URL/Key 时必须 skip。不得把实际值、响应正文、Provider 原始异常或连接地址写入源码、配置、文档、命令记录、日志或 Git。


## Developing local Provider settings and connection diagnostics (v0.31.1)

1. Provider configuration fields are `base_url`, plaintext `api_key`, `timeout_s`, and `retry_delay_s`. Keep `api_key_env` support only in loaders that migrate older local files; do not emit it from new saves or the settings schema.
2. Use `GatewayConfig.to_persisted_dict()` only for the ignored local models file. Use `to_dict()` for fingerprints, archives, previews that cross trust boundaries, and promotion/control metadata.
3. Settings saves must pass the complete non-empty key map to `ModelGateway.replace_config()`. This is important: an omitted map means generation-control operations preserve current credentials, while an explicit empty map intentionally clears credentials. `reload_from_file()` derives an explicit map from disk so clearing a persisted key is honored.
4. Keep save ordering transactional: snapshot files, atomically write models/runtime/time, replace Gateway clients, apply runtime values, and restore all files and runtime values if activation fails.
5. Provider diagnostics probe `/models` with the unsaved draft. Add tests with `httpx.MockTransport`; never make unit tests depend on a real service or real key. Bound response excerpts and redact the submitted key before returning any detail.
6. Regression tests must separately verify the intentional local disclosure and the still-secret public surfaces: settings GET returns the key, while `public_config()`, traces, logs, evaluations, model generation archives, and DLC exports do not.
7. A Provider wrapper error such as HTTP 400 with `Console Go: Upstream request failed` is not, by itself, proof of an invalid request body. Preserve a bounded Provider error summary and inspect the content-free request diagnostics (`message_roles`, tool names/count, payload bytes, and tool-call/result ID mismatches) before changing schemas.
8. Keep retry responsibilities separate: `ModelProfile.max_retries` controls how many additional attempts occur; `ProviderConfig.retry_delay_s` controls the fixed cooldown before each additional attempt. With defaults, one Profile can make three requests separated by two 5-second waits.

## DLC 时间系统开发指南 (v0.32.0)

### 最小配置

`content.json` 可增加：

```json
{
  "time_system": {
    "enabled": true,
    "calendar": {
      "calendar_id": "campaign_calendar",
      "display_name": "远征历",
      "epoch_label": "远征纪元",
      "start_year": 1,
      "start_month_id": "month_1",
      "start_day": 1,
      "weekdays": ["一", "二", "三", "四", "五", "六", "日"],
      "start_weekday": "一",
      "months": [{"month_id": "month_1", "display_name": "启程月", "days": 30}]
    },
    "seasons": [],
    "cycles": [{
      "cycle_id": "command_turn",
      "display_name": "指挥回合",
      "kind": "turn",
      "period_days": 7,
      "duration_days": 7,
      "offset_days": 0,
      "enabled": true
    }],
    "boundary_events": [],
    "auto_pause_conditions": []
  }
}
```

### Authoring rules

1. Month, season, cycle, boundary-event and pause IDs must be stable ASCII identifiers. Month lengths are positive integers; the sum defines `year_days`. The start month/day/weekday must exist in the same calendar.
2. Seasons use inclusive day-of-year bounds. A season with `start_day > end_day` crosses the year boundary. Do not overlap seasons; use Studio validation before Registry validation.
3. Cycle `duration_days` must be between 1 and `period_days`. `offset_days` is the first start day index. Keep gameplay cadence in DLC data instead of adding topic branches to Orchestrator.
4. Boundary events may filter by cycle, season, month or day and may wake known Agents or project a known Issue Type. Payload must remain safe JSON and should carry narrative data, not instructions for executing code.
5. Condition pauses use the common causal condition shape. A derived metric pause requires the metric to grant the `rule` audience. Boundary pauses may leave `conditions` empty. Use `edge_triggered` for thresholds that should fire only when transitioning from false to true, and `cooldown_days` to suppress rapid retriggers.

### Runtime and UI checklist

- Reload the active Generation after editing and inspect `GET /api/time-system`; never read raw DLC time definitions directly from feature components.
- Preserve `time_system_runtime` in saves. Do not reset receipts during ordinary hot reload; only same-ID receipts are eligible for preservation.
- Public UI code must use the safe projection and must not copy authored condition trees. Closing an auto-pause banner is not equivalent to calling `POST /api/resume`.
- Add or update `TemporalSystemTools.test.js` for authoring behavior and `TemporalStateTools.test.js` for public formatting. Backend contract/runtime changes require `test_temporal_contract.py`, `test_temporal_runtime.py` and Generation integration coverage.
- `frontend/dist/`, `runtime/` and `.pytest_cache/` are generated/runtime artifacts and must not be committed.

## 开发 Provider 共享队列与 Token/队列面板（v0.32.0）

### 配置字段

Provider 配置新增并持久化以下字段：

```json
{
  "max_concurrent": 2,
  "queue_interval_s": 1.0
}
```

- `max_concurrent`：该 Provider 下所有 Profile 合计允许同时进行的 HTTP 请求数，范围 `1..128`。第三方或限流不明确的接口建议从 `1–2` 开始。
- `queue_interval_s`：同一 Provider 相邻 HTTP 请求开始之间的最小秒数，范围 `0..3600`；`0` 表示不主动停顿，保守起点为 `0.5–1` 秒。
- `ModelProfile.max_concurrent` 仍然保留，但只作为单 Profile 二级上限。不要把它误认为 Provider 总并发。
- `ModelProfile.max_retries` 决定额外尝试次数；每次重试都是新的 HTTP 尝试，必须重新加入 Provider 队列。`retry_delay_s` 是重试前冷却，`queue_interval_s` 是请求开始节流，两者会同时生效。

### 实现规则

1. 一个 `ModelGateway` generation 必须为每个 Provider 创建且只创建一个 `APIRequestQueue`，并注入该 Provider 的全部 `LLMClient`。
2. semaphore 槽位只覆盖排队后实际 HTTP 尝试；排队取消必须清理请求记录并计入 `cancelled`，不得泄漏活动槽位。
3. 派发间隔以请求“开始”时间计算。等待间隔的请求应显示为 `spacing`，普通等待显示为 `queued`，已占用上游请求显示为 `active`。
4. 热更新先预建新队列和新客户端，再原子切换 generation。保留旧的两参数 `_create_clients(config, runtime_keys)` 测试/定制工厂兼容入口，不能因新增队列参数破坏 promotion reload。
5. 队列上下文必须使用 allowlist；不要把 messages、tools、headers、API Key、base URL 或原始异常加入 snapshot。

### 指标接口

`GET /api/debug/llm-metrics` 的稳定顶层形状：

```json
{
  "totals": {
    "calls": 0,
    "successes": 0,
    "errors": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0
  },
  "entries": [],
  "generation": 1,
  "queues": []
}
```

每个 `queues[]` 项包含 Provider 名称、`max_concurrent`、`queue_interval_s`、`active`、`waiting`、`spacing`、累计等待/失败/取消统计和有限的 `requests` 安全投影。前端设置中心打开时每 2 秒轮询；接口暂不可用时只显示安全错误提示，不能清空已保存配置。

### UI 与回归检查

- Provider 页的每个数字框必须同时显示名称、用途、合法范围和热更新提示。前端 `PROVIDER_FIELD_FALLBACKS` 是旧后端 schema 的兼容兜底，不能只依赖服务端字段元数据。
- 共享并发和出队间隔必须解释与 Profile 并发的层级关系，并显示建议值，禁止只呈现裸数字框。
- 修改后运行：
  - `npm test -- --runInBand`
  - `npm run build`
  - `python -m pytest tests/unit/test_api_request_queue.py tests/unit/test_llm_client.py tests/unit/test_model_gateway.py tests/unit/test_model_promotion_apply.py tests/unit/test_settings_center.py -q`
- 排查 `turn exceeded time budget` 时，先对照 action log 和 LLM trace：如果多次请求接近 `timeout_s × 尝试次数 + retry_delay_s`，再查看 `/api/debug/llm-metrics` 的 Provider 等待、最长等待和失败数，避免把已成功的工具查询误判为根因。

## Deadline-aware model calls and timeout investigation (v0.32.0)

### Deadline propagation

`TurnEngine.run()` adds `_llm_deadline_monotonic`, `_turn_deadline_monotonic`, and `_turn_reserve_s` to the internal round trace context. `ModelGateway.chat()` extracts only the model deadline and passes it to `LLMClient.chat()`; these private keys are not included in the public `TraceContext` projection or Provider queue metadata.

When changing this path, preserve all of the following invariants:

1. Waiting for a Profile semaphore must stop at the logical model deadline.
2. Every retry must rejoin the shared Provider queue with that same deadline.
3. Waiting for a Provider slot and `queue_interval_s` pacing must stop at the deadline and remove the request from `_waiting`.
4. `client.post(..., timeout=...)` must use the smaller of `ProviderConfig.timeout_s` and the remaining logical deadline.
5. Retry cooldown must not start if it leaves no useful time for the next attempt.
6. A nested `TimeoutError` raised by the client is an `llm_error`; only the supervisor's own unfinished task at the hard turn deadline is `time_budget`.

### Runtime diagnosis

If `/api/debug/llm-metrics` returns `totals` and `entries` but has neither `generation` nor `queues`, the running Python process is an old generation. Restart the backend before evaluating Provider shared-queue behavior. Refreshing the browser is insufficient.

For action failures, correlate `chain_id`, `turn_id`, and round number across `/api/debug/action-log` and `runtime/llm_traces.jsonl`. A successful tool round followed by a zero-token model failure near `timeout_s * attempts + retry delays` indicates local HTTP timeout/retry exhaustion, even when a relay dashboard reports no upstream HTTP error.

### Regression commands

- `python -m pytest tests/unit/test_api_request_queue.py tests/unit/test_llm_client.py tests/unit/test_model_gateway.py tests/unit/test_turn_engine.py -q`
- `npm test --prefix frontend`
- `npm run build --prefix frontend`

Regression coverage must include queue-deadline cleanup, bounded retry count, per-attempt timeout capping, non-empty timeout diagnostics, propagation through `ModelGateway`, and legacy metrics payload detection.

## Developing DLC dynamic lifecycles (v0.33.0)

### Minimal authoring shape

`lifecycle` is a top-level sibling of `actors`, `agents`, and `time_system` in a bundle-v1 `content.json`:

```json
{
  "lifecycle": {
    "enabled": true,
    "recent_feedback_limit": 50,
    "actor_templates": [],
    "positions": [],
    "transitions": []
  }
}
```

Use ASCII-safe IDs for template, position, transition, cycle, season, and month identifiers. Entity, Actor, organization, and Agent references may use existing Unicode IDs. Keep template source Agents static and present in the same resolved DLC graph so model routing and memory inheritance are deterministic.

### Template and position rules

- A template's `actor` object omits `actor_id`; `spawn_actor` assigns it from `actor_id_pattern`. Optional `entity` and `agent` objects create projections in the same transaction.
- Dynamic Agents require `agent.source_agent_id`. Use `agent.system_prompt` only for role-specific additions; model Profile, task routing, multi-round budget, tools, memory settings, and Provider routing are inherited from the source Agent and may be narrowed by a safe patch.
- A position must reference an existing organization and declares capacity, incumbents, candidates, role, rank, and score fields. Valid score sources are `attribute`, `state`, `membership_rank`, and `actor_id`. Add an Actor-ID ascending score with zero weight when an explicit stable tie-breaker helps authors understand the order.

### Transition and operation rules

Valid transition triggers are `condition`, `manual`, `day_start`, `week_start`, `month_start`, `year_start`, `season_start`, `season_end`, `cycle_start`, and `cycle_end`. Time-bound triggers must reference IDs declared in `time_system`. Conditions reuse the causal-condition DSL and obey the same hidden-state and audience rules.

The seven operation payloads are edited in DLC Studio and validated again by the backend. Keep multi-operation transitions small and cohesive because all commands are committed as one atomic batch. Use `{sequence}`, `{transition_id}`, `{template_id}`, and `{actor_id}` only in supported pattern fields. Do not depend on array order or random selection for succession; use position scores and stable Actor IDs.

### Hot reload, rollback, and saves

Lifecycle definitions are Generation-bound. Never mutate an existing contract object after publication. Build and validate the next Generation first, then atomically switch the orchestrator. Runtime snapshots must preserve receipt IDs, sequence counters, cooldowns, condition latches, tombstones, dynamic Actor/Agent projections, positions, and recent feedback. Reload/rollback code must not resurrect retired Actors or reuse sequence IDs from a newer Generation.

### DLC Studio workflow

1. Open DLC Studio and select “动态生命周期”.
2. Create Actor templates first, including optional entity and dynamic Agent sections.
3. Create positions and verify organization/Actor references.
4. Create transitions, choose a trigger, add conditions, then add one or more of the seven operations.
5. Enable player feedback for important automatic transitions.
6. Save only after the local editor reports no structural/reference errors; then run backend Registry validation because it has the complete resolved DLC graph.

### Regression commands

```powershell
python -m pytest tests/unit/test_lifecycle_contract.py tests/unit/test_lifecycle_runtime.py tests/unit/test_lifecycle_orchestrator.py tests/unit/test_lifecycle_state.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py -q
cd frontend
npm test
npm run lint
npm run build
```

Also run `python -m pytest tests/unit/test_version_consistency.py -q` after a release bump. Before committing, validate `dlcs/example_frontier`, run `git diff --check`, and confirm that `frontend/dist/`, runtime state, API keys, and Provider URLs are not staged.

## Developing DLC rhythm rules (v0.34.0)

### Minimal authoring shape

`rhythm` is a top-level sibling of `issue_types`, `random_events`, `time_system`, and `lifecycle` in a bundle-v1 `content.json`:

```json
{
  "rhythm": {
    "enabled": true,
    "max_active_issues": 8,
    "attention_budget": 12,
    "attention_recovery_per_day": 4,
    "queue_limit": 128,
    "recent_feedback_limit": 32,
    "quiet_period_days": 0,
    "critical_bypasses_quiet": true,
    "urgency_costs": {
      "low": 1,
      "normal": 2,
      "high": 3,
      "critical": 5
    },
    "rules": []
  }
}
```

Use ASCII-safe unique `rule_id` values. Each enabled rule must match at least one declared `issue_type_ids` entry or authored `event_types` entry. Rules are evaluated by descending `priority` and then stable rule ID; do not rely on source-array order as a tie-breaker. `attention_cost` may be omitted to use the matched urgency cost.

### Admission, cooldown, mutex, and quiet periods

- `max_active_issues` is global; `max_active` is per rule. Both count non-terminal Issues.
- Attention is restored from authoritative elapsed `sim_time`, not wall-clock time. Keep costs and daily recovery proportional to the number of meaningful decisions expected per game day.
- `cooldown_days` starts from a successful activation. `mutual_exclusion_group` prevents overlapping active arcs in the same group.
- Global and rule `quiet_period_days` suppress new matching content after activation/settlement. `allow_during_quiet` is rule-specific; `critical_bypasses_quiet` only affects critical Issue traffic.
- `queue_limit` bounds deferred Issue candidates. Random events are never queued: they are either admitted or suppressed before injection.

### Escalation, de-escalation, and climax

`escalation` defines `after_days`, `interval_days`, `max_level`, `urgency_by_level`, and optional player-safe `feedback_by_level`. The runtime derives due levels from game time, so reloads and replays remain deterministic.

`deescalation` may periodically evaluate shared causal `conditions`, move the Issue to `target_level`, set it to `open` or `monitoring`, and emit bounded feedback. `climax` waits for its configured level and delay, evaluates the same causal DSL, moves the Issue to a terminal status, optionally closes same-group Case and DecisionRequest records, and starts a post-climax quiet period. De-escalation and climax levels must not exceed the escalation maximum.

Conditions must obey normal knowledge visibility and resource-reference validation. Do not encode hidden operands in feedback strings, Event payloads, or UI labels. Keep feedback explanatory but authored; P2.4 will add generalized outcome-causality presentation.

### Hot reload, rollback, saves, and API

Rhythm definitions are Generation-bound. Build and validate the next Generation before publication. Compatible attention/queue/cooldown/progression state is retained on hot reload; rollback and save/load restore the runtime snapshot against current `sim_time`. Never mutate a published `RhythmContract` in place.

Use `GET /api/rhythm` for diagnostics and UI consumption. It intentionally returns public rule summaries, budget/queue counters, recent feedback, and recent suppressed-event summaries, but not evaluated condition operands. If a new runtime field affects deterministic progression, add it to both `snapshot()` and `restore()` and cover reload plus save/load behavior.

### DLC Studio workflow

1. Open DLC Studio and select “节奏导演”.
2. Configure the global active limit, attention capacity/recovery, queue, feedback, quiet-period, and urgency costs.
3. Add rules, choose Issue Types and/or Event Types, then order them with explicit priorities.
4. Configure admission overrides, cooldown, mutex group, and quiet behavior.
5. Enable escalation first; then configure de-escalation and climax levels/conditions within that maximum.
6. Save only after the editor reports no ID, reference, numeric, urgency, or causal-condition errors; backend Registry validation remains authoritative for the complete resolved DLC graph.

### Regression commands

```powershell
python -m pytest tests/unit/test_rhythm_contract.py tests/unit/test_rhythm_runtime.py tests/unit/test_rhythm_orchestrator.py tests/unit/test_rhythm_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py -q
python -m pytest tests/unit/test_version_consistency.py -q
cd frontend
npm test
npm run lint
npm run build
```

Before committing, validate `dlcs/example_frontier`, run `git diff --check`, scan the new editor and sample package for replacement characters, and confirm that runtime state, local API keys, Provider URLs, and generated `frontend/dist/` files are not staged.


## Developing DLC outcome feedback rules (v0.35.0)

### Minimal contract

Add an optional `outcome_feedback` block to `content.json`:

```json
{
  "outcome_feedback": {
    "enabled": true,
    "recent_limit": 128,
    "pending_limit": 256,
    "receipt_limit": 2048,
    "metric_delta_epsilon": 0.000001,
    "immediate": {
      "enabled": true,
      "include_command_changes": true,
      "include_metric_changes": true,
      "max_changes": 12,
      "max_metric_changes": 8
    },
    "case_summaries": {
      "enabled": true,
      "include_decisions": true,
      "include_changes": true,
      "include_metric_changes": true
    },
    "rules": [
      {
        "rule_id": "supply_followup",
        "display_name": "补给行动后续报告",
        "enabled": true,
        "priority": 100,
        "source_kinds": ["action"],
        "delivery": "delayed",
        "action_ids": ["deliver_supplies"],
        "issue_type_ids": [],
        "event_types": [],
        "metric_ids": ["regional_readiness"],
        "delay_days": 2,
        "conditions": [],
        "title": "补给报告送达",
        "summary": "前线已完成补给清点。",
        "reason": "此前执行了补给行动。",
        "reveal_text": "",
        "create_issue_type_id": "supply_review",
        "attach_evidence": true
      }
    ]
  }
}
```

Omit the whole block to disable this capability for a legacy DLC. Keep every `rule_id` stable after release because receipts, delayed work, snapshots, and reload compatibility use stable rule/source identity.

### Field semantics and references

- Top-level `recent_limit`, `pending_limit`, and `receipt_limit` bound delivered messages, future deliveries, and idempotency history. `metric_delta_epsilon` suppresses insignificant floating-point metric movement.
- `immediate` controls automatic feedback derived from authoritative world commands and Derived Metric deltas. Keep the per-record limits small enough that a single action remains readable.
- `case_summaries` controls automatic summaries when a Case closes and may include the selected Decision, command changes, and metric changes.
- `source_kinds` accepts `action`, `decision`, `case_closed`, `causal_event`, `lifecycle`, and `rhythm`. `delivery` accepts `immediate`, `delayed`, or `causal_reveal`; `delay_days` uses game days and may be zero.
- `action_ids`, `issue_type_ids`, `event_types`, and `metric_ids` are optional filters. Action, Issue Type, and Derived Metric references must resolve in the expanded DLC Registry. Event types must resolve through the DLC event catalogs used by initial, ongoing, random, causal, lifecycle, or rhythm content.
- `conditions` uses the same generic causal-condition objects as gameplay rules. Do not encode hidden thresholds or secret facts into `title`, `summary`, or `reason`. For a `causal_reveal`, put the disclosure in `reveal_text`; it is emitted only after the rule actually matches.
- `create_issue_type_id` optionally creates a follow-up Issue when feedback is delivered. Set `attach_evidence` only when the resulting public feedback is safe to attach to that Issue.

### Reload, persistence and safe authoring

- The runtime is Generation-bound. Snapshot/restore, Generation archive, rollback, and process restart preserve bounded recent messages, delayed work, and receipts. Replayed source events remain idempotent.
- Hot reload preserves pending deliveries only when their contract/rule identity remains compatible. Treat changes to rule IDs, delivery semantics, filters, or text as behavior changes and test an existing save before publishing the DLC.
- The player API deliberately excludes condition trees, thresholds, private source payloads, hidden facts, and unrevealed causes. Never add those fields to the public projection for editor convenience.
- In DLC Studio, open “结果反馈”, edit the Chinese-described fields, use catalog selectors for references, then review the generated Diff before applying. Moving or editing a card must retain its editor state; internal `__editor_key` values must never be serialized.

### Regression commands

```powershell
python -m pytest tests/unit/test_outcome_feedback_contract.py tests/unit/test_outcome_feedback_runtime.py tests/unit/test_outcome_feedback_orchestrator.py tests/unit/test_outcome_feedback_api.py tests/unit/test_dlc_registry.py tests/unit/test_dlc_runtime.py -q
cd frontend
npm test
npm run lint
npm run build
```

Also validate the expanded example DLC through `DLCRegistry`, exercise snapshot/restore and same-fingerprint hot reload, and inspect `GET /api/outcome-feedback` plus Tick payloads to ensure hidden causal data never reaches the player projection.
## Developing cross-genre playability audits (v0.36.0)

### Validation and audit responsibilities

Do not add playability heuristics to `DLCRegistry.validate()`. Registry validation is authoritative for document shape, IDs, references, bounds, dependency safety, and runtime-load legality. `DLCRegistry.audit_playability()` is advisory authoring analysis: it checks whether legal content declares a credible seven-stage loop and returns Chinese, actionable gaps. A package may therefore be valid but not playable.

The stable audit check IDs are:

1. `player_surface` — player identity, controlled objects, and a visible UI surface.
2. `action_roles` — player action plus Agent participation such as `wake_agent`.
3. `issue_intake` — an enabled event/report can reach the player and create an Issue with evidence.
4. `costly_decision` — a DecisionRequest points to an Action with an anchored cost or authoritative duration.
5. `world_simulation` — Action output enters time/ongoing/causal processing and can change world state.
6. `outcome_feedback` — enabled immediate/authored feedback can reach the player.
7. `progression` — objectives/endings form a campaign, or an enabled time cycle sustains a sandbox.

Keep `audit_version` stable when adding only labels or evidence. Increment it when check meaning or report shape changes. Report evidence must contain only authored IDs and player-safe labels; do not expose Prompt text, hidden knowledge, Provider configuration, or runtime secrets.

### Authoring an auditable action chain

For an Action-driven causal loop, the Action's `emit_event.event.event_type` must be consumed by an enabled `causal_event_rules[].source_event_types`. If the world change is delayed, declare an authoritative duration/ongoing event or causal delay, then apply a deterministic `world_effects` command. A disconnected emitted event may pass Schema validation but must fail `world_simulation`.

The minimal evidence path should remain explicit in DLC JSON:

```text
initial/random/time/Agent report
  → issue_type + evidence + decision option
  → action cost/duration + wake_agent + emit_event
  → causal rule + world effect
  → outcome_feedback
  → objective/ending or sandbox cycle
```

Use namespaced package-local IDs for relationship types and other globally merged catalogs. The `example_orbital` package uses `orbital_command`, `orbital_trust`, and `orbital_rivalry`; do not copy generic IDs from another enabled package. The deep-sea reference follows the same rule with `abyssal_command`, `abyssal_trust`, and `abyssal_rivalry`.

### Studio and API

Both `GET /api/dlcs` and `GET /api/dlcs/{id}` include a `playability` object. The Studio must render this server-owned result rather than duplicate Python heuristics in JavaScript. `PlayabilityAuditTools.js` may normalize missing/old payloads only; it must not infer new gameplay rules. The refresh action reloads package detail and never writes content.

When extending the panel, keep all gaps and explanations in Chinese, preserve accessible region/score labels, and keep the warning that static 7/7 does not replace a real playthrough. Do not hide Registry validation errors behind the playability score.

### Regression commands

```powershell
python -m pytest tests/unit/test_dlc_playability.py tests/unit/test_dlc_playability_api.py tests/integration/test_cross_genre_dlc_playthrough.py tests/integration/test_abyssal_dlc_playthrough.py -q
node --test frontend/src/PlayabilityAuditTools.test.js
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

Before publishing, validate `example_frontier`, `example_orbital`, and `example_abyssal`, require 7/7 from `audit_playability()`, run `git diff --check`, scan edited UTF-8 files for the replacement character, and stage only the milestone files. Never stage local API keys, `config/models.json`, runtime databases, `.codex-local` helper scripts, or concurrent Settings/API work.

## 开发 DLC Studio 快速创作流程（v0.37.0）

### 批量输入格式

快速创建页面接受竖线（`|`）或 Tab 分隔的纯文本。空行和以 `#` 开头的中文注释行会被忽略。实体列顺序为：

```text
实体 ID | 名称 | 类型 | 当前状态 | 中文说明
```

Agent/Actor 套件列顺序为：

```text
Agent ID | 显示名 | Actor ID | 所在实体 ID | 角色 | 中文说明 | Actor 类型
```

实体类型必须来自 `QuickCreateTools.js` 的题材无关白名单；Actor 类型也必须来自显式白名单。不要在 React 组件中另建一套解析规则。新增类型时应先扩展后端 Schema/Registry，再同步工具白名单、中文说明和测试。

### 冲突与原子性规则

- `skip`：实体 ID 冲突时跳过该实体；Agent ID 或 Actor ID 任一冲突时，Agent/Actor 整套跳过，禁止只创建一半。
- `overwrite`：用当前快速创建基础模板替换冲突项。该模式会重置旧条目的高级字段，界面必须持续显示中文警告。
- 同一批输入中的重复 ID、非法稳定 ID、未知类型和缺失所在实体都是阻断错误；只有警告而无错误时才可保存。
- Agent 所在实体可以引用既有实体，也可以引用同批新实体。引用检查必须基于合并后的实体 ID 集合。
- 工具函数必须返回新对象，不可原地修改传入的 `content`；保持 Agent 与 Actor 的创建、跳过和覆盖行为可预测且可测试。

### 玩家 direct / command 语义

`gameplay.player.controls[]` 的 `target_type` 只能是 `entity` 或 `agent`。`control_mode=direct` 表示玩家直接操作该目标；`control_mode=command` 表示玩家通过命令、授权或委派影响目标。相同 `target_type + target_id` 不得重复。保存前必须验证绑定 Actor 和所有控制目标存在。

自动建议只用于生成可编辑草稿：优先玩家角色 Agent，其次第一个启用 Agent；不得在保存时偷偷重写用户选择。规范化时保留 `gameplay` 下未知扩展字段，确保未来 DLC Schema 扩展不会被旧版编辑器清除。

### Studio 保存与导航约束

快速创建和玩家身份编辑器必须调用 `DLCStudio.jsx` 提供的统一 Content 保存回调，不得直接请求文件写入 API。这样才能完整保留 Diff、确认、History、Undo/Redo、Registry 校验和错误反馈。七步审计按钮只做页签导航；审计规则继续由后端 `DLCRegistry.audit_playability()` 拥有，前端不得推断或篡改审计结论。

所有新增字段、错误、警告、空状态和覆盖风险都必须有明确中文说明。新增交互时还需检查键盘操作、按钮禁用状态、`aria-label` 和窄屏布局。

### 回归命令

```powershell
node --test frontend/src/QuickCreateTools.test.js frontend/src/GameplayEditorTools.test.js
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
git diff --check
```

提交前还要确认 `VERSION`、`multiagent_framework/version.py`、`frontend/package.json` 与 `frontend/package-lock.json` 四处版本一致。共享文档和并行 Settings/API 文件只能按本任务 hunk 暂存；不要提交本地 Key、`config/models.json`、运行数据库或 `.codex-local` 进度文件。

## 开发长对局 catch-up 时间语义（v0.38.0）

### 事件时间规则

新增事件生产者必须填写 `EventObject.generated_at`，并使用实际发生时间而不是当前轮询时间：随机模板使用 `scheduled_at`，时间边界使用 marker 时间，因果子事件使用 `due_at`。进入 Orchestrator 后统一调用 `_effective_event_time()`；不要在各分支自行复制 clamp 逻辑。

以下消费者必须使用同一个有效事件时间：

1. `CausalEventEngine.observe(..., sim_time=...)` 与对应 `CausalConditionContext.sim_time`；
2. 时间边界、随机事件、因果事件的 `RhythmRuntime.allow_event()`；
3. `execute_causal_world_commands()` 的命令时间；
4. 历史事件投影出的 Issue/Case/DecisionRequest 创建时间和截止期；
5. 因果结果反馈的记录时间与延迟到期时间；
6. 发往 Agent 或玩家的事件唤醒负载中的 `generated_at`。

普通 API 或玩家当下操作继续使用当前 `_sim_time`。只有来源事件明确携带权威时间时才调用内部 `_open_issue_at()`，避免允许客户端任意伪造历史 Issue。

### 回归要求

~~~powershell
python -m pytest tests/unit/test_event_generator.py tests/unit/test_rhythm_orchestrator.py tests/unit/test_outcome_feedback_orchestrator.py -q
python -m pytest tests/unit/test_version_consistency.py -q
~~~

长跳时测试至少要覆盖：历史来源事件的因果延迟、当前 tick 后仍应 pending 的未来子事件、Rhythm 冷却序列、世界命令/库存账本时间、Issue 截止期与逾期状态、已经逾期的延迟反馈补发。测试断言应同时检查 `generated_at` 和持久化时间字段，不能只检查事件数量。

不要把“保留每条事件的发生时间”误写成“跨来源全局严格排序”。当前 tick 仍按既有阶段处理时间边界、生命周期、随机与因果来源；若未来引入统一时间队列，需要单独设计跨来源稳定排序、预算、公平性和零延迟链上限。

## 开发跨来源统一时间排序（v0.39.0）

### 新事件生产者接入规则

1. 生产者必须提供权威发生时间；事件写入 `EventObject.generated_at`，边界 marker 写入 `sim_time`。
2. 在 `Orchestrator._tick()` 的统一时间线中分配明确 `source_rank`。只有兼容性需要时才增加新 rank，不得依赖字典顺序或批次阶段碰巧排序。
3. 同源稳定顺序必须可重放：普通入队使用单调 `insertion_sequence`；因果 pending 使用 `(due_at, event_id)`。
4. 事件只有在从时间线取出后才能执行节奏判断、世界效果、Issue 投影、Agent 唤醒和下游因果观察，所有消费者使用同一个 `event_time`。
5. 历史 lifecycle marker 必须传 `evaluate_conditions=False`；condition transition 只在当前模拟时刻统一评估一次，避免长跳时重复触发或提前锁存。

### 预算与失败语义

- 因果硬预算为每 tick 128 条。达到预算后不得丢弃 pending；剩余事件由下一 tick 继续。已物化 temporal/random 条目会完成，因此文档和测试只能声明“预算内全局排序”。
- 生命周期总尝试预算为 256，`LifecyclePollResult.truncated` 必须向 Tick 结果传播。若新增 deferred timeline，必须同时覆盖存档、恢复、Generation 切换、热重载和幂等 Receipt，不能只做进程内队列。
- 被 Rhythm 抑制、世界效果 rejected/error 的因果事件都已经从 pending 消费，必须标记持久状态 dirty；只有世界效果成功时才注入 Agent 唤醒和下游因果链。
- 同刻优先级固定为 temporal → lifecycle marker → random → causal → current condition lifecycle。修改优先级属于兼容性变更，必须新增世界状态可观测回归，而不只断言结果数组顺序。

### 回归命令

```powershell
python -m pytest tests/unit/test_event_generator.py tests/unit/test_causal_events.py tests/unit/test_lifecycle_runtime.py tests/unit/test_lifecycle_orchestrator.py tests/unit/test_rhythm_orchestrator.py tests/unit/test_outcome_feedback_orchestrator.py -q
python -m pytest tests/integration/test_dlc_temporal_generation.py tests/integration/test_cross_genre_dlc_playthrough.py -q
python -m compileall -q multiagent_framework routers
python -m pytest tests/unit/test_version_consistency.py -q
```

至少覆盖：不同来源跨日归并、同刻稳定 tie、零延迟因果、未来 pending 不提前消费、历史因果效果早于未来生命周期实体生成、`next_due_key()` 不改变 snapshot、历史 marker 不更新 condition latch、因果 128 条预算后 pending 保留，以及 lifecycle `truncated` 传播。



## Developing the persistent deferred timeline (v0.40.0)

### Required invariants

1. Materialized producer output enters `DeferredTimelineRuntime` before Agent wakeups, Issues, or world side effects. Do not poll Temporal or Random while the queue is non-empty.
2. Keep `(occurrence_time, source_rank, sequence)` stable. Same-time precedence remains temporal, lifecycle marker, random, causal, then condition lifecycle.
3. Once the causal budget is exhausted, only already queued same-time ranks below causal may continue. Never execute a future head past an older due causal item.
4. Orchestrator lifecycle entries pass one marker per poll. `next_transition_index` is a transition cursor, not a marker-within-batch cursor. Batched marker polling requires a second cursor before it is safe.
5. Deferred lifecycle entries must reference the committed `archive_id`. A hot reload must not reinterpret an old fingerprint with the current contract.
6. Producer polling and `enqueue_many()` form one publication transaction. If enqueue fails, restore both Temporal and Random snapshots.
7. Snapshot restore is atomic and fail-closed. Skipping one malformed item would invalidate the global ordering proof.
8. Mark RuntimePersistor dirty after consuming or updating the head; otherwise restart can replay an already consumed queue.

### Lifecycle cursor rules

`LifecycleRuntime.poll(..., start_transition_index=N)` skips only contract transitions with an index below `N`. `LifecyclePollResult.next_transition_index` is set only when matching candidate work remains unattempted because of budget. Disabled, manual, or non-matching transitions must not create false truncation. When a continuation uses an archived generation, save its isolated runtime state back into head metadata.

### Regression commands

```powershell
python -m pytest tests/unit/test_timeline_runtime.py tests/unit/test_event_generator.py tests/unit/test_lifecycle_runtime.py tests/unit/test_lifecycle_orchestrator.py -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m compileall -q multiagent_framework routers
python -m pytest tests/unit/test_version_consistency.py -q
git diff --check
```

Required cases include: 129 earlier causal events blocking later temporal/random work, second-tick continuation at causal item 129, paused save/new-Orchestrator recovery with stable sequence and payload, 300 lifecycle attempts split exactly 256 + 44, DLC hot reload during continuation, in-place head updates preserving the key, and malformed/oversized snapshots failing closed.

## Operating and testing the deferred timeline (v0.41.0)

### Public status contract

Use `Orchestrator.deferred_timeline_status()` or `GET /api/timeline` for diagnostics. New callers must consume only these aggregate fields: pending/capacity/utilization, oldest occurrence time and age, archive count/capacity, backpressure/restore-failure counters, bounded restore error code, and last snapshot entry count/bytes/timings. Never add event payloads, archive bodies, Agent IDs, entity IDs, prompts, memory, or world secrets to this projection.

`OperationalMetricsCollector` must continue to copy a fixed whitelist rather than serializing `public_status()` wholesale. Non-mapping or malformed status values fail closed to zero/empty aggregates. Prometheus names introduced in v0.41.0 use gauges for current queue/snapshot state and counters for cumulative backpressure/restore failures.

### Backpressure and restore rules

1. Producer polling and timeline publication are one transaction. If `enqueue_many()` fails for capacity, restore every producer cursor/snapshot involved in that poll before returning.
2. Increment backpressure exactly once per rejected publication or archive-capacity rejection; do not count ordinary validation errors as successful publication.
3. Snapshot restore remains atomic and fail closed. Never skip malformed entries, repair sequence order, or partially accept archives.
4. Preserve accumulated operational counters when a restore fails, then increment `restore_failure_count` and expose only the stable error code.
5. `measure_snapshot()` is opt-in. Do not JSON-encode the full queue on every metrics scrape.

### Crash replay gates

- World Command committed, Causal queue not acknowledged: restart must return a replayed command result and must not apply inventory/resource changes twice.
- Lifecycle Receipt committed, cursor not acknowledged: restart must resolve as `already_applied` and must not create a second dynamic Actor/Entity.
- Canonical command normalization must preserve field presence when presence participates in idempotency. In particular, an explicit empty `reason` is not equivalent to an omitted `reason`.
- Agent wakeups use a durable at-least-once Outbox. Persisted `inflight` work returns to `pending` after restart; duplicates remain possible across the downstream side-effect/ACK crash boundary, so do not claim exactly-once delivery.

### Regression commands

```powershell
python -m pytest tests/unit/test_timeline_runtime.py tests/unit/test_timeline_api.py tests/unit/test_operational_metrics.py tests/unit/test_event_generator.py tests/unit/test_causal_world_effects.py tests/unit/test_lifecycle_runtime.py tests/unit/test_lifecycle_orchestrator.py -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m compileall -q multiagent_framework routers tests/unit
python -m pytest tests/unit/test_version_consistency.py -q
cd frontend
npm test
npm run lint
npm run build
```

Required stress/recovery coverage includes 20,000 queued lifecycle markers, exact UTF-8 snapshot bytes, stable full drain ordering, producer cursor rollback at capacity, aggregate-only API/metrics output, World Command pre-ack restart replay, and Lifecycle Receipt pre-ack restart replay.

## Durable Agent wakeup delivery (v0.42.0)

### Delivery contract

`WakeupOutboxRuntime` replaces the process-only wakeup list while retaining the small list-like surface required by legacy FC handlers. New orchestration code must use `enqueue()`, `claim_pending()`, `ack()`, and `retry()`.

1. Delivery is **at-least-once**, never exactly-once. A crash after a downstream side effect but before persisted ACK may replay the wakeup.
2. Entries transition `pending → inflight → ack/remove`. Agent errors, Router/command exceptions, background cancellation, and unexpected background failures return claimed delivery IDs to `pending`.
3. Snapshot restore converts every valid `inflight` entry back to `pending`. Malformed snapshots fail closed as a whole; never salvage individual entries.
4. Player-recipient wakeups are terminal after Issue contract rejection or successful Issue creation. Disabled and missing targets are terminal. Transient Issue creation failures retry.
5. `request_action` runs inside the existing background wakeup task and awaits ActionAgent dispatch before ACK; do not reintroduce a detached child-task crash window.

### Stable identities and idempotency

- Timer: `timer:<timer_id>`.
- Event: deterministic hash of `event_id + target`.
- Routed chain: deterministic hash of `chain_id + target + canonical JSON payload`.
- Legacy wakeup without source identity: persisted monotonic sequence; its chain ID derives from the delivery ID.
- Merged wakeups must preserve every `delivery_id`, `timer_id`, and `chain_id` for ACK/retry and Chain Log diagnostics.
- Downstream World Command IDs, Issue `external_id`, Lifecycle Receipts, action invocation IDs, and other receipt-backed boundaries remain responsible for replay idempotency.

### Timer claim/ack ordering

Use `TimerSystem.claim_due()` in the Orchestrator. Claim removes a due Timer from the heap but leaves its SQLite row intact. Enqueue the stable Timer wakeup next; if Outbox validation/capacity rejects it, call `release_claim()`. Delete the SQLite row only through `ack(timer_id)` after terminal wakeup completion.

Timer ACK intentionally occurs before Outbox removal. A crash between those operations can replay the persisted Outbox entry, but cannot lose both durable sources. Reversing the order would also permit duplicates and can leave a Timer row to regenerate; neither ordering provides exactly-once, so preserve the documented no-loss ordering and downstream idempotency.

### Snapshot and capacity rules

- Hard entry capacity: 32,768.
- Per-payload UTF-8 JSON size: 256 KiB.
- Aggregate payload bytes: 64 MiB, enforced on enqueue and restore.
- Maximum nesting depth: 16; maximum visited nodes: 20,000; maximum ID length: 256.
- Reject NaN, Infinity, non-string mapping keys, arbitrary Python objects, and any non-JSON-safe payload.
- `RuntimePersistor.flush()`, manual `save_state()`, and startup loading share `runtime_snapshot()` / `restore_runtime_snapshot()`. When adding runtime state, update this single contract and its parity tests instead of maintaining separate field lists.

### Aggregate-only operations metrics

The simulator projection and Prometheus output may expose only:

- `pending_wakeups`
- `inflight_wakeups`
- `wakeup_outbox_total`
- `wakeup_outbox_payload_bytes`

Never expose payloads, target Agent IDs, delivery IDs, chain IDs, prompts, memories, provider credentials, or hidden world state through operational metrics.

### Regression commands

```powershell
python -m pytest tests/unit/test_wakeup_runtime.py tests/unit/test_wakeup_orchestrator.py tests/unit/test_timer_system.py tests/unit/test_runtime_persistor.py tests/unit/test_event_generator.py tests/unit/test_operational_metrics.py -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m compileall -q main.py multiagent_framework routers tests
python -m pytest tests/unit/test_version_consistency.py -q
cd frontend
npm test -- --run
npm run lint
npm run build
```

Required recovery coverage includes: inflight snapshot restart to pending, Router failure retry, request_action completion before ACK, Timer claimed before crash, Timer + persisted Outbox replay deduplication, malformed and aggregate-oversized snapshot rejection, automatic state.json parity, startup Outbox restore, and secret-free aggregate metrics.

## 第三题材 JSON-only 参考包（v0.43.0）

- `dlcs/example_abyssal/dlc.json`：默认禁用的已发布 manifest，题材标记为 `deepsea-survival`。
- `dlcs/example_abyssal/content.json`：完整声明深海栖站玩家身份、Actor/Agent、维护配额价值锚、压力壳指标、有限证据 Issue、深潜修复巡检、Agent 唤醒、延迟因果链、反馈、目标与结局。
- `tests/integration/test_abyssal_dlc_playthrough.py`：不得 mock 掉 Registry/WorldState/Orchestrator 的玩法执行；只允许使用确定性 LLM stub 隔离外部网络。验收必须真实扣除维护配额、进入 Wakeup Outbox、推进一游戏日、应用 causal world effect、生成反馈并锁定胜利结局。

新增题材参考包时，优先复用 bundle-v1 声明能力，不得因为题材术语新增核心分支。每个包必须使用独立命名空间，默认 `enabled=false`，通过 Registry 0 error、七步静态审计 7/7 和独立真实 playthrough。

### 回归命令

```powershell
python -m pytest tests/integration/test_cross_genre_dlc_playthrough.py tests/integration/test_abyssal_dlc_playthrough.py -q
python -c "from pathlib import Path; from multiagent_framework.dlc_registry import DLCRegistry; r=DLCRegistry(Path('.')); assert all(not r.validate(i) and r.audit_playability(i).playable for i in ('example_frontier','example_orbital','example_abyssal'))"
python -m pytest tests/unit/test_version_consistency.py -q
```

## 深海分支玩法声明指南（v0.44.0）

### 用统一价值锚表达多资产成本

- 可交换资产必须声明为 `kind=asset` 并设置正数 `anchor_rate`。
- 希望两种代币保持同一面额时，应使用相同 `anchor_rate`；Action 的 `preconditions` 和 `costs` 填写 `base_value`，不要手工填写资产数量。
- 多资产成本应放在同一个 Action 的 `costs[]` 中。运行时会先完整校验再原子提交，任何一种资产不足都不得出现部分扣费。
- 指标必须声明为 `kind=metric`，不能作为库存转移资产。`补给通行度` 因此只接受世界命令调整，不进入统一锚价值总额。

### 把即时收益转换为延迟且可恢复的后果

1. Action 的 composite result 先提交即时指标变化、状态变化、Agent 唤醒和源事件。
2. Causal rule 监听源事件并设置 `delay_days`，到期后同时应用后续指标变化和 `create_issue`。
3. 后续 Issue 必须提供至少一条可恢复路径，避免高收益选择成为不可逆死路；恢复 Action 也应保留成本或副作用，而不是免费撤销前一选择。
4. Rhythm rule 应为后续 Issue 配置独立优先级、升级、降级和高潮条件，避免它被常规报告噪声长期压制。
5. Outcome Feedback 的筛选字段必须与实际来源相符。因果事件没有派生指标变化时，规则的 `metric_ids` 必须留空，否则 `OutcomeFeedbackRuntime` 会因没有匹配的 metric delta 而跳过该规则。

### 深海参考 ID

- Actions：`abyssal_patrol`、`abyssal_emergency_isolation`、`abyssal_reopen_supply_channel`
- Issues：`abyssal_security_report`、`abyssal_supply_pressure`
- Events：`abyssal_isolation_started`、`abyssal_supply_pressure`、`abyssal_supply_channel_reopened`
- Feedback：`abyssal_isolation_immediate_result`、`abyssal_supply_pressure_reveal`、`abyssal_supply_reopen_immediate_result`

### 回归命令

```powershell
python -m pytest tests/integration/test_cross_genre_dlc_playthrough.py tests/integration/test_abyssal_dlc_playthrough.py -q
python -m pytest tests/unit/test_action_contract.py tests/unit/test_issue_contract.py tests/unit/test_causal_world_effects.py tests/unit/test_outcome_feedback_contract.py tests/unit/test_rhythm_runtime.py tests/unit/test_version_consistency.py -q
python -m pytest tests/integration -q
python -m pytest tests/unit -q
cd frontend
npm test -- --run
npm run lint
npm run build
```

验收必须断言两种资产的 `anchor_rate` 和 `base_value`，不能只检查“数量变少”；延迟路径必须推进到一个游戏日、确认 causal world effect 已 applied、找到新 Issue 与 pending DecisionRequest，并实际执行恢复 Action。当前完整单元测试的唯一已知失败位于并行维护的 LLM timeout 浮点边界，未经文件所有者授权不得在本玩法发布中顺手修改。


## DLC Studio 玩法分支向导开发指南（v0.45.0）

### 作者使用流程

1. 先在“快速创建”上半区建立实体、Actor 与 Agent，并在“玩家身份”页配置 gameplay.player 和直接控制实体。
2. 在“玩法分支模板向导”中填写稳定前缀。前缀只允许字母、数字、点、下划线和连字符；发布后不要随意更名。
3. 分别选择接收初始 Issue 的玩家 Agent、写入 Action 权限的玩家 Actor、负责报告/唤醒的 Agent，以及接收 Capability 的 Actor。
4. 为两种库存资产填写同一个 anchor_rate。Action 成本填写基础价值 base_value，不要按资产数量手工换算。
5. 配置即时收益指标、取舍指标、即时下降、延迟下降、恢复提升和恢复副作用。延迟单位是游戏日。
6. 保持默认“拒绝冲突”生成差异预览；只有确认要重建同前缀整套分支时才使用“覆盖整套同前缀分支”。
7. 应用差异后运行 Registry、七步可玩性审计，并至少真实推进一次游戏内时间，确认延迟 Issue 与恢复 Action 可执行。

### 生成器维护规则

- 生成逻辑必须保持纯函数：输入 content + form + conflictPolicy，输出 nextContent + errors + warnings + counts，不得直接请求 API 或修改文件。
- 新增区段时同时扩展稳定键冲突检查、计数摘要和原子测试；不得出现主 Action 已生成但 Issue、Capability 或反馈缺失的半套状态。
- actions[].permissions.actor_ids 使用玩家行动授权 Actor；actors[].capability_ids 使用能力持有 Actor。修改推导逻辑时必须保留这一区分。
- 两种资产必须声明相同正数 anchor_rate，并在副资源 Metadata 中记录 parity_with；前置条件和成本都使用 anchored_inventory + base_value。
- 延迟 Rule 的 source_event_types 必须与主 Action emit 的事件完全一致；create_issue.issue_type_id 必须指向生成的后续 Issue。
- Outcome Feedback 的 source_kinds、action_ids 和 event_types 必须与实际来源匹配；没有派生指标筛选需求时保持 metric_ids 为空数组。
- UI 必须继续只调用 onSaveContent() 进入 Studio Diff，不得直接 PUT、发布或热重载 DLC。

### 回归命令

- cd frontend；node --test src/GameplayBranchTools.test.js
- npm test
- npm run lint
- npm run build
- python -m pytest tests/unit/test_version_consistency.py -q
- 对 example_frontier、example_orbital、example_abyssal 运行 Registry validate 和七步可玩性审计。

发布验收除工具测试外，还应将一个新前缀分支叠加到真实参考包草稿并执行 Registry 校验，防止前端结构测试遗漏后端跨引用约束。

## Provider 本地配置、连接诊断与共享队列开发指南（v0.46.0）

### 配置规则

- 本地 Provider 配置写入 Git 忽略的 `config/models.json`；允许明文 `api_key`，但禁止把真实 Key 写入 `config/models.example.json`、测试、日志、Trace、截图或提交产物。
- Provider 字段：`base_url`、`api_key`、`timeout_s`、`retry_delay_s`、`max_concurrent`、`queue_interval_s`。新增字段时同时更新 `ProviderConfig`、持久化/展示投影、`runtime_settings.settings_schema()`、设置中心回退元数据、示例配置和测试。
- Profile 的 `max_concurrent` 与 Provider 的 `max_concurrent` 含义不同：前者是该 Profile 的逻辑调用上限，后者是共享的真实 HTTP 并发上限。
- 保存设置必须保持预览、乐观锁、文件快照、热激活和失败回滚的原子顺序；不要恢复为“先写文件、激活失败后留下半套配置”。

### 连接测试

`POST /api/settings/providers/test` 使用草稿 Base URL、Key、超时和可选模型 ID 请求 `/models`。修改诊断逻辑时必须覆盖成功、模型不存在、401/403、404、429、5xx、超时与连接失败，并验证响应中不包含输入 Key。不要把 Prompt 或模型请求正文加入该接口。

### APIRequestQueue 约束

- 同一 Provider 的所有 `LLMClient` 必须持有同一个队列实例；不同 Provider 不共享。
- 每次 HTTP attempt 都调用队列，重试不可复用上一次许可。
- Deadline 必须覆盖队列等待、出队间隔和 HTTP 阶段；取消与超时后清理等待状态。
- `snapshot()` 只可暴露 allowlist 元数据与聚合计数。扩展指标前先添加泄密回归测试。
- 热重载时不要提前关闭仍被在途调用引用的旧 Generation 客户端。

### 回归命令

```powershell
python -m pytest tests/unit/test_api_request_queue.py tests/unit/test_provider_connection.py tests/unit/test_llm_client.py tests/unit/test_model_gateway.py tests/unit/test_settings_center.py tests/unit/test_settings_security.py tests/unit/test_turn_engine.py -q
python -m pytest -q
python -m compileall -q multiagent_framework routers tests
cd frontend
npm test -- --run
npm run lint
npm run build
```

当前完整 Python 门禁有一个已知浮点边界：`test_turn_deadline_caps_each_http_attempt_timeout` 可能观察到 `0.2000000000007276`，断言要求 `<= 0.2`。除非同时定义并评审 deadline 舍入策略，否则不要用扩大断言或吞掉 Deadline 的方式掩盖该问题。
