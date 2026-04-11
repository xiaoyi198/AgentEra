# 世界模拟器 — 大明王朝·朝堂风云 开发文档

## 一、项目概述

这是一个基于 **多 Agent 框架 + LLM 驱动** 的古代朝廷模拟器。玩家扮演皇帝，处理来自各级官员的奏折，下达旨意。各级官员由 AI Agent 驱动，会根据自己的性格（忠直/贪腐/圆滑）产生不同的行为。

### 核心玩法
1. **事件发生** → 基层知县 Agent 被唤醒
2. **Agent 决策** → 通过 `submit_action` 表达意图（汇报/隐瞒/越级/执行）
3. **Router 路由** → LLM 解析意图，决定消息传递路径和延迟
4. **定时器等待** → 模拟古代公文传递的时间延迟
5. **逐级上报** → 知府 → 布政使 → 内阁 → 皇帝
6. **皇帝批复** → 批准/驳回/下旨，通过太监传达回基层执行

---

## 二、项目文件结构

```
世界模拟器/
├── main.py                          # 【后端入口】初始化 + lifespan + 注册路由
├── routers/                         # 【API 路由层】按功能拆分的 FastAPI Router 模块
│   ├── __init__.py
│   ├── _state.py                    # 共享全局状态（orchestrator/tianji_agent/ws_clients）
│   ├── simulation.py                # 模拟控制（start/pause/resume）+ 设置 + 天玑 AI + WebSocket
│   ├── saves.py                     # 存档管理（存/读/列表/重命名/删除）
│   ├── editor.py                    # Agent 编辑器（官员/地点/人格/Prompt CRUD）
│   ├── worldstate.py                # 世界状态（实体/库存 CRUD）
│   └── debug.py                     # 调试接口（timeline/timers/router-log/action-log）
├── requirements.txt                 # Python 依赖（fastapi, uvicorn, httpx, websockets, pydantic）
├── start.bat / start.sh             # 一键启动脚本
├── saves/
│   └── {存档名}/                    # 每个存档一个子目录（用户命名或自动生成）
│       ├── meta.json                # 元信息（save_name/saved_at/sim_day/agent_count等）
│       ├── runtime.json             # 运行时（agent_memories/pending_memorials/seconds_per_day）
│       ├── daming_court.db          # SQLite 数据库快照（热备份）
│       └── chain_log.jsonl          # 调用链日志快照
├── daming_court.db              # SQLite 数据库（官员表、地点表、定时器表、世界状态表）
├── chain_log.jsonl                  # 调用链日志（JSONL 格式，记录所有消息传递过程）
├── debug_llm.jsonl                  # LLM 调用调试日志
│
├── config/                          # 全局配置
│   ├── llm.json                     # LLM API 配置（base_url, api_key, model, timeout_s, max_concurrent）
│   └── time.json                    # 时间倍率配置（seconds_per_day；注意：tick_interval 字段存在但后端未使用）
│
├── multiagent_framework/            # 【框架层】通用多 Agent 框架，可复用于其他 DLC
│   ├── __init__.py
│   ├── types.py                     # 【核心】所有数据结构定义
│   ├── llm_client.py                # 【核心】OpenAI 兼容 LLM 客户端
│   ├── fc_tools.py                  # 【核心】FC 工具定义中央注册表（所有工具 schema）
│   ├── fc_handlers.py               # 【核心】FC 处理函数中央注册表（每工具一个 handler）
│   ├── agent_runtime.py             # 【核心】Agent 基类 + 官员/机构 Agent 实现
│   ├── action_agent.py              # 【核心】行动模拟 Agent（读写世界状态）
│   ├── tianji_agent.py              # 【调试】天玑 AI（全工具注入，独立于主循环）
│   ├── router.py                    # 【核心】世界运行法则（LLM 驱动的路由中枢）
│   ├── orchestrator.py              # 【核心】主循环，驱动整个模拟
│   ├── court_graph.py               # 朝廷层级图（官员/地点查询）
│   ├── timer_system.py              # 定时器系统（heapq + SQLite 持久化）
│   ├── chain_log.py                 # 调用链日志（JSONL 追加写入）
│   ├── world_state.py               # 世界状态数据库（实体/库存管理）
│   └── memory_compressor.py         # 【核心】LLM 记忆压缩（Agent 记忆超阈值时自动摘要）
│
├── daming_court/                    # 【DLC 层】大明朝廷内容包
│   ├── __init__.py
│   ├── loader.py                    # 数据加载器（JSON → 官员/Agent/事件）
│   ├── config/
│   │   ├── officials.json           # 官员数据（ID/品级/位置/上级/性格）
│   │   ├── personas.json            # 人格模板（贪腐/忠直/圆滑的 prompt 描述）
│   │   ├── events.json              # 初始事件（蝗灾等）
│   │   └── world_state.json         # 初始世界状态（地点描述/库存）
│   └── prompts/
│       ├── official_template.txt    # 官员通用 prompt 模板
│       ├── router.txt               # Router system prompt
│       ├── action_agent.txt         # 行动 Agent prompt 模板
│       ├── tianji.txt               # 天玑 AI system prompt（可选，缺失时使用内置默认）
│       └── {官员ID}.txt             # 某官员的专用 prompt（可选，优先级最高）
│
├── frontend/                        # 前端（React + Vite）
│   ├── src/
│   │   ├── main.jsx                 # 前端入口（皇帝视角）
│   │   ├── App.jsx                  # 【皇帝视角】主界面（奏折列表/批复/在途公文）
│   │   ├── GodView.jsx              # 【上帝视角】调试界面（时间线/Router日志/定时器/Prompt编辑）
│   │   ├── WorldStateView.jsx       # 【世界状态】实体/库存 CRUD + 地点坐标编辑
│   │   ├── AgentEditor.jsx          # 【Agent 编辑器】可视化编辑官员/地点/人格
│   │   ├── TianJiView.jsx           # 【天玑 AI】全工具调试对话页
│   │   ├── editor-main.jsx          # 编辑器独立入口
│   │   ├── tianji-main.jsx          # 天玑独立入口
│   │   ├── hooks/useWebSocket.js    # WebSocket hook
│   │   ├── App.css / editor.css     # 样式
│   │   └── index.css                # 全局样式
│   ├── index.html / editor.html / tianji.html
│   ├── package.json
│   └── vite.config.js
│
└── tests/                           # 测试
    ├── unit/                        # 单元测试
    └── integration/                 # 集成测试
```

---

## 三、核心模块详解

### 3.1 types.py — 数据结构定义

**作用**：定义框架中所有数据结构，是整个项目的数据契约。

| 数据结构 | 用途 |
|---------|------|
| `TimerEntry` | 定时器条目（timer_id/fire_at/target_agent/payload/chain_id/parent_timer_id/created_at/cancelled） |
| `ChainLogEntry` | 调用链记录（entry_id/event_id/sim_time/caller_agent_type/fc_called/distortion_note/hop_count/routed_to/timer_id/trigger_at/wakeup_type/status）；追踪信息失真与消息传递 |
| `AgentAction` | Agent 输出的行动意图（agent_id/action/content/original_intent/is_distorting/urgency/metadata） |
| `EventObject` | 系统事件（蝗灾/旱灾等）；含 generated_at 字段 |
| `RouterCommand` | Router 输出的指令（command_type/target/targets/payload/delay_seconds/chain_id/source_action） |
| `ActionResult` | ActionAgent 完成行动后的结果（含 error 和 fc_trace 字段） |
| `TickResult` | 一次 tick 推送给前端的数据；有 `to_dict()` 方法 |
| `PlayerAction` | 皇帝的操作（approve/reject/hold/issue_decree）；含 note 字段 |
| `LLMResponse` | LLM 响应数据类（content/tool_calls/input_tokens/output_tokens/latency_ms/raw） |

**关键枚举**：
- `RouterCommandType`: WAKE_AGENT, CREATE_TIMER, EXECUTE_POLICY, RELAY_DECREE, BROADCAST, NO_OP
- `PlayerActionType`: APPROVE, REJECT, HOLD, ISSUE_DECREE

> **注意**：`BROADCAST` 和 `NO_OP` 目前没有对应的 FC handler，如果 Router 返回这两种类型，`dispatch_fc` 会返回错误。

---

### 3.2 orchestrator.py — 主循环（核心调度器）

**作用**：驱动整个模拟的主循环，管理时间推进、Agent 调用、定时器检查、玩家操作处理。

**核心逻辑**：
```
run() 主循环（0.25s 间隔）
  │
  ├─ 用真实流逝时间推进 sim_time（不被 LLM 阻塞）
  │
  └─ _tick() 每帧处理
      ├─ 1. 检查到期定时器 → 加入待唤醒队列
      ├─ 2. 处理待唤醒
      │     ├─ 皇帝奏折 → 添加到 TickResult.new_memorials
      │     └─ Agent 调用 → 后台 asyncio.Task（不阻塞主循环）
      ├─ 3. 处理玩家操作队列
      ├─ 4. 收集在途公文状态
      └─ 5. Token 统计
```

**重要方法**：
| 方法 | 作用 |
|-----|------|
| `run(tick_interval=0.25)` | 主循环，实时推进 sim_time |
| `_tick()` | 单次 tick：检查定时器 → 派发唤醒 → 处理玩家操作 |
| `_process_agents_background()` | 后台并行调用 Agent → Router 批量处理 → 执行指令 |
| `_call_agent_safe()` | 调用单个 Agent，记录 chain log，异常安全 |
| `_build_fc_context()` | 组装 FCContext（sim_time / pending_wakeups / _run_action 等） |
| `_execute_commands()` | 遍历 RouterCommand，调用 dispatch_fc 统一执行 |
| `_handle_player_action()` | 处理皇帝操作：找太监 → 唤醒太监传达旨意 |
| `_fuzzy_find_agent()` | 模糊匹配 Agent（Router 返回地名/简称时容错） |
| `inject_event()` | 注入事件，为每个 target_agent 创建唤醒 |
| `save_state()` | 将内存定时器刷到 SQLite；数据库热备份/chain_log 拷贝/runtime.json 写入由 `/api/save` 完成 |
| `load_state(state)` | 从 runtime.json 的 `agent_memories` 恢复所有 Agent 记忆；SQLite/chain_log 文件替换由 `/api/load` 完成 |
| `register_agent(agent)` | 注册 Agent |
| `get_agent(agent_id)` | 按 ID 查询 Agent |
| `inject_player_action(action)` | async，注入玩家操作到队列 |
| `set_time_scale(seconds_per_day)` | 设置时间倍率（需暂停） |
| `pause()` / `resume()` / `stop()` | 模拟控制 |
| `drain_agent_tasks(timeout)` | async，等待所有后台 Agent 任务完成 |

**时间系统**：
- `sim_time`：游戏世界时间，单位游戏秒，1 游戏天 = 86400 游戏秒
- `seconds_per_day`：速度倍率，多少真实秒 = 1 游戏天
- 推进公式：`sim_time += real_elapsed * (86400 / seconds_per_day)`

---

### 3.3 router.py — 世界运行法则（LLM 驱动的路由中枢）

**作用**：接收一批 Agent 的 `submit_action`，用 LLM 解析意图，决定消息怎么传递。Router 在描述官员信息时会调用 `court.get_distance()` 获取浮点天数，并以 `距离约X.X天` 形式传给 LLM。

**FC 工具集**（Router 可调用的工具）：
| 工具 | 作用 |
|-----|------|
| `wake_agent(target, message)` | 直接唤醒目标 Agent，投递消息（0 延迟） |
| `create_timer(target, delay_days, message, from_agent, urgency?)` | 创建延迟定时器，N 天后送达；`urgency` 为可选传递系数 |
| `execute_policy(policy, location, executor, from_agent?, description?)` | 调用世界模拟 Agent 执行政策效果；可选 `from_agent` 和 `description` |
| `relay_decree(decree, targets, urgency?)` | 传递皇帝圣旨给指定官员；`urgency` 为可选传递系数 |

> FC 工具参数中的 `?` 后缀表示可选参数。上述完整参数列表与 DEV_GUIDE 中 Agent/Router 工具表一致。

**重要方法**：
| 方法 | 作用 |
|-----|------|
| `process_batch(actions)` | 批量处理 AgentAction，返回 RouterCommand 列表 |
| `_build_batch_description()` | 构建给 LLM 的描述（含官员 ID 列表、层级关系、直属下级、距离天数） |
| `_parse_commands()` | 解析 LLM 的 function call 响应为 RouterCommand 列表 |
| `_fc_to_command()` | 将单个 FC 调用转换为 RouterCommand |
| `update_time_scale(seconds_per_day)` | 更新时间倍率（影响距离计算中的时间缩放） |
| `_default_prompt()` | 默认 Router system prompt |

---

### 3.4 fc_tools.py — FC 工具定义注册表

**作用**：所有 FC 工具的唯一定义来源。各 Agent 按需 import 所需工具分组，不再各自维护。

| 分组常量 | 用途 |
|---------|------|
| `WORLD_STATE_TOOLS` | 世界状态读写（4 个，不含 complete_action） |
| `ACTION_AGENT_TOOLS` | `WORLD_STATE_TOOLS + complete_action` |
| `ROUTER_TOOLS` | `wake_agent / create_timer / execute_policy / relay_decree` |
| `AGENT_TOOLS` | `submit_action / request_action` |
| `ALL_TOOLS` | 天玑用，全量 11 个工具 |

**扩展规则**：新增工具只需在此文件定义 schema + 加入对应分组，无需改动任何 Agent 文件。

---

### 3.5 fc_handlers.py — FC 处理函数注册表

**作用**：每个 FC 工具有且仅有一个 handler。所有调用方（Orchestrator / ActionAgent / TianJiAgent）组装 `FCContext` 后调用 `dispatch_fc`，行为完全一致。

**核心抽象**：

`FCContext` 数据类打包所有依赖：
- `world_state`, `court`, `timers`, `chain_log`, `action_agent`
- `pending_wakeups`, `run_action`, `agent_tasks` — Orchestrator 共享引用（可选）
- `sim_time`, `seconds_per_day`（**始终为 86400 游戏秒/天常数，非速度倍率**）, `caller_id`, `chain_id`, `source_action`
- `changes_made` — ActionAgent 收集世界状态变更列表

**关键函数**：
| 函数 | 说明 |
|------|------|
| `dispatch_fc(name, ctx, args)` | 统一分发入口，async-aware |
| `command_type_to_fc_name(cmd_type)` | RouterCommandType → FC 工具名 |
| `command_to_fc_args(cmd)` | RouterCommand → FC 参数 dict |
| `parse_agent_fc_response(response, agent_id)` | LLM 响应 → `list[AgentAction]`（支持双 FC 同时返回） |
| `parse_submit_action / parse_request_action` | FC args → AgentAction |
| `_resolve_entity_id(query, world_state)` | 模糊实体 ID 匹配（ID 精确匹配 → 名称包含 → 类型包含） |

**扩展规则**：新增工具只需写一个 handler 函数，加入 `FC_HANDLERS` 字典即可。

---

### 3.7 agent_runtime.py — Agent 运行时

**作用**：Agent 基类定义 + 官员/机构 Agent 实现。

**类层次**：
```
BaseAgent (抽象基类)
  ├─ OfficialAgent    — 官员 Agent，模拟单个官员
  └─ InstitutionAgent — 机构 Agent，模拟机构中除一把手外的所有员工
```

**FC 工具集**（Agent 可调用的工具，二选一）：
| 工具 | 作用 |
|-----|------|
| `submit_action(action, content, original_intent, is_distorting, urgency, metadata?)` | 表达意图：do_nothing / report_message / do_something；`metadata` 为可选字典 |
| `request_action(action_type, target_location, description, resources)` | 触发实际行动（调查/赈灾/抓捕等），绕过 Router 直接给 ActionAgent |

**关键设计**：
- Agent 只能**表达意图**，不直接执行
- `original_intent`（真实想法）vs `content`（实际传递）支持信息失真模拟
- `is_distorting` 由 Agent 自己声明是否故意歪曲信息
- `process()` 返回 `list[AgentAction]`，支持双 FC（submit_action + request_action 同时发出）

**AgentConfig 字段**：
- `llm_client` — LLM 客户端实例
- `system_prompt` — 系统 prompt
- `tools` — FC 工具列表
- `personality_seed` — 人格种子
- `compress_threshold` — 记忆压缩阈值（默认 40 条）
- `keep_recent` — 压缩时保留最近 N 条（默认 20）

**BaseAgent 方法**：
- `add_memory(role, content)` — 添加一条记忆
- `get_messages()` — 构建消息列表（含摘要压缩）
- `set_compressor(compressor)` — 注入记忆压缩器
- `_maybe_compress()` — 记忆超阈值时自动压缩
- `_record_response_to_memory()` — 格式化 LLM 响应写入记忆

---

### 3.8 tianji_agent.py — 天玑调试 Agent

**作用**：全工具注入的调试对话 Agent，独立于 Orchestrator 主循环。开发者可在不启动模拟的情况下，通过对话直接读写世界状态、触发行动推演、模拟路由指令。

**设计特点**：
- 维护独立的 `messages` 对话历史，不进入 Orchestrator 的任何路径
- `attach_orchestrator(orch)` 反向注入后共享 `sim_time / pending_wakeups`，所有工具产生与主模拟一致的真实副作用
- **当前限制**：`main.py` 创建 TianJiAgent 时未传入 orchestrator，也未调用 `attach_orchestrator()`，因此 `wake_agent` 和 `relay_decree` 无法正常工作；`create_timer` 和 `execute_policy` 仍可正常使用
- 所有工具调用以 `tianji_` 前缀写入 chain_log，上帝视角可见，不污染主模拟 chain_id
- 统一走 `dispatch_fc(name, FCContext, args)` 执行，与 Orchestrator 共用同一份 handler 代码

**工具集**（三类，共 11 个）：
| 类别 | 工具 |
|------|------|
| 世界状态 | `query_entity` / `query_inventory` / `update_entity_status` / `update_inventory` / `complete_action` |
| Router | `wake_agent` / `create_timer` / `execute_policy` / `relay_decree` |
| Agent FC | `submit_action` / `request_action` |

**重要方法**：
| 方法 | 作用 |
|------|------|
| `reset()` | 清空对话历史，重新注入 system prompt |
| `chat(user_text)` | 主入口，async generator，yield SSE 事件（assistant_delta / tool_call / tool_result / tool_round_start / error / done） |
| `_build_ctx()` | 组装 FCContext，挂载 Orchestrator 时共享其运行时引用 |
| `_dispatch_tool(name, args)` | 统一分发，执行后写 chain_log |
| `_build_tools()` | 返回 ALL_TOOLS schema 列表 |
| `_summarize_result(result)` | 工具结果摘要辅助函数，用于 chain_log 展示 |
| `_load_system_prompt()` | 加载 system prompt，缺失时使用 `_DEFAULT_SYSTEM_PROMPT` |

---

### 3.9 action_agent.py — 行动模拟 Agent

**作用**：模拟官员行动的过程和结果，读写世界状态数据库。

**FC 工具集**（ActionAgent 可调用的工具）：
| 工具 | 作用 |
|-----|------|
| `query_entity(entity_id)` | 查询实体的描述与当前状况 |
| `query_inventory(entity_id)` | 查询实体的库存 |
| `update_entity_status(entity_id, new_status)` | 更新实体状况 |
| `update_inventory(entity_id, category, item_name, quantity_delta)` | 增减库存 |
| `complete_action(result_description, duration_days, success)` | 结束行动，返回结果 |

**工作流程**：
1. 收到行动请求（action_type/executor_id/target_location/description）
2. 构建动态 system prompt（注入世界状态上下文）
3. 多轮 FC 循环（最多 5 轮）：组装 `FCContext` → `dispatch_fc` → 查询实体 → 更新状态/库存 → complete_action
4. 返回 ActionResult（结果描述/耗时/成功与否/变更记录）

---

### 3.10 court_graph.py — 朝廷层级图

**作用**：基于 SQLite 存储官员和地理数据，提供层级查询和距离计算。

**数据结构**：
- `Official`: id, name, rank, location_id, role, agent_type, superior_id, personality_seed, enabled, extra_prompt
- `Location`: id, name, region, x, y（平面直角坐标，单位：里，以京城为原点）

**距离计算**：
- 采用平面直角坐标系，以京城 `(0, 0)` 为原点，单位：里（1 里 ≈ 500 米）
- 两地点间直线距离：`math.sqrt((x1-x2)² + (y1-y2)²)`
- 通信天数 = `距离(里) / 传递速度(里/天) * 传递系数`
- 基准速度：`TRAVEL_SPEED_LI_PER_DAY = 300`（快马急递日夜兼程）
- 传递系数 `TravelCoeff`：URGENT=0.5（六百里加急），NORMAL=1.0（普通公文），SLOW=2.0（民信）
- 最短耗时 `MIN_TRAVEL_DAYS = 0.1`（同城传递）

**重要查询方法**：
| 方法 | 作用 |
|-----|------|
| `get_official(id)` | 获取官员信息 |
| `get_superior(id)` | 获取直属上级 |
| `get_subordinates(id)` | 获取直属下级列表 |
| `get_chain_to_emperor(id)` | 从某官员到皇帝的完整上报链 |
| `get_distance(from_id, to_id, mode)` | 获取两名官员之间的通信耗时（天），mode 可选 TravelCoeff |
| `get_officials_at_location(location_id)` | 获取某地的所有官员 |
| `get_all_locations()` | 获取所有地点 |
| `get_all_officials()` | 获取所有官员 |
| `get_officials_by_rank(rank)` | 按品级查询官员 |
| `get_location(location_id)` | 获取单个地点 |
| `add_official(official)` | 添加官员 |
| `add_location(location)` | 添加地点 |
| `bulk_load_officials(officials)` | 批量加载官员 |

---

### 3.11 timer_system.py — 定时器系统

**作用**：管理延迟定时器，模拟古代公文传递的时间延迟。

**核心操作**：
| 方法 | 作用 |
|-----|------|
| `schedule(entry)` | 添加定时器 |
| `cancel(timer_id)` | 取消定时器（懒删除） |
| `pop_due(current_time)` | 弹出所有到期的定时器 |
| `get_in_transit()` | 获取所有在途定时器 |
| `peek_next_time()` | 查看下一个定时器的触发时间 |
| `count()` | 活跃定时器数量 |
| `save_all()` / `load_from_db()` | 持久化/恢复 |
| `export_all()` / `import_all(timers)` | 导出/导入（存档用） |

**实现**：heapq 最小堆 + SQLite 持久化

---

### 3.12 chain_log.py — 调用链日志

**作用**：记录所有消息传递过程，支持上帝视角查看信息失真。

**核心功能**：
| 方法 | 作用 |
|-----|------|
| `append(entry)` | 追加日志（同时写内存 + JSONL 文件） |
| `get_chain(chain_id)` | 按 chain_id 查询整条调用链 |
| `get_distortions(chain_id=None)` | 查询失真记录（可选按 chain_id 过滤） |
| `get_withheld()` | 查询所有被压下的消息 |
| `get_event_chains(event_id)` | 按事件 ID 查询相关链路 |
| `get_max_hop(chain_id)` | 获取某条链的最大跳数（用于循环检测） |
| `all_entries()` | 获取所有条目（调试 API 用） |
| `to_god_view(chain_id)` | 上帝视角：显示原始信息 vs 传递版本 |
| `clear()` | 清空所有日志 |
| `load_from_file()` | 从 JSONL 文件恢复 |

---

### 3.13 world_state.py — 世界状态数据库

**作用**：存储世界中所有实体的动态状态和库存。

**与 CourtGraph 的关系**：
- CourtGraph 管"这个官是谁、在哪、听谁的"（结构层，不变）
- WorldState 管"这个地方/机构发生了什么、有多少粮"（动态层，可变）
- 两者通过相同 ID 关联

**数据结构**：
- `Entity`: id, type, name, description, current_status, updated_at, metadata
- `InventoryItem`: entity_id, category, item_name, quantity, description

**重要方法**：
| 方法 | 作用 |
|-----|------|
| `get_entity(id)` | 获取实体 |
| `upsert_entity(entity)` | 插入或更新实体 |
| `update_status(id, new_status, sim_time)` | 更新实体状况 |
| `delete_entity(id)` | 删除实体（级联删除库存） |
| `get_entities_by_type(entity_type)` | 按类型筛选实体 |
| `get_all_entities()` | 获取所有实体 |
| `search_entities(keyword)` | 模糊搜索实体（ID/名称包含关键词） |
| `get_inventory(id)` | 获取库存 |
| `update_inventory(id, category, item_name, delta, reason)` | 相对增减库存 |
| `set_inventory(id, category, item_name, quantity, desc)` | 绝对赋值库存 |
| `delete_inventory_item(id, category, item_name)` | 删除单条库存记录 |
| `get_context_for_action(location_id, executor_id)` | 为 ActionAgent 构建上下文 |
| `export_all()` | 导出全部数据（供 API 使用） |
| `bulk_load(entities, inventory)` | 批量导入实体和库存 |
| `restore_all(data)` | 从导出数据完整恢复 |

---

### 3.14 llm_client.py — LLM 客户端

**作用**：封装 OpenAI 兼容的 LLM API 调用。

**核心功能**：
- 异步 HTTP 调用（httpx）
- 自动重试（指数退避）
- 并发控制（Semaphore）
- Token 用量统计
- 调试日志（debug_llm.jsonl）
- 配置热更新
- `chat_stream()` — 流式调用，yield delta/tool_call_delta/done 事件；出错自动退化为非流式

**核心类型**：
- `LLMConfig` — 配置（base_url/api_key/model/timeout_s/max_concurrent）
  - `from_file(path)` — 从 JSON 文件加载
  - `save_to_file(path)` — 保存到文件
  - `to_display_dict()` — 返回脱敏字典（api_key 打码）
- `LLMResponse` — LLM 响应数据类（content, tool_calls, input_tokens, output_tokens, latency_ms, raw）
- `LLMClient` 方法：
  - `chat()` — 异步调用（非流式）
  - `chat_stream()` — 流式调用
  - `close()` — 关闭 HTTP 客户端
  - `config` — 属性，当前配置

---

### 3.141 memory_compressor.py — 记忆压缩器

**作用**：当 Agent 记忆超过阈值时，用 LLM 自动生成长摘要，避免上下文溢出。

**核心功能**：
- `LLMMemoryCompressor` — 接收 LLM 客户端实例
- `compress(messages, keep_recent=20)` — 保留最近 N 条消息，对更早部分生成摘要
- 压缩后的格式由 `BaseAgent.serialize_memory()` 以 v2 dict 保存：`{version: 2, summary: "...", messages: [...]}`
- 阈值由 `AgentConfig.compress_threshold` 控制（默认 40 条）

**核心类型**：
- `LLMConfig` — 配置（base_url/api_key/model/timeout_s/max_concurrent）
  - `from_file(path)` — 从 JSON 文件加载
  - `save_to_file(path)` — 保存到文件
  - `to_display_dict()` — 返回脱敏字典（api_key 打码）
- `LLMResponse` — LLM 响应数据类（content, tool_calls, input_tokens, output_tokens, latency_ms, raw）
- `LLMClient` 方法：
  - `chat()` — 异步调用（非流式）
  - `chat_stream()` — 流式调用
  - `close()` — 关闭 HTTP 客户端
  - `config` — 属性，当前配置

---

### 3.15 loader.py — DLC 数据加载器

**作用**：从 JSON 配置加载官员、地点、事件、世界状态，创建 Agent 实例。

**加载流程**：
1. `load_world_state()` → 加载实体和库存到 WorldState
2. `load_officials()` → 加载官员列表
3. `load_locations()` → 将 `officials.json` 中的坐标同步进 `CourtGraph.locations`（WorldStateView 坐标面板依赖此数据源，缺失会导致坐标显示为 0）
4. `create_agents()` → 为每个官员创建 Agent 实例，注入 system prompt
5. `load_events()` → 加载初始事件

**Prompt 优先级**：
1. 专用文件 `daming_court/prompts/{官员ID}.txt`（最高）
2. 通用模板 `official_template.txt`
3. 默认模板

**记忆压缩注入**：`create_agents()` 创建 Agent 后，Orchestrator 会为每个 Agent 注入 `LLMMemoryCompressor`，使 `_maybe_compress()` 在记忆超阈值时自动触发 LLM 摘要。

---

## 四、数据流与调用链

### 4.1 完整信息流

```
事件注入（蝗灾）
    │
    ▼
知县 Agent 被唤醒
    │
    ├─ submit_action → "上报灾情给知府"
    │       │
    │       ▼
    │   Router (LLM) 解析意图
    │       │
    │       ├─ 查层级：知县 → 知府（直属上级，距离1天）
    │       ├─ 调用 create_timer(target="知府", delay_days=1)
    │       │
    │       ▼
    │   TimerSystem 创建定时器（1游戏天后送达）
    │
    ▼ （1游戏天后定时器触发）
    │
知府 Agent 被唤醒
    │
    ├─ submit_action → "上报给布政使" 或 "压下不报"
    │       │
    │       ▼
    │   Router 处理...
    │
    ▼ （逐级上报）
    │
皇帝收到奏折
    │
    ├─ 玩家操作：approve / reject / hold / issue_decree
    │
    ▼
太监 Agent 被唤醒
    │
    ├─ relay_decree → 逐级下达
    │
    ▼
基层执行
```

### 4.2 调用链追踪（Chain Log）

每个消息都有唯一的 `chain_id`，整条链路的每一步都记录在 `chain_log.jsonl`：

```
chain_abc123:
  [hop=1] 吴县知县_张三 → submit_action → "蝗灾严重，请知府拨粮" (distorted: false)
  [hop=2] Router → create_timer → 苏州知府_况钟 (delay: 1 day)
  [hop=3] 苏州知府_况钟 → submit_action → "江南蝗灾，请求布政使赈灾" (distorted: true)
  [hop=4] Router → create_timer → 江南布政使_周忱 (delay: 2 days)
  ...
  [hop=7] 皇帝 → approve → "准奏，拨粮三千石"
```

上帝视角可以对比 `original_text`（真实意图）vs `transmitted_text`（实际传递），看到信息失真。

---

## 五、API 接口一览

### 5.1 模拟控制

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/start` | POST | 启动模拟 |
| `/api/pause` | POST | 暂停模拟 |
| `/api/resume` | POST | 恢复模拟 |
| `/api/saves` | GET | 列出所有存档（按 saved_at 倒序） |
| `/api/save` | POST | 存档（需先暂停，body: `{save_name, memorials:[...]}`，写入 `saves/{name}/`） |
| `/api/load` | POST | 读档（body: `{save_name}`，恢复 SQLite/chain_log/Agent 记忆） |
| `/api/saves/rename` | POST | 重命名存档（body: `{old_name, new_name}`） |
| `/api/saves/{name}` | DELETE | 删除存档 |

### 5.2 玩家操作

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/player-action` | POST | 皇帝操作（approve/reject/hold/issue_decree） |

### 5.3 设置与配置

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/settings` | GET/POST | LLM + 时间配置（热更新） |
| `/api/time-scale` | GET/POST | 时间倍率（需暂停） |
| `/api/prompts` | GET/POST | Prompt 文件读写 |

### 5.4 天玑 AI

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/tianji/chat` | POST | SSE 流式对话（body: `{message: str}`） |
| `/api/tianji/reset` | POST | 清空对话历史，重新注入 system prompt |
| `/api/tianji/history` | GET | 返回当前 messages（供刷新后恢复展示） |
| `/api/tianji/tools` | GET | 返回工具 schema，按类别分组 |

### 5.5 调试接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/debug/timeline?after=N&limit=50` | GET | chain log 增量拉取 |
| `/api/debug/timers` | GET | 定时器状态（pending + fired） |
| `/api/debug/router-log?after=N` | GET | Router 决策日志增量拉取 |
| `/api/debug/action-log?after=N` | GET | ActionAgent 执行日志增量拉取 |
| `/api/chain-log/{chain_id}` | GET | 单条链的上帝视角 |
| `/api/token-usage` | GET | Token 用量统计 |
| `/api/in-transit` | GET | 在途公文列表 |

### 5.6 世界状态

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/world-state` | GET | 返回所有实体 + 库存 |
| `/api/world-state/{entity_id}` | GET | 返回单个实体详情 + 库存 |
| `/api/world-state/{entity_id}` | PUT | 创建或更新实体 |
| `/api/world-state/{entity_id}` | DELETE | 删除实体（级联删除库存） |
| `/api/world-state/{entity_id}/status` | POST | 手动修改实体 current_status（调试用） |
| `/api/world-state/{entity_id}/inventory` | PUT | 新增或覆盖单条库存 |
| `/api/world-state/{entity_id}/inventory/{category}/{item_name}` | DELETE | 删除单条库存 |

### 5.7 Agent 编辑器

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/editor/officials` | GET | 返回完整的 officials + locations 配置 |
| `/api/editor/officials/{id}` | PUT/DELETE | 更新/删除官员 |
| `/api/editor/officials/batch` | POST | 批量保存官员 |
| `/api/editor/locations/batch` | POST | 批量保存地点（同步写入 officials.json 和 CourtGraph） |
| `/api/editor/locations/{location_id}` | GET | 获取单个地点详情（含坐标） |
| `/api/editor/locations/{location_id}` | PUT | 更新单个地点（同步写入 DB 和 JSON） |
| `/api/editor/prompt-preview` | POST | 预览官员 system prompt 渲染结果 |
| `/api/editor/personas` | GET | 返回所有人格种子 |
| `/api/editor/personas/{name}` | PUT/DELETE | 新增/删除人格种子 |
| `/api/editor/personas/batch` | POST | 批量保存人格种子 |

### 5.8 WebSocket

| 端点 | 说明 |
|------|------|
| `/ws` | 实时 tick 推送（每 0.25s 广播 TickResult） |

---

## 六、数据库结构

### SQLite 表结构（daming_court.db）

> CourtGraph（官员表、地点表）、TimerSystem（定时器表）、WorldState（实体表、库存表）共用同一个 `daming_court.db` SQLite 文件。

```sql
-- 官员表
CREATE TABLE officials (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    location_id TEXT,
    role TEXT NOT NULL DEFAULT 'official',  -- official / eunuch / player
    agent_type TEXT NOT NULL DEFAULT 'official',  -- official / institution
    superior_id TEXT,
    personality_seed TEXT DEFAULT '忠直',
    enabled INTEGER DEFAULT 1,            -- 是否参与模拟
    extra_prompt TEXT DEFAULT ''           -- 附加个性化提示词
);

-- 地点表（平面直角坐标系，以京城为原点，单位：里）
CREATE TABLE locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT,
    x REAL DEFAULT 0,                     -- 东正西负（里）
    y REAL DEFAULT 0                      -- 北正南负（里）
);

-- 定时器表
CREATE TABLE pending_timers (
    timer_id TEXT PRIMARY KEY,
    fire_at REAL NOT NULL,
    target_agent TEXT NOT NULL,
    payload TEXT NOT NULL,
    chain_id TEXT,
    parent_timer_id TEXT,
    created_at REAL,
    cancelled INTEGER DEFAULT 0
);

-- 实体表
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,  -- location / person / institution / organization / object
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    current_status TEXT DEFAULT '',
    updated_at REAL DEFAULT 0,
    metadata TEXT DEFAULT '{}'
);

-- 库存表
CREATE TABLE inventory (
    entity_id TEXT NOT NULL,
    category TEXT NOT NULL,  -- 粮食 / 货币 / 兵力 / 物资
    item_name TEXT NOT NULL,
    quantity REAL DEFAULT 0,
    description TEXT DEFAULT '',
    PRIMARY KEY (entity_id, category, item_name),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
```

**迁移兼容**：`court_graph.py` 启动时会自动检测旧数据库缺少 `enabled`、`extra_prompt`、`x`、`y` 列并执行 `ALTER TABLE ADD COLUMN`。

---

## 七、当前数据配置

### 官员层级（officials.json）

```
皇帝 (rank=15, player)
  └─ 司礼监太监_王振 (rank=13, eunuch, 圆滑)
       └─ 内阁_杨士奇 (rank=10, 忠直)
            └─ 通政司 (rank=9, institution)
                 └─ 江南布政使_周忱 (rank=8, 忠直)
                      └─ 苏州知府_况钟 (rank=5, 忠直)
                           ├─ 吴县知县_张三 (rank=1, 贪腐, enabled, 有extra_prompt)
                           └─ 昆山知县_李四 (rank=1, 忠直)
  └─ 户部 (rank=12, institution, 圆滑)
```

### 地点配置（平面直角坐标系，京城为原点 `(0,0)`，单位：里）

| 地点 | 区域 | X (里) | Y (里) |
|------|------|--------|--------|
| 京城 | 直隶 | 0 | 0 |
| 江南布政使司 | 江南 | 200 | -1490 |
| 苏州府 | 江南 | 450 | -1730 |
| 吴县 | 江南 | 448 | -1732 |
| 昆山县 | 江南 | 510 | -1680 |

> 距离计算示例：吴县 → 苏州府 = `sqrt((448-450)² + (-1732+1730)²) ≈ 2.8 里`，
> 普通传信耗时 `2.8 / 300 ≈ 0.009 天`（同级别极近，取 `MIN_TRAVEL_DAYS=0.1`）。
> 吴县 → 京城 = `sqrt(448² + 1732²) ≈ 1789 里`，
> 普通传信耗时 `1789 / 300 ≈ 6.0 天`。

### 人格模板（personas.json）

- **贪腐**：倾向于隐瞒灾情以避免追责，偶尔索取好处，上报时淡化问题严重性
- **忠直**：如实上报，不夸大不隐瞒，为百姓着想
- **圆滑**：善于揣摩上意，措辞委婉，优先保护自己

---

## 八、前端结构

### 8.1 App.jsx — 皇帝视角

- 奏折列表（来自各级官员的上报）
- 批复操作（批准/驳回/留中/下旨）
- 在途公文列表（显示剩余送达时间）
- 模拟时间显示（天/时辰/刻）
- 控制面板（启动/暂停/设置/存档/读档）
  - 未运行：显示"开始模拟" + 有存档时显示"读档（第N天）"
  - 暂停中：显示"继续" + "存档" + 有存档时显示"读档"

### 8.2 GodView.jsx — 上帝视角

- 时间线（chain log 增量拉取，自动滚动，天玑 AI 调用以青色高亮）
- Router 决策日志
- ActionAgent 执行日志（`/api/debug/action-log` 增量拉取）
- 定时器状态（pending + fired）

### 8.5 TianJiView.jsx — 天玑 AI（`/tianji.html`）

- 三栏布局：工具列表面板 | 对话区 | 世界状态快照
- 工具调用块折叠展示，可展开查看完整参数与返回值
- 会话重置按钮；顶部快捷导航到皇帝视角和 Agent 编辑器

### 8.3 AgentEditor.jsx — Agent 编辑器

- 官员树形结构（按层级展示，支持拖拽调整上下级）
- 官员属性编辑（品级/驻地自由填写/上级/性格/启用状态/附加提示词）
- 启用/禁用切换（树上圆点一键操作，禁用后不创建 Agent）
- "活跃"标签：有 extra_prompt 的官员标记为活跃
- 人格种子编辑（CRUD，保存后立即热重载）
- 系统 Agent 配置占位（开发中）
- 导入/导出 JSON（批量迁移官员配置）
- Prompt 预览（实时渲染 system prompt）

### 8.4 WorldStateView.jsx — 世界状态管理

- 实体列表（按类型筛选 + 搜索关键词）
- 支持 5 种实体类型：地点/人物/机构/组织/物品
- 实体 CRUD：新建、编辑属性（类型/名称/描述/当前状况）、删除（级联删库存）
- 库存行内编辑：点击数量或单位直接修改，回车保存
- 新增库存行：支持类目自动补全（粮食/货币/兵力/物资/人口/税收）
- 地点坐标编辑：仅 `type=location` 时显示，可编辑 X/Y 坐标和区域
- 坐标说明：京城为原点 (0,0)，1 里 ≈ 500 米，传信速度 300 里/天

---

## 九、如何添加新内容

> 添加新官员、新事件、世界状态实体、自定义 Prompt 的详细操作指南见 [DEV_GUIDE.md](DEV_GUIDE.md)。

---

## 十、待完成功能（TODOS）

### v1 未完成
- **Agent 编辑器**：优化
- **随机事件生成器**：周期性自动触发蝗灾/旱灾/民变
- **时间模拟器**：科学计算过程耗时
- **集成测试**：mock LLM 的端到端链路测试

### v1 已完成（本期新增）
- **天玑 AI**：全工具注入的调试对话 Agent，独立前端页面，SSE 流式回复，工具调用可视化
- **记忆压缩**：LLM 记忆压缩器，Agent 记忆超阈值时自动摘要，v2 格式存档

### v1.1+
- **Agent 孵化机制**：机构 Agent 在某些条件下自动孵化出独立的官员 Agent
- **更多事件类型**：当前基本只有蝗灾样例
- **早朝机制**：定时批量召见，而非实时推进

---

## 十一、关键设计原则

1. **Agent 只表达意图，不直接执行**：所有行动由 Router（世界运行法则）仲裁
2. **信息失真由 Agent 自主产生**：`original_intent` vs `content`，`is_distorting` 由 Agent 声明
3. **时间推进不被 LLM 阻塞**：Agent 调用作为后台 asyncio.Task
4. **定时器延迟用游戏时间**：`delay_days * 86400` 游戏秒，改速度自然影响触发快慢
5. **DLC 分离**：框架层（multiagent_framework）可复用于其他场景
6. **FC 工具单点定义**：工具 schema 在 `fc_tools.py`，handler 在 `fc_handlers.py`，每个工具只有一份代码；添加新工具只需改这两个文件
7. **FCContext 模式**：调用方组装上下文，handler 消费上下文；Orchestrator 和天玑用同一个 handler，靠 context 字段决定执行路径（有 `run_action` → 异步调度；无则直接 await）
8. **共享数据库**：CourtGraph、TimerSystem、WorldState 共用 `daming_court.db`，各自管理独立的表
9. **记忆压缩**：Agent 记忆超阈值时自动触发 LLM 摘要，序列化格式为 v2 dict（`{version, summary, messages}`），兼容 v1 list 反序列化
