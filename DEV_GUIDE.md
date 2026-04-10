# 开发指南 — 大明王朝·朝堂风云

## 项目结构

```
世界模拟器/
├── multiagent_framework/        # 框架层（通用，可复用于其他 DLC）
│   ├── types.py                 # 所有数据结构：TimerEntry, ChainLogEntry, AgentAction, RouterCommand, TickResult, PlayerAction, ActionResult
│   ├── timer_system.py          # 定时器：heapq + SQLite 持久化
│   ├── chain_log.py             # 调用链日志：JSONL 追加写入 + 内存索引
│   ├── court_graph.py           # 层级图：SQLite 存储官员/地理，查询上下级/距离
│   ├── llm_client.py            # LLM 客户端：OpenAI-compatible，自动重试，token 计数，debug 日志
│   ├── fc_tools.py              # FC 工具定义中央注册表（所有工具 schema，按 Agent 分组）
│   ├── fc_handlers.py           # FC 处理函数中央注册表（每工具一个 handler，统一分发）
│   ├── agent_runtime.py         # Agent 基类 + OfficialAgent + InstitutionAgent（含记忆序列化）
│   ├── action_agent.py          # 行动模拟 Agent：读写世界状态，多轮 FC 循环
│   ├── tianji_agent.py          # 天玑调试 Agent：注入全工具集，独立于 Orchestrator
│   ├── world_state.py           # 世界状态数据库：实体（地点/人物/机构）+ 库存管理
│   ├── router.py                # 世界运行法则（Router）：LLM 驱动，解析意图→路由→定时器
│   └── orchestrator.py          # 主循环：实时推进，后台 Agent 调用，暂停/存档/读档
│
├── daming_court/                # 大明朝廷 DLC
│   ├── loader.py                # 数据加载：JSON→官员/Agent/事件/世界状态
│   ├── config/
│   │   ├── officials.json       # 官员数据（ID/品级/位置/上级/人格/启用状态）
│   │   ├── personas.json        # 人格模板（贪腐/忠直/圆滑）
│   │   ├── events.json          # 初始事件（蝗灾）
│   │   └── world_state.json     # 初始世界状态（地点描述/库存）
│   └── prompts/
│       ├── official_template.txt # 官员通用 prompt 模板
│       ├── router.txt           # Router system prompt
│       ├── action_agent.txt     # 行动 Agent prompt 模板
│       ├── tianji.txt           # 天玑 AI system prompt（可选，缺失时使用内置默认）
│       └── {官员ID}.txt         # 某官员的专用 prompt（可选，优先级最高）
│
├── config/
│   ├── llm.json                 # LLM API 配置（base_url, api_key, model）
│   └── time.json                # 时间倍率（seconds_per_day）
│
├── frontend/                    # React + Vite 前端
│   └── src/
│       ├── main.jsx             # 前端入口（皇帝视角）
│       ├── App.jsx              # 皇帝视角主界面（奏折/批复/在途公文/设置）
│       ├── GodView.jsx          # 上帝视角调试界面（时间线/Router日志/定时器/Prompt编辑）
│       ├── WorldStateView.jsx   # 世界状态管理（实体/库存 CRUD + 地点坐标编辑）
│       ├── AgentEditor.jsx      # Agent 编辑器（官员/地点/人格的可视化编辑）
│       ├── TianJiView.jsx       # 天玑 AI 调试页（三栏：工具列表/对话/世界状态）
│       ├── editor-main.jsx      # 编辑器独立入口（editor.html）
│       ├── tianji-main.jsx      # 天玑独立入口（tianji.html）
│       ├── App.css / editor.css / tianji.css  # 样式
│       ├── index.css            # 全局样式
│       └── hooks/useWebSocket.js
│
├── tests/
│   ├── unit/                    # pytest 单元测试
│   └── integration/             # 集成测试
│
├── saves/
│   └── save.json                # 存档文件（单存档槽位：sim_time/agent_memories/pending_memorials）
├── daming_court.db              # SQLite 数据库（官员/地点/定时器/实体/库存）
├── chain_log.jsonl              # 调用链日志文件
├── debug_llm.jsonl              # LLM 调用调试日志
├── main.py                      # FastAPI 后端入口
├── start.bat / start.sh         # 一键启动
└── requirements.txt
```

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

**Agent 工具**（官员/机构 Agent 使用，二选一）：

| 工具 | 作用 |
|------|------|
| `submit_action(action, content, original_intent, is_distorting, urgency)` | 表达意图：do_nothing / report_message / do_something |
| `request_action(action_type, target_location, description, resources)` | 触发实际行动（调查/赈灾/抓捕等），绕过 Router 直接给 ActionAgent |

**Router 工具**（世界运行法则使用）：

| 工具 | 作用 |
|------|------|
| `wake_agent(target, message, from_agent)` | 直接唤醒目标 Agent，投递消息 |
| `create_timer(target, delay_days, message, from_agent)` | 创建延迟定时器，N 天后送达 |
| `execute_policy(policy, location, executor)` | 调用 ActionAgent 执行政策效果 |
| `relay_decree(decree, targets)` | 传递皇帝圣旨给指定官员 |

**ActionAgent 工具**（行动模拟使用）：

| 工具 | 作用 |
|------|------|
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

- 每个工具以具名常量定义（`WAKE_AGENT_TOOL`, `CREATE_TIMER_TOOL` 等），共 11 个
- 预组合工具集：
  - `WORLD_STATE_TOOLS` — 世界状态读写（不含 complete_action）
  - `ACTION_AGENT_TOOLS` — `WORLD_STATE_TOOLS + COMPLETE_ACTION_TOOL`
  - `ROUTER_TOOLS` — `wake_agent / create_timer / execute_policy / relay_decree`
  - `AGENT_TOOLS` — `submit_action / request_action`
  - `ALL_TOOLS` — 天玑用，全量 11 个工具
- **添加新工具**：在此文件定义工具 schema，加入对应分组；Agent 代码无需改动

### multiagent_framework/fc_handlers.py

FC 处理函数的中央注册表：

- `FCContext` — 执行上下文数据类，打包所有依赖：
  - `world_state`, `court`, `timers`, `chain_log`, `action_agent`
  - `pending_wakeups`, `run_action`, `agent_tasks` — Orchestrator 共享引用
  - `sim_time`, `seconds_per_day`, `caller_id`, `chain_id`, `source_action`
  - `changes_made` — ActionAgent 收集世界状态变更
- **每个 FC 工具一个 handler**，全部注册在 `FC_HANDLERS: dict[str, (handler, is_async)]`：
  - 世界状态组：`handle_query_entity / handle_query_inventory / handle_update_entity_status / handle_update_inventory`
  - `handle_complete_action` — 纯解析，返回结构化结果，调用方决定收尾
  - Router 组（async）：`handle_wake_agent / handle_create_timer / handle_execute_policy / handle_relay_decree`
- `dispatch_fc(name, ctx, args)` — 统一分发入口，async-aware
- `command_type_to_fc_name(cmd_type)` — RouterCommandType 枚举 → FC 工具名
- `command_to_fc_args(cmd)` — RouterCommand → FC 参数 dict（Orchestrator 桥接用）
- `parse_submit_action / parse_request_action` — FC args → AgentAction
- `parse_agent_fc_response(response, agent_id)` — LLM 响应 → AgentAction（官员/机构 Agent 用）

所有数据结构定义。修改字段时注意同步：
- `ChainLogEntry` — chain_log.py 的序列化/反序列化
- `TickResult` — 前端 WebSocket 接收的数据格式
- `AgentAction` — agent_runtime.py 的 FC 解析
- `RouterCommand` — router.py 的输出 + orchestrator.py 的执行
- `ActionResult` — action_agent.py 的输出，送回发起者
- `EventObject` — daming_court/loader.py 的事件加载

### multiagent_framework/orchestrator.py

主循环关键逻辑：
- `run(tick_interval=0.25)` — tick 循环，用 `time.monotonic()` 推进游戏时间
- `_tick()` — 检查定时器 → 派发唤醒（皇帝奏折 vs Agent 调用）→ 处理玩家操作
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
- `save_state()` — 存档：需先暂停，调用 `timers.save_all()`，序列化所有 Agent 记忆，返回含 sim_time / seconds_per_day / agent_memories / timer_count 的 dict
- `load_state(state)` — 读档：恢复 sim_time + 速度倍率，从 SQLite 加载定时器，从 JSONL 加载 chain log，逐 Agent 恢复记忆

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
- `last_response_text` — 记录 LLM 原始响应供调试
- `MAX_HOPS = 10` — 最大跳数，防止无限循环

### multiagent_framework/agent_runtime.py

Agent 运行时：
- `BaseAgent` — 抽象基类，管理 memory（deque）、system prompt、LLM 调用
  - `serialize_memory()` — 将 deque 序列化为可持久化的 list（存档用）
  - `deserialize_memory(data)` — 从 list 恢复 deque（读档用）
- `OfficialAgent.process()` — 官员 Agent 主入口：
  - 将事件/消息加入记忆
  - 构建 messages（system + memory）
  - 调用 LLM（tools=AGENT_TOOLS）
  - 调用 `parse_agent_fc_response()`（来自 fc_handlers）解析双 FC 响应
  - 记录自己的行动到记忆
- `InstitutionAgent.process()` — 机构 Agent，逻辑类似

### multiagent_framework/action_agent.py

行动模拟 Agent（独立于 BaseAgent 类层次）：
- `process(payload)` — 模拟一次行动：
  - 构建动态 system prompt（注入世界状态上下文）
  - 多轮 FC 循环（最多 max_rounds=5 轮）：
    - 组装 `FCContext(world_state=..., changes_made=..., caller_id=...)` 后调用 `dispatch_fc`
    - 直到 `complete_action` 或达到最大轮次
  - 返回 ActionResult
- `_build_system_prompt()` — 加载 prompt 模板，填充字段并注入世界上下文

### multiagent_framework/tianji_agent.py

天玑调试 Agent（独立于 Orchestrator）：
- `TianJiAgent` — 与用户对话，注入全部工具集（ALL_TOOLS，11 个）
- 构造参数：`llm_client / world_state / court_graph / timer_system / chain_log / action_agent / router / orchestrator（可选）`
- `attach_orchestrator(orch)` — 反向注入 Orchestrator，共享 `sim_time / pending_wakeups / _agent_tasks`；使天玑 `wake_agent / create_timer` 等产生与主模拟一致的真实副作用
- `reset()` — 清空 messages，重新注入 system prompt
- `chat(user_text)` — 主入口，async generator，yield SSE 事件流：
  - `{"type": "assistant_delta", "text": "..."}` — LLM 文本
  - `{"type": "tool_call", "name": "...", "arguments": {...}}` — 工具调用
  - `{"type": "tool_result", "name": "...", "result": {...}}` — 工具结果
  - `{"type": "tool_round_start", "round": N}` — 工具轮开始
  - `{"type": "done"}` — 本轮结束
- `_build_ctx()` — 组装 `FCContext`，当挂载了 Orchestrator 时共享其运行时引用
- `_dispatch_tool(name, args)` — 统一分发：`submit_action` → Router；`request_action` → `execute_policy` 路径；其余 → `dispatch_fc`
- 所有工具调用以 `tianji_` 前缀写入 chain_log，上帝视角可见且不污染主模拟

### multiagent_framework/world_state.py

世界状态数据库（与 CourtGraph 并列）：
- `Entity` — 世界中的实体（id/type/name/description/current_status/metadata）
- `InventoryItem` — 实体的库存（entity_id/category/item_name/quantity）
- `WorldState` 类：
  - `get_entity()`, `upsert_entity()`, `update_status()` — 实体 CRUD
  - `delete_entity()` — 删除实体（级联删除库存）
  - `get_inventory()`, `set_inventory()`, `update_inventory()` — 库存 CRUD
  - `delete_inventory_item()` — 删除单条库存记录
  - `get_context_for_action(location_id, executor_id)` — 为 ActionAgent 构建上下文
  - `export_all()` — 导出全部数据（供 API 使用）

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
- 旧数据库自动迁移：检测缺失列并 `ALTER TABLE ADD COLUMN`

### multiagent_framework/timer_system.py

定时器系统：
- `TimerEntry` — 定时器条目（heapq 按 fire_at 排序）
- `schedule()`, `cancel()`, `pop_due()` — 核心操作
- `get_in_transit()` — 获取在途公文
- `save_all()`, `load_from_db()` — SQLite 持久化

### multiagent_framework/chain_log.py

调用链日志：
- `append()` — 追加日志（内存 + JSONL 文件）
- `get_chain(chain_id)` — 按链路查询
- `get_distortions()` — 查询失真记录
- `get_withheld()` — 查询被压下的消息
- `to_god_view(chain_id)` — 上帝视角对比

### multiagent_framework/llm_client.py

LLM 客户端：
- `LLMConfig` — 配置（base_url/api_key/model/timeout_s/max_concurrent）
- `LLMClient.chat()` — 异步调用（非流式），自动重试，并发控制
- `LLMClient.chat_stream()` — 流式调用，yield delta/tool_call_delta/done 事件；出错时退化为非流式
- `TokenCounter` — 累计 token 统计
- `update_config()` — 热更新配置

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
- `_load_dlc(orch, court, llm, world_state, load_from_save=False)` — 加载大明朝廷 DLC
  - `load_from_save=False`：完整加载（世界状态 JSON + 官员/地点坐标/Agent + 初始事件注入）
  - `load_from_save=True`：读档模式，跳过世界状态 JSON 覆盖和初始事件注入，仅重建 Agent 对象（记忆由 `load_state()` 恢复）
  - 官员加载后会调用 `load_locations()` 将 `officials.json` 中的坐标同步写入 `CourtGraph.locations`，保证 WorldStateView 坐标面板数据源一致
- `broadcast_tick()` — WebSocket 广播 tick 结果
- REST API 见下方 API 一览
- WebSocket `/ws` — 实时 tick 推送 + 接收玩家操作

### frontend/src/App.jsx

皇帝视角主界面：
- 奏折列表（new_memorials）
- 批复操作（approve/reject/hold/issue_decree）
- 在途公文列表（in_transit，显示剩余时间）
- 模拟时间显示（天/时辰/刻）
- 控制面板（启动/暂停/设置/存档/读档）
  - 未运行时：显示"开始模拟" + "读档（第N天）"（有存档时）
  - 暂停时：显示"继续" + "存档" + "读档"（有存档时）
- `saveInfo` 状态：启动时从 `/api/save-info` 查询，存档成功后更新
- `handleSave` — 将当前 `memorials` 一起 POST 给 `/api/save`
- `handleLoad` — 调用 `/api/load`，恢复 simTime/memorials/inTransit/running 状态

### frontend/src/GodView.jsx

上帝视角调试界面：
- 时间线（chain log 增量拉取，自动滚动）
- 天玑 AI 的工具调用日志也会出现在此时间线（`tianji` 类型，青色高亮）
- Router 决策日志
- 定时器状态（pending + fired）
- Prompt 编辑器（official_template / router / personas，支持热更新）

### frontend/src/TianJiView.jsx

天玑 AI 调试页面（`tianji.html`）：
- 三栏布局：工具列表（按类别折叠）| 对话区 | 世界状态面板
- SSE 解析：工具调用块按 gid 幂等合并，防止 React 批量更新导致重复渲染
- `assistantText` 快照机制：在调用 `setDisplayMsgs` 前捕获当前值，防止 React 批量 updater 闭包读到已重置的空字符串
- 工具调用块可折叠，展示参数和返回值

### frontend/src/AgentEditor.jsx

Agent 编辑器（独立页面 editor.html）：
- 官员树形结构（按层级展示，支持拖拽调整上下级）
- 官员属性编辑（品级/驻地自由填写/上级/性格/启用状态/附加提示词）
- 启用/禁用切换（树上圆点一键操作）
- "活跃"标签：有 extra_prompt 的官员
- 人格种子编辑（CRUD，保存后立即热重载）
- 系统 Agent 配置占位（开发中）
- 导入/导出 JSON
- Prompt 预览（实时渲染）

### frontend/src/WorldStateView.jsx

世界状态管理（独立在主前端路由中）：
- 实体列表（按类型筛选 + 搜索）
- 5 种实体类型：地点/人物/机构/组织/物品
- 实体 CRUD（新建、编辑、删除，删除级联删库存）
- 库存行内编辑（点击单元格直接修改，回车/失焦保存）
- 新增库存行（类目自动补全：粮食/货币/兵力/物资/人口/税收）
- 地点坐标编辑（仅 location 类型显示，可编辑 X/Y/区域）

## 数据库结构

### SQLite 表结构（daming_court.db）

```sql
-- 官员表（CourtGraph 管理）
CREATE TABLE officials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    location_id TEXT,
    role TEXT NOT NULL DEFAULT 'official',      -- official / eunuch / player
    agent_type TEXT NOT NULL DEFAULT 'official', -- official / institution
    superior_id TEXT,
    personality_seed TEXT DEFAULT '忠直',
    enabled INTEGER DEFAULT 1,                  -- 是否参与模拟
    extra_prompt TEXT DEFAULT ''                 -- 附加个性化提示词
);

-- 地点表（CourtGraph 管理，平面直角坐标系，单位：里）
CREATE TABLE locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    x REAL DEFAULT 0,                           -- 东正西负（里）
    y REAL DEFAULT 0                            -- 北正南负（里）
);

-- 定时器表（TimerSystem 管理）
CREATE TABLE pending_timers (
    timer_id TEXT PRIMARY KEY,
    fire_at REAL NOT NULL,
    target_agent TEXT NOT NULL,
    payload TEXT NOT NULL,           -- JSON
    chain_id TEXT,
    parent_timer_id TEXT,
    created_at REAL,
    cancelled INTEGER DEFAULT 0
);

-- 实体表（WorldState 管理）
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,              -- location / person / institution / organization / object
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    current_status TEXT DEFAULT '',
    updated_at REAL DEFAULT 0,
    metadata TEXT DEFAULT '{}'       -- JSON
);

-- 库存表（WorldState 管理）
CREATE TABLE inventory (
    entity_id TEXT NOT NULL,
    category TEXT NOT NULL,          -- 粮食 / 货币 / 兵力 / 物资
    item_name TEXT NOT NULL,
    quantity REAL DEFAULT 0,
    description TEXT DEFAULT '',
    PRIMARY KEY (entity_id, category, item_name),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
```

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

1. **前端上帝视角** — Prompt 管理 tab，直接编辑保存（Router prompt 立即生效）
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

### 模拟控制

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/start` | POST | 启动模拟 |
| `/api/pause` | POST | 暂停（等 Agent 完成） |
| `/api/resume` | POST | 继续 |
| `/api/save` | POST | 存档（需先暂停，body: `{memorials:[...]}`，写入 `saves/save.json`） |
| `/api/load` | POST | 读档（重建 Orchestrator + 恢复状态，返回 sim_time/pending_memorials） |
| `/api/save-info` | GET | 查询是否有存档（返回 `{exists, sim_day, saved_at}`） |

### 玩家操作

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/player-action` | POST | 皇帝操作（approve/reject/hold/issue_decree） |

### 设置与配置

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/settings` | GET/POST | LLM + 时间配置（热更新） |
| `/api/time-scale` | GET/POST | 时间倍率（需暂停） |
| `/api/prompts` | GET/POST | Prompt 文件读写 |

### 调试接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/debug/timeline?after=N&limit=50` | GET | chain log 增量拉取 |
| `/api/debug/timers` | GET | 定时器状态（pending + fired） |
| `/api/debug/router-log?after=N` | GET | Router 决策日志增量拉取 |
| `/api/chain-log/{chain_id}` | GET | 单条链的上帝视角 |
| `/api/token-usage` | GET | Token 用量统计 |
| `/api/in-transit` | GET | 在途公文列表 |

### 世界状态

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/world-state` | GET | 返回所有实体 + 库存 |
| `/api/world-state/{entity_id}` | GET | 返回单个实体详情 + 库存 |
| `/api/world-state/{entity_id}` | PUT | 创建或更新实体 |
| `/api/world-state/{entity_id}` | DELETE | 删除实体（级联删除库存） |
| `/api/world-state/{entity_id}/status` | POST | 手动修改实体 current_status（调试用） |
| `/api/world-state/{entity_id}/inventory` | PUT | 新增或覆盖单条库存 |
| `/api/world-state/{entity_id}/inventory/{category}/{item_name}` | DELETE | 删除单条库存 |

### 天玑 AI

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tianji/chat` | POST | SSE 流式对话（body: `{message: str}`） |
| `/api/tianji/reset` | POST | 清空对话历史，重新注入 system prompt |
| `/api/tianji/history` | GET | 返回当前 messages（供刷新后恢复展示） |
| `/api/tianji/tools` | GET | 返回工具 schema，按类别分组（前端工具面板用） |

### Agent 编辑器

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/editor/officials` | GET | 返回完整的 officials + locations 配置 |
| `/api/editor/officials/{id}` | PUT | 更新或新增一个官员 |
| `/api/editor/officials/{id}` | DELETE | 删除一个官员 |
| `/api/editor/officials/batch` | POST | 批量保存官员（覆盖全部） |
| `/api/editor/locations/batch` | POST | 批量保存地点（同步写入 officials.json 和 CourtGraph） |
| `/api/editor/locations/{location_id}` | GET | 获取单个地点详情（含坐标） |
| `/api/editor/locations/{location_id}` | PUT | 更新单个地点（同步写入 DB 和 JSON） |
| `/api/editor/prompt-preview` | POST | 预览官员 system prompt 渲染结果 |
| `/api/editor/personas` | GET | 返回所有人格种子 |
| `/api/editor/personas/{name}` | PUT | 新增或更新人格种子 |
| `/api/editor/personas/{name}` | DELETE | 删除人格种子 |
| `/api/editor/personas/batch` | POST | 批量保存人格种子（覆盖） |

### WebSocket

| 端点 | 方向 | 说明 |
|------|------|------|
| `/ws` | S→C | 实时 tick 推送（TickResult） |
| `/ws` | C→S | 发送玩家操作（player_action） |

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
        │   ├─ agent.process(payload) → AgentAction
        │   ├─ 如果是 request_action → 绕过 Router，直接分发给 ActionAgent
        │   └─ 如果是 submit_action → 记录 chain log，返回 action
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

### 官员层级

```
皇帝 (rank=15, player)
  ├─ 司礼监太监_王振 (rank=13, eunuch, 圆滑)
  │    └─ 内阁_杨士奇 (rank=10, 忠直)
  │         └─ 通政司 (rank=9, institution)
  │              └─ 江南布政使_周忱 (rank=8, 忠直)
  │                   └─ 苏州知府_况钟 (rank=5, 忠直)
  │                        ├─ 吴县知县_张三 (rank=1, 贪腐)
  │                        └─ 昆山知县_李四 (rank=1, 忠直)
  └─ 户部 (rank=12, institution, 圆滑)
```

### 人格模板（personas.json）

- **贪腐**：倾向于隐瞒灾情以避免追责，偶尔索取好处，上报时淡化问题严重性，优先考虑自己的仕途和钱袋
- **忠直**：如实上报，不夸大不隐瞒，为百姓着想。哪怕可能得罪上级，也要把真实情况传递出去
- **圆滑**：善于揣摩上意，措辞委婉，优先保护自己。根据接收者的喜好调整措辞，既不完全说谎也不完全说实话
