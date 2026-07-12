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
- `_tick()` — 检查定时器 → 弹出到期的 OngoingEvent → 派发唤醒（皇帝奏折 vs Agent 调用）→ 处理玩家操作
  - 同一 Agent 的多个唤醒消息会自动合并（拼接为一条 payload）
- `_apply_matured_event(ev)` — 应用到期 OngoingEvent 的 expected_result（更新实体状态 + 库存变化）
- `_process_agents_background()` — **后台 asyncio.Task**，不阻塞时间流动
  - 并行调用所有被唤醒的 Agent
  - Router 批量处理
  - 执行 Router 指令
- `_call_agent_safe()` — 调用单个 Agent，记录 chain log，异常安全
  - 检测 `request_action` → 绕过 Router，直接分发给 ActionAgent
  - 检测 hop 超限 → 标记 loop_detected
- `_run_action()` — 运行 ActionAgent 模拟行动，完成后创建 Timer 把结果送回发起者
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
- `LLMConfig` — 配置（base_url/api_key/model/timeout_s/max_concurrent）
：
  - `LLMConfig.from_file(path)` — 从 JSON 文件加载配置
  - `LLMConfig.save_to_file(path)` — 保存配置到文件
  - `LLMConfig.to_display_dict()` — 返回脱敏字典（api_key 打码显示）
- `LLMResponse` — LLM 响应数据类（content, tool_calls, input_tokens, output_tokens, latency_ms, raw）
- `LLMClient.chat()` — 异步调用（非流式），自动重试，并发控制
- `LLMClient.chat_stream()` — 流式调用，yield delta/tool_call_delta/done 事件；出错时退化为非流式
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

Do not place a real secret in JSON, `.env.example`, logs, tests, or committed files. The settings page can accept a direct key for the current process, but persistence intentionally stores only `api_key_env`. Rotate any key that was previously exposed in plaintext.

Validation commands:

```powershell
python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .
python -m pytest -q
python -m compileall -q multiagent_framework routers
cd frontend
npm run build
```

`tests/unit/test_llm_client.py` covers environment resolution, non-persistence of plaintext secrets, limit clamping, display redaction, and the `max_concurrent=1` stream-fallback regression.


## Task-aware model configuration (Phase 1)

Copy `config/models.example.json` to ignored local `config/models.json`, then configure only environment-variable names in Provider entries. Never add `api_key` to a Provider; the loader rejects plaintext keys.

Core task policy names:

| Task | Current caller |
|---|---|
| `router.simple` | reserved for deterministic/hybrid routing phase |
| `router.ambiguous` | Router semantic arbitration |
| `agent.turn` | official and institution Agents |
| `action.simulation` | ActionAgent world simulation |
| `memory.compress` | long-memory summarization |
| `tianji.chat` | TianJi interactive debugging |

Each task maps to an ordered list of model profile names. The first compatible profile is tried first; Provider/model errors fall through to later profiles. Tool calls skip profiles that explicitly lack the `tools` or `function_calling` capability. Each profile owns an independent concurrency semaphore.

Read-only diagnostics:

```text
GET /api/debug/model-config
GET /api/debug/llm-metrics
GET /api/debug/llm-traces?limit=100&task_type=action.simulation
```

`GET /api/debug/model-config` is safe for DLC Studio profile dropdowns. It intentionally omits Provider URLs, environment-variable names, local source paths, credentials, and Authorization metadata.

For security, change Provider `base_url` and `api_key_env` only in the ignored local configuration file. The settings page shows the active environment-variable name as read-only. A remote `base_url` change is accepted only together with a new session-only key, and the previous environment secret is never reused for the new host.

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

Never place API keys in these files. Profiles reference `api_key_env`; the environment provides the secret. Promotion artifacts, mappings, previews, logs, and error responses must not contain Provider URLs, environment variable names, credentials, Prompt/message/memory text, filesystem paths, Agent/Trace/command/tool-call IDs, or raw exceptions.

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

The complete local release gate for version `0.10.0` is **360 Python tests**, **6 frontend tests**, frontend lint/build, Python compile, Prompt contracts at **100%**, deterministic trajectory replay at **100%**, and `git diff --check`.
