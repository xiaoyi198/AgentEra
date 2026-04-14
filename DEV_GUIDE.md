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
| 随机事件生成器 | ❌ 未实现 | 周期性生成灾害/民变 |
| 时间模拟器 | ❌ 未实现 | 科学计算过程耗时 |

### 两套 FC 工具集

> 所有工具定义集中在 `fc_tools.py`，处理逻辑集中在 `fc_handlers.py`。
> 添加新工具只需在这两个文件各加一处，Agent 文件无需改动。

**Agent 工具**（官员/机构 Agent 使用）：

| 工具 | 作用 |
|------|------|
| `submit_action(action, content, original_intent, is_distorting, urgency, metadata?)` | 表达意图：do_nothing / report_message / do_something；`metadata` 为可选字典 |
| `request_action(action_type, target_location, description, resources)` | 触发实际行动（调查/赈灾/抓捕等），绕过 Router 直接给 ActionAgent |

**Router 工具**（世界运行法则使用）：

| 工具 | 作用 |
|------|------|
| `list_agents(location_id?)` | 列出所有可唤醒 Agent 的 ID 和基本信息，确认合法目标 |
| `wake_agent(target, message, from_agent)` | 直接唤醒目标 Agent，投递消息 |
| `create_timer(target, delay_days, message, from_agent, urgency?)` | 创建延迟定时器，N 天后送达；`urgency` 为可选传递系数 |
| `execute_policy(policy, location, executor, from_agent?, description?)` | 调用 ActionAgent 执行政策效果；可选 `from_agent` 和 `description` |
| `relay_decree(decree, targets, urgency?)` | 传递皇帝圣旨给指定官员；`urgency` 为可选传递系数 |

**ActionAgent 工具**（行动模拟使用）：

| 工具 | 作用 |
|------|------|
| `list_entities(entity_type?)` | 列出所有实体的 ID、类型和名称，确认合法实体 ID |
| `list_agents(location_id?)` | 列出所有可唤醒 Agent 的 ID 和基本信息 |
| `query_entity(entity_id)` | 查询实体的描述与当前状况 |
| `query_inventory(entity_id)` | 查询实体的库存 |
| `update_entity_status(entity_id, new_status)` | 更新实体状况 |
| `update_inventory(entity_id, category, item_name, quantity_delta)` | 增减库存 |
| `complete_action(result_description, duration_days, success)` | 结束行动，返回结果 |

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
             → ActionAgent 模拟行动（查询实体/更新状态/库存）
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
| WorldState | "这个地方/机构发生了什么、有多少粮"（动态层） | ActionAgent 持续更新 |
| 两者通过相同 ID 关联（如 `location_id="吴县"` 在两边都有） | | |

## 关键文件说明

### multiagent_framework/fc_tools.py

FC 工具定义的中央注册表：

- 每个工具以具名常量定义（`WAKE_AGENT_TOOL`, `LIST_ENTITIES_TOOL` 等），共 13 个
- 预组合工具集：
  - `ACTION_AGENT_TOOLS` — `list_entities / query_entity / query_inventory / update_entity_status / update_inventory + list_agents + complete_action`（7 个）
  - `ROUTER_TOOLS` — `list_agents / wake_agent / create_timer / execute_policy / relay_decree`
  - `AGENT_TOOLS` — `submit_action / request_action`
  - `ALL_TOOLS` — 天玑用，全量 13 个工具
- **添加新工具**：在此文件定义工具 schema，加入对应分组；Agent 代码无需改动

> **注意**：`RouterCommandType` 枚举包含 `BROADCAST` 和 `NO_OP`，但 `fc_handlers.py` 中没有对应 handler。如果 Router 返回这两种类型，`dispatch_fc` 会返回 `{"ok": False, "error": "未知 FC"}`。

### multiagent_framework/fc_handlers.py

FC 处理函数的中央注册表：

- `FCContext` — 执行上下文数据类，打包所有依赖：
  - `world_state`, `court`, `timers`, `chain_log`, `action_agent`
  - `pending_wakeups`, `run_action`, `agent_tasks` — Orchestrator 共享引用
  - `sim_time`, `seconds_per_day`（**始终为 86400 游戏秒/天常数，非速度倍率**）, `caller_id`, `chain_id`, `source_action`
  - `changes_made` — ActionAgent 收集世界状态变更
- **每个 FC 工具一个 handler**，全部注册在 `FC_HANDLERS: dict[str, (handler, is_async)]`：
  - 世界状态组：`handle_query_entity / handle_query_inventory / handle_update_entity_status / handle_update_inventory`
  - `handle_complete_action` — 纯解析，返回结构化结果，调用方决定收尾
  - Router 组（async）：`handle_wake_agent / handle_create_timer / handle_execute_policy / handle_relay_decree`
- `dispatch_fc(name, ctx, args)` — 统一分发入口，async-aware
- `command_type_to_fc_name(cmd_type)` — RouterCommandType 枚举 → FC 工具名
- `command_to_fc_args(cmd)` — RouterCommand → FC 参数 dict（Orchestrator 桥接用）
- `parse_submit_action / parse_request_action` — FC args → AgentAction
- `parse_agent_fc_response(response, agent_id)` — LLM 响应 → `list[AgentAction]`（支持双 FC 同时返回 submit_action + request_action）
- `_resolve_entity_id(query, world_state)` — 模糊实体 ID 匹配辅助函数（ID 精确匹配 → 名称包含 → 类型包含）

所有数据结构定义。修改字段时注意同步：
- `TimerEntry` — timer_system.py 的序列化/反序列化；完整字段：timer_id, fire_at, target_agent, payload, chain_id, parent_timer_id, created_at, cancelled
- `ChainLogEntry` — chain_log.py 的序列化/反序列化；完整字段：entry_id, event_id, sim_time, caller_agent_type, fc_called, distortion_note, hop_count, routed_to, timer_id, trigger_at, wakeup_type, status
- `TickResult` — 前端 WebSocket 接收的数据格式
- `AgentAction` — agent_runtime.py 的 FC 解析；额外字段：agent_id, urgency（默认"normal"）, metadata（可选字典）
- `RouterCommand` — router.py 的输出 + orchestrator.py 的执行；额外字段：targets（BROADCAST 用列表）, delay_seconds, source_action
- `ActionResult` — action_agent.py 的输出，送回发起者；额外字段：error, fc_trace
- `ActionResult` — action_agent.py 的输出，送回发起者
- `EventObject` — daming_court/loader.py 的事件加载；额外字段：generated_at（默认 0.0）
- `PlayerAction` — 皇帝操作；额外字段：note（备注）
- `TickResult` — 前端 WebSocket 接收的数据格式；有 `to_dict()` 方法

### multiagent_framework/orchestrator.py

主循环关键逻辑：
- 属性：`sim_time`（游戏时间）、`sim_day`（游戏天数）、`is_paused`、`is_running` — 运行状态查询
- `run(tick_interval=0.25)` — tick 循环，用 `time.monotonic()` 推进游戏时间
- `_tick()` — 检查定时器 → 派发唤醒（皇帝奏折 vs Agent 调用）→ 处理玩家操作
  - 同一 Agent 的多个唤醒消息会自动合并（拼接为一条 payload）
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
- `_handle_player_action()` — 确定性逻辑，皇帝→太监→逐级传达
- `_fuzzy_find_agent()` — Router 返回模糊 ID 时的容错匹配（ID包含关键词 → 地点匹配 → 名字匹配）
- `save_state()` — 将内存定时器刷到 SQLite（`timers.save_all()`）；存档的数据库热备份、chain log 拷贝、runtime.json 写入由 `main.py` 的 `/api/save` 完成
- `load_state(state)` — 恢复 Agent 记忆（从 runtime.json 传入的 `agent_memories`）；SQLite 和 chain log 的文件替换由 `main.py` 的 `/api/load` 完成
- `register_agent(agent)` / `get_agent(agent_id)` — Agent 注册与查询
- `inject_player_action(action)` — async，注入玩家操作到队列
- `set_time_scale(seconds_per_day)` — 设置时间倍率（需暂停）
- `pause()` / `resume()` / `stop()` — 模拟控制
- `drain_agent_tasks(timeout)` — async，等待所有后台 Agent 任务完成

### multiagent_framework/router.py

Router 是 LLM 驱动的，不是规则引擎：
- `process_batch(actions)` — 批量处理多个 AgentAction，返回 RouterCommand 列表
- `_build_batch_description()` — 构建给 LLM 的描述，包含：
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
- `BaseAgent` — 抽象基类，管理 memory（deque）、system prompt、LLM 调用
  - `serialize_memory()` — 将记忆序列化为可持久化的 v2 dict（`{version: 2, summary, messages}`），兼容 v1 list 格式反序列化
  - `deserialize_memory(data)` — 从 v2 dict 或 v1 list 恢复 deque（读档用）
  - `add_memory(role, content)` — 添加一条记忆
  - `get_messages()` — 构建消息列表（支持摘要压缩后拼接）
  - `set_compressor(compressor)` — 注入记忆压缩器
  - `_maybe_compress()` — 自动压缩过长记忆（超过阈值时触发 LLM 摘要）
  - `_record_response_to_memory()` — 格式化 LLM 响应并写入记忆
- `AgentConfig` — Agent 配置：llm_client, system_prompt, tools, personality_seed, compress_threshold（默认 40）, keep_recent（默认 20）
- `OfficialAgent.process()` — 官员 Agent 主入口，返回 `list[AgentAction]`（支持同时 submit_action + request_action）：
  - 将事件/消息加入记忆
  - 构建 messages（system + memory）
  - 调用 LLM（tools=AGENT_TOOLS）
  - 调用 `parse_agent_fc_response()`（来自 fc_handlers）解析双 FC 响应
  - 记录自己的行动到记忆
- `InstitutionAgent.process()` — 机构 Agent，逻辑类似，同样返回 `list[AgentAction]`

### multiagent_framework/action_agent.py

行动模拟 Agent（独立于 BaseAgent 类层次）：
- `agent_id` 属性 — 固定返回 `"action_agent"`
- `agent_type` 属性 — 固定返回 `"world_sim"`
- `process(payload)` — 模拟一次行动：
  - 构建动态 system prompt（注入世界状态上下文）
  - 多轮 FC 循环（最多 max_rounds=5 轮）：
    - 组装 `FCContext(world_state=..., changes_made=..., caller_id=...)` 后调用 `dispatch_fc`
    - 直到 `complete_action` 或达到最大轮次
  - 返回 ActionResult
- `_build_system_prompt()` — 加载 prompt 模板，填充字段并注入世界上下文
- `_load_template()` — 加载 action_agent.txt prompt 模板

### multiagent_framework/tianji_agent.py

天玑调试 Agent（独立于 Orchestrator）：
- `TianJiAgent` — 与用户对话，注入全部工具集（ALL_TOOLS，11 个）
- 构造参数：`llm_client / world_state / court_graph / timer_system / chain_log / action_agent / router / orchestrator（可选）`
- **注意**：当前 `main.py` 创建 TianJiAgent 时未传入 orchestrator，也未调用 `attach_orchestrator()`，因此 `wake_agent` 和 `relay_decree` 无法正常工作（`pending_wakeups` 为 None）。`create_timer` 仍可工作（直接使用 timer_system），`execute_policy` 走 ActionAgent 直连路径仍可工作。
- `attach_orchestrator(orch)` — 反向注入 Orchestrator，共享 `sim_time / pending_wakeups / _agent_tasks`；使天玑 `wake_agent / relay_decree` 等产生与主模拟一致的真实副作用
- `reset()` — 清空 messages，重新注入 system prompt
- `chat(user_text)` — 主入口，async generator，yield SSE 事件流：
  - `{"type": "assistant_delta", "text": "..."}` — LLM 文本
  - `{"type": "tool_call", "name": "...", "arguments": {...}}` — 工具调用
  - `{"type": "tool_result", "name": "...", "result": {...}}` — 工具结果
  - `{"type": "tool_round_start", "round": N}` — 工具轮开始
  - `{"type": "error", "text": "..."}` — LLM 调用错误
  - `{"type": "done"}` — 本轮结束
- `_build_ctx()` — 组装 `FCContext`，当挂载了 Orchestrator 时共享其运行时引用
- `_dispatch_tool(name, args)` — 统一分发：`submit_action` → Router；`request_action` → `execute_policy` 路径；其余 → `dispatch_fc`
- `_summarize_result(result)` — 工具结果摘要辅助函数，用于 chain_log 展示
- `_CHAIN_PREFIX = "tianji_"` — 天玑 chain_log 前缀常量
- `_load_system_prompt()` — 加载 system prompt，缺失时使用 `_DEFAULT_SYSTEM_PROMPT`
- 所有工具调用以 `tianji_` 前缀写入 chain_log，上帝视角可见且不污染主模拟

### multiagent_framework/world_state.py

世界状态数据库（与 CourtGraph 并列）：
- `Entity` — 世界中的实体（id/type/name/description/current_status/metadata）
- `InventoryItem` — 实体的库存（entity_id/category/item_name/quantity）
- `WorldState` 类：
  - `get_entity()`, `upsert_entity()`, `update_status()` — 实体 CRUD
  - `delete_entity()` — 删除实体（级联删除库存）
  - `get_entities_by_type(entity_type)` — 按类型筛选实体
  - `get_all_entities()` — 获取所有实体
  - `search_entities(keyword)` — 模糊搜索实体（ID/名称包含关键词）
  - `get_inventory()`, `set_inventory()`, `update_inventory()` — 库存 CRUD
  - `delete_inventory_item()` — 删除单条库存记录
  - `get_context_for_action(location_id, executor_id)` — 为 ActionAgent 构建上下文
  - `export_all()` — 导出全部数据（供 API 使用）
  - `bulk_load(entities, inventory)` — 批量导入实体和库存
  - `restore_all(data)` — 从导出数据完整恢复

### multiagent_framework/court_graph.py

层级图：
- `Official` — 官员数据（id/name/rank/location_id/role/agent_type/superior_id/personality_seed/enabled/extra_prompt）
- `Location` — 地理位置（id/name/region/x/y），平面直角坐标系，以京城为原点，单位：里
- `distance_li(loc1, loc2)` — 计算两地点间直线距离（里）
- `TravelCoeff` — 传递系数：URGENT=0.5, NORMAL=1.0, SLOW=2.0
- `TRAVEL_SPEED_LI_PER_DAY` = 300（基准速度：快马日夜兼程）
- `MIN_TRAVEL_DAYS` = 0.1（同城传递最短耗时）
- `CourtGraph` 类：
  - `get_official()`, `get_superior()`, `get_subordinates()` — 层级查询
  - `get_chain_to_emperor()` — 完整上报链
  - `get_distance(from_id, to_id, mode)` — 通信耗时（天），mode 可选 TravelCoeff
  - `get_officials_at_location()` — 按地点查询
  - `get_all_locations()` — 获取所有地点
  - `get_all_officials()` — 获取所有官员
  - `get_officials_by_rank(rank)` — 按品级查询
  - `get_location(location_id)` — 获取单个地点
  - `add_official(official)` — 添加官员
  - `add_location(location)` — 添加地点
  - `bulk_load_officials(officials)` — 批量加载官员
- 旧数据库自动迁移：检测缺失列并 `ALTER TABLE ADD COLUMN`

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
  - `LLMConfig.from_file(path)` — 从 JSON 文件加载配置
  - `LLMConfig.save_to_file(path)` — 保存配置到文件
  - `LLMConfig.to_display_dict()` — 返回脱敏字典（api_key 打码显示）
- `LLMResponse` — LLM 响应数据类（content, tool_calls, input_tokens, output_tokens, latency_ms, raw）
- `LLMClient.chat()` — 异步调用（非流式），自动重试，并发控制
- `LLMClient.chat_stream()` — 流式调用，yield delta/tool_call_delta/done 事件；出错时退化为非流式
- `LLMClient.close()` — 关闭 HTTP 客户端
- `LLMClient.config` — 属性，当前配置
- `TokenCounter` — 累计 token 统计
- `update_config()` — 热更新配置

### multiagent_framework/memory_compressor.py

LLM 记忆压缩器：
- `LLMMemoryCompressor` — 当 Agent 记忆超过阈值时，用 LLM 摘要压缩旧记忆
- `compress(messages, keep_recent=20)` — 压缩记忆：保留最近 N 条，对更早的内容生成摘要
- 压缩后的记忆格式（v2）：`{version: 2, summary: "...", messages: [...]}`
- 阈值由 `AgentConfig.compress_threshold` 控制（默认 40 条）
- Orchestrator 初始化时创建并注入到各 Agent

### daming_court/loader.py

DLC 数据加载：
- `load_officials()` — JSON → Official 列表
- `load_locations()` — JSON → Location 列表
- `load_personas()` — 加载人格种子配置
- `load_world_state()` — JSON → 实体 + 库存，批量导入 WorldState
- `load_events()` — JSON → EventObject 列表
- `create_agents()` — 为每个官员创建 Agent 实例
- `_build_system_prompt()` — Prompt 优先级：专用文件 > 模板 > 默认

### main.py

FastAPI 后端入口：
- `init_orchestrator(load_from_save=False)` — 从配置文件初始化 Orchestrator + TianJiAgent（LLM/时间/CourtGraph/Timer/ChainLog/Router/ActionAgent/WorldState）
  - 创建 `LLMMemoryCompressor` 并注入各 Agent
  - CourtGraph、TimerSystem、WorldState 共用同一个 `daming_court.db` SQLite 文件
- `_load_dlc(orch, court, llm, world_state, load_from_save=False)` — 加载大明朝廷 DLC
  - `load_from_save=False`：完整加载（世界状态 JSON + 官员/地点坐标/Agent + 初始事件注入）
  - `load_from_save=True`：读档模式，跳过世界状态 JSON 覆盖和初始事件注入，仅重建 Agent 对象（记忆由 `load_state()` 恢复）
  - 官员加载后会调用 `load_locations()` 将 `officials.json` 中的坐标同步写入 `CourtGraph.locations`，保证 WorldStateView 坐标面板数据源一致
- `broadcast_tick()` — WebSocket 广播 tick 结果
- REST API 见 [PROJECT_DOC.md](PROJECT_DOC.md) 第五节
- WebSocket `/ws` — 实时 tick 推送 + 接收玩家操作

> 前端组件（App.jsx、GodView.jsx、TianJiView.jsx、AgentEditor.jsx、WorldStateView.jsx）的详细说明见 [PROJECT_DOC.md](PROJECT_DOC.md) 第八节。

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

2. 如果是新地点，还需在 `locations` 数组添加：
```json
{
  "id": "松江",
  "name": "松江府",
  "region": "江南",
  "x": 350,
  "y": -1600
}
```

> 地点用平面直角坐标系，以京城 (0,0) 为原点，单位：里（1 里 ≈ 500 米）。

3. （可选）如需自定义 prompt，创建 `daming_court/prompts/松江知府_陈五.txt`

4. 重启后端，自动加载

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
      "current_status": "蝗灾肆虐，庄稼受损"
    }
  ],
  "inventory": [
    {
      "entity_id": "吴县",
      "category": "粮食",
      "item_name": "稻米",
      "quantity": 5000,
      "description": "石"
    }
  ]
}
```

## 如何调整 Prompt

三种方式（不需要改代码）：

1. **前端上帝视角** — 时间线、Router 日志、ActionAgent 执行日志、定时器状态（Prompt 编辑器已移至独立管理页面）
2. **Agent 编辑器** — 可视化编辑人格种子，预览 prompt 渲染结果
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
            ├─ WAKE_AGENT → 加入 _pending_wakeups（下次 tick 处理）
            ├─ CREATE_TIMER → timers.schedule()
            ├─ EXECUTE_POLICY → _run_action()（后台 Task）
            └─ RELAY_DECREE → 为每个 target 加入 _pending_wakeups
```

### ActionAgent 执行流程

```
Orchestrator._run_action(payload, source_agent_id, chain_id)
  │
  ├─ action_agent.process(payload) → ActionResult
  │   ├─ 构建 system prompt（注入世界状态上下文）
  │   ├─ 多轮 FC 循环（最多5轮）：
  │   │   ├─ LLM 调用 → 解析工具调用
  │   │   ├─ query_entity / query_inventory → 返回数据
  │   │   ├─ update_entity_status / update_inventory → 修改世界状态
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
Orchestrator._handle_player_action(action)
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
