# 世界模拟器

一个基于 `FastAPI + React + SQLite + LLM Function Calling` 的多智能体朝堂模拟器。

当前内置的第一个世界包是 `大明朝廷`：玩家扮演皇帝，基层官员接收灾情后会逐级上报、隐瞒、越级、执行行动，Router 负责决定信息如何在朝廷体系中流转，ActionAgent 负责把行动真正落到世界状态上。

## 项目能做什么

- 用多 Agent 模拟古代官僚体系中的信息流转
- 用 LLM 驱动官员决策、Router 路由和行动推演
- 用定时器系统模拟公文在不同层级和地理距离间的传递延迟
- 记录整条调用链，支持查看信息失真、压下不报、循环链路
- 提供 4 个前端视图
  - 皇帝视角：看奏折、批复、下旨、查看在途公文
  - 上帝视角：看 Agent 时间线、Router 决策、定时器、Prompt 热编辑
  - Agent 编辑器：改官员树、地点、人格种子、Prompt 预览、导入导出
  - 世界状态：实体/库存 CRUD、地点坐标编辑
- 支持存档 / 读档
- 支持运行时修改模型配置与时间倍率

## 当前场景

仓库当前实现的是一个较小但完整可运行的样例朝廷：

- 皇帝
- 司礼监太监 `王振`
- 内阁 `杨士奇`
- 通政司、户部
- 江南布政使 `周忱`
- 苏州知府 `况钟`
- 吴县知县 `张三`
- 昆山知县 `李四`

默认初始事件为 `苏州府蝗灾`，会同时唤醒吴县、昆山两个基层官员。

## 技术栈

- 后端：`Python 3.11+`、`FastAPI`、`uvicorn`
- 前端：`React 19`、`Vite`
- 存储：`SQLite`
- LLM 调用：OpenAI 兼容接口 + Function Calling
- 测试：`pytest`

## 架构概览

核心链路：

1. 初始事件注入到基层官员
2. 官员 Agent 调用 `submit_action` 或 `request_action`
3. `RouterAgent` 解析意图并生成世界指令
4. `TimerSystem` 负责延时送达，模拟古代文书传递
5. 消息逐级上传，最终形成送达皇帝的奏折
6. 玩家批复或下旨
7. 旨意再通过太监和既有层级向下传达
8. 需要实际执行的事务交给 `ActionAgent`，写回 `WorldState`

### 核心模块

- `main.py`：FastAPI 入口，暴露 REST API 和 WebSocket
- `multiagent_framework/orchestrator.py`：主循环、时间推进、后台 Agent 调度
- `multiagent_framework/agent_runtime.py`：官员 Agent / 机构 Agent 运行时
- `multiagent_framework/router.py`：LLM 驱动的世界路由中枢
- `multiagent_framework/action_agent.py`：行动模拟 Agent，读写世界状态
- `multiagent_framework/timer_system.py`：定时器堆 + SQLite 持久化
- `multiagent_framework/chain_log.py`：调用链日志与上帝视角数据来源
- `multiagent_framework/court_graph.py`：朝廷层级与地点结构
- `multiagent_framework/world_state.py`：动态世界状态与库存
- `daming_court/loader.py`：DLC 内容加载器

### 两类数据层

- `CourtGraph`：谁是谁、在哪、归谁管。偏静态结构层。
- `WorldState`：某地现在发生了什么、还有多少粮。偏动态状态层。

## 目录结构

```text
世界模拟器/
├── main.py
├── requirements.txt
├── start.bat
├── start.sh
├── config/
│   ├── llm.json
│   └── time.json
├── multiagent_framework/
│   ├── action_agent.py
│   ├── agent_runtime.py
│   ├── chain_log.py
│   ├── court_graph.py
│   ├── llm_client.py
│   ├── orchestrator.py
│   ├── router.py
│   ├── timer_system.py
│   ├── types.py
│   └── world_state.py
├── daming_court/
│   ├── loader.py
│   ├── config/
│   │   ├── officials.json
│   │   ├── personas.json
│   │   ├── events.json
│   │   └── world_state.json
│   └── prompts/
│       ├── official_template.txt
│       ├── router.txt
│       └── action_agent.txt
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       ├── GodView.jsx
│       ├── WorldStateView.jsx
│       ├── AgentEditor.jsx
│       └── hooks/useWebSocket.js
└── tests/
    └── unit/
```

## 快速开始

### 1. 安装依赖

后端：

```bash
python -m pip install -r requirements.txt
```

前端：

```bash
cd frontend
npm install
```

### 2. 配置 LLM

编辑 `config/llm.json`：

```json
{
  "base_url": "https://your-openai-compatible-endpoint/v1",
  "api_key": "${LLM_API_KEY}",
  "model": "gpt-4o-mini",
  "timeout_s": 30,
  "max_concurrent": 4
}
```

说明：

- `api_key` 支持直接写明文
- 也支持 `${ENV_VAR_NAME}` 形式，从环境变量读取
- 当前仓库里的 `config/llm.json` 含有真实密钥，建议本地立刻替换并避免提交

### 3. 启动服务

方式一：分别启动

后端：

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

前端：

```bash
cd frontend
npm run dev -- --host
```

方式二：使用脚本

Windows：

```bash
start.bat
```

Linux / macOS：

```bash
sh start.sh
```

### 4. 打开页面

- 主界面：`http://localhost:3000`
- Agent 编辑器：`http://localhost:3000/editor.html`
- 后端 API：`http://localhost:8000`
- 世界状态在主界面的 Tab 中切换

## 基本玩法

1. 进入主界面后点击“开始模拟”
2. 初始蝗灾事件会唤醒基层官员
3. 等待奏折逐步传到皇帝视角
4. 对奏折执行 `批准 / 驳回 / 留中`
5. 或直接手写圣旨并下达
6. 在右侧观察公文在途情况
7. 切换到上帝视角查看完整链路和失真

## 时间系统

- 游戏内部时间单位是“游戏秒”
- `86400` 游戏秒 = `1` 游戏天
- `seconds_per_day` 表示多少真实秒对应 1 个游戏天
- 默认配置当前为 `300`，也就是 5 分钟过 1 个游戏天
- 运行中不能直接调速，需先暂停

## 主要前端页面

### 皇帝视角

- 奏折列表
- 批注与批复
- 圣旨下达
- 在途公文展示
- 启动、暂停、继续、存档、读档
- 模型配置和时间倍率设置

### 上帝视角

- Agent 对话时间线
- Router 决策日志
- 定时器待触发 / 已触发列表
- Prompt 在线编辑

### Agent 编辑器

- 官员树结构编辑
- 拖拽改直属上级
- 启用 / 禁用官员
- 驻地自由填写
- 编辑人格种子
- 导入/导出 JSON
- 预览某个官员最终生成的 system prompt

### 世界状态

- 实体列表，按类型筛选 + 搜索
- 新建/编辑/删除实体（级联删库存）
- 库存行内编辑（点击单元格直接修改）
- 地点坐标编辑（X/Y，平面直角坐标系）

## 配置文件说明

### `config/llm.json`

LLM 接口配置：

- `base_url`
- `api_key`
- `model`
- `timeout_s`
- `max_concurrent`

### `config/time.json`

时间配置：

```json
{
  "seconds_per_day": 300.0,
  "tick_interval": 1.0
}
```

### `daming_court/config/officials.json`

定义官员与地点：

- `officials[]`：官员、机构、太监、皇帝
- `locations[]`：地点与距京距离

官员关键字段：

- `id`：唯一 ID，建议 `职位_姓名`
- `role`：`official` / `eunuch` / `player`
- `agent_type`：`official` / `institution`
- `personality_seed`：人格模板名
- `superior_id`：直属上级
- `enabled`：是否参与模拟（禁用后不创建 Agent）
- `extra_prompt`：附加个性提示词（有则标记为"活跃"）

### 地点坐标

地点使用平面直角坐标系，以京城 `(0, 0)` 为原点，单位：里（1 里 ≈ 500 米）。

- 传信速度基准：300 里/天（快马急递日夜兼程）
- 通信天数 = `直线距离(里) / 速度(里/天) * 传递系数`
- 传递系数：加急 0.5，普通 1.0，民信 2.0

### `daming_court/config/events.json`

定义初始事件，例如蝗灾、旱灾、民变。

### `daming_court/config/personas.json`

定义人格种子，目前默认有：

- `贪腐`
- `忠直`
- `圆滑`

## API 一览

### 模拟控制

- `POST /api/start`
- `POST /api/pause`
- `POST /api/resume`
- `POST /api/save`
- `POST /api/load`
- `GET /api/save-info`
- `GET /api/time-scale`
- `POST /api/time-scale`

### 玩家动作

- `POST /api/player-action`

### 调试与观察

- `GET /api/chain-log/{chain_id}`
- `GET /api/token-usage`
- `GET /api/in-transit`
- `GET /api/debug/timeline`
- `GET /api/debug/timers`
- `GET /api/debug/router-log`
- `GET /api/world-state`
- `GET /api/world-state/{entity_id}`
- `PUT /api/world-state/{entity_id}`
- `DELETE /api/world-state/{entity_id}`
- `POST /api/world-state/{entity_id}/status`
- `PUT /api/world-state/{entity_id}/inventory`
- `DELETE /api/world-state/{entity_id}/inventory/{category}/{item_name}`

### Prompt 管理

- `GET /api/prompts`
- `POST /api/prompts`

### 编辑器接口

- `GET /api/editor/officials`
- `PUT /api/editor/officials/{official_id}`
- `DELETE /api/editor/officials/{official_id}`
- `POST /api/editor/officials/batch`
- `POST /api/editor/locations/batch`
- `GET /api/editor/locations/{location_id}`
- `PUT /api/editor/locations/{location_id}`
- `POST /api/editor/prompt-preview`
- `GET /api/editor/personas`
- `PUT /api/editor/personas/{persona_name}`
- `DELETE /api/editor/personas/{persona_name}`
- `POST /api/editor/personas/batch`

### 设置接口

- `GET /api/settings`
- `POST /api/settings`

### WebSocket

- `GET /ws`

用途：

- 后端向前端持续推送 tick 结果
- 前端也可以通过 WebSocket 发玩家操作

## 数据与日志文件

- `daming_court.db`：SQLite 主数据库
- `chain_log.jsonl`：消息调用链日志
- `debug_llm.jsonl`：LLM 调用摘要日志
- `saves/save.json`：单存档槽位

## 测试

运行后端单元测试：

```bash
pytest tests/unit -q
```

当前仓库已经覆盖了这几类基础测试：

- `TimerSystem`
- `CourtGraph`
- `ChainLog`

## 适合继续扩展的方向

- 新增更多历史 / 架空世界包（DLC）
- 增加随机事件生成器
- 增加更细的财政、治安、灾害扩散和物资消耗模型
- 增加更多机构 Agent 和派系关系
- 增加集成测试和端到端测试
- 将多存档、版本迁移、回放系统做完整

## 已知现状

- 项目核心链路已经成型，但仍偏原型 / 验证阶段
- 前端根目录还有一个默认的 Vite 模板 `frontend/README.md`，尚未替换
- 测试主要覆盖框架基础模块，尚未覆盖完整模拟流程
- LLM 配置文件里当前存在真实密钥，不适合继续保留在仓库中

## 开发建议

如果你准备继续在这个项目上开发，建议先做这几件事：

1. 把 `config/llm.json` 改成环境变量引用，移除明文密钥。
2. 为完整链路补一组集成测试：事件注入 -> 上报 -> 皇帝批复 -> 下行执行。
3. 给 `README` 之外再补一个面向贡献者的 `ARCHITECTURE.md` 或更新现有 `DEV_GUIDE.md`。
