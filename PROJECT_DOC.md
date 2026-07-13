# 世界模拟器 — 大明王朝·朝堂风云 开发文档

## 一、项目概述

这是一个基于 **多 Agent 框架 + LLM 驱动** 的古代朝廷模拟器。玩家扮演皇帝，处理来自各级官员的奏折，下达旨意。各级官员由 AI Agent 驱动，会根据自己的性格（忠直/贪腐/圆滑/部门/刚正/野心）产生不同的行为。

### 核心玩法
1. **事件发生** → 基层知县 Agent 被唤醒
2. **Agent 决策** → 通过 `submit_action` 表达意图（汇报/隐瞒/越级/执行）或 `request_action` 触发行动
3. **Router 路由** → LLM 解析意图，决定消息传递路径和延迟
4. **定时器等待** → 模拟古代公文传递的时间延迟
5. **逐级上报** → 知府 → 布政使 → 内阁 → 皇帝
6. **皇帝批复** → 批准/驳回/下旨，通过太监传达回基层执行
7. **ActionAgent 执行** → 查询实体、更新状态、排队延迟事件、增减资源代币
8. **OngoingEvent 到期** → 系统自动应用延迟变化（状态更新 + 库存增减）

---

## 二、项目文件结构

```
世界模拟器/
├── main.py                          # 【后端入口】初始化 + lifespan + 注册路由
├── routers/                         # 【API 路由层】按功能拆分的 FastAPI Router 模块
│   ├── __init__.py
│   ├── _state.py                    # 共享全局状态（orchestrator/tianji_agent/ws_clients）
│   ├── simulation.py                # 模拟控制 + 设置 + 天玑 AI + WebSocket + 奏折持久化 + 新游戏
│   ├── saves.py                     # 存档管理（存/读/列表/重命名/删除）
│   ├── editor.py                    # Agent 编辑器（官员/地点/人格/Prompt CRUD）
│   ├── worldstate.py                # 世界状态（实体/库存/关系/事件 CRUD）
│   └── debug.py                     # 调试接口（timeline/timers/router-log/action-log）
├── requirements.txt                 # Python 依赖（fastapi, uvicorn, httpx, websockets, pydantic）
├── start.bat / start.sh             # 一键启动脚本
├── runtime/                         # 运行时状态（自动创建，gitignore）
│   ├── world.db                     # SQLite 数据库（官员/地点/定时器/世界状态/关系/事件）
│   ├── chain_log.jsonl              # 调用链日志
│   ├── state.json                   # 运行时状态（sim_time/seconds_per_day/memorials）
│   └── agents/                      # Agent 记忆持久化（{agent_id}.json）
├── saves/
│   └── {存档名}/                    # 每个存档一个子目录
│       ├── meta.json                # 元信息（save_name/saved_at/sim_day/agent_count等）
│       ├── runtime.json             # 运行时（agent_memories/pending_memorials/seconds_per_day）
│       ├── daming_court.db          # SQLite 数据库快照（热备份）
│       └── chain_log.jsonl          # 调用链日志快照
│
├── config/                          # 全局配置
│   ├── llm.json                     # LLM API 配置（base_url, api_key, model, timeout_s, max_concurrent）
│   └── time.json                    # 时间倍率配置（seconds_per_day；tick_interval 未使用）
│
├── multiagent_framework/            # 【框架层】通用多 Agent 框架，可复用于其他 DLC
│   ├── __init__.py
│   ├── types.py                     # 【核心】所有数据结构定义
│   ├── llm_client.py                # 【核心】OpenAI 兼容 LLM 客户端
│   ├── fc_tools.py                  # 【核心】FC 工具定义中央注册表（14 个工具）
│   ├── fc_handlers.py               # 【核心】FC 处理函数中央注册表（每工具一个 handler）
│   ├── agent_runtime.py             # 【核心】Agent 基类 + 官员/机构 Agent 实现
│   ├── action_agent.py              # 【核心】行动模拟 Agent（读写世界状态 + 排队延迟事件）
│   ├── tianji_agent.py              # 【调试】天玑 AI（全工具注入，独立于主循环）
│   ├── router.py                    # 【核心】世界运行法则（LLM 驱动的路由中枢）
│   ├── orchestrator.py              # 【核心】主循环，驱动整个模拟
│   ├── court_graph.py               # 朝廷层级图（官员查询 + 坐标委托 WorldState）
│   ├── timer_system.py              # 定时器系统（heapq + SQLite 持久化）
│   ├── chain_log.py                 # 调用链日志（JSONL 追加写入）
│   ├── world_state.py               # 世界状态数据库（实体/库存/关系/事件/资源代币）
│   ├── memory_compressor.py         # 【核心】LLM 记忆压缩（Agent 记忆超阈值时自动摘要）
│   ├── runtime_paths.py             # 运行时路径常量（runtime/ 目录结构）
│   └── runtime_persistor.py         # 运行时状态持久化（后台定期 flush 到磁盘）
│
├── daming_court/                    # 【DLC 层】大明朝廷内容包
│   ├── __init__.py
│   ├── loader.py                    # 数据加载器（JSON → 官员/Agent/事件/世界状态）
│   ├── config/
│   │   ├── officials.json           # 官员数据（ID/品级/位置/上级/性格）
│   │   ├── personas.json            # 人格模板（贪腐/忠直/圆滑/部门/刚正/野心的 prompt 描述）
│   │   ├── events.json              # 初始事件（蝗灾等）
│   │   └── world_state.json         # 初始世界状态（实体/库存/关系/坐标）
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
│   │   ├── App.jsx                  # 【皇帝视角】主界面（奏折列表/批复/在途公文/关系图）
│   │   ├── AppNav.jsx               # 【导航】浮动可拖拽导航栏（朝堂/编修/天玑）
│   │   ├── GodView.jsx              # 【上帝视角】调试界面（时间线/Router日志/定时器）
│   │   ├── WorldStateView.jsx       # 【世界状态】实体/库存/关系/事件 CRUD + 资源代币
│   │   ├── AgentEditor.jsx          # 【Agent 编辑器】可视化编辑官员/地点/人格 + 系统Agent配置
│   │   ├── TianJiView.jsx           # 【天玑 AI】全工具调试对话页
│   │   ├── RelationsView.jsx        # 【关系图】ReactFlow 实体关系可视化
│   │   ├── editor-main.jsx          # 编辑器独立入口
│   │   ├── tianji-main.jsx          # 天玑独立入口
│   │   ├── hooks/useWebSocket.js    # WebSocket hook
│   │   ├── design-system.css        # 共享设计令牌、背景、字体、表单、动效与滚动条
│   │   ├── App.css / editor.css / tianji.css / AppNav.css / RelationsView.css
│   │   └── index.css                # 基础 reset
│   ├── index.html / editor.html / tianji.html
│   ├── package.json                # 依赖（含 @dagrejs/dagre, reactflow）
│   └── vite.config.js
│
└── tests/                           # 测试
    └── unit/                        # 单元测试
```

---

## 三、核心模块详解

### 3.1 types.py — 数据结构定义

**作用**：定义框架中所有数据结构，是整个项目的数据契约。

| 数据结构 | 用途 | 完整字段 |
|---------|------|---------|
| `TimerEntry` | 定时器条目 | timer_id, fire_at, target_agent, payload, chain_id, parent_timer_id, created_at, cancelled |
| `ChainLogEntry` | 调用链记录 | chain_id, entry_id, event_id, sim_time, caller, caller_agent_type, fc_called, original_text, transmitted_text, is_distorted, distortion_note, hop_count, routed_to, timer_id, trigger_at, wakeup_type, status |
| `AgentAction` | Agent 输出的行动意图 | agent_id, action, content, original_intent, is_distorting, urgency, metadata |
| `EventObject` | 系统事件 | event_id, event_type, severity, target_agents, initial_payload, generated_at |
| `RouterCommandType` | Router 输出指令类型枚举 | WAKE_AGENT, EXECUTE_POLICY, RELAY_DECREE, BROADCAST, NO_OP |
| `RouterCommand` | Router 输出的指令 | command_type, target, targets, payload, delay_seconds, chain_id, source_action |
| `ActionResult` | ActionAgent 完成行动后的结果 | action_type, executor_id, target_location, result_description, duration_days, changes_made, success, error, fc_trace |
| `TickResult` | 一次 tick 推送给前端的数据 | sim_time, new_memorials, in_transit, chain_entries, agent_errors, token_usage；有 `to_dict()` 方法 |
| `PlayerAction` | 皇帝的操作 | action_type, memorial_id, content, note |
| `OngoingEvent` | 正在进行的事件 | event_id, entity_id, event_type, description, start_time, end_time, expected_result, source_action_id |

**关键枚举**：
- `RouterCommandType`: WAKE_AGENT, EXECUTE_POLICY, RELAY_DECREE, BROADCAST, NO_OP
- `PlayerActionType`: APPROVE, REJECT, HOLD, ISSUE_DECREE

> **注意**：`BROADCAST` 和 `NO_OP` 目前没有对应的 FC handler，如果 Router 返回这两种类型，`dispatch_fc` 会返回错误。

---

### 3.2 orchestrator.py — 主循环（核心调度器）

**作用**：驱动整个模拟的主循环，管理时间推进、Agent 调用、定时器检查、OngoingEvent 到期处理、玩家操作处理。

**核心逻辑**：
```
run() 主循环（0.25s 间隔）
  │
  ├─ 用真实流逝时间推进 sim_time（不被 LLM 阻塞）
  │
  └─ _tick() 每帧处理
      ├─ 1. 检查到期定时器 → 加入待唤醒队列
      ├─ 1.5 弹出到期的 OngoingEvent → _apply_matured_event()（写回实体状态/库存）
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
| `run(tick_interval=0.25)` | 主循环，实时推进 sim_time；启动 persistor 后台任务 |
| `_tick()` | 单次 tick：检查定时器 → 弹出到期事件 → 派发唤醒 → 处理玩家操作 |
| `_process_agents_background()` | 后台并行调用 Agent → Router 批量处理 → 执行指令 |
| `_call_agent_safe()` | 调用单个 Agent，记录 chain log，异常安全 |
| `_apply_matured_event(ev)` | 应用到期 OngoingEvent 的 expected_result（更新状态 + 库存） |
| `_build_fc_context()` | 组装 FCContext |
| `_execute_commands()` | 遍历 RouterCommand，调用 dispatch_fc 统一执行 |
| `_handle_player_action(action, result)` | 处理皇帝操作：找太监 → 唤醒太监传达旨意 |
| `_fuzzy_find_agent()` | 模糊匹配 Agent |
| `inject_event()` | 注入事件 |
| `save_state()` | 返回 dict（sim_time, seconds_per_day, timer_count, agent_memories） |
| `load_state(state)` | 恢复状态（sim_time, seconds_per_day, 记忆等） |
| `inject_player_action(action)` | async，注入玩家操作到队列 |
| `set_time_scale(seconds_per_day)` | 设置时间倍率（需暂停） |
| `pause()` / `resume()` / `stop()` | 模拟控制 |
| `drain_agent_tasks(timeout)` | 等待后台 Agent 任务完成 + flush persistor |

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
| `wake_agent(target, message, from_agent, urgency?)` | 向目标 Agent 投递消息；延迟由代码根据双方坐标与 urgency 计算 |
| `execute_policy(policy, location, executor?, from_agent?, description?)` | 调用世界模拟 Agent 执行政策效果 |
| `relay_decree(decree, targets, urgency?)` | 传递皇帝圣旨给指定官员 |
| `list_agents(location_id?)` | 列出所有可唤醒 Agent |

**重要方法**：
| 方法 | 作用 |
|-----|------|
| `process_batch(actions, context=None)` | 批量处理 AgentAction，返回 RouterCommand 列表；`context` 为可选额外上下文 |
| `_build_batch_description(actions, context)` | 构建给 LLM 的描述 |
| `_parse_commands()` | 解析 LLM 的 function call 响应 |
| `_fc_to_command()` | 将单个 FC 调用转换为 RouterCommand |
| `update_time_scale(seconds_per_day)` | 更新时间倍率 |
| `_default_prompt()` | 默认 Router system prompt |

---

### 3.4 fc_tools.py — FC 工具定义注册表

**作用**：所有 FC 工具的唯一定义来源。各 Agent 按需 import 所需工具分组。

| 分组常量 | 用途 | 工具数 |
|---------|------|--------|
| `ACTION_AGENT_TOOLS` | 世界状态读写 + 事件 CRUD + Agent 列表 + 结束行动 | 9 |
| `ROUTER_TOOLS` | Agent 调度 | 4 |
| `AGENT_TOOLS` | Agent 意图表达 | 2 |
| `ALL_TOOLS` | 天玑用，全量 | 14 |

**扩展规则**：新增工具只需在此文件定义 schema + 加入对应分组，无需改动任何 Agent 文件。

**工具完整列表**：
| 工具 | 分组 | 参数 |
|------|------|------|
| `list_entities` | ACTION / ALL | `entity_type?`（location/institution/organization） |
| `query_entity` | ACTION / ALL | `entity_id` |
| `update_entity_status` | ACTION / ALL | `entity_id, new_status` |
| `update_inventory` | ACTION / ALL | `entity_id, resource_name, quantity_delta, reason?` |
| `add_ongoing_event` | ACTION / ALL | `entity_id, description, duration_days, expected_result, event_type?` |
| `modify_ongoing_event` | ACTION / ALL | `event_id, description?, duration_days?, expected_result?` |
| `delete_ongoing_event` | ACTION / ALL | `event_id` |
| `list_agents` | ROUTER / ACTION / ALL | `location_id?` |
| `complete_action` | ACTION / ALL | `result_description, duration_days, success?` |
| `wake_agent` | ROUTER / ALL | `target, message, from_agent, urgency?` |
| `execute_policy` | ROUTER / ALL | `policy, location, executor?, from_agent?` |
| `relay_decree` | ROUTER / ALL | `decree, targets, urgency?` |
| `submit_action` | AGENT / ALL | `action, content, original_intent, is_distorting, urgency?, metadata?` |
| `request_action` | AGENT / ALL | `action_type, target_location, description, resources?` |

> **注意**：`query_inventory` 已合并进 `query_entity`（一次性返回实体状态 + 库存 + 正在进行的事件）。

---

### 3.5 fc_handlers.py — FC 处理函数注册表

**作用**：每个 FC 工具有且仅有一个 handler。所有调用方（Orchestrator / ActionAgent / TianJiAgent）组装 `FCContext` 后调用 `dispatch_fc`，行为完全一致。

**核心抽象**：

`FCContext` 数据类打包所有依赖：
- `world_state`, `court`, `timers`, `chain_log`, `action_agent`
- `pending_wakeups`, `run_action`, `agent_tasks` — Orchestrator 共享引用（可选）
- `sim_time`, `seconds_per_day`（传入速度倍率值）, `caller_id`, `chain_id`, `source_action`
- `changes_made` — ActionAgent 收集世界状态变更列表

**Handler 分组**：
| 组 | Handler | 异步 |
|----|---------|------|
| 世界状态 | `handle_list_entities / handle_query_entity / handle_update_entity_status / handle_update_inventory` | 否 |
| 正在进行的事件 | `handle_add_ongoing_event / handle_modify_ongoing_event / handle_delete_ongoing_event` | 否 |
| 行动完成 | `handle_complete_action` | 否 |
| Agent 列表 | `handle_list_agents` | 否 |
| Router | `handle_wake_agent / handle_execute_policy / handle_relay_decree` | 是 |

**关键函数**：
| 函数 | 说明 |
|------|------|
| `dispatch_fc(name, ctx, args)` | 统一分发入口，async-aware |
| `command_type_to_fc_name(cmd_type)` | RouterCommandType → FC 工具名 |
| `command_to_fc_args(cmd)` | RouterCommand → FC 参数 dict |
| `parse_agent_fc_response(response, agent_id)` | LLM 响应 → `list[AgentAction]`（支持双 FC 同时返回） |
| `parse_submit_action / parse_request_action` | FC args → AgentAction |
| `_resolve_entity_id(world_state, entity_id)` | 模糊实体 ID 匹配（精确匹配 → search_entities 的 id/name/description LIKE 搜索） |

**扩展规则**：新增工具只需写一个 handler 函数，加入 `FC_HANDLERS` 字典即可。

---

### 3.6 agent_runtime.py — Agent 运行时

**作用**：Agent 基类定义 + 官员/机构 Agent 实现。

**类层次**：
```
BaseAgent (抽象基类)
  ├─ OfficialAgent    — 官员 Agent，模拟单个官员
  └─ InstitutionAgent — 机构 Agent，模拟机构中除一把手外的所有员工
```

**AgentConfig 字段**：
| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent_id` | str | `""` | Agent 标识 |
| `agent_type` | str | `"official"` | 类型标识 |
| `display_name` | str | `""` | 显示名称 |
| `system_prompt` | str | `""` | 系统 prompt |
| `tools` | list[dict] | `[]` | FC 工具列表 |
| `personality_seed` | str | `""` | 人格种子 |
| `max_memory` | int | `20` | 记忆 deque 最大长度 |
| `compress_threshold` | int | `40` | 压缩阈值 |
| `keep_recent` | int | `20` | 压缩时保留最近 N 条 |

> **注意**：`llm_client` 不是 AgentConfig 字段，而是 `BaseAgent.__init__()` 的单独参数。

**BaseAgent 方法**：
- `add_memory(role, content)` — 添加一条记忆
- `get_messages()` — 构建消息列表（含摘要压缩后拼接）
- `set_compressor(compressor)` — 注入记忆压缩器
- `_maybe_compress()` — 记忆超阈值时自动压缩，合并已有摘要
- `_record_response_to_memory()` — 格式化 LLM 响应写入记忆
- `serialize_memory()` — 序列化为 v2 dict（`{version, summary, messages}`）
- `deserialize_memory(data)` — 从 v2 dict 或 v1 list 恢复 deque

**关键设计**：
- Agent 只能**表达意图**，不直接执行
- `original_intent`（真实想法）vs `content`（实际传递）支持信息失真模拟
- `is_distorting` 由 Agent 自己声明是否故意歪曲信息
- `process()` 返回 `list[AgentAction]`，支持双 FC（submit_action + request_action 同时发出）

---

### 3.7 tianji_agent.py — 天玑调试 Agent

**作用**：全工具注入的调试对话 Agent，独立于 Orchestrator 主循环。

**工具集**（四类，共 14 个）：
| 类别 | 工具 |
|------|------|
| 世界状态 | `list_entities / query_entity / update_entity_status / update_inventory / add_ongoing_event / modify_ongoing_event / delete_ongoing_event` |
| 行动完成 | `complete_action` |
| Agent 调度 | `list_agents / wake_agent / execute_policy / relay_decree` |
| Agent 行为 | `submit_action / request_action` |

**重要方法**：
| 方法 | 作用 |
|------|------|
| `reset()` | 清空对话历史，重新注入 system prompt |
| `chat(user_text)` | 主入口，async generator，yield SSE 事件 |
| `_build_ctx()` | 组装 FCContext |
| `_dispatch_tool(name, args)` | 统一分发 |
| `get_history()` | 返回当前 messages 列表 |
| `_load_system_prompt()` | 加载 system prompt |

**SSE 事件类型**：
- `assistant_delta` — LLM 文本流
- `tool_call` — 工具调用（含 `call_id`）
- `tool_result` — 工具结果（含 `call_id`）
- `tool_round_start` — 工具轮开始
- `error` — LLM 调用错误
- `done` — 本轮结束

> **注意**：`_summarize_result()` 是模块级函数，不是类方法。`session_id` 是实例属性。

---

### 3.8 action_agent.py — 行动模拟 Agent

**作用**：模拟官员行动的过程和结果，读写世界状态数据库。

**FC 工具集**（ACTION_AGENT_TOOLS，9 个）：
| 工具 | 作用 |
|-----|------|
| `list_entities(entity_type?)` | 列出实体 |
| `query_entity(entity_id)` | 查询实体完整状态（含库存 + 进行中的事件） |
| `update_entity_status(entity_id, new_status)` | 更新实体状况 |
| `update_inventory(entity_id, resource_name, quantity_delta, reason?)` | 增减资源代币 |
| `add_ongoing_event(entity_id, description, duration_days, expected_result, event_type?)` | 排队延迟事件 |
| `modify_ongoing_event(event_id, description?, duration_days?, expected_result?)` | 修改进行中的事件 |
| `delete_ongoing_event(event_id)` | 取消进行中的事件 |
| `list_agents(location_id?)` | 列出 Agent |
| `complete_action(result_description, duration_days, success?)` | 结束行动 |

**工作流程**：
1. 收到行动请求（action_type/executor_id/target_location/description）
2. 构建动态 system prompt（注入世界状态上下文）
3. 多轮 FC 循环（默认 max_rounds=7）：
   - 组装 `FCContext` → `dispatch_fc` → 查询实体 → 更新状态/库存/排队事件 → complete_action
4. 返回 ActionResult（结果描述/耗时/成功与否/变更记录）

---

### 3.9 court_graph.py — 朝廷层级图

**作用**：基于 SQLite 存储官员数据，提供层级查询。坐标信息委托给 WorldState。

**数据结构**：
- `Official`: id, name, rank, location_id, role, agent_type, superior_id, personality_seed, enabled, extra_prompt

**重要查询方法**：
| 方法 | 作用 |
|-----|------|
| `get_official(id)` | 获取官员信息 |
| `get_superior(id)` | 获取直属上级 |
| `get_subordinates(id)` | 获取直属下级列表 |
| `get_chain_to_emperor(id)` | 从某官员到皇帝的完整上报链 |
| `get_distance(from_id, to_id, speed_coeff)` | 通信耗时（天），坐标从 WorldState.metadata 获取，无坐标时回退 5.0/speed_coeff |
| `get_officials_at_location()` | 按地点查询 |
| `get_all_officials()` | 获取所有官员 |
| `get_officials_by_rank(rank)` | 按品级查询 |
| `set_world_state(ws)` | 注入 WorldState 引用（用于坐标查询） |
| `add_official(official)` | 添加官员 |
| `bulk_load_officials(officials)` | 批量加载官员 |

> **迁移说明**：`Location` 数据类、`distance_li()` 函数、`get_all_locations()`、`get_location()`、`add_location()` 已移除。坐标信息现在由 WorldState Entity 的 metadata 字段管理。数据库迁移仅检测 `enabled` 和 `extra_prompt` 列。

---

### 3.10 timer_system.py — 定时器系统

与 DEV_GUIDE 一致，无重大变更。

---

### 3.11 chain_log.py — 调用链日志

与 DEV_GUIDE 一致，无重大变更。

---

### 3.12 world_state.py / world_commands.py — 事务化世界状态

**作用**：存储实体动态状态、资产库存、非库存指标/参数、关系与进行中事件；所有核心写入通过 `WorldCommandBus` 在单个 SQLite 事务中执行。

**资源分层**：

- `asset`：可支出库存，存于 `inventory`，带 `anchor_rate` 与版本号。
- `metric`：世界指标，存于 `entity_values`，不参与库存余额与价值合计。
- `parameter`：模拟参数，存于 `entity_values`，按资源目录的 min/max 约束。
- `resource_catalog` 是权威资源定义；内置资源与 DLC 自定义资源使用同一注册方式。

**核心数据结构**：

- `Entity`：含 `version`，状态更新支持乐观并发控制。
- `InventoryItem`：资产数量、版本、价值锚；`base_value = quantity × anchor_rate`。
- `EntityValue`：独立指标或参数及其版本。
- `ResourceDefinition`：name/unit/kind/default/min/max/anchor_rate/metadata。
- `OngoingEvent`：延迟生效的状态与资源变化。
- `WorldCommand` / `WorldCommandResult`：稳定 command ID、canonical payload hash、回执、链路和错误信息。

**命令类型**：`UPDATE_STATUS`、`ADJUST_RESOURCE`、`SET_RESOURCE`、`CREATE_EVENT`、`UPDATE_EVENT`、`DELETE_EVENT`、`APPLY_MATURED_EVENT`。

**事务与审计规则**：

1. 命令使用 `BEGIN IMMEDIATE` 和进程内 `RLock` 串行化写事务。
2. 同 command ID + 同 payload 返回原回执，不重复落账；同 ID + 不同 payload 返回 `idempotency_conflict`。
3. 成功和拒绝回执均持久化到 `world_commands`。
4. 资产变更写入 `inventory_ledger`，记录数量差、锚定价值差、余额、原因、模拟时间与 chain ID。
5. 余额不足、版本冲突、非法资源或非有限数字会使整条命令回滚。
6. 成熟事件在一个事务中完成状态更新、全部资源变化、ledger 和事件删除；任一步失败则事件保留且无部分落账。

**重要方法**：

| 方法 | 作用 |
|-----|------|
| `get_entity_values(id, kind?)` | 查询指标/参数读模型 |
| `get_inventory_valuation(id)` | 返回资产总基础价值与逐项价值 |
| `list_resource_definitions()` | 查询资源目录 |
| `upsert_resource_definition(definition)` | 注册或更新 DLC 资源定义 |
| `get_inventory_ledger(id)` | 查询资产审计账本 |
| `get_world_command(command_id)` | 查询幂等命令回执 |
| `update_status(...)` | 兼容 API；转发 `UPDATE_STATUS` 命令 |
| `set_inventory/update_inventory(...)` | 兼容 API；按目录自动路由到资产、指标或参数 reducer |
| `apply_matured_events(sim_time)` | 原子应用全部到期事件 |
| `bulk_load(...)` | 单事务批量导入 |
| `restore_all(data)` | 单事务完整恢复，失败整体回滚 |
| `export_all()` | 导出 schema v2、资源目录、价值、指标/参数、ledger 与事件 |

`pop_matured_events()` 只保留为兼容只读接口，不再删除事件；Orchestrator 必须调用 `apply_matured_events()`。

---

### 3.13 llm_client.py — LLM 客户端

**核心类型**：
- `LLMConfig` — 配置（base_url/api_key/model/timeout_s/max_concurrent）
  - `from_file(path)` / `save_to_file(path)` / `to_display_dict()`
- `LLMResponse` — LLM 响应数据类（content, tool_calls, input_tokens, output_tokens, latency_ms, raw）
- `LLMClient` 方法：
  - `chat()` — 异步调用（非流式）
  - `chat_stream()` — 流式调用
  - `close()` — 关闭 HTTP 客户端
  - `config` — 属性，当前配置
  - `async update_config(new_config)` — 热更新配置
- `TokenCounter` — 累计 token 统计

---

### 3.14 memory_compressor.py — 记忆压缩器

**核心接口**：
- `MemoryCompressor` — Protocol 协议类，定义 `compress(messages, existing_summary, agent_context)` 接口

**默认实现**：
- `LLMMemoryCompressor` — 基于协议的 LLM 压缩器
  - `compress(messages, existing_summary, agent_context)` → 压缩并返回摘要消息 dict
  - `existing_summary` — 上一次压缩的摘要（需合并），或 None
  - `agent_context` — Agent 身份描述字符串
  - 内部 `_compress_failures` 计数器，超 3 次退化为截断

**辅助函数**：
- `is_summary_message(msg)` — 检测消息是否为压缩摘要
- `find_safe_split_point(messages, target)` — 找到安全分割点（避免切断 tool_calls 组）
- `format_messages_for_compression(messages)` — 格式化消息列表为可读文本

**常量**：
- `SUMMARY_META_KEY = "_meta"` — 摘要消息标记键
- `SUMMARY_TYPE_VALUE = "memory_summary"` — 摘要类型值

---

### 3.15 runtime_paths.py — 运行时路径

- `RUNTIME_DIR = Path("runtime")` — 运行时目录
- `WORLD_DB` — `runtime/world.db`
- `CHAIN_LOG` — `runtime/chain_log.jsonl`
- `STATE_FILE` — `runtime/state.json`
- `AGENTS_DIR` — `runtime/agents/`
- `ensure_runtime_dir()` — 确保 runtime/ 及子目录存在
- `runtime_exists()` — 判断 runtime 是否已初始化

---

### 3.16 runtime_persistor.py — 运行时状态持久化

- `RuntimePersistor` — 负责 Orchestrator 的 in-memory 状态定期落盘到 `runtime/` 目录
  - `mark_agent_dirty(agent_id)` — 标记 Agent 记忆需要落盘
  - `mark_state_dirty()` — 标记 state.json 需要落盘
  - `async flush()` — 批量序列化并原子写入（state.json + agents/*.json）
  - `async run_flush_loop(interval=2.0)` — 后台定时 flush
  - `start()` / `stop()` — 启停 flush loop

设计要点：mark_*_dirty 只标记，不做 IO；flush() 批量序列化并原子写入（.tmp → rename）。

---

### 3.17 loader.py — DLC 数据加载器

**加载流程**：
1. `load_world_state()` → 加载实体、库存和关系到 WorldState，返回 (entity_count, inventory_count, relation_count)
2. `load_officials()` → 加载官员列表
3. `create_agents()` → 为每个官员创建 Agent 实例，注入 system prompt
4. `load_events()` → 加载初始事件

> **注意**：`load_locations()` 已移除。坐标信息现在通过 `load_world_state()` 从 world_state.json 的实体 metadata 加载。

**Prompt 优先级**：
1. 专用文件 `daming_court/prompts/{官员ID}.txt`（最高）
2. 通用模板 `official_template.txt`
3. 默认模板

**记忆压缩注入**：`create_agents()` 创建 Agent 后，Orchestrator 会为每个 Agent 注入 `LLMMemoryCompressor`。

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
    │       ├─ 查层级：知县 → 知府（直属上级）
    │       ├─ 调用 wake_agent(target="知府", from_agent="知县", urgency="normal")
    │       │
    │       ▼
    │   handler 根据双方坐标自动算延迟 → 写 TimerSystem（异地）或 pending_wakeups（同城）
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
基层执行（ActionAgent）
    │
    ├─ 知县 Agent request_action → ActionAgent 模拟行动
    │   ├─ query_entity → 查看灾情
    │   ├─ update_inventory → 扣除赈灾粮款
    │   ├─ add_ongoing_event → 目标地点排队 N 天后到账
    │   └─ complete_action → 回执 N 天后送达
    │
    ▼
OngoingEvent 到期
    │
    └─ _apply_matured_event() → 自动更新 current_status + 增减库存
```

### 4.2 调用链追踪（Chain Log）

每个消息都有唯一的 `chain_id`，整条链路的每一步都记录在 `chain_log.jsonl`：

```
chain_abc123:
  [hop=1] 吴县知县_张三 → submit_action → "蝗灾严重，请知府拨粮" (distorted: false)
  [hop=2] Router → wake_agent → 苏州知府_况钟 (urgency=normal, delay: 0.1 day)
  [hop=3] 苏州知府_况钟 → submit_action → "江南蝗灾，请求布政使赈灾" (distorted: true)
  [hop=4] Router → wake_agent → 江南布政使_周忱 (urgency=express, delay: 1.4 day)
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
| `/api/new-game` | POST | 清空 runtime 并重新初始化（新游戏） |
| `/api/memorials` | GET | 返回当前持久化的奏折列表 |
| `/api/saves` | GET | 列出所有存档（按 saved_at 倒序） |
| `/api/save` | POST | 存档（需先暂停） |
| `/api/load` | POST | 读档（恢复 SQLite/chain_log/Agent 记忆） |
| `/api/saves/rename` | POST | 重命名存档 |
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
| `/api/tianji/history` | GET | 返回当前 messages |
| `/api/tianji/tools` | GET | 返回工具 schema，按类别分组 |

### 5.5 调试接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/debug/timeline?after=N&limit=50` | GET | chain log 增量拉取 |
| `/api/debug/timers` | GET | 定时器状态 |
| `/api/debug/router-log?after=N` | GET | Router 决策日志增量拉取 |
| `/api/debug/action-log?after=N` | GET | ActionAgent 执行日志增量拉取 |
| `/api/chain-log/{chain_id}` | GET | 单条链的上帝视角 |
| `/api/token-usage` | GET | Token 用量统计 |
| `/api/in-transit` | GET | 在途公文列表 |

### 5.6 世界状态

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/world-state` | GET | 导出 schema v2：实体、资产、指标/参数、资源目录、关系、事件与账本 |
| `/api/world-state/{entity_id}` | GET | 返回实体版本、锚定资产、指标、参数、总价值、关系与事件 |
| `/api/world-state/{entity_id}` | PUT | 创建或更新实体 |
| `/api/world-state/{entity_id}` | DELETE | 删除实体（级联删库存/关系/事件） |
| `/api/world-state/{entity_id}/status` | POST | 事务化修改状态；支持 `Idempotency-Key` 与 `expected_version` |
| `/api/world-state/{entity_id}/inventory` | PUT | 按资源目录更新资产/指标/参数；支持幂等与乐观版本 |
| `/api/world-state/{entity_id}/inventory/{category}/{item_name}` | DELETE | 保留兼容入口；注册资源不可直接删除 |
| `/api/relations` | GET | 列出所有关系 |
| `/api/world-state/{entity_id}/relations` | GET | 获取实体关系（parents/children/siblings） |
| `/api/relations` | POST | 创建或覆盖关系 |
| `/api/relations` | DELETE | 删除关系 |
| `/api/world-state/{entity_id}/events` | GET | 列出实体的正在发生的事件 |
| `/api/world-state/{entity_id}/events` | POST | 创建正在发生的事件 |
| `/api/world-state/events/{event_id}` | PATCH | 修改事件 |
| `/api/world-state/events/{event_id}` | DELETE | 取消事件 |

### 5.7 Agent 编辑器

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/editor/officials` | GET | 返回 officials + locations 配置 |
| `/api/editor/officials/{id}` | PUT/DELETE | 更新/删除官员 |
| `/api/editor/officials/batch` | POST | 批量保存官员 |
| `/api/editor/locations/{location_id}` | GET | 获取地点详情（从 WorldState metadata） |
| `/api/editor/locations/{location_id}` | PUT | 更新地点（写入 WorldState metadata + DB） |
| `/api/editor/prompt-preview` | POST | 预览官员 system prompt |
| `/api/editor/personas` | GET | 返回所有人格种子 |
| `/api/editor/personas/{name}` | PUT/DELETE | 新增/删除人格种子 |
| `/api/editor/personas/batch` | POST | 批量保存人格种子 |

> **注意**：`/api/editor/locations/batch` 已移除。

### 5.8 WebSocket

| 端点 | 说明 |
|------|------|
| `/ws` | 实时 tick 推送（每 0.25s 广播 TickResult） |

---

## 六、数据库结构

### SQLite 表结构（runtime/world.db）

> CourtGraph（官员表）、TimerSystem（定时器表）、WorldState（实体表、库存表、关系表、事件表）共用同一个 `runtime/world.db` SQLite 文件。

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

-- 实体表（状态版本用于 optimistic concurrency）
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    current_status TEXT DEFAULT '',
    updated_at REAL DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 0
);

-- 仅存放可支出资产；anchor_rate 从 resource_catalog 读取
CREATE TABLE inventory (
    entity_id TEXT NOT NULL,
    category TEXT NOT NULL,
    item_name TEXT NOT NULL,
    quantity REAL DEFAULT 0,
    description TEXT DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, category, item_name),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

-- 权威资源定义，DLC 可注册自定义资产、指标和参数
CREATE TABLE resource_catalog (
    name TEXT PRIMARY KEY,
    unit TEXT DEFAULT '',
    kind TEXT NOT NULL,                 -- asset / metric / parameter
    default_quantity REAL DEFAULT 0,
    min_quantity REAL,
    max_quantity REAL,
    anchor_rate REAL,
    metadata TEXT DEFAULT '{}',
    updated_at REAL DEFAULT 0
);

-- 非库存指标与参数
CREATE TABLE entity_values (
    entity_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    value REAL DEFAULT 0,
    description TEXT DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, name),
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);

-- 资产审计账本
CREATE TABLE inventory_ledger (
    entry_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    resource_name TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    quantity_delta REAL NOT NULL,
    anchor_rate REAL NOT NULL,
    base_value_delta REAL NOT NULL,
    balance_after REAL NOT NULL,
    reason TEXT DEFAULT '',
    sim_time REAL DEFAULT 0,
    chain_id TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    UNIQUE(command_id, entity_id, resource_name, sequence)
);

-- 幂等命令回执；成功与拒绝均保留
CREATE TABLE world_commands (
    command_id TEXT PRIMARY KEY,
    command_type TEXT NOT NULL,
    entity_id TEXT DEFAULT '',
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT DEFAULT '{}',
    error_code TEXT DEFAULT '',
    error_message TEXT DEFAULT '',
    chain_id TEXT DEFAULT '',
    intent_id TEXT DEFAULT '',
    schema_version TEXT DEFAULT '1.0',
    sim_time REAL DEFAULT 0,
    created_at REAL NOT NULL
);

-- 实体关系表
CREATE TABLE entity_relations (
    parent_id TEXT NOT NULL,
    child_id  TEXT NOT NULL,
    rel_type  TEXT NOT NULL,  -- subordinate / affiliated
    note      TEXT DEFAULT '',
    PRIMARY KEY (parent_id, child_id, rel_type)
);
CREATE INDEX idx_rel_child  ON entity_relations(child_id);
CREATE INDEX idx_rel_parent ON entity_relations(parent_id);

-- 正在进行的事件表
CREATE TABLE ongoing_events (
    event_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    event_type TEXT DEFAULT 'state_change',  -- state_change / inventory_arrival
    description TEXT DEFAULT '',
    start_time REAL DEFAULT 0,
    end_time REAL DEFAULT 0,
    expected_result TEXT DEFAULT '{}',  -- JSON: {"new_status": ..., "inventory_deltas": [...]}
    source_action_id TEXT DEFAULT '',
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
CREATE INDEX idx_event_entity ON ongoing_events(entity_id);
CREATE INDEX idx_event_endtime ON ongoing_events(end_time);

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
```

**迁移兼容**：`court_graph.py` 启动时会自动检测旧数据库缺少 `enabled`、`extra_prompt` 列并执行 `ALTER TABLE ADD COLUMN`。坐标列(`x`, `y`)不再在 officials 表，已迁移到 WorldState 的 Entity metadata。

---

## 七、当前数据配置

### 官员层级（officials.json）

```
皇帝 (rank=15, player)
  ├─ 司礼监太监_王振 (rank=13, eunuch, 圆滑)
  │    └─ 内阁_杨士奇 (rank=10, 忠直)
  │         └─ 通政司 (rank=9, institution, 部门)
  │              └─ 江南布政使_周忱 (rank=8, 忠直)
  │                   └─ 苏州知府_况钟 (rank=5, 忠直)
  │                        ├─ 吴县知县_张三 (rank=1, 贪腐)
  │                        └─ 昆山知县_李四 (rank=1, 忠直)
  ├─ 户部 (rank=12, institution, 部门)
  ├─ 吏部 (rank=12, institution, 部门)
  ├─ 礼部 (rank=12, institution, 部门)
  ├─ 兵部 (rank=12, institution, 部门)
  ├─ 刑部 (rank=12, institution, 部门)
  └─ 工部 (rank=12, institution, 部门)
```

### 地点配置（坐标存储在 WorldState Entity metadata 中）

| 地点 | 类型 | 区域 | X (里) | Y (里) |
|------|------|------|--------|--------|
| 京城 | location | 直隶 | 0 | 0 |
| 北京 | location | 直隶 | 0 | 0 |
| 南京 | location | 江南 | 429 | -1741 |
| 江南布政使司 | institution | 江南 | 200 | -1490 |
| 苏州府 | location | 江南 | 450 | -1730 |
| 吴县 | location | 江南 | 448 | -1732 |
| 昆山县 | location | 江南 | 510 | -1680 |

### 人格模板（personas.json）

- **贪腐**：倾向于隐瞒灾情以避免追责，偶尔索取好处，上报时淡化问题严重性
- **忠直**：如实上报，不夸大不隐瞒，为百姓着想，哪怕可能得罪上级
- **圆滑**：善于揣摩上意，措辞委婉，优先保护自己，不完全说谎也不完全说实话
- **部门**：作为机构人格，按部门职责行事，关注部门利益
- **刚正**：刚正不阿，敢于直言进谏
- **野心**：有野心，善于经营人脉，寻求权力扩张的机会

### 资源代币

资源目录将数值分为 `asset`、`metric`、`parameter`。内置资产为金钱、粮草、工料、兵力；科技点、满意度、人口属于指标；生活质量、税率、生产系数属于参数。资产通过 `anchor_rate` 折算到统一基础价值，DLC 可注册新的资源定义；不适用值仍用 `-1` 表示且禁止修改。

---

## 八、前端结构

### 8.1 AppNav.jsx — 浮动导航栏

- 可拖拽的底部浮动导航，在朝堂/编修/天玑三个页面间切换
- 导航带页面印章图标、当前页高亮与位置持久化；移动端自动收紧
- 三个入口页面共享此组件

### 8.2 App.jsx — 皇帝视角

- 奏折列表（来自各级官员的上报）
- 批复操作（批准/驳回/留中/下旨）
- 在途公文列表（显示剩余送达时间）
- 关系图切换（RelationsView 组件）
- 新游戏按钮
- 模拟时间显示（天/时辰/刻）
- 控制面板（启动/暂停/设置/存档/读档）

### 8.3 GodView.jsx — 上帝视角

- 时间线（chain log 增量拉取，自动滚动，天玑 AI 调用以青色高亮）
- Router 决策日志
- ActionAgent 执行日志
- 定时器状态

### 8.4 WorldStateView.jsx — 世界状态管理

- 实体列表（按类型筛选 + 搜索关键词）
- 实体 CRUD：新建、编辑属性（类型/名称/描述/当前状况）、删除
- 锚定资产行内编辑：显示数量、价值锚、基础价值、总价值和版本
- 世界指标独立只读展示，不计入库存与资产价值
- 核心参数独立编辑，写入时发送 `Idempotency-Key` 与 `expected_version`
- 正在进行的事件 CRUD（OngoingEvent 管理）
- 实体关系编辑
- 地点坐标编辑（仅 `type=location` 时显示）

### 8.5 AgentEditor.jsx — Agent 编辑器

- 官员树形结构 + 系统Agent 配置（Router/ActionAgent/模板/天玑 prompt）
- 官员属性编辑
- 启用/禁用切换
- 人格种子编辑（CRUD）
- Prompt 预览

### 8.6 TianJiView.jsx — 天玑 AI

- 三栏布局：工具列表面板 | 对话区 | 世界状态快照
- 工具调用块折叠展示
- 会话重置按钮
- SSE 流式回复

### 8.7 共享视觉系统

- `design-system.css` 定义全局色彩、字号、字体栈、圆角、阴影、表单焦点、滚动条与低动态偏好
- 视觉方向为“宣纸墨色 × 宫廷朱砂 × 克制鎏金”，三个入口共享品牌印章与层级规则
- `App.css`、`editor.css`、`tianji.css` 保留各自业务布局，并在文件末尾提供页面级视觉覆盖
- 桌面端维持高信息密度；窄屏下主页面、编辑器与天玑工作台切换为纵向布局
- 站点 favicon、HTML 标题和页面品牌文案已统一

### 8.8 RelationsView.jsx — 关系图

- ReactFlow + dagre 自动布局的实体关系可视化
- 节点可拖拽
- 显示实体之间的关系边（subordinate/affiliated）

---

## 九、如何添加新内容

> 添加新官员、新事件、世界状态实体、自定义 Prompt 的详细操作指南见 [DEV_GUIDE.md](DEV_GUIDE.md)。

---

## 十、待完成功能（TODOS）

### v1 未完成
- **时间模拟器**：科学计算过程耗时
- **集成测试**：mock LLM 的端到端链路测试

### v1 已完成
- **天玑 AI**：全工具注入的调试对话 Agent，独立前端页面，SSE 流式回复，工具调用可视化
- **记忆压缩**：LLM 记忆压缩器，Agent 记忆超阈值时自动摘要，v2 格式存档
- **OngoingEvent 系统**：延迟生效事件，到期自动落账（状态 + 库存）
- **实体关系系统**：实体间有向关系边（subordinate/affiliated）
- **资源目录与价值锚**：资产/指标/参数分层，资产统一折算基础价值，版本与账本可审计，机构不适用项为 -1
- **运行时持久化**：RuntimePersistor 后台定期 flush 到 runtime/ 目录
- **关系图前端**：RelationsView (ReactFlow) 实体关系可视化

### v1.1+
- **Agent 孵化机制**：机构 Agent 在某些条件下自动孵化出独立的官员 Agent
- **更多事件类型**：当前基本只有蝗灾样例

---

## 十一、关键设计原则

1. **Agent 只表达意图，不直接执行**：所有行动由 Router（世界运行法则）仲裁
2. **信息失真由 Agent 自主产生**：`original_intent` vs `content`，`is_distorting` 由 Agent 声明
3. **时间推进不被 LLM 阻塞**：Agent 调用作为后台 asyncio.Task
4. **定时器延迟用游戏时间**：`delay_days * 86400` 游戏秒，改速度自然影响触发快慢
5. **DLC 分离**：框架层（multiagent_framework）可复用于其他场景
6. **FC 工具单点定义**：工具 schema 在 `fc_tools.py`，handler 在 `fc_handlers.py`，每个工具只有一份代码；添加新工具只需改这两个文件
7. **FCContext 模式**：调用方组装上下文，handler 消费上下文；Orchestrator 和天玑用同一个 handler，靠 context 字段决定执行路径
8. **共享数据库**：CourtGraph、TimerSystem、WorldState 共用 `runtime/world.db`，各自管理独立的表
9. **记忆压缩**：Agent 记忆超阈值时自动触发 LLM 摘要，序列化格式为 v2 dict（`{version, summary, messages}`），兼容 v1 list 反序列化
10. **OngoingEvent 原子落账**：Orchestrator 只调用 `apply_matured_events()`；状态、全部资源变化、账本和事件删除同事务提交
11. **资源目录与统一价值锚**：资产、指标、参数分表；资产使用 `quantity × anchor_rate` 形成可比较的基础价值
12. **命令幂等与可审计**：核心世界写入拥有稳定 command ID、乐观版本、成功/拒绝回执和资产账本
13. **运行时持久化**：RuntimePersistor 每 2 秒后台 flush 到磁盘，原子写入防崩溃损坏
---

## 十二、2026-07 优化路线

Agent 多轮调用、DLC Studio、Hybrid Router、世界事务/价值锚、Memory v3、Prompt Registry 与多模型任务路由的完整设计和分阶段验收标准，见 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)。


---

## Phase 0: LLM configuration security (2026-07-10)

- `LLMConfig` supports `${ENV_VAR}` references and the explicit `api_key_env` field.
- `save_to_file()` never writes an in-memory plaintext API key; only the environment-variable reference is persisted.
- `/api/settings` and the settings UI preserve and edit `api_key_env`. A key entered directly in the UI is session-only unless the process environment is configured.
- Timeout and concurrency limits are clamped to at least `1`.
- Streaming fallback calls the unlocked request path while holding the shared semaphore, preventing a deadlock when `max_concurrent=1`.
- Safe templates: `config/llm.example.json` and `.env.example`. The local `config/llm.json` remains ignored by Git.


## Phase 1: task-aware Model Gateway and Trace (2026-07-10)

- `multiagent_framework/model_gateway.py` defines ProviderConfig, ModelProfile, task policies, independent per-profile clients/semaphores, capability checks, ordered fallback, redacted rotating JSONL traces, and per-task/model/provider metrics.
- Startup prefers ignored local `config/models.json`; when absent it adapts the legacy `config/llm.json` into one default profile without changing behavior.
- Task bindings: `router.ambiguous`, `agent.turn`, `action.simulation`, `memory.compress`, and `tianji.chat`. Ordinary DLC agents receive an agent-bound adapter so traces include their entity ID.
- Multi-round ActionAgent and TianJi calls reuse one `trace_id` and `turn_id`; spans include chain, round, task, profile, provider, model, token, latency, fallback, and termination metadata. Prompt/response bodies and keys are excluded from gateway traces.
- Debug APIs: `/api/debug/model-config`, `/api/debug/llm-metrics`, and `/api/debug/llm-traces`.
- Provider endpoints and environment-variable names are local configuration only. The unauthenticated legacy settings API cannot forward an existing environment secret to a changed `base_url`, and cannot select a different environment variable remotely.
- Safe template: `config/models.example.json`. Local `config/models.json` is ignored by Git.

## Phase 2：通用 TurnEngine 与普通 Agent 多轮执行（2026-07-10）

### 已实现架构

- 新增 `multiagent_framework/turn_engine.py`，统一管理一次逻辑 turn 内的多轮 LLM + FC 循环。
- `TurnBudget` 同时限制：最大轮数、最大工具调用数、输入输出 token 总量、墙钟时间、连续无进展轮数。
- 每轮复用同一 `trace_id / turn_id / chain_id / agent_id / inbox_sequence / sim_time_snapshot`，仅递增 `round`。
- `ActionAgent` 已删除本地循环，迁移到通用 `TurnEngine`，仍以 `complete_action` 作为终止工具并保留 `fc_trace` 兼容输出。
- 普通官员与机构 Agent 现在支持：
  1. `list_entities / query_entity / list_agents` 只读查询；
  2. `submit_action / request_action` 提交一个或多个意图；
  3. `finish_turn` 显式结束；
  4. 查询、接收、重复意图拒绝和终止结果都以真实 tool message 写入记忆。
- `submit_action / request_action` 的即时返回值为 `accepted_pending_execution`，明确表示“已接收但未执行完成”，不再伪造 `{"status":"已执行"}`。
- Router/FC 的真实调度返回会以 `【系统执行反馈】` 写回源 Agent；ActionAgent 最终结果通过 Timer 以 `【真实行动结果】` 回流，并携带 `intent_id` 关联原始意图。

### 并发与持久化

- 每个 `BaseAgent` 拥有独立、公平的 `asyncio.Lock` 和单调递增的 inbox sequence。
- 相邻 tick 可以继续并行处理不同 Agent，但同一个 Agent 的收件、记忆写入、压缩、多轮 LLM 调用和反馈写入严格串行。
- `RuntimePersistor` 改为调用 `await agent.snapshot_memory()`，在 Agent 锁内获得一致快照，避免压缩或工具消息追加期间落盘。
- Orchestrator 在调用 Agent 前注入继承的 chain IDs、模拟时间和时间倍率，修复普通 Agent 多轮 trace 缺失入站因果链的问题。

### 可观测性

- 新增 `GET /api/debug/agent-turns`，仅返回脱敏执行状态：busy、inbox sequence、停止原因、是否完成、轮数、工具调用数、token 和耗时。
- 具体每轮模型 span 继续通过 `/api/debug/llm-traces` 查看；Prompt、响应正文和密钥不会进入 Model Gateway trace。

### 测试

`tests/unit/test_turn_engine.py` 覆盖：

- 重复查询的 no-progress 自动停止；
- 普通 Agent 的“查询 → 决策 → submit → finish”；
- 真实 query 结果和 `accepted_pending_execution` 进入记忆；
- 同一 Agent 的并发调用被串行化并按 inbox sequence 排序；
- Router 真实调度结果自动回写；
- ActionAgent 迁移后仍正确完成并保留 chain/round trace。


## Phase 3：Hybrid Router 与链路安全（2026-07-10）

- 确定性优先，歧义意图才调用 `router.ambiguous` 模型策略。
- `IntentEnvelope` / RouterCommand v2 贯穿 chain、intent、hop、schema 与幂等信息。
- 候选目标受 `CourtGraph` 可达性和有界候选集约束；非法目标、重复边、循环和超跳进入 dead-letter。
- `ChainLog` 持久化路由边索引并在重载时重建。

## Phase 4：世界命令、事务与统一价值锚（2026-07-10）

- 新增 `world_commands.py` 与 `WorldCommandBus`，核心状态、资源和事件写入均由单事务 reducer 执行。
- 新增 `resource_catalog`、`entity_values`、`inventory_ledger`、`world_commands`；旧指标/参数从 inventory 幂等迁移。
- 所有资产通过 `anchor_rate` 折算为统一基础价值；前端显示逐项和总锚定价值。
- Agent tool-call ID、Router command ID 与 API `Idempotency-Key` 贯穿到世界命令，重放不会重复落账。
- 成熟事件原子应用；失败完整回滚并保留事件。
- 验收基线：72 项 Python 测试通过；Python compileall、前端生产构建、`git diff --check` 和乱码扫描通过。
- Phase 5 DLC Registry、通用 Runtime Loader 与 DLC Studio 核心/高级编辑工作流已完成；依赖版本约束和 Content 迁移辅助也已落地，下一阶段聚焦真实模型轨迹评测、规模测试与生产化。


## Phase 5：DLC Registry、Runtime Loader 与 Studio MVP（2026-07-10）

### Registry 与包格式

- `multiagent_framework/dlc_registry.py` 提供 `DLCManifest`、`DLCPackage`、`DLCValidationIssue` 与 `DLCRegistry`。
- 支持 `legacy-v1` 和 `bundle-v1`：内置 `daming_court/dlc.json` 作为兼容 manifest；Studio 新包位于 `dlcs/<id>/dlc.json + content.json + prompts/`。
- Registry 负责 manifest 发现、package-root 路径约束、原子 JSON 写入、draft/publish/enable 生命周期、依赖拓扑排序以及跨已发布 DLC 的实体/Agent/资源 ID 冲突检查。`dependency_constraints` 支持严格 SemVer、比较运算、`^`、`~`、通配符和 `||`；校验会报告缺失、重复、自依赖、循环依赖及已安装版本不匹配，发布/启用还会强制依赖先发布/启用。
- `legacy-v1` 会规范化为统一 read model；旧 inventory 中的 metric/parameter 会在读取时迁移到 `entity_values`，资产继续保留统一 `anchor_rate`。
- `dlcs/example_frontier` 是默认停用、校验通过的最小 `bundle-v1` 示例，可作为新 DLC 模板。

### Runtime Loader

- `multiagent_framework/dlc_runtime.py` 提供 `PromptRegistry`、`AgentFactory`、`DLCBundleLoader` 与 `DLCLoadReport`。
- 启动时按依赖顺序加载所有 `published + enabled` DLC；资源定义先注册，再以单事务装载 entity、inventory、entity value、relation 和 ongoing event。
- Agent 配置被转换为 `Official` 及 `OfficialAgent/InstitutionAgent`，玩家角色和 disabled Agent 不生成模型实例；初始事件仅在新世界 seed 时注入。
- `main.py` 从 primary DLC 取得 router/action/tianji Prompt，运行主路径不再依赖固定 Prompt 文件；旧 `_load_legacy_dlc` 仅作为无可用 manifest 时的兼容 fallback。

### DLC Studio 与 API

- 后端 `routers/dlc.py` 提供 `/api/dlcs` 下的列表、创建、详情、manifest/content 保存、校验、发布、启停、迁移 preview/apply 及实体/Agent/资源 keyed CRUD。迁移执行支持 `expected_source_schema_version` 乐观并发保护。
- API 只能通过 Registry 操作限定 package root，entrypoint 经过 resolve + relative boundary 检查，不允许路径穿越；Provider URL、API key 和 secret env 不属于 DLC 远程编辑范围。
- 前端新增 `frontend/dlc.html`、`DLCStudio.jsx`、`DLCContentEditors.jsx` 与 `DLCStudio.css`，可从零创建草稿、编辑 manifest，并以专用表单维护实体、Agent、资源、库存、实体数值、关系、初始/持续事件、Persona 和版本化 Prompt blocks。
- Prompt 编辑器支持 block 优先级、启停、required variables、metadata、组合预览、字符数与 token 估算；raw JSON 编辑仍作为完整 schema 的高级入口。Manifest 区域提供 DLC 依赖选择、已安装版本显示和版本范围编辑。
- 所有结构化内容修改仍通过完整 content 的原子 `PUT /api/dlcs/{id}/content` 保存，避免跨字段引用在多次部分写入之间暴露不一致状态。
- `frontend/src/DLCStudioTools.js` 提供安全 Content 导入/导出、规范化、稳定键 Diff 与最多 50 个持久化快照的 Undo/Redo。导出格式为 `world-simulator-dlc-content-v1`，仅包含 DLC ID、名称、版本和 Content，不包含完整 manifest、API Key、Provider URL 或 secret env。
- 实体、Agent、资源、其他结构化表单、Raw JSON 和导入操作都先生成完整 Content Diff，经确认后再执行原子 PUT；Undo/Redo 也使用同一确认与持久化路径。Diff 可识别 keyed 新增/删除/修改、数组顺序、schema 版本和自定义扩展字段。
- 导入兼容标准 envelope 或纯 Content JSON，自动补齐标准集合，拒绝错误 section shape 和未知 envelope 版本；切换 DLC 会建立新的历史基线，Manifest 保存、校验和发布不会清空 Content 历史。Content migration preview 同样进入 Diff/确认/原子 PUT/历史栈，可撤销和重做。
- 发布前会校验关系类型与自引用、事件 ID/严重度/目标/时间/JSON payload、Persona 非空内容、Prompt 结构与允许变量，以及库存价值锚一致性。库存条目不能覆盖资源目录的 `anchor_rate`，运行时也只采用 catalog anchor。
- 内置 legacy DLC 在 Studio 中只读，避免旧格式与新 bundle 写模型互相覆盖。bundle Content 支持 `0.9 -> 1.0` 迁移：`officials -> agents`、resource `type -> kind`、inventory `resource_name/name -> item_name`，并以原子写入和 source schema guard 防止过期预览覆盖。

### 验收

- 新增 `tests/unit/test_dlc_registry.py` 和 `tests/unit/test_dlc_runtime.py`，覆盖路径安全、legacy 规范化、原子 round-trip、引用/锚校验、发布启停、CRUD、跨包冲突、依赖排序、SemVer 范围、依赖循环/生命周期、Content 迁移及运行时物化；`test_dlc_api.py` 覆盖迁移 preview/apply 和过期预览保护。
- Phase 5 初始验收曾以 84 项 Python 测试为基线；当前全项目验证结果统一以文末“Current verification baseline”为准。



## Phase 6/7: Model policy Studio, Prompt Registry, and Memory v3 (2026-07-10)

### DLC and Agent model policy

- `DLCManifest.model_bindings` accepts the canonical nested form:

```json
{
  "tasks": {
    "agent.turn": "fast_tools",
    "router.ambiguous": "deep_reasoning",
    "action.simulation": "deep_reasoning",
    "memory.compress": "cheap_long_context",
    "tianji.chat": "deep_reasoning"
  },
  "agents": {
    "mayor": "deep_reasoning"
  }
}
```

- Legacy flat task mappings remain readable. Registry validation rejects malformed sections, empty profile names, and Agent bindings that reference an Agent outside the package.
- Agent selection precedence is `content.agents[].model_profile` -> `manifest.model_bindings.agents[agent_id]` -> `manifest.model_bindings.tasks["agent.turn"]` -> global ModelGateway task policy.
- World-global model tasks (`router.ambiguous`, `action.simulation`, `memory.compress`, and `tianji.chat`) use the primary DLC task override when present. Ordinary Agent clients remain package-scoped so a primary DLC override cannot leak into a secondary DLC.
- Unknown profiles are rejected before world seeding. Model traces record the selected task/profile/provider/model without storing prompt bodies, response bodies, or credentials.

### DLC Studio model policy UI

- `/dlc.html` now loads profile metadata from the read-only, redacted `/api/debug/model-config` endpoint.
- The Agent form can select an explicit `model_profile`; selecting inherit removes the field instead of persisting an invalid empty string.
- The Model Policy tab edits task and Agent bindings, previews effective precedence, lists configured model capabilities, and saves through the existing manifest API.
- Dropdown values only come from configured profiles. The Studio cannot read or modify API keys, Provider URLs, or secret environment variables.
- Deterministic `router.simple` is intentionally not offered as a model task because that route does not call an LLM.

### Versioned Prompt Registry

- `multiagent_framework/prompt_registry.py` provides versioned templates, priority-ordered blocks, required variables, missing/unknown/unused-variable lint, character budgets, token estimates, deterministic SHA-256 hashes, render reports, and definition export.
- DLC runtime keeps the old `dlc_runtime.PromptRegistry` import path while using the framework registry internally.
- `DLCLoadReport` now includes `prompt_hashes` and the selected `model_profiles` for every materialized Agent.
- `multiagent_framework/prompt_evaluation.py` provides deterministic gameplay Prompt contract evaluation with weighted required checks, version/hash pinning, required/allowed variables, character/token budgets, literal/regex assertions, ordered tool-policy checks, JSON output, and a CI-friendly non-zero exit code.
- `tests/evals/daming_prompt_contracts.json` freezes four real gameplay scenarios for the official Agent, ActionAgent, Router, and Tianji prompts. The current suite scores 100% for tool-first behavior, hierarchy/ID boundaries, no false completion, dry-run separation, and write-operation warnings. This static contract gate complements rather than replaces future live-model trajectory evaluation.

### Memory v3

- `multiagent_framework/memory_store.py` defines episodic, semantic, relationship, and commitment records with status, entities, importance, recency, expiry, source chain, and metadata.
- Retrieval is deterministic and budgeted. It combines text and Chinese-bigram overlap, entity overlap, importance, recency decay, memory-kind weights, and commitment status.
- Accepted `submit_action` / `request_action` intents create unresolved commitments. Final execution feedback resolves or fails the matching commitment; queued dispatch feedback does not close it early.
- Agent snapshots now use `{version: 3, summary, messages, structured_memory}` and remain compatible with v1 message lists and v2 summary/message objects.
- `RuntimePersistor` writes v3 snapshots under the Agent lock, and startup restore round-trips unresolved structured commitments.


## Phase 7: replay/live trajectory evaluation (2026-07-11)

- `multiagent_framework/trajectory_evaluation.py` executes schema-v1 suites through the shared `TurnEngine` in deterministic `replay` mode or credential-gated `live` mode through `ModelGateway`.
- Checks cover tool legality, terminal completion, routing accuracy, round/tool/token/time budgets, cumulative model latency, anchored resource conservation, memory recall, commitment outcome, and optional human score.
- `tests/evals/daming_trajectory_scenarios.json` contains four representative multi-round Agent, ambiguous Router, anchored world-transfer, and bounded decision trajectories. Replay currently passes at 100%.
- Replay and live may declare different expectations. Static replay evidence cannot silently satisfy live memory/world claims; real integrations inject a `live_tool_executor` and emit internal `_evaluation_evidence`, which is removed before the tool result returns to the model.
- Reports contain checks and aggregates only. They do not serialize messages, prompts, response bodies, Provider URLs, API keys, authorization values, or internal evidence. Live mode fails explicitly when no candidate model profile has runtime credentials.
- `TurnOutcome.model_latency_ms` accumulates model-reported latency across rounds, and `/api/debug/agent-turns` exposes it beside total elapsed time.
- Replay command: `python -m multiagent_framework.trajectory_evaluation tests/evals/daming_trajectory_scenarios.json --mode replay --output runtime/evals/daming-core-replay.json`.

### Current verification baseline

- `python -m pytest -q -p no:cacheprovider`: **244 passed**.
- `python -m compileall -q main.py multiagent_framework routers daming_court`: passed.
- `python -m multiagent_framework.prompt_evaluation tests/evals/daming_prompt_contracts.json --project-root .`: **4 scenarios, 100%, passed**.
- `npm test`: **6 DLC Studio tool tests passed** (safe import/export, format guard, keyed/order/extension diff, migration diff, Undo/Redo).
- `npm run lint`: passed with **0 errors and 0 warnings**.
- `npm run build`: passed, including `dlc.html`.
- `git diff --check`: passed.
- Suspicious mojibake scan: passed.
- The only test warning is the pre-existing inability to create `.pytest_cache` in this workspace; it does not affect test execution.

## Phase 8: deterministic scale evaluation and hot-path optimization (2026-07-11)

### Scale harness

- `multiagent_framework/scale_evaluation.py` exercises the real DLC registry/runtime, `CourtGraph` + deterministic Router batch path, SQLite-backed `WorldCommandBus`, and structured memory stores.
- `tests/evals/daming_scale_smoke.json` currently covers 6 DLC packages, 120 DLC Agents, 240 Router Agents, 600 Router actions, 40 world entities, 600 world commands, 40 memory stores, 2,400 memory records, and 120 memory queries.
- Reports are written atomically under ignored `runtime/evals/`; the current report is `runtime/evals/daming-scale-smoke.json`.
- `tests/evals/daming_scale_soak.json` adds a 100-cycle deterministic soak profile. Each cycle atomically updates and rediscovers one published DLC, advances one matured world event, executes one non-atomic mixed accepted/rejected command batch, and forces bounded memory pruning while retaining an unresolved commitment.
- Latest soak report: `runtime/evals/daming-scale-soak.json`, PASS in 5,012.090 ms with 100 DLC reloads, 100 world ticks, 2,400 accepted and 100 rejected commands, 100 committed mixed batches, 2,941 compacted memory records, zero remaining events, and zero anchored-value conservation error. This is a local baseline; environment-specific repeated-sample thresholds are still required before CI enforcement.

### Router hot-path

- `CourtGraph` now keeps thread-safe, detached-value indexes by official ID, superior, rank, and location. The first read loads one SQLite snapshot; normal routing then avoids per-action connection/query churn.
- `add_official()` and `bulk_load_officials()` invalidate every index while holding the graph lock. `refresh_cache()` explicitly reloads out-of-band database changes.
- `bulk_load_officials()` uses `executemany()`. SQLite connections use `closing(...)` so Windows temporary databases do not retain file handles.
- Smoke throughput improved from 143 Router actions/s (4,186 ms) to 5,017 actions/s (120 ms), about 35x in the latest run while preserving deterministic routing, unique intent IDs, chain propagation, and zero LLM calls.

### World-command batch transactions

- `WorldCommandBus.execute_batch(commands, atomic=True)` executes a command group with one connection and outer transaction. The first rejection rolls back newly applied sibling commands and returns explicit `rolled_back` / `not_executed` outcomes while retaining the rejected command audit receipt.
- `atomic=False` wraps each command in a SQLite savepoint. Successful siblings commit, rejected siblings leave no partial inventory/event/ledger mutation, and every result retains its own receipt semantics.
- Same-ID/same-payload replay, different-payload conflict, optimistic versions, per-command ledger rows, and base-value anchors retain their single-command behavior.
- `WorldState.apply_matured_events()` now uses the non-atomic batch path so one bad matured event cannot block valid events in the same world tick.
- The smoke profile uses batches of 100. World throughput improved from 142 commands/s (4,221 ms) to 1,912 commands/s (314 ms), about 13.4x, with 600 applied commands, 20 idempotent replays, 640 ledger rows, and zero base-value conservation error.
- End-to-end smoke time improved from 9,434 ms to 1,492 ms while peak traced memory remained below 1.5 MB.

### ModelGateway atomic hot reload and administration boundary

- Every model call now acquires one immutable configuration generation containing the task policy, profile metadata, and client map. Normal and streaming calls retain that generation until completion, so a reload cannot mix a new policy with an old client or interrupt an in-flight stream.
- A reload validates and deep-copies the candidate, prebuilds every replacement client, then swaps configuration, runtime-key map, clients, and generation under one state lock. Validation or client-construction failure leaves the active generation unchanged.
- Clients from a generation with active calls move to a retired pool and close only when its final call releases. Trace and response metadata retain the captured profile/provider/model and `config_generation`.
- Error redaction keeps all runtime credentials observed during the process lifetime, including retired generations, so a late old-generation failure cannot expose a replaced key. Prompt and response bodies remain excluded.
- `GET /api/debug/model-config` now returns `public_config()` only: generation, profile/model/capability metadata, task policies, and credential-availability booleans. Provider URLs, environment-variable names, local source paths, Authorization values, and keys are omitted.
- Local-file preflight and activation are available through `POST /api/debug/model-config/preview` and `POST /api/debug/model-config/reload`. They are hidden unless `WORLD_SIM_ENABLE_ADMIN_API=1` and require a constant-time checked `X-Admin-Token` matching `WORLD_SIM_ADMIN_TOKEN`. Responses and failure details are deliberately secret-free.
- Regression coverage includes successful activation, validation/build rollback, ordinary and streaming in-flight calls, captured-generation traces, retired-key redaction, atomic `TaskLLMClient` reads, public metadata filtering, disabled/wrong-token administration, safe preview/reload, and invalid-file rollback.

### DLC Runtime atomic hot reload (2026-07-11)

- `DLCBundleLoader` now owns an immutable materialized runtime snapshot, a monotonically increasing generation, stable package hashes, managed Agent/official sets, archived Agent memory, a reload lock, and a bounded secret-free operational event log.
- `preview_reload()` rediscovers and fully materializes the candidate DLC set without mutating the active runtime. It reports package IDs/hashes, Agent add/remove/change counts, world change counts, changed runtime task bindings, model profile names, warnings, and validation errors; it never returns Prompt/persona bodies, Provider URLs, credential environment names, authorization values, or local paths.
- `reload_active()` validates and builds every candidate Agent and world-global model binding before activation. Unknown model profiles or unsafe world migrations reject the candidate and preserve the active generation.
- Agent publication uses one map replacement through `Orchestrator.activate_agent_snapshot()`. Pending wakeups resolve the new map on their next tick, while an already-started turn keeps its direct reference to the retired Agent until completion.
- Memory migration concurrently waits for old Agents' turn locks before taking final snapshots, so reload latency follows the slowest safe point rather than the sum of all busy Agents. Removed Agent memory is archived and can be restored if that Agent is later reintroduced. Background Agent tasks now remove themselves from the tracked task set when complete.
- `WorldState.apply_dlc_schema_update()` applies non-destructive schema additions. Existing entity status/version/timestamps, inventory quantities, dynamic entity values, and ongoing-event progress are retained; initial events are never replayed during hot reload.
- Resource `kind` changes and asset `anchor_rate` changes are rejected by default because they require an explicit value migration. New resources, entities, inventory rows, relations, and ongoing events may be added online.
- World schema changes and managed Court officials are written in one SQLite `BEGIN IMMEDIATE` transaction. Any official or world write failure rolls back both domains; `CourtGraph.refresh_cache()` runs only after commit.
- Primary-DLC runtime bindings for `router.ambiguous`, `action.simulation`, `memory.compress`, and `tianji.chat` are hot-updatable. Tianji retains its session/history and replaces only the system message and model client.
- Operations are exposed through `GET /api/debug/dlc-runtime`, `POST /api/debug/dlc-runtime/preview`, and `POST /api/debug/dlc-runtime/reload`. Mutating endpoints reuse the opt-in constant-time admin-token boundary and return generic validation errors.
- Regression coverage includes Agent add/remove/change, in-flight retirement, memory migration/archive/restore, dynamic world preservation, initial-event suppression, anchor migration rejection, model-profile rollback, world/Court transactional rollback, global Prompt/model rebinding, Tianji history retention, and API redaction.
- Secret-free production metrics now expose reload attempts/successes/failures, current reload state, current/last/max waiting in-flight Agents, total/last/max reload duration, memory-migration duration, unsafe/anchor/kind migration rejection counters, and cumulative world rows added by category. Success and failure events retain only aggregate counts, stable failure codes, timing, generation, and package IDs.


### DLC Runtime generation replay and non-destructive rollback (2026-07-11)

- The loader retains a bounded in-memory history of the latest 20 activated configuration blueprints. History Agents are fresh, memory-empty instances, so episodic/structured memory is not duplicated per generation. Public status exposes only generation numbers, package IDs, stable package hashes, and the active flag.
- Rollback clones fresh Agent instances from a retained blueprint; it never republishes a retired Agent object. Matching current Agent memory is migrated at safe points, and Agents reintroduced by rollback may restore archived memory.
- Rollback is monotonic: replaying generation 1 while generation 2 is active creates generation 3. Generation numbers never move backward, so traces and operational events remain totally ordered.
- Rollback restores Agent/official membership, Prompt registries, global Router/Action/Memory/Tianji bindings, and static schema definitions represented by the retained blueprint.
- Dynamic world state is not rewound. Inventory quantities, entity status/version/timestamps, dynamic values, event progress, and entities/resources introduced by later generations remain present. Preview explicitly returns `dynamic_world_state_preserved` and `world_additions_are_not_deleted` warnings.
- `POST /api/debug/dlc-runtime/rollback/preview` and `POST /api/debug/dlc-runtime/rollback` accept only `source_generation` behind the existing admin boundary. Missing generations return generic external errors and safe `generation_not_available` operational events.
- Rollback attempts/successes/failures are included in aggregate runtime metrics. History is persisted as encrypted and signed configuration blueprints; cross-restart rollback reconstructs fresh Agents and current gateway clients without serializing dynamic memory or credentials.

## Phase 8: atomic runtime task bindings (2026-07-12)

- `multiagent_framework/runtime_bindings.py` defines immutable `RuntimeTaskBinding` and `RuntimeBindingsSnapshot` objects plus a `RuntimeBindingsHub`. The hub activates a complete generation with one active-object reference assignment.
- Router, ActionAgent, LLMMemoryCompressor, and Tianji capture exactly one binding at the start of each logical model-backed call. The captured Prompt, LLM client, model profile, and runtime generation remain paired for the complete call.
- Calls already in flight finish on the old generation. Calls started after activation capture the new generation; they cannot observe an old Prompt with a new client or a new Prompt with an old client. Tianji captures once for the whole multi-round chat turn rather than once per internal tool round.
- `Orchestrator.prepare_runtime_bindings()` resolves a complete candidate without mutating the active runtime. `activate_runtime_bindings()` publishes it atomically and only then mirrors legacy component fields for debugging compatibility; bound production calls do not read those mirrors.
- DLC initial load, reload, and monotonic rollback seal the fully resolved four-task binding set into the generation blueprint. A rollback therefore restores the exact source-generation Prompt/client/profile combination while publishing it under a new generation number.
- `/api/debug/dlc-runtime` exposes only binding generation and model-profile identifiers. Prompt bodies and client/provider configuration remain absent.
- `tests/unit/test_runtime_bindings.py` covers immutable/redacted metadata, in-flight old/new isolation for all four task types, common generation capture, DLC reload, and DLC rollback.
- Current Python verification baseline: `python -m pytest -q` -> **198 passed**.

## Phase 8: encrypted generation archive and external operations metrics (2026-07-12)

### Cross-restart generation archive

- `multiagent_framework/generation_archive.py` persists configuration-only DLC generation blueprints with AES-256-GCM encryption, HMAC-SHA256 integrity, and HKDF-separated keys.
- Archive writes use a temporary file, `fsync`, and `os.replace`; retention is bounded to 20 generations by default and deletion verifies the resolved target remains below the archive root.
- Startup restores valid historical generations without preventing the current DLC from loading when one archive is corrupt, tampered, encrypted with another key, or uses an unknown schema.
- Rollback reconstructs fresh Agent instances and resolves current `ModelGateway` clients. Dynamic memory, structured-memory records, summaries, client objects, API keys, Authorization values, Provider URLs, secret environment names/values, and local configuration paths are excluded.
- Production deployments should set `WORLD_SIM_DLC_ARCHIVE_KEY` to an externally managed 32-byte URL-safe Base64 key. The public runtime reports only enabled/schema/count/issue metadata and never reports the key or archive path.

### Unified operational metrics

- `multiagent_framework/operational_metrics.py` combines simulator gauges, Agent and memory gauges, monotonic Router/Agent-turn lifecycle counters, `ModelGateway` aggregates, DLC reload/archive counters, and SQLite-backed `WorldCommandBus` receipt aggregates.
- `WorldCommandBus.metrics_snapshot()` reports only status/type/error-code counts plus process-lifetime call, batch, replay, idempotency-conflict, and crash counters. Command payloads, result bodies, command IDs, chain IDs, and error messages are never exported.
- `Orchestrator.operational_metrics_snapshot()` is independent of bounded debug deques and records Router batches/actions/commands/failures plus Agent turns/completions/errors/rounds/tool calls/input tokens/output tokens.
- Alert thresholds are finite, nonnegative values loaded from environment configuration. Ratio thresholds are clamped to `[0, 1]`; invalid values fall back to documented defaults. Output contains numeric thresholds but not their environment variable names.
- Active alerts currently cover LLM error/fallback rates, DLC reload failure rate and maximum duration, world-command rejection rate, Agent-turn error rate, archive issue count, and busy-Agent ratio.
- `GET /api/metrics` returns schema-versioned JSON and `GET /api/metrics/prometheus` returns Prometheus text exposition. Both are disabled by default and require the independent `X-Metrics-Token` boundary configured by `WORLD_SIM_ENABLE_METRICS_API` and `WORLD_SIM_METRICS_TOKEN`.
- Prometheus labels are limited to bounded task/profile/provider/model, world-command status/type, and alert identifiers. Agent IDs, Prompt/persona text, command/chain IDs, Provider URLs, filesystem paths, secrets, and error text are forbidden from labels and payloads.

### Verification baseline

- Full Python suite: `python -m pytest -q -p no:cacheprovider` -> **198 passed**.
- Focused metrics coverage verifies persisted aggregates, replay semantics, lifecycle counters, finite/clamped thresholds, active alerts, Prometheus escaping, disabled/forbidden/authorized/503 API behavior, independent token authentication, and secret omission.

## Phase 8: environment-specific scale calibration (2026-07-12)

- `multiagent_framework/scale_evaluation.py` now reports `invariants_passed` and `performance_passed` independently for every phase. `passed` remains their conjunction, preserving existing consumers while allowing a new environment baseline to replace an obsolete performance threshold without weakening simulator invariants.
- `multiagent_framework/scale_calibration.py` builds a deterministic, secret-free baseline from at least three compatible reports. It validates the normalized `ScaleConfig`, suite, workload fingerprint, and explicit phase order before aggregating elapsed time and peak memory.
- Calibration statistics include min/median/P90/P95/max, MAD, an upper outlier envelope, accepted/outlier counts, and headroom-adjusted recommended limits. A single high outlier cannot silently expand the enforcement threshold.
- `compare` always requires source invariants to pass, then independently checks elapsed and peak-memory limits. Legacy reports without split status fields fall back conservatively to the original `passed` field.
- Suite peak memory is the maximum measured phase peak; per-phase peak resets can no longer make the suite report accidentally inherit only the final phase peak.
- Five local Windows 100-cycle samples produced `runtime/evals/daming-scale-baseline-local.json`. No outliers were removed. Recommended local limits are DLC 258.253 ms, Router 147.314 ms, World 387.191 ms, Memory 240.637 ms, and Soak 4,297.548 ms.
- `runtime/evals/daming-scale-comparison-local.json` passes against that baseline with all invariant, elapsed, and memory checks true.
- Baseline and comparison artifacts contain aggregate workload/statistical data only. Input paths, prompts, messages, provider data, credentials, archive paths, IDs, and raw errors are not serialized.
- `tests/unit/test_scale_calibration.py` covers robust statistics, outliers, compatibility, split/legacy status semantics, finite values, environment IDs, regression classification, redaction, deterministic IDs, phase ordering, CLI outputs, and exit codes.
- Current full Python verification baseline: `python -m pytest -q -p no:cacheprovider` -> **244 passed**.

## Phase 8: deployment alert calibration (2026-07-12)

- `multiagent_framework/operational_calibration.py` calibrates the eight existing operational alert signals from at least three independent schema-v1 metrics snapshots and compares a candidate snapshot with a reviewed baseline.
- Ratio signals require real exposure denominators: LLM calls, DLC attempts, persisted world commands, Agent turns, or configured Agents. Zero-exposure snapshots cannot dilute an error rate or fabricate a healthy sample.
- Duplicate `collected_at` values are rejected so copying one snapshot cannot inflate sample count. Environment IDs are path/control-character safe.
- Statistics include min/median/P90/P95/max, MAD, outlier bounds, accepted counts, and kind-specific headroom/margins. High outliers may be isolated statistically, but every raw policy-ceiling exceedance is counted and makes the baseline `ready=false`.
- Recommended thresholds are capped by the built-in safety policy. Archive issues remain fixed at zero and can never be normalized through calibration.
- Comparison refuses an unreviewed baseline, validates its safe environment/Baseline IDs, ignores stale source thresholds/alerts, and outputs only bounded per-signal value/threshold/pass aggregates.
- CLI exit codes are `0` for ready/pass, `1` for review-required calibration or detected regression, and `2` for malformed/incompatible input.
- `runtime/evals/daming-operational-baseline-local-fixture.json` and `runtime/evals/daming-operational-comparison-local-fixture.json` verify the complete toolchain against five deterministic synthetic snapshots. This fixture is not deployment evidence and must not be used as a production threshold source.
- The fixture candidate passes all eight signals. Its recommendations are LLM error 0.024, LLM fallback 0.0357, DLC reload failure 0.02, DLC max 148.75 ms, world-command rejection 0.0507, Agent-turn error 0.02, archive issues 0, and busy-Agent ratio 0.357.
- `tests/unit/test_operational_calibration.py` covers safety caps, exposure requirements, duplicate samples, policy incidents, archive invariants, malformed values, regression classification, zero-exposure comparison, tampered baselines, redaction, atomic outputs, and CLI exit codes.
- Current full Python verification baseline: `python -m pytest -q -p no:cacheprovider` -> **244 passed**.

## Phase 8: signed baseline approval registry (2026-07-12)

- Added a transactional SQLite registry for `scale` and `operational-alerts` baselines. Each approved version preserves canonical aggregate payloads, a deterministic Baseline ID, approval metadata, and an HMAC-SHA256 signature that includes the database version number.
- The signing key can be injected by the deployment environment or generated once in a restricted sidecar key file. A derived domain-separated signing key is used for baseline rows, active pointers, and audit events; public status exposes only a short key fingerprint.
- Approval is idempotent per baseline type, environment, and Baseline ID. `BEGIN IMMEDIATE` serializes concurrent approvals and activations without replacing unrelated environment pointers.
- Activation pointers are signed and verified against the referenced baseline type and environment. Audit records are signed and semantically checked against linked versions, including action, target, and previous-version identity.
- Rollback supports an explicit approved version or, by default, reverses the most recent activation transition for that environment. Consecutive default rollbacks can therefore toggle between the last two active versions.
- Registry input rejects unreviewed operational baselines, invalid IDs, noncanonical or mismatched payloads, Provider URLs, absolute paths, Prompt/message/error bodies, credentials, and credential environment references.
- The CLI provides `approve`, `activate`, `rollback`, `status`, `gate-scale`, and `gate-operational`. Exit codes are `0` for success/pass, `1` for a valid regression, and `2` for invalid or untrusted input. Gate errors are normalized and do not expose candidate details.
- Local workflow verification approved three versions, maintained two independent active pointers, rolled scale back to its previous version, and passed both scale and operational gates. The operational fixture validates tooling only and is not production evidence.
- Verification: baseline-registry tests **18 passed**; full Python suite **262 passed**; Prompt contracts **100%**; trajectory replay **100%**; scale and operational comparisons passed; frontend tests **6 passed**, lint passed, and production build passed.

## DLC deterministic random events (2026-07-12)

- `multiagent_framework/event_generator.py` schedules DLC-defined world events using SHA-256-derived deterministic interval, probability, and severity channels. One game day is 86,400 simulation seconds.
- Templates live in the normalized `random_events` content array and include `template_id`, `event_type`, `target_agents`, `initial_payload`, severity range, interval-day range, probability, occurrence cap, and enabled state.
- The scheduler supports bounded catch-up (`max_attempts`), configuration fingerprints, state-preserving hot reload, changed-template reset at current simulation time, and restart-safe snapshot/restore.
- DLC Runtime archives and restores template blueprints across generation rollback. Orchestrator polls before wakeup dispatch and exposes only event id/type/severity/target count/generated time in `TickResult`; payloads and Agent identifiers are not included in public generated-event metadata.
- Runtime `state.json` persists scheduler fingerprints and counters. New templates discovered for an old save are scheduled from the restored current simulation time instead of replaying historical events.
- DLC Studio now edits random-event templates with Agent selection, JSON payload editing, schedule validation, and reserved runtime-field protection. The built-in Daming DLC includes low-frequency locust and grain-transport templates.
- Verification baseline after integration: **273 Python tests passed**; focused random-event/DLC/runtime/persistence suite **58 passed**; frontend **6 tests passed**, lint and production build passed.
## Deterministic DLC action-time simulation (2026-07-12)

The world engine now owns authoritative action and ongoing-event durations. An LLM-proposed `duration_days` value is treated only as an untrusted workload hint and cannot directly control a timer.

### Rule model and deterministic selection

- `multiagent_framework/time_simulator.py` defines `ActionTimeRule`, `TimeEstimate`, and `TimeSimulator`.
- DLC content exposes a normalized `time_rules` array. Each rule defines `rule_id`, `action_types`, `base_days`, `min_days`, `max_days`, workload/severity/distance/resource/failure factors, priority, and enabled state.
- Selection is deterministic: an exact action-type match wins over `*`; ties use descending priority and then lexical Rule ID. When no enabled rule matches, `builtin.safe_fallback` is used.
- Configuration publication is atomic. All candidate rules are parsed and validated before the active tuple and SHA-256 fingerprint change.

The authoritative calculation is:

```text
raw_days =
    base_days
  + workload_factor * safe_workload_hint
  + severity_factor * normalized_severity
  + distance_factor * travel_days
  - resource_factor * log1p(anchored_inventory_value)

if action failed:
    raw_days *= success_factor

duration_days = clamp(min_days, max_days, max(0, raw_days))
```

Booleans, strings, negative numbers, NaN, and infinity are never accepted as trusted numeric inputs. Unsafe runtime hints are replaced with deterministic fallbacks; DLC definitions reject them during validation.

### Runtime integration

- `ActionAgent` derives resource capacity from the executor's inventory `base_value` and travel time from entity coordinates using the common travel-speed constant.
- `Orchestrator` schedules the action receipt timer from the authoritative estimate and includes only safe rule/source/component metadata in action logs.
- Function-calling handlers for adding and modifying ongoing events use the same estimator. `request_action` may provide severity in the bounded range 1..10.
- Manual ongoing-event APIs default to `duration_mode=estimate`. Trusted operators may explicitly choose `duration_mode=override`; both modes reject negative and non-finite durations.
- Runtime snapshots persist only the time-rule fingerprint and counts, not rule bodies, action descriptions, entity identifiers, resources, prompts, credentials, paths, or raw errors.

### DLC lifecycle and Studio

Registry validation, Runtime materialization, encrypted generation archives, hot reload, restart restoration, and monotonic rollback all carry `time_rules`. DLC Studio supports creating, validating, importing, exporting, diffing, and restoring these rules. The built-in Daming and Frontier DLCs contain example rules.

Random-event catch-up is globally ordered by `(next_due, template_id)`, so a bounded poll budget services the oldest due template first rather than exhausting one template before considering the others.

Verification includes deterministic formula components, invalid-hint replacement, rule priority, atomic configuration, public-metadata redaction, ActionAgent/ongoing-event integration, API estimate/override behavior, and cross-restart generation rollback of time rules.

Final verification after this integration: **286 Python tests passed**; Python compile passed; Prompt contracts and deterministic trajectory replay both scored **100%**; local scale and operational fixture comparisons passed; frontend **6 tests**, lint, and production build passed; `git diff --check`, UTF-8 replacement-marker scan, and CRLF checks passed.

## Phase checkpoint: Mock LLM end-to-end world scenario (2026-07-12)

Status: **completed and verified**.

Delivered:

- Added `tests/integration/test_mock_world_scenario.py`, which drives the real Orchestrator rather than a reduced handler-only fixture.
- The scenario covers deterministic DLC random-event scheduling, Agent wakeup, a two-round OfficialAgent query/request/finish turn, a four-round ActionAgent query/spend/schedule/complete turn, anchored inventory valuation, authoritative action and ongoing-event duration rules, receipt timers, SQLite restart, atomic matured-event application, action-result wakeup, commitment resolution, traces, chain logs, and operational counters.
- The test proves that `request_action` bypasses Router batching while still producing dispatch feedback and a later authoritative receipt.
- Public generated-event metadata is checked to exclude template payloads and Agent identifiers.
- Restart coverage reopens both WorldState and TimerSystem from SQLite before delayed effects fire, then verifies that the ongoing event and receipt complete without losing state.
- The scenario exposed a production defect in `Orchestrator._tick`: the matured-event loop reused the outer `result` name and replaced `TickResult` with `WorldCommandResult`. The loop now uses `matured_result`, preserving the tick response after one or more events mature.

Acceptance evidence:

- Focused orchestration and adjacent regression suite: **51 passed**.
- Full Python compile passed and the complete suite is now **287 passed**.
- Frontend: **6 tests passed**, lint passed, and production build passed.
- Prompt contracts and deterministic trajectory replay both scored **100%**.
- Local scale comparison and deterministic operational-fixture comparison passed; the operational fixture remains toolchain evidence only.
- Diff, UTF-8/replacement-marker, and Markdown BOM/CRLF checks are part of the final repository gate.

Next: extend scenario coverage to a DLC generation reload while delayed world work is still in flight and to credentialed model-profile trajectory matrices.

## Phase checkpoint: generation-scoped in-flight world commitments (2026-07-12)

Status: **completed and verified**.

Delivered:

- DLC runtime generations already published Prompt/client pairs atomically, but ActionAgent previously kept a mutable `TimeSimulator` reference. A reload occurring during a multi-round model call could therefore finish old Prompt/model work under the new generation's duration policy.
- `ActionAgent.configure_time_rules()` now validates and publishes a new `TimeSimulator` object instead of mutating the active engine. Every action captures the engine once alongside its immutable runtime binding.
- The captured engine is used both by function tools that create delayed ongoing events and by the final authoritative action estimate. Work accepted by generation N therefore retains generation N's duration rule even if generation N+1 activates while the model call is awaiting a response.
- `ActionResult` and the safe action log now expose `runtime_generation` and `time_rule_fingerprint`, providing configuration provenance without Prompt text, model details, payloads, credentials, paths, IDs, or raw errors.
- Added `tests/integration/test_dlc_generation_inflight.py`. It starts generation-1 work, blocks the model call, atomically reloads generation 2 with a different ActionAgent Prompt, model profile, and time rules, then proves the in-flight action and ongoing event remain on generation 1 while the next action uses generation 2.
- The scenario reopens SQLite WorldState and the encrypted generation archive, publishes the current registry as monotonic generation 3, rolls back generation 1 as new generation 4, and proves neither restart nor rollback changes the delayed event's frozen `end_time`/`expected_result` or rewinds live world status.
- After rollback, the persisted event matures using its committed result rather than being recomputed from the newly active rule set.

Acceptance evidence:

- Generation/runtime focused regression suite: **56 passed**.
- Full Python compile passed and the complete suite is now **289 passed**.
- Frontend: **6 tests passed**, lint passed, and production build passed.
- Prompt contracts and deterministic trajectory replay both scored **100%**.
- The current 100-cycle local scale candidate and calibrated local comparison passed all invariant, elapsed-time, and peak-memory checks.
- The deterministic operational fixture remains toolchain evidence only; credentialed live-model and independent CI/staging/production evidence are still pending.

Next: implement credentialed live-model trajectory matrices across task, DLC, Agent, provider, and model profile, then add reviewed human scoring and deployment-specific gates.
## Phase checkpoint: credential-gated live trajectory model matrix (2026-07-12)

Status: **implementation completed and locally verified; credentialed production evidence remains pending**.

### Strict model-profile isolation

- `ModelGateway` and `TaskLLMClient` now support `strict_profile=True`. An explicit profile selected for evaluation is the only candidate; provider failure, capability failure, or credential absence cannot silently fall back to another task-policy profile.
- Normal production routing retains the existing ordered fallback behavior unless strict mode is explicitly requested.
- Secret-free profile metadata exposes only profile, provider name, model identifier, capabilities, and tool support. Provider URLs, credential environment names/values, configuration paths, prompts, messages, responses, Agent IDs, Trace IDs, command IDs, and raw errors remain excluded.

### Matrix runner and review workflow

- `multiagent_framework/trajectory_matrix.py` expands every configured `task x profile x repetition` cell and runs the existing multi-round `TrajectoryEvaluator` in live strict-profile mode.
- Cells aggregate run/pass counts, automated score mean/minimum, P50/P95 elapsed latency, mean/total tokens, tool legality, termination, routing, memory recall, commitment outcome, anchored resource conservation, review coverage, and human-score mean.
- Human review accepts exactly six bounded dimensions: correctness, tool discipline, memory quality, world consistency, latency value, and cost value. Notes/free text, duplicate reviewer/cell/scenario entries, non-finite values, out-of-range scores, and references outside the matrix are rejected.
- Review templates contain only safe cell/scenario identifiers and empty score dimensions. No model input or output body is copied into the template or report.
- Recommendations are `provisional` until review coverage reaches the configured threshold. Only a fully automated-passing `reviewed` winner is emitted into the optional production task-policy patch; the tool never changes active model configuration automatically.
- CLI exit codes are `0` for an approved reviewed matrix or successful template-only generation, `1` for a valid but regressed/not-yet-approved matrix, and `2` for invalid input, missing credentials, or unsafe configuration.

### Daming live suite

- Added a five-task controlled live suite covering `agent.turn`, `router.ambiguous`, `action.simulation`, `memory.compress`, and `tianji.chat`.
- The trajectory scorer now supports exact required tool sets and required tool sequences, so multi-round query-before-commit/query-before-answer behavior is tested rather than inferred from terminal success.
- The action scenario validates common anchored inventory value conservation. The memory scenario validates commitment retention through compression. Tianji must inspect world state before submitting advice.
- Fake-live tests exercise the complete suite without network access. Real provider calls remain credential-gated and are not part of the default local/CI test command.

Acceptance evidence:

- Matrix/model/trajectory focused suite: **51 passed**.
- Full Python compile passed and the complete suite is now **312 passed**.
- Prompt contracts and deterministic trajectory replay remain **100%**.
- The review-template-only command generated **9** safe review targets without requiring provider credentials.
- Credentialed repeated runs, independent human reviews, and CI/staging/production promotion evidence are still required before any matrix recommendation is production-approved.

## Phase checkpoint: review integrity and cohort-partitioned model selection (2026-07-12)

Status: **implemented and fully locally gated; credentialed and independent production evidence remains pending**.

### Review integrity gates

- Matrix configuration now supports `minimum_reviewers_per_target`; the repository live matrix requires **2 independent reviewers for every cell/scenario target**.
- A target contributes to review coverage only after reviewer quorum is met. Duplicate reviewer/cell/scenario submissions are rejected, so copying one identity into multiple slots cannot satisfy quorum.
- `maximum_review_disagreement` bounds reviewer disagreement using the population standard deviation of each target's six-dimension composite score. The repository threshold is **15**; exceeding it keeps the cell `provisional` even when coverage is complete.
- Task approval now requires every automated-passing candidate in the ranking to be `reviewed`. Reviewing only the apparent winner can no longer hide an unevaluated alternative or produce a production policy recommendation.
- Cell reports expose only safe aggregate reviewer count, coverage, human-score mean, and maximum disagreement. Reviewer notes and free text remain unsupported.

### Cohort-partitioned evaluation

- Suites and matrices may declare safe cohort labels. Explicit cohorts evaluate only matching scenarios; a cohort/task pair with no matching scenario is rejected instead of silently falling back to the global suite.
- Default `all` cells preserve the existing `task::profile` identity. Explicit cells use `cohort::task::profile`, and `all` cannot be mixed with explicit cohorts in one matrix.
- Reviewed global recommendations are emitted under `task_policies`; reviewed cohort recommendations are isolated under `cohort_task_policies`. No recommendation mutates active configuration automatically.
- Cohorts are evaluation partitions rather than raw Agent identities. Reports continue to exclude Agent IDs, prompts, messages, responses, credentials, Provider URLs, paths, Trace/command/tool-call IDs, and raw errors.

### Current acceptance evidence

- Matrix/model/trajectory focused suite: **69 passed**; matrix-specific suite: **40 passed**.
- Full Python compile passed and the complete backend suite is now **330 passed**.
- Frontend: **6 tests passed**, lint passed, and production build passed.
- Prompt contracts and deterministic trajectory replay both remain **100%**.
- Template-only generation produced **9 unique cell/scenario targets and 18 reviewer slots**, exactly two slots per target, without credentials or sensitive fields.
- `git diff --check` passed. Real credentialed repeated runs, independent reviewer submissions, and CI/staging/production promotion evidence are still required before production approval.

## Phase checkpoint: environment-aware model-policy promotion gate (2026-07-12)

Status: **implemented and locally verified; real staging evidence remains pending**.

- `multiagent_framework/model_promotion_gate.py` joins a reviewed live trajectory matrix, its exact generated policy patch, and optional environment-specific scale/operational gate results into one deterministic deployment decision.
- Promotion requires the configured matrix/suite identity, live mode, minimum repetitions, full automated pass, matrix approval, reviewer quorum, complete review coverage, bounded reviewer disagreement, and the complete required task/cohort target set.
- Every cell must pass all repetitions and be reviewed. Every ranking must match the exact profiles represented by its cells. The supplied policy patch must exactly equal the reviewed rankings, preventing a reviewed artifact from being paired with a substituted model policy.
- The repository staging gate requires all five `daming-core` tasks, three repetitions, two reviewers per target, 100% review coverage, maximum disagreement 15, and both scale and operational gate evidence from the same `staging` environment.
- A valid but incomplete/regressed evidence set produces a safe `blocked` decision with bounded reason codes. Malformed, inconsistent, cross-environment, policy-substituted, or sensitive artifacts are rejected as input errors.
- Output contains only safe identifiers, counts, policy identifiers, baseline IDs, canonical SHA-256 fingerprints, prerequisite status, and reason codes. It excludes transcripts, Prompt/memory bodies, credentials, Provider URLs, paths, Agent/Trace/command/tool-call IDs, and raw errors.
- CLI exit codes are `0` for approved, `1` for a valid blocked promotion, and `2` for malformed or unsafe evidence. The gate evaluates artifacts only and never edits active model configuration.

Current acceptance evidence:

- Model-promotion gate suite: **16 passed**.
- Matrix/model/trajectory/promotion focused suite: **84 passed**.
- Full Python compile passed and the complete backend suite is now **346 passed**.
- Frontend: **6 tests passed**, lint passed, and production build passed.
- Prompt contracts and deterministic trajectory replay remain **100%**; Diff check passed.
- Actual promotion remains blocked until credentialed live matrix reviews plus same-environment signed staging scale and operational gate artifacts are supplied.

## Phase checkpoint: generation-safe model-policy promotion apply (2026-07-12)

Status: **implemented and locally verified; credentialed staging approval remains pending**.

### Central scoped policy layers

- `GatewayConfig` now persists `dlc_task_policies` and `agent_task_policies` in addition to global `task_policies`.
- Central resolution order is explicit profile override, Agent scoped policy, DLC scoped policy, global task policy, default policy, then available models.
- Runtime bindings remain stronger than central policy: Agent source `model_profile`, manifest Agent binding, and manifest task binding are resolved before the central Agent/DLC/global layers.
- `TaskLLMClient` carries both `dlc_id` and `agent_id` through candidate selection, credential availability, chat, streaming, token counting, and compatibility adapters.
- DLC materialized generations archive the package ID for every Agent so rollback preserves scoped model-routing semantics.

### Approved promotion preview and apply

- `ModelPromotionApplier` accepts only an approved Promotion Gate artifact whose matrix and policy SHA-256 fingerprints match a strict cohort-to-target mapping.
- Every promoted profile must exist, every mapped DLC/Agent must be present in the active DLC runtime, and mappings must be complete, non-overlapping, and bound to the current Gateway generation.
- Preview emits only safe identifiers, counts, generation numbers, and canonical fingerprints. It does not expose Provider URLs, credential metadata, Prompt/message/memory bodies, paths, trace identifiers, or raw errors.
- Apply recomputes the plan, verifies the caller-supplied plan fingerprint, rereads the source configuration, rejects external edits since preview, writes through a flushed/fsynced temporary file, atomically replaces the source, and activates the exact candidate with optimistic generation checking.
- If Gateway activation fails, the original configuration file is restored atomically. Existing calls retain their captured clients and old generation; new calls resolve through the newly activated scoped policy.
- Authenticated endpoints are `POST /api/debug/model-promotion/preview` and `POST /api/debug/model-promotion/apply`. They use the same opt-in admin boundary as model reload.

### Version identity

- Release identity is now `0.10.0` and is synchronized across `VERSION`, `multiagent_framework.version.__version__`, FastAPI OpenAPI metadata, and the frontend package manifest.
- `tests/unit/test_version_consistency.py` prevents backend/frontend version drift.

Acceptance evidence:

- Complete Python suite: **360 passed**.
- Python compile completed successfully.
- Frontend: **6 tests passed**, lint passed, and production build passed.
- Prompt contracts and deterministic trajectory replay both scored **100%**.
- External-file-change and in-flight-generation regression tests passed; `git diff --check` passed.

Production activation is still pending credentialed live-model runs, independent review quorum, and same-environment staging scale/operational evidence.


## Phase checkpoint: DLC Studio model-promotion workbench (2026-07-12)

Status: **implemented and locally verified; credentialed staging evidence remains pending**.

- `frontend/src/ModelPromotionWorkbench.jsx` adds an operator-facing Promotion Gate import, cohort mapping generator, authenticated Preview, safe plan summary, and generation-bound Apply flow to DLC Studio.
- Quick mapping targets the currently selected DLC. Agent targets are opt-in checkboxes rather than implicit all-Agent expansion, reducing accidental policy scope.
- The Admin Token exists only in component memory and is sent only in `X-Admin-Token`; it is not persisted, exported, copied into artifacts, or rendered after entry.
- `parsePromotionArtifact`, `parsePromotionMapping`, and `createPromotionMapping` enforce schema v1, approved decisions, lowercase SHA-256 policy fingerprints, safe IDs, positive Gateway generations, and object-shaped cohort policies before requests are sent.
- A deterministic input fingerprint binds Preview state to the exact Promotion text, Mapping text, selected DLC, and public Gateway generation. Any change disables Apply until a new Preview succeeds.
- Apply submits the Preview `plan_fingerprint`, clears the old preview, and reloads `GET /api/debug/model-config` so the UI observes the activated generation.
- The UI renders only safe generation numbers, change/target counts, and shortened fingerprints. Backend validation failures remain generic and do not expose credentials, provider metadata, paths, Prompt/message/memory text, IDs from traces or commands, or raw exceptions.

### Version identity and acceptance evidence

- Release identity is `0.11.0`, synchronized across `VERSION`, backend package version, frontend package and lockfile, FastAPI metadata, and README.
- Complete Python suite: **360 passed**; focused Promotion apply/API suite: **11 passed**.
- Frontend: **9 tests passed**, lint passed, and production build passed.
- Python compile passed. Prompt contracts and deterministic trajectory replay both remain **100%**.
- Production activation still requires protected live-model runs, independent reviewer quorum, same-environment staging scale/operational evidence, controlled rollout, and observation of the new generation.

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


## Phase checkpoint: deterministic causal event chains (2026-07-12)

Status: **implemented and locally verified**.

### Runtime architecture

- `multiagent_framework/causal_events.py` defines validated `CausalEventRule` and `CausalCondition` schemas plus a deterministic, restart-safe scheduler. A source event is observed once, matching rules are evaluated in stable priority/ID order, and complete child `EventObject` values are materialized immediately.
- Stable SHA-256 sampling makes probability decisions reproducible for the same rule, source event, and causal chain. Child IDs are deterministic, duplicate source injection is idempotent, severity is clamped to `1..10`, and target Agents may be inherited or explicitly overridden.
- Conditions support `anchored_inventory`, `metric`, and `parameter`. Anchored inventory compares `quantity * anchor_rate` in base-value units; metrics and parameters use their typed `entity_values` stores. Missing facts fail closed.
- Materialized children retain their delay, targets, payload, severity, and causal metadata across DLC hot reload. Rule fire counts are reserved when scheduling, not when polling, preventing restart/reload re-entry from over-scheduling.
- Chain depth, per-rule fire limits, a 128-event per-Tick causal budget, a 10,000 pending-event cap, bounded observed IDs, and bounded completed-chain retention prevent unbounded event storms. Aggregate dropped counts are observable without exposing event payloads or identifiers.
- Scheduler snapshots persist observed IDs, per-chain rule counts, pending materialized events, ordering, and dropped aggregates. Restore validates shapes, sizes, finite times, severity, target lists, payload maps, IDs, and count bounds before replacing live state. Legacy state without `causal_events` restores as an empty scheduler.

### DLC and Studio integration

- Bundle Content adds `causal_event_rules`. Registry validation checks rule schema, duplicate IDs, Agent references, entity/resource references, and resource-kind compatibility. Active DLC materialization also rejects cross-package rule-ID collisions.
- Runtime snapshots, generation blueprints, preview/reload, rollback, public aggregate status, startup restoration, and `RuntimePersistor` all include causal scheduler state or causal rule configuration as appropriate. Rules are configured before initial events are injected.
- DLC Studio adds a dedicated causal-chain editor and includes rules in import/export, stable keyed Diff, history, counts, and validation-oriented forms. The editor supports source types, child type, priority, enablement, delay, probability, severity delta, depth/fire limits, Agent targets, inherited payload keys, conditions JSON, and initial payload JSON.
- Generated-event API metadata identifies causal origin but excludes target Agent lists and event payloads. Public scheduler status contains only counts, next due time, dropped count, and a configuration fingerprint.

### Version identity

- Release identity is `0.13.0`, synchronized across canonical, backend, frontend, lockfile, FastAPI, and README metadata.
- Acceptance evidence: **412 Python tests passed**; **12 frontend tests passed**; frontend lint/build and Python compile passed; Prompt contracts and deterministic trajectory replay both scored **100%**; `git diff --check` and maintained-document encoding checks passed.

## Phase checkpoint: causal rule authoring workbench and deterministic draft preview (2026-07-12)

Status: **implemented and locally verified**.

### Isolated analysis architecture

- `multiagent_framework/causal_analysis.py` projects DLC seed inventory and typed entity values into a read-only fact provider. It never mutates the live Orchestrator, scheduler, SQLite database, or package files.
- Static analysis emits a stable graph fingerprint, event-type nodes, rule edges, authored origins, fanout counts, duplicate-semantic warnings, bounded-cycle and zero-delay-cycle warnings, immediate wildcard-loop warnings, external-source information, and draft reference errors for missing Agents, entities, resources, or incompatible resource kinds.
- Deterministic dry-run uses an isolated `CausalEventEngine`, stable source IDs, the existing probability semantics, the authored value anchors, a caller-bounded horizon, and a hard 256-event request ceiling. The result includes a timeline, parent/rule edges, pending events, truncation state, and the static analysis report.
- `POST /api/dlcs/{dlc_id}/causal-events/dry-run` accepts either persisted content or an unsaved normalized Studio draft. Draft analysis does not save or publish content. Spoofed causal metadata, malformed sources, oversized limits, and invalid rule documents fail with controlled 400 responses.

### Studio authoring workflow

- The causal editor replaces raw Conditions JSON with repeatable structured rows for fact kind, entity, compatible resource/value, comparison operator, and finite threshold. Resource options are filtered to `asset`, `metric`, or `parameter` according to the selected condition kind.
- Anchored inventory rows display the selected resource anchor rate and explicitly preview `quantity * anchor_rate` semantics. Existing Agent target selectors remain available.
- The deterministic preview builds a request from the current unsaved rule form and complete in-memory DLC draft, then renders rule/error/warning counts, pending counts, diagnostics, and a horizontally ordered event timeline.
- Pure frontend helpers build the event-type catalog and immutable dry-run draft, preventing preview from mutating Studio history before the author confirms Save.

### Version identity and acceptance evidence

- Release identity is `0.14.0`, synchronized across canonical, backend, frontend, lockfile, FastAPI, and README metadata.
- Acceptance evidence: **419 Python tests passed**; **13 frontend tests passed**; frontend lint/build and Python compile passed. Prompt contracts and deterministic trajectory replay remain part of the required final gate.


## Phase checkpoint: transactional causal world effects (2026-07-12)

Status: **implemented and locally verified**.

### Runtime command semantics

- `CausalEventRule.world_effects` supports `adjust_resource`, `set_resource`, `update_status`, `create_ongoing_event`, and `transfer_anchored_value`. Rules are normalized with bounded effect and command counts.
- Matching rules materialize complete internal `WorldCommand` plans when the child event is scheduled. Quantity, anchor rate, ongoing-event ID, start/end time, and expected result are therefore frozen across DLC reload, generation rollback, restart, and delayed polling.
- `multiagent_framework/causal_world_effects.py` converts each due child plan into one atomic command batch. Stable IDs use `causal:{event_id}:{index}`, so replay returns stored receipts instead of applying balances or events twice.
- The Orchestrator commits the batch before waking target Agents or observing the child for downstream causal rules. A rejected or crashed batch produces a bounded safe generated-event summary and stops that branch; no partial world mutation or downstream propagation occurs.
- Aggregate operational metrics count effect batches, commands, rejections, and crashes without exposing internal command payloads, entity/resource identifiers, event payloads, or credentials.

### Unified anchored-value transfer

- `transfer_anchored_value` freezes debit and credit quantities from the scheduled base value: `debit = base_value / source_anchor_rate`, `credit = base_value / destination_anchor_rate`.
- Both adjustments execute in the same SQLite transaction. Insufficient source balance, invalid references, or any sibling failure rolls back status changes, inventory writes, ledger rows, ongoing events, and the transfer credit.
- Inventory ledger metadata records only the internal audit fields needed for reconciliation: anchored base value, frozen anchor rate, and debit/credit side. The two ledger base-value deltas sum to zero.

### Ongoing events and isolated authoring preview

- `create_ongoing_event` emits a deterministic `CREATE_EVENT` command with fixed start/end times and normalized expected results. Existing `apply_matured_events()` remains the authoritative atomic maturity reducer.
- DLC Registry validates every effect entity/resource reference, requires asset resources for anchored transfers and ongoing inventory deltas, and blocks invalid packages before publication.
- `causal_analysis.py` applies resource effects atomically to isolated DLC seed facts. Downstream conditions see updated inventory, metrics, and parameters, while live SQLite and the running Orchestrator remain untouched. Rejected preview batches stop downstream traversal.
- Dry Run nodes expose bounded command previews and effect status only. Full internal command bodies remain absent from Agent payloads and public generated-event APIs.

### DLC Studio

- The causal-rule editor now includes a structured transactional world-effect builder with entity/resource selectors, asset-only transfer selectors, anchor-rate context, ongoing-event result JSON, and validation for all five effect types.
- Dry Run timelines display command type, entity, resource/quantity, anchored base value, ongoing-event end day, and rejection code without persisting the draft.

### Version identity and acceptance evidence

- Release identity is `0.15.0`, synchronized across `VERSION`, backend package metadata, frontend package metadata/lockfile, FastAPI, and README.
- Full backend suite: **428 passed**. Frontend: **14 tests passed**, lint passed, and production build passed. Python compile passed.
- Focused coverage includes anchored-value conservation, atomic rollback, idempotent replay, frozen snapshot semantics, deterministic ongoing-event creation, malformed-plan rejection, isolated downstream fact propagation, Registry references, and unsaved Studio drafts.


## Phase checkpoint: anchored-value economic invariants and dashboard (2026-07-13)

Status: **implemented and locally verified**.

### Economic invariant model

- `WorldState.get_economic_invariants()` derives the current asset economy from authoritative SQLite state. For each asset resource it aggregates quantity, catalog anchor rate, base value (`quantity * anchor_rate`), and holder count; the public result never includes entity IDs.
- Gross positive and negative inventory-ledger flows are separated from transfer credits/debits to compute cumulative issuance, destruction, and non-transfer net base value. Atomic rejected causal batches write no ledger rows and therefore cannot distort these totals.
- Every newly materialized `transfer_anchored_value` pair receives a stable SHA-256-derived `transfer_id` shared by debit and credit commands. Reconciliation reports batch/entry counts, debit and credit totals, net difference, incomplete batches, mismatched batches, maximum absolute mismatch, and a conservation flag.
- Scheduler restore remains compatible with `0.15.0` snapshots that lack `transfer_id`. Legacy ledger entries are paired by causal event identity and adjacent effect index; new snapshots validate transfer IDs at 64 characters or fewer.

### API, metrics, and frontend

- `GET /api/world-state/economy` is a static route registered before `/{entity_id}`. It returns economic invariants, simulation time, and aggregate causal-effect batch/command/rejection/failure counts plus rejection rate.
- `/api/metrics/json` now exposes `economy.inventory`, `economy.ledger`, `economy.transfers`, and `economy.causal_effects`. Prometheus exports current value, issuance/destruction totals, transfer reconciliation signals, and causal-effect rejection/failure counters without adding an uncalibrated production alert threshold.
- `WorldStateView.jsx` adds a responsive economic dashboard with manual refresh, automatic refresh after asset changes, conservation status, top-eight resource valuation, and causal-effect health.
- Public economy surfaces intentionally omit entity, command, chain, event, and transfer identifiers as well as credentials, Provider metadata, Prompt/message/memory bodies, filesystem paths, and raw exceptions.

### Restart and acceptance evidence

- `tests/integration/test_causal_economic_scenario.py` verifies a multi-hop harvest → distribution → tax → replenishment chain across SQLite and causal-engine restart, including a cross-anchor transfer, a downstream anchored condition, delayed replenishment, ongoing-event maturity, replay idempotency, issuance/destruction accounting, and zero ledger drift.
- Legacy snapshot tests remove `transfer_id`, restore and execute the pending transfer, then verify conservation through compatibility pairing. Oversized IDs fail closed.
- Release identity is `0.16.0`, synchronized across `VERSION`, backend package metadata, frontend package/lockfile, FastAPI metadata, and README.
- Complete local gate: **434 Python tests passed**, **14 frontend tests passed**, frontend lint/build passed, Python compile passed, Prompt contracts scored **100%**, and deterministic trajectory replay scored **100%**.

The persistent product goal remains active. This checkpoint completes the immediate anchored-economy observability gap while longer production soak tests, credentialed model matrices, reusable causal templates, deeper multi-turn memory evaluation, and task/DLC/Agent model refinement remain ongoing.
