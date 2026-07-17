# AgentEra 世界模拟器

> 当前版本：**v0.40.0**

AgentEra 是一个基于 **FastAPI + React + SQLite + LLM Function Calling** 的多 Agent 世界模拟器。内置“大明朝堂”内容包，玩家可以扮演皇帝处理奏折、下达旨意，并观察官员、机构、资源、关系和延迟事件在持续世界中的演化。

项目不仅包含游戏运行时，也提供 DLC Studio、通用 Actor 组件、模型路由、多轮记忆、真实模型轨迹评测和安全晋级门禁，便于快速创建新的世界与 Agent 群体。

## 核心能力

- **多轮 Agent**：支持多轮 Function Calling、工具预算、终止条件、异常降级和调用轨迹。
- **多层记忆**：支持短期记忆、长期摘要、承诺状态、压缩恢复和跨进程持久化。
- **世界模拟器**：实体、关系、库存、数值、随机事件、延迟行动和 SQLite 持久化。
- **统一价值锚**：资产库存使用 `quantity × anchor_rate` 计算统一基础价值。
- **类型化 Agent 经济动作**：ActionAgent 使用生产、消费和统一锚值转移工具；跨资源调拨按基础价值换算并原子提交。
- **行动因果桥接**：`request_action` 可显式选择把有界、脱敏的 `ActionResult` 摘要发布为因果源事件。
- **经济不变量监控**：汇总当前价值、累计发行/销毁、转移守恒与因果效果拒绝/故障，提供安全 API、Prometheus 指标和世界状态仪表盘。
- **DLC Studio**：新增“快速创建”与“玩家身份”页签，可批量生成实体、Actor、Agent 套件并可视化配置 direct/command 控制对象；其余关系、事件、资源、行动、Issue、目标、时间、因果、反馈、知识与 UI Manifest 继续提供中文说明、即时校验、Diff、撤销/重做和导入导出。
- **七步可玩性验收**：DLC 列表与 Studio 中文面板按玩家界面、行动角色、Issue 入口、有限信息成本决策、世界模拟、结果反馈、目标/沙盒进程七项展示证据与缺口；每张卡片可直接跳到对应编辑页，静态审计和 Registry 合法性校验仍相互独立。
- **通用 Actor 组件**：DLC 可声明与 LLM Agent 解耦的角色属性、Trait、需求、目标、能力、身份、组织成员关系和状态；Actor 可独立存在，也可被 Agent 显式绑定，运行时状态随 Generation、存档、热重载和回滚管理。
- **DLC 玩法契约**：DLC 可声明题材无关的玩家身份、演员和直接/命令控制对象；运行时、热重载、回滚、API 与主界面按同一 Generation 切换。
- **DLC 关系契约**：DLC 可声明有向/无向关系、强度区间、公开性、有效期、多维值和图样式；关系目录与边随 Generation、存档、热重载和回滚一致切换。
- **DLC 通用行动契约**：DLC 可声明参数、目标、控制权限、前置条件、锚定成本、权威耗时、冷却和组合结果；预览无副作用，执行采用原子世界命令与 invocation 幂等回放。
- **Generation 能力注册表**：玩家、普通 Agent 与 Router 共用 DLC `capability_id`；框架以稳定工具解释执行 `noop`、`message`、`world_action`、`contract_action`、`broadcast` 五类安全 handler，并随热重载、归档、回滚和重启恢复切换。
- **通用因果条件**：25 种事实来源与 12 种比较操作符统一覆盖实体、Actor、关系、事件历史、持续事件、时间、目标、聚合指标、三层知识、新鲜度、保密级别、调查和世界阶段；生产运行、Registry、静态分析与 Studio Dry Run 共用同一求值语义。
- **Reusable causal authoring**: define package-local condition groups and rule templates, retain references or materialize explicit overrides in DLC Studio, and use identical expansion semantics for validation, Dry Run, reload, rollback, and production runtime.
- **Causal graph replay workbench**: inspect resolved rule edges and authoring provenance, export secret-free deterministic replay fixtures, and compare old baselines against the current unsaved Studio draft without mutating saved content or live runtime.
- **事务化通用因果效果**：23 种效果覆盖资源/状态、持续事件、锚值守恒转移、实体生命周期、Agent 启停、关系/职位、Issue、三层知识、传播、调查、目标、行动解锁和世界阶段；整批原子提交、失败回滚，并使用稳定命令 ID 保证重启重放幂等。
- **三层知识与调查**：显式区分世界真相、Actor 主观认知与玩家知识，记录来源、置信度、新鲜度、保密、作用域、失真和状态；过期信息禁止继续传播，调查可按受限结果策略把核验结果投影到 Actor 或玩家层。
- **DLC 派生指标**：DLC 可用确定性公式组合实体指标、参数、Actor 属性、关系、持续事件、模拟天数与统一锚定库存价值；Objective、因果规则、Agent Prompt、主界面和安全 API 按受众读取同一 Generation 的结果。
- **晋级工作台**：导入已批准 Promotion Gate，快速生成 DLC/Agent cohort mapping，执行 generation-bound Preview/Apply。
- **Generation 观测与安全回滚**：加密保留模型配置历史，按代聚合调用质量，并将历史配置作为新的递增 Generation 原子恢复。
- **Generation 热重载**：DLC、Prompt、模型与时间规则按代发布；在途工作继续使用开始时的版本语义。
- **任务模型路由**：可按全局任务、DLC、Agent 和显式 Profile 选择不同模型。
- **详细中文设置中心**：在主界面可视化管理 Provider、模型 Profile、三级任务路由、Agent 多轮预算、记忆压缩、世界 Tick 与安全运维状态；支持后端校验、变更预览、Generation 乐观锁和热更新。
- **通用 Issue / Case / DecisionRequest**：DLC 可声明题型、证据、期限、紧迫度和动态选项，运行时支持创建、决策、执行 Action、关闭、过期、幂等与存档恢复。
- **权威目标与结局**：主 DLC 可声明主目标、可选目标、失败条件、有序/独立里程碑与胜利/失败/中立结局；框架只从权威世界事实和持久回执判定，客户端与 LLM 不能直接改写完成状态。
- **模型评测与晋级**：支持真实轨迹矩阵、独立人工评审、cohort 分区、环境门禁、安全预览、原子应用和失败回滚。

## 模型路由优先级

从高到低：

1. 调用方显式 Profile 覆盖
2. Agent 源文件中的显式 `model_profile`
3. DLC manifest 的 Agent 绑定
4. DLC manifest 的任务绑定
5. 中央 `agent_task_policies`
6. 中央 `dlc_task_policies`
7. 全局 `task_policies`
8. 默认模型策略

显式 Profile 可启用严格模式，禁止失败后回退到其他候选模型，适用于公平评测。

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 20+
- npm

### 安装

```powershell
python -m pip install -r requirements.txt
cd frontend
npm install
```

### 配置模型

复制模型配置示例：

```powershell
Copy-Item config/models.example.json config/models.json
```

在 `config/models.json` 中配置 Provider、模型 Profile 和任务策略。密钥必须通过 `api_key_env` 指向环境变量，不能把明文密钥写入配置文件。

```powershell
$env:WORLD_SIM_PRIMARY_API_KEY = "your-key"
```

兼容旧版 `config/llm.json`，但 DLC/Agent 分层模型策略和晋级应用要求使用 `config/models.json`。

### 启动

```powershell
# 后端
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 前端
cd frontend
npm run dev -- --host
```

也可以在 Windows 下运行 `start.bat`。

### 页面

- 游戏主界面：`http://localhost:3000`
- Agent 编辑器：`http://localhost:3000/editor.html`
- DLC Studio：`http://localhost:3000/dlc.html`
- 天玑调试界面：`http://localhost:3000/tianji.html`
- 后端 OpenAPI：`http://localhost:8000/docs`

## 详细中文设置中心 (v0.22.0)

主界面右上角齿轮已升级为完整设置中心，用于替代旧版只覆盖少量字段的简陋弹窗。界面按中文说明组织为九类：配置总览、接口服务商、模型 Profile、任务模型路由、Agent 多轮调用、多轮记忆、世界模拟、安全与运维、高级 JSON。

- Provider：新增、删除和改名 Provider，配置 Base URL、API Key 环境变量名、超时，并可临时输入仅驻留当前后端进程内存的 Session Key。
- 模型 Profile：配置 Provider、模型 ID、能力标签、并发和重试；Profile 改名会同步更新三级任务路由引用。
- 任务路由：可视化编辑全局 `task_policies`、DLC `dlc_task_policies` 和 Agent `agent_task_policies`，候选 Profile 顺序即失败回退顺序。
- Agent 多轮调用：分别控制普通 Agent 与 ActionAgent 的轮数、工具调用数、Token、耗时和无进展终止预算。
- 多轮记忆：控制短期记忆容量、压缩阈值、保留条数、结构化记忆容量/检索/字符预算和压缩温度；`knowledge_context_max_records` / `knowledge_context_char_budget` 限制每轮可见知识条数与字符数，`derived_metric_context_max_records` / `derived_metric_context_char_budget` 限制只读派生指标条数与字符数。后两项设为 `0` 可关闭派生指标注入，四项均支持热更新。
- 世界模拟：控制游戏日与现实秒换算及后台 Tick 间隔；保存后可热更新当前 Orchestrator。
- 安全与运维：只读展示管理 API、管理令牌、归档加密密钥和告警相关环境变量是否配置，不展示其值。
- 高级 JSON：只编辑模型文档中的 Provider、Profile 与三级路由五个段；保存前统一经过后端 Schema 校验，并展示 Warning 与变更 Diff。

运行时数值保存在 `config/runtime.json`（示例见 `config/runtime.example.json`），模型与路由仍保存在 `config/models.json`。设置接口为：

- `GET /api/settings/schema`：读取中文字段元数据、范围、选项和安全说明。
- `GET /api/settings`：读取脱敏设置文档、当前 Generation 与环境状态。
- `POST /api/settings/validate`：无副作用校验并生成 Warning、规范化文档和 Diff。
- `POST /api/settings`：使用 `expected_generation` 乐观锁原子保存并激活配置。

API Key 不会通过设置 API 明文回传，也不会写入 JSON 或浏览器存储。长期凭据应使用 `api_key_env` 指向环境变量；Session Key 只适合本机临时调试。若已配置密钥的 Provider 修改到不同主机，必须在预览警告后显式确认 `allow_secret_host_change`。模型、Agent 预算、记忆和时间参数可热更新；管理 API 开关、管理令牌、归档加密密钥等环境变量仍需在进程启动前配置并重启生效。

## DLC 玩法契约 (v0.20.0)

主 DLC 可在 `content.json` 中声明题材无关的玩家身份和控制对象：

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

- `kind` 支持 `entity` 与 `agent`；`mode` 支持 `direct` 与 `command`。
- `direct` Agent 是玩家直接接收端，不创建自主 LLM Agent；`command` Agent 仍自主运行，但属于玩家可命令对象。
- 只有 primary DLC 提供当前运行时玩家契约，避免多个内容包争夺玩家身份。
- 显式 `gameplay.player` 优先于旧 `agents[].role == "player"`；未配置时继续从旧角色自动推导，兼容现有“大明朝廷”内容。
- `GET /api/gameplay` 返回稳定的题材无关契约。主界面标题、玩家身份、控制对象数量和视角按钮使用该接口，不再依赖“皇帝”等硬编码。
- GameplayContract 与 DLC Generation 一起归档、热重载和回滚，旧归档缺失字段时也会使用兼容推导。

## DLC 通用行动契约 (v0.21.0)

DLC 的 `content.json` 可新增 `actions` 数组。框架负责解释并执行规则，DLC 负责行动名称、参数、成本、条件和结果内容：

```json
{
  "actions": [
    {
      "action_id": "frontier_patrol",
      "display_name": "组织边境巡逻",
      "parameters": [
        {"name": "alert_gain", "type": "number", "default": 8, "minimum": 1, "maximum": 20}
      ],
      "target": {"kind": "entity", "required": true, "controlled_only": true},
      "permissions": {
        "control_modes": ["direct"],
        "control_kinds": ["entity"],
        "control_ids": ["边境哨站"]
      },
      "preconditions": [
        {"kind": "anchored_inventory", "entity_id": "$control", "name": "边境银券", "operator": "gte", "base_value": 10}
      ],
      "costs": [
        {"entity_id": "$control", "resource_name": "边境银券", "base_value": 10}
      ],
      "duration": {"action_type": "frontier_patrol", "proposed_days": 1},
      "cooldown": {"days": 3, "scope": "control_target"},
      "result": {
        "mode": "wake_agent",
        "agent_id": "哨站守备官",
        "payload": {"target_id": "$target"}
      }
    }
  ]
}
```

运行时约束：

- 参数支持 `string`、`integer`、`number`、`boolean`，并支持 required/default/enum/minimum/maximum。
- 目标支持 `none`、`entity`、`agent`；权限从同一 Generation 的 `GameplayContract.controlled_objects` 校验，拒绝 actor 冒用。
- 成本统一用 `base_value` 声明，再按资源目录中的 `anchor_rate` 换算实际库存数量；成本与世界效果在同一原子批次提交。
- 耗时由 `TimeSimulator` 决定，LLM 或客户端只能提供有界输入提示，不能直接指定权威完成时间。
- 结果支持 `world_commands`、`ongoing_event`、`wake_agent`、`emit_event` 和 `composite`。
- `invocation_id` 是执行幂等键；相同请求可安全回放，不同请求复用同一 ID 会返回冲突。`expected_generation` 用于拒绝过期界面提交。
- 冷却、执行 receipt 与行动定义一起支持存档恢复；热重载和 Generation 回滚不会清空已经承诺的冷却或回执。

通用 API：

- `GET /api/actions`：读取当前 Generation 的行动目录。
- `POST /api/actions/{action_id}/preview`：无副作用预览权限、条件、成本、耗时、冷却、命令和副作用。
- `POST /api/actions/{action_id}/execute`：按原子世界命令执行行动并记录幂等 receipt。

`example_frontier` 包含可执行的 `frontier_patrol` 示例，DLC Studio 的“行动”页签可编辑基础字段以及参数、目标、权限、条件、成本、耗时、冷却和结果 JSON。

## 通用 Issue / Case / DecisionRequest (v0.22.0)

DLC 可通过 `content.json` 的 `issue_types` 声明题材无关的待处理事项类型，包括来源限制、必需证据、默认紧迫度、期限、是否创建 Case，以及静态或动态 DecisionOption。运行时由 `IssueManager` 统一管理：

- Issue 支持外部幂等键、证据追加、期限刷新、过期和关闭；Case 聚合关联 Issue 并可独立结案。
- DecisionRequest 冻结创建时的选项语义，可预览或解析为同一 Generation 的通用 Action；解析使用 `invocation_id` 保证幂等。
- 题型目录、Action 引用和动态选项在 DLC Registry 阶段校验，并随 Generation 发布、归档、恢复和回滚。
- 运行时快照和手动存档保存 Issue、Case、DecisionRequest、执行回执及顺序；旧 memorial 存档可兼容迁移。
- 主界面可查看待处理 Issue 与动态选项，DLC Studio 可编辑 `issue_types`；示例 DLC 提供可运行题型。

通用 API 包括 `GET /api/issue-types`、`GET/POST /api/issues`、追加证据、`GET /api/cases`、关闭 Case、`GET /api/decision-requests`，以及 DecisionRequest 的 preview/resolve。旧 `/api/memorials` 继续作为兼容视图，但新玩法不应依赖“大明奏折”等框架硬编码。

## DLC 目标、里程碑与结局 (v0.23.0)

主 DLC 可在 `content.json` 顶层声明 `objectives` 与 `endings`。依赖 DLC 仍可贡献实体、Action 和 Issue，但不会与主 DLC 争夺 Campaign 结局：

- 目标分类为 `primary`、`optional`、`failure`；里程碑可选 `ordered` 或 `independent`。
- 条件树支持 `all`、`any`、`not`，事实来源包括锚定库存、指标、参数、实体状态、模拟天数、Issue/Case/Decision 数量和 Action 成功回执数量。
- 结局结果支持 `victory`、`defeat`、`neutral`。同一 Tick 同时满足多个结局时先比较 `priority`，相同优先级再按 `ending_id` 确定；首个结局锁定后不可反转。
- 不声明目标和结局即为显式沙盒，不会因为“零目标”自动胜利。目标、里程碑和结局回执只生成一次，并随运行时快照、手动存档、重启、Generation 归档、热重载和回滚恢复。
- `GET /api/objectives` 返回 Campaign、目标、里程碑、条件诊断与进度；`GET /api/endings` 返回可达结局、已满足状态和锁定结果。
- 主界面提供可折叠目标面板；DLC Studio 的“目标”和“结局”页签提供中文字段说明、引用下拉框、可视化嵌套条件树、高级 JSON、导入导出、Diff 与历史撤销/重做。

`example_frontier` 展示了巡逻经费、Action 次数、警戒指标、15 天失败期限，以及胜利/失败结局的完整组合。

## DLC UI Manifest（v0.24.0）

主 DLC 可在 `content.json.ui_manifest` 中配置题材化界面，而无需修改 React 核心代码。依赖 DLC 不会覆盖主 DLC 的界面所有权。

- `title`、`subtitle`、`brand.short_mark`：配置产品标题、副标题和短标识。
- `terminology`：配置玩家、事项、案件、决策、消息、世界、实体、关系、目标、库存、指标等 30 项通用术语。
- `navigation`：只允许选择框架内置的主视图 `dashboard/world/relations/debug` 与应用页面 `gameplay/editor/dlc_studio/assistant`；DLC 不能注入 URL。
- `dashboard`：选择三栏、双栏或单栏布局，并将目标、事项列表、事项详情、玩家消息和在途消息安排到受控区域。
- `issue_page`：选择事项列表/详情的可见字段，并控制是否展示已解决事项。
- `entity_detail`：选择实体基础字段，以及摘要、持续事件、位置、库存、指标和参数 Section。

`GET /api/ui-manifest` 返回当前 Generation 的归一化 Manifest。首次加载、热重载、Generation 归档、历史回滚和重启恢复使用同一份配置；老归档没有该字段时自动回退到安全默认值。主界面仅把 Manifest 映射到框架拥有的组件与固定路由，不执行 DLC 提供的代码、样式、HTML 或链接。

DLC Studio 的“界面”编辑器提供完整中文说明、白名单下拉选择、排序/显隐编辑、本地即时校验、重复 ID 检查、高级 JSON、导入导出、Diff 和历史撤销/重做。保存仍经过 Registry 校验与 Content Diff 确认。`example_frontier` v1.3.0 提供“边境指挥中心”完整示例。

## 经济监控

世界状态页会加载 `GET /api/world-state/economy`，显示：

- 全局当前基础价值及按资源的数量、锚率、基础价值和持有者数；
- 从不可变库存账本计算的累计发行、累计销毁与非转移净值；
- 通过稳定 `transfer_id` 对账的锚值转移批次、借贷总额、净差额、不完整批次和不守恒批次；
- 因果世界效果的批次、命令、拒绝、故障和拒绝率。

旧版 `v0.15.0` 快照没有 `transfer_id` 时，运行时会按因果事件与效果序号兼容配对。公开 API、JSON 指标和 Prometheus 指标只提供聚合值，不返回实体、命令、链路、转移标识、Prompt、密钥或原始异常。

## DLC 开发

DLC 可以声明：

- 实体与初始库存
- Agent、机构和上下级关系
- 人格与 Prompt 模板
- 初始事件、随机事件和延迟事件
- 资源定义与统一价值锚
- 行动耗时规则
- 通用行动、Issue / Case / DecisionRequest 题型、目标、里程碑、失败条件与结局
- DLC/Agent 显式模型绑定
- 因果规则的通用条件（25 种事实来源、12 种操作符、多类型值与 Filter JSON）和事务化通用效果（23 种资源、实体、Agent、关系、Issue、知识、目标、行动与阶段效果）
- 派生指标、聚合公式、受众权限、缺失策略，以及用于 UI、目标、规则和 Agent 的稳定指标 ID

DLC Studio 支持草稿校验、发布、Generation 归档、热重载和回滚。运行时不会因为新版本发布而重算已经承诺的延迟结果。

## 模型策略晋级

生产模型策略采用分阶段流程：

```text
真实模型轨迹 → 自动检查 → 独立人工评审 → cohort 排名
→ 环境规模/运行门禁 → Promotion Gate
→ Preview → 指纹确认 → 原子 Apply → Generation 热重载
```

管理 API 默认关闭。启用时必须同时配置管理开关与管理令牌。预览和应用结果只返回安全标识、计数与 SHA-256 指纹，不返回 Provider 地址、环境变量名、凭据、Prompt 或消息正文。

DLC Studio 的“晋级工作台”可直接完成上述 Preview/Apply 流程：

1. 导入或粘贴已批准的 Promotion Gate JSON。
2. 选择当前 DLC，可选地显式勾选 Agent，并生成 cohort mapping。
3. 在密码输入框中临时输入 Admin Token；令牌只保存在页面内存并仅用于请求头。
4. 先 Preview 并检查 Generation、变化数、目标数和计划指纹，再 Apply。
5. Promotion、Mapping、DLC 或 Gateway Generation 任一变化后，旧 Preview 自动失效。
6. 在同一工作台刷新 Generation 观测，查看每代调用、错误、fallback、Token、平均/最大延迟和在途调用。
7. 选择非当前 Generation，先生成绑定当前代与计划指纹的回滚 Preview，再确认 Apply；恢复操作始终创建新的递增 Generation，不会倒退代号。

模型 Generation 归档默认写入运行时目录并使用 AES-GCM 加密。管理 API 和界面只返回配置指纹、结构计数与运行聚合，不返回可解密配置、Provider 连接元数据、凭据、Prompt/消息/记忆正文、路径或原始异常。

工作台地址仍为 `http://localhost:3000/dlc.html`。管理接口必须通过 `WORLD_SIM_ENABLE_ADMIN_API` 和 `WORLD_SIM_ADMIN_TOKEN` 显式启用。

## 通用 Actor 组件（v0.26.0）

DLC 在 `content.json.actors` 中声明题材无关的 Actor。`actors` 是权威角色定义，`agents[].actor_id` 只负责把 LLM 调用者绑定到角色；未填写 `actor_id` 的旧 Agent 会自动投影同 ID Actor，因此旧 DLC 无需立即迁移。Actor 可以没有 Agent，适合库存管理员、组织、群体或暂未启用 LLM 的角色。

Actor 组件包括：

- `attributes`：有界数值或标量属性；
- `traits`：带强度、标签和描述的稳定特质；
- `needs`：当前值、目标值、上下界和变化率；
- `goals`：优先级、状态、进度与期限；
- `capability_ids`：引用当前 Generation 的 DLC 能力；
- `identities`：身份、称号、实体和有效期；
- `memberships`：组织、角色、状态和权重；
- `states`：可持久化的安全 JSON 动态状态；
- `metadata`：编辑和扩展信息，默认不会注入模型 Prompt。

DLC Studio 的“Actor / 角色组件”页签提供完整中文说明、即时校验、Actor-only 创建和 Agent 绑定选择。运行时提供 `GET /api/actors` 与 `GET /api/actors/{actor_id}`；Prompt 可使用 `{actor_id}`、`{actor_attributes}`、`{actor_traits}`、`{actor_needs}`、`{actor_goals}`、`{actor_capabilities}`、`{actor_identities}`、`{actor_memberships}` 和 `{actor_states}`。

## 通用因果条件、效果与三层知识（v0.30.0）

`content.json.causal_condition_groups[].conditions`、`causal_rule_templates[].rule.conditions` 与 `causal_event_rules[].conditions` 使用同一套确定性条件契约：

- 事实来源共 25 种：原有实体、Actor、关系、事件、时间、目标、聚合指标和阶段来源，新增 `world_truth`、`actor_belief`、`knowledge_confidence`、`knowledge_freshness`、`knowledge_secrecy` 与 `investigation`；`player_knowledge` 也升级为显式知识层查询。
- 操作符共 12 种，值支持数值、文本、布尔和 JSON 列表；知识条件可按 holder、key、字段、来源、置信度、新鲜度、保密级别和调查状态做有界筛选。
- Runtime、生产 Orchestrator、Registry、静态分析和 DLC Studio Dry Run 调用同一求值器。没有显式知识 Provider 时知识条件 fail-closed，不会回退读取世界真相。
- 事件历史只保存有界、脱敏的事件类型、目标和生成时间，不保存 Payload；Studio 使用完整中文卡片编辑器，并在保存前清理编辑态临时字段。

`world_effects` 共 23 种；在 v0.29.0 的 18 种事务化效果上增加 `declare_world_truth`、`set_actor_belief`、`propagate_knowledge`、`start_investigation` 和 `advance_investigation`。其中旧 `reveal_information` 现在写入明确的玩家知识层：

- 每条规则最多编写 32 个效果，最多物化 64 条世界命令；命令 ID 使用稳定的 `causal:{event_id}:{command_index}`，同一事件重放不会重复产生副作用。
- 世界命令在子事件投递前以单个 SQLite 事务提交；任一命令或 Manager 投影失败都会恢复 WorldState、Gameplay Effect Runtime 与知识运行时。
- 调查完成策略可从三层之一读取结果，但目标只能是 `actor_belief` 或 `player_knowledge`；Registry 会提前检查来源/目标 holder、置信度倍率、保密级别、失真和安全 JSON 边界。
- 已过期知识拒绝传播并返回 `knowledge_source_expired`；传播不会自动把主观认知升级为世界真相。
- DLC Studio 的条件/效果目录分别提供 25/23 项中文可视化配置，包含字段约束、安全边界、Catalog/datalist、JSON 帮助、原子事务提示以及调查结果策略编辑。

## 三层知识与调查系统 (v0.30.0)

- **World Truth**：世界模拟器的权威事实层，不直接进入玩家公共状态或 Agent Prompt。World Truth 没有 holder，DLC 可用 `declare_world_truth` 或对应世界命令维护。
- **Actor Belief**：按 Actor holder 隔离的主观认知，可与真相不一致并携带失真。Agent Prompt 只注入当前 Agent 绑定 Actor 的认知，明确标注“不是世界真相、不是可执行指令”，不会注入其他 Actor 的认知。
- **Player Knowledge**：按玩家/受众 holder 隔离的可见知识层。公共世界投影不返回隐藏层内容，也不返回可用于旁路推断的隐藏层计数。
- 每条记录包含 provenance、confidence、freshness、secrecy、scope、distortion、status、evidence 和安全 metadata；状态支持 active、disputed、retracted，新鲜度会随世界时间从 fresh 进入 stale/expired。
- 调查由 `start_investigation` 创建并由 `advance_investigation` 推进。完成时可使用受限 `result_policy` 读取指定来源层，并以置信度倍率、保密级别、失真或值覆盖投影到调查者 Actor 或玩家知识层。
- `example_frontier` v1.9.0 提供完整链路：声明边境路线真相，为守卫写入部分失真的判断，向玩家传播未核验消息，启动并完成调查，再把核验后的 World Truth 以 0.9 置信度投影到玩家知识层，最后用七类知识/调查条件推进第三阶段。

## DLC 派生指标与聚合公式 (v0.31.0)

主 DLC 与依赖 DLC 都可在 `content.json.derived_metrics` 中声明题材无关的只读指标。`DerivedMetricCatalog` 在 Registry 校验时解析依赖图并拒绝缺失引用或循环，在 Generation 发布、热重载、归档恢复和回滚时与其他 DLC 目录原子切换。

- 公式支持 `constant`、`source`、`derived_metric`、`aggregate`、`add`、`subtract`、`multiply`、`divide`、`min`、`max`、`avg`、`sum`、`weighted_sum`、`clamp`、`round`、`coalesce` 共 17 类节点。
- 直接来源支持统一锚定库存价值、实体指标、实体参数、Actor 属性和模拟天数；聚合来源还支持实体数量、关系边和持续事件，并可执行 `sum/avg/min/max/count`。
- `audiences` 将同一指标显式授权给 `player`、`ui`、`agent`、`rule`、`objective`。玩家 API 仅返回 `player` 指标；Agent 只接收 `agent` 指标摘要；规则和目标分别要求对应受众，不会跨受众旁路读取。
- `missing_policy` 可设为 `fail` 或 `zero`。公式有 12 层深度、256 节点、64 子项和有限数值限制；除零、缺失来源、非有限结果与循环均产生稳定错误状态。
- `GET /api/derived-metrics` 与 `GET /api/derived-metrics/{metric_id}` 返回当前 Generation 的安全结果，不暴露公式、Metadata、底层 Provider 异常或隐藏受众指标。UI Manifest 可添加 `kind: "derived_metrics"` 的 Dashboard Widget。
- DLC Studio 的“派生指标”页签提供五类受众、缺失策略、全部公式节点、Catalog 引用、常用 Filter、依赖图、循环/缺失检查、有限数值与深度/节点检查，并纳入导入导出、Keyed Diff、历史和撤销/重做。

`example_frontier` v1.10.0 的“边境综合战备度”按 55% 哨站警戒度、25% 守备 Actor 警觉属性和 20% 边境银券统一锚定库存价值计算，供玩家、界面、Agent、因果规则和目标共同读取。

## DLC 日历、季节、玩法周期与自动暂停 (v0.32.0)

DLC 可在 `content.json.time_system` 声明完整的题材历法和长期玩法节奏，框架只负责解释、调度和安全展示，不写死朝代、月份、星期或回合文案。

- 历法支持自定义纪元、起始年/月/日/星期、1-64 个非均匀月份和自定义星期循环；当前日期由权威 `sim_time` 确定性换算。
- 季节支持普通与跨年区间；Registry 拒绝越界或重叠定义。
- `turn`、`meeting`、`task`、`custom` 周期分别配置周期、持续时长和偏移，可同时激活并向主界面公开进度。
- 日/周/月/年、季节开始/结束、周期开始/结束均可生成确定性边界事件，选择 Agent 唤醒目标、Issue Type 和安全 Payload。
- 自动暂停既可复用通用因果条件，也可绑定时间边界，并支持 `all/any`、冷却天数、仅一次和上升沿语义；暂停提示可关闭，但不会隐式恢复模拟。
- `GET /api/time-system` 与 Tick WebSocket 返回当前日期、季节、周期和脱敏暂停回执，不公开条件值或隐藏事实引用。DLC Studio 的“历法与周期”页提供完整中文编辑、引用检查、JSON Payload、Diff、History 和导入导出。

`example_frontier` v1.11.0 演示 360 天边境驻防历、四季、7 天指挥回合、30 天联席会议、14 天巡防任务、回合报告事件、会议自动暂停和战备度低于 30 的上升沿告警。

## DLC 动态 Actor、Agent、职位与组织生命周期 (v0.33.0)

DLC 可在 `content.json.lifecycle` 声明动态角色模板、职位和确定性生命周期规则。生命周期与 DLC Generation 绑定，首次加载、热重载、历史回滚、存档和读档都使用同一份契约；运行时变更统一经过 WorldCommand 原子批次，不允许 DLC 执行任意 Python 或前端代码。

- Actor 模板可同时创建动态 Actor、可选世界实体和可选动态 Agent。动态 Agent 从 `source_agent_id` 对应静态 Agent 继承模型 Profile、任务模型路由、多轮预算、工具权限、记忆窗口和 API Provider 路由；生成后拥有独立 ID，并支持记忆迁移后继续分化。
- 职位声明组织、角色、等级、容量、初始任职者、候选 Actor 和确定性评分字段。评分支持 Actor 属性、状态、组织成员等级与 Actor ID；同分时以 Actor ID 保证重放稳定。
- 规则支持出生、死亡、晋升、继任、空缺、组织分裂、组织合并、实体转化和自定义九类语义，可绑定条件、日/周/月/年、季节和玩法周期边界，并支持 `all/any`、`once`、`cooldown_days` 与上升沿。
- 七类白名单操作为 `spawn_actor`、`retire_actor`、`appoint_position`、`vacate_position`、`transform_entity`、`split_organization`、`merge_organizations`。同一规则的全部操作要么整体成功，要么整体回滚。
- Receipt、序列号、冷却与条件 Latch 会进入存档；Actor 退休使用 Tombstone，避免热重载或回放时意外复活；动态 Agent 会同步停用，相关职位会自动腾空。
- DLC Studio 的“动态生命周期”页签提供完整中文可视化编辑器，可创建模板、职位、评分、规则、时间过滤、通用因果条件、七类操作和玩家反馈，并在保存前检查 ID、引用、数值范围与 JSON 结构。

`example_frontier` v1.12.0 提供动态巡防副官模板、巡防队长职位、双周生成、评分继任、退役、瞭望塔季节转化、守军分裂和组织合并示例。

## DLC 节奏导演：注意力、升级与高潮收束 (v0.34.0)

DLC 可在 `content.json.rhythm` 声明长期叙事节奏。框架以确定性预算和规则控制 Issue 与随机事件的进入时机，不替 DLC 编写剧情，也不会把隐藏条件或内部队列泄露给玩家。

- 全局配置包含活跃 Issue 上限、注意力预算、每游戏日恢复量、排队上限、最近反馈数量、全局平静期，以及 critical 紧迫度是否绕过平静期。
- 每条规则可按 Issue Type 或 Event Type 匹配，并配置优先级、注意力覆盖成本、规则级活跃上限、冷却、互斥组、规则平静期及绕过策略。
- Issue 可按经过的游戏日确定性升级紧迫度；满足通用因果条件后可降级到指定等级与状态；达到高潮等级且满足条件后可收束 Issue、同组 Case 和 DecisionRequest，并进入高潮后平静期。
- 随机事件在注入前经过同一节奏准入层；低优先级噪声会因预算、冷却、互斥组或平静期被抑制，结果记录在有界的安全反馈中。
- 节奏契约、注意力、队列、冷却、升级/降级/高潮状态随 Generation 热重载、归档、回滚与存读档恢复。`GET /api/rhythm` 返回脱敏的当前预算、队列、规则摘要和最近反馈。
- DLC Studio 的“节奏导演”页签提供全中文可视化编辑，覆盖全局预算、规则排序、Issue/Event 匹配、升级、降级因果条件、高潮因果条件和本地引用校验，并沿用统一 Diff、确认、撤销/重做与保存流程。

`example_frontier` v1.13.0 提供四条参考规则：边境安全危机升级链、稳定化情报确认、低成本常规报告，以及会被冷却和平静期抑制的边境流言噪声。

## 玩家结果反馈：即时变化、延迟报告与因果揭示 (v0.35.0)

DLC 可在顶层 `outcome_feedback` 声明玩家结果反馈契约。框架把行动、决策、结案、因果事件、生命周期与节奏事件转换为玩家可理解的公开结果，但不替 DLC 编写剧情，也不会泄露隐藏条件、世界真相或 Actor 私有认知。

- 自动即时反馈从安全的 WorldCommand 差异和玩家可见派生指标中生成“发生了什么”与确定性原因，并通过 epsilon 和条数上限抑制噪声。
- 自定义规则支持即时反馈、延迟报告与事后因果揭示，可按来源、Action、Issue Type、Event Type、派生指标和通用因果条件匹配。
- 结案总结可汇总公开决策、变化与指标结果；延迟队列、最近反馈和幂等回执均为有界状态。
- 结果反馈进入 Generation 初载、热重载、归档、回滚、重启恢复与存读档；兼容配置热重载时保留待交付反馈，重复 Tick 或读档不会重复发放。
- `GET /api/outcome-feedback` 与 Tick 的 `outcome_feedback` 字段只返回玩家安全投影。主界面新增“结果反馈”Widget，展示即时变化、延迟报告、结案总结、因果揭示和指标原因。
- DLC Studio 的“结果反馈”页签提供完整中文说明，覆盖容量、自动反馈、结案总结、规则来源、交付方式、延迟、引用、因果条件、公开文案、后续 Issue 与证据联动，并支持添加、复制、删除和排序。

`example_frontier` v1.14.0 提供巡逻即时结果、边境决策延迟报告和稳定化因果揭示三条参考规则，并在 UI Manifest 中启用结果反馈 Widget。

## 测试

```powershell
# 后端
pytest -q

# 前端
cd frontend
npm test
npm run lint
npm run build
```

外部 OpenAI-compatible 连接 Smoke Test 默认不会访问网络；只有显式提供临时环境变量时才运行，并会自动补齐 `/v1`、拉取模型列表、过滤明显的非聊天模型，再发送最小 Chat Completions 请求：

```powershell
$env:AGENTERA_TEST_BASE_URL='http://example.test:3000'
$env:AGENTERA_TEST_API_KEY='<temporary-key>'
# 可选；留空时测试从 /v1/models 自动选择候选模型
$env:AGENTERA_TEST_MODEL=''
python -m pytest -q tests/integration/test_external_api_connection.py
```

临时密钥只通过当前进程环境传入，不得写入 `.env`、配置文件、测试夹具、日志或 Git；未配置 URL/Key 时这组测试自动 skip，临时通道失效不阻断离线发布门禁。

## 文档

- [PROJECT_DOC.md](PROJECT_DOC.md)：架构、数据流、运行时和 API 说明
- [DEV_GUIDE.md](DEV_GUIDE.md)：开发、配置、测试和扩展指南
- [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)：长期优化计划与阶段检查点

## 版本管理

项目使用 GitHub 仓库 **xiaoyi198/AgentEra**。功能开发默认使用 `codex/...` 分支，通过阶段性提交和 Pull Request 合并到 `main`。

版本号采用语义化版本：

- `VERSION`：项目权威版本号
- 后端 `multiagent_framework.version.__version__`
- 前端 `frontend/package.json`
- 前端锁文件 `frontend/package-lock.json`

发布时四处版本必须保持一致。


## v0.40.0 Release Notes

v0.40.0 upgrades the v0.39.0 per-tick heap into a persistent deferred timeline. Materialized temporal events, lifecycle markers, random events, and lifecycle condition continuations now survive tick budgets, save/load, rollback, and process restarts without allowing later work to overtake earlier work.

- Added `DeferredTimelineRuntime`, ordered by `(occurrence_time, source_rank, sequence)`, with a 32,768-entry hard limit and fail-closed snapshot restore.
- Preserved the causal budget of 128 and lifecycle budget of 256 while deferring later sources whenever an older causal item or lifecycle continuation remains.
- Added `start_transition_index` / `next_transition_index` lifecycle cursors, so a marker matching more than 256 transitions resumes from the exact unattempted transition instead of replaying receipts or starving the tail.
- Archived lifecycle contracts once per generation fingerprint. DLC hot reload affects future materialization, while already queued lifecycle work keeps its committed semantics.
- Added `deferred_timeline` to `save_state()` / `load_state()`, including stable sequence numbers, event payloads, lifecycle cursors, and deduplicated contract archives. Old saves without the field remain compatible.
- Restored the generated Issue source label to `time system` in Chinese UI text.
- Added regressions for 129 earlier causal events blocking later temporal/random work, restart recovery, 300 lifecycle transitions resuming as 256 + 44, hot-reload preservation, and malformed/oversized snapshots.

Validation: **791 Python unit tests passed**; **11 integration tests passed, 2 skipped**; **97 frontend tests passed**; ESLint and Vite build passed.

## v0.39.0 发布说明

- Orchestrator 将时间边界、生命周期 marker、随机事件和已到期因果事件放入同一 tick 时间队列，长跳时按权威 `generated_at` / marker `sim_time` 跨来源归并，不再先执行全部生命周期后再处理更早的因果效果。
- 同一时刻采用稳定优先级：时间边界 → 生命周期 marker → 随机事件 → 因果事件 → 当前时刻条件型生命周期；零延迟因果链会在其来源之后继续参与排序。
- `CausalEventEngine.next_due_key()` 支持不消费 pending 的最早到期窥视；历史 marker 轮询通过 `evaluate_conditions=False` 避免重复评估当前条件，条件型生命周期只在 tick 当前时刻评估一次。
- 因果事件仍保留每 tick 128 条硬预算，生命周期仍保留 256 次尝试预算。严格跨来源排序适用于预算内；因果风暴超出预算时旧 pending 留到下一 tick，而已物化的非因果条目继续完成，本版本不宣称任意负载下的无限严格排序。
- 新增跨 temporal/random/causal、历史 causal 与未来 lifecycle、因果 peek 稳定性、生命周期条件延迟评估回归；相关定向验证 **78 passed**，完整 Integration **11 passed, 2 skipped**，前端 **97 passed** 且 lint/build 通过。完整 Unit 运行达到 **784 passed**，另有 1 个由并行 LLM/API 改动产生的 deadline 浮点边界失败，不属于本时间队列改动。
- `VERSION`、Python 包版本和前端 package/lock 四处统一升级到 `0.39.0`。

## v0.38.0 发布说明

本版本修复长对局、大步推进时间时历史事件被压平到当前时刻的问题：

- Orchestrator 统一计算事件有效时间，将 DLC 时间边界、随机事件和因果事件携带的 `generated_at` 限定在 `0..当前模拟时间`，并用于因果观察、条件上下文、节奏准入和因果世界命令。
- 历史事件触发的因果延迟从真实发生时间开始计算；例如一次推进到第 5 天时，第 1 天事件的 1 天延迟后果会在第 2 天到期，而不会被错误推迟到第 6 天。
- 玩家 Issue 的 `created_at`、截止期与逾期状态，以及因果结果反馈的 `created_at/due_at` 均保留来源事件时间；已经逾期的延迟反馈会在当前 catch-up tick 交付。
- Agent 唤醒负载新增规范化 `generated_at`，Rhythm 冷却按第 1、2、3……天的历史发生时间逐条判定，不再把同批历史事件视为同时发生。
- 新增长跳时回归，覆盖因果 pending 边界、Issue 逾期、Rhythm 两天冷却、世界命令账本时间和延迟反馈立即补发；四处产品版本统一升级为 `0.38.0`。
- 当前仍保留时间边界、随机事件、因果事件的既有阶段顺序；本版本修复各事件自身的权威时间语义，但不宣称已实现跨来源的全局时间归并队列。

## v0.37.0 发布说明

本版本继续推进 DLC Studio 快速创作流程：

- 新增完整中文“玩家身份”编辑器，可配置 identity_id、显示名称、绑定 Actor，以及 entity/agent 的 direct / command 控制语义；支持从已有 Agent 智能填充，并在保存前检查引用、重复项和必填字段。
- 新增实体、Actor、Agent 批量创建工作台，支持竖线或 Tab 表格输入、中文示例、缺失实体引用检查、跳过/覆盖冲突策略和原子 Agent/Actor 套件生成。
- 批量写入和玩家身份编辑继续复用 Studio 的 Content Diff、确认、历史、撤销/重做与 Registry 保存链路，不直接覆盖 DLC 文件。
- 七步可玩性审计卡片新增“查看/去补齐”导航，可直接定位玩家身份、界面、Agent、行动、Issue、事件、因果链、时间、结果反馈、目标与结局编辑页。
- 前端新增 9 项工具回归测试；AgentEra 四处产品版本统一升级为 0.37.0。长期 Goal 保持 active，下一阶段继续做真实 API 多轮 Agent 与长对局世界模拟压力验收。

## v0.35.0 发布说明

本版本完成长期玩法框架 Goal 的 P2.4“玩家可理解的结果反馈”：

- 新增 Generation-bound `OutcomeFeedbackContract` / `OutcomeFeedbackRuntime`，统一处理自动即时反馈、延迟报告、结案总结、指标变化原因与事后因果揭示。
- 反馈规则复用通用因果条件和 DLC 静态引用校验；最近反馈、待交付队列与幂等回执均有界，并支持存档、热重载、Generation 归档和回滚。
- Orchestrator 将 Action、Decision、Case、因果事件、生命周期、节奏与 Tick 结果接入反馈运行时；新增 `GET /api/outcome-feedback`，所有公开字段使用玩家安全投影。
- 主界面新增结果反馈面板；DLC Studio 新增完整中文“结果反馈”编辑器，并修复移动规则时展开状态错位及移动端 Manifest Dashboard 裁切。
- `example_frontier` 升级到 v1.14.0，提供即时、延迟和因果揭示参考规则；AgentEra 四处产品版本统一升级为 `0.35.0`。
- 长期 Goal 继续保持 active；P2.4 已完成，下一阶段进入跨题材 DLC 的端到端可玩性验收与剩余框架缺口收敛。

## v0.34.0 发布说明

本版本完成长期玩法框架 Goal 的 P2.3：

- 新增 Generation-bound `RhythmContract` / `RhythmRuntime`，统一处理活跃 Issue 上限、注意力预算与按游戏日恢复、排队上限、规则优先级、冷却、互斥组和全局/规则平静期。
- Issue 创建接入节奏准入与排队；运行时支持按时间升级紧迫度、按通用因果条件降级，以及高潮阶段对 Issue、同组 Case 和 DecisionRequest 的确定性收束。
- 随机事件进入世界前复用节奏规则，可抑制低优先级噪声；critical 绕过、反馈上限和安全公开状态均由 DLC 配置。
- 节奏契约和运行时状态进入 DLC 初载、热重载、Generation 归档、历史回滚、重启恢复与存读档，并新增 `GET /api/rhythm`。
- DLC Studio 新增完整中文“节奏导演”编辑器；`example_frontier` 升级到 v1.13.0，提供危机升级、条件降级、高潮收束、稳定化、常规报告和随机流言抑制示例。
- AgentEra 发布版本统一升级为 `0.34.0`；长期 Goal 保持 active，P2.3 已完成，下一阶段进入 P2.4“玩家可理解的结果反馈”。

## v0.33.0 发布说明

- 新增 Generation-bound `LifecycleContract` 与 `LifecycleRuntime`，覆盖动态 Actor、动态 Agent、职位、出生/死亡/晋升/继任/空缺、实体转化和组织分合。
- 七类生命周期操作统一展开为原子 WorldCommand Batch；Receipt、序列、冷却、上升沿 Latch、Actor Tombstone、职位状态和玩家反馈可存档恢复，并支持热重载、Generation Archive 与回滚。
- 动态 Agent 从静态来源 Agent 继承模型选择、多轮预算、工具、记忆和 Provider 路由，并在创建时迁移记忆；退休时同步停用，避免孤立 Agent。
- DLC Registry 新增生命周期结构、跨目录引用和时间引用校验；Orchestrator Tick、存档、读档和公共状态已接入生命周期运行时。
- DLC Studio 新增完整中文“动态生命周期”编辑器；`example_frontier` 升级到 `1.12.0`，AgentEra 四处产品版本同步到 `0.33.0`。

长期 Goal 继续保持 active；P0、P1.1-P1.6、P2.1 与 P2.2 已完成，下一阶段进入 P2.3。

## v0.32.0 发布说明

- 新增 Generation-bound `TemporalContract` 与 `TemporalRuntime`，支持 DLC 自定义历法、非均匀月份、星期、普通/跨年季节和回合/会议/任务/自定义周期。
- 新增八类时间边界、确定性事件 ID、Agent/Issue 投影，以及条件型和边界型自动暂停；暂停回执支持冷却、仅一次、上升沿、存档恢复和热重载继承，公开状态不泄露隐藏条件值。
- 新增 `GET /api/time-system` 和 Tick 时间投影；主界面显示 DLC 日期、季节、活跃周期与明显的中文自动暂停原因，保留旧模拟时间作为兼容信息。
- DLC Studio 新增“历法与周期”详细中文编辑器，覆盖月份排序、跨年季节、周期、边界事件、Issue/Agent 引用、Payload JSON、自动暂停和通用因果条件，并纳入导入导出、Diff、History 与撤销/重做。
- `example_frontier` 升级到 `1.11.0`；AgentEra 四处产品版本同步到 `0.32.0`。

长期 Goal 继续保持 active；P0、P1.1-P1.6 与 P2.1 已完成，下一阶段进入 P2.2 动态生命周期。P2.2-P2.4 尚未完成。

## v0.31.0 发布说明

- 新增 Generation-bound `DerivedMetricCatalog`，DLC 可用 17 类确定性公式节点组合直接世界来源、聚合来源和其他派生指标；Registry 拒绝缺失依赖、循环、超限结构、非有限数值与非法引用。
- Objective DSL、因果条件、Agent Prompt、主界面和 UI Manifest 共用同一指标目录；`player/ui/agent/rule/objective` 五类受众彼此隔离，Catalog 热切换后既有 Agent 也只读取当前 Generation。
- 新增安全派生指标 API 和主游戏指标面板；公开接口只暴露 `player` 受众结果，不返回公式、Metadata、隐藏指标或底层异常。
- DLC Studio 新增完整中文派生指标编辑器，覆盖全部公式树节点、世界来源、聚合方式、Catalog/Filter 辅助、依赖图与循环/缺失/结构检查，并接入导入导出、Diff、History 和撤销/重做。
- 详细中文设置中心新增知识上下文与派生指标上下文的条数/字符预算；`example_frontier` 升级至 `1.10.0`，用统一锚定库存价值、实体指标和 Actor 属性计算“边境综合战备度”，并接入目标、失败条件、规则和 Dashboard。AgentEra 四处产品版本同步到 `0.31.0`。
- 发布门禁：全量 Python `665 passed, 2 skipped`、前端 `58 passed`，Python compile、ESLint、Vite production build、`git diff --check`、新增/未跟踪行凭据与乱码扫描、四处版本一致性检查和 `example_frontier` Registry `0 issues` 均通过；2 个外部 API 测试因当前进程未注入 `AGENTERA_TEST_*` 环境变量而按设计跳过，未发起外部网络请求。

长期 Goal 继续保持 active；P0 与 P1.1-P1.6 已完成，下一阶段进入 P2.1 DLC 日历、季节、回合/会议/任务周期和自动暂停条件。P2 尚未完成。

## v0.30.0 发布说明

- 新增 SQLite 权威三层知识运行时，显式区分 `world_truth`、`actor_belief` 与 `player_knowledge`，统一记录来源、置信度、新鲜/陈旧/过期、五级保密、作用域、失真、状态、证据和安全 Metadata。
- 新增知识传播与调查状态机；过期来源拒绝传播。调查完成结果策略在 authored-effect 阶段提前验证来源/目标层、holder、置信度倍率、保密、失真和值覆盖，并禁止把调查结果写回 World Truth。
- 通用条件目录从 18 扩展到 25 项，通用效果目录从 18 扩展到 23 项；Runtime、World Command、Registry、静态分析、Dry Run、Generation 生命周期与事务回滚使用同一知识契约。
- Agent Prompt 只投影当前 Agent 绑定 Actor 的主观认知，不暴露 World Truth 或其他 Actor Belief；玩家公共投影不返回隐藏层内容和隐藏层计数。
- DLC Studio 为三层知识、传播、调查和结果策略提供详细中文字段说明、安全边界、即时校验和安全序列化；`example_frontier` 升级到 `1.9.0`，演示从真相、失真认知、传播到调查核验的完整链路。
- AgentEra 四处产品版本同步到 `0.30.0`。发布门禁通过全量 Python `637 passed, 2 skipped`、前端 `50 passed`、Python compile、ESLint、Vite production build、`git diff --check`、新增行凭据/乱码扫描和 `example_frontier` Registry 零问题校验；2 个外部 API 测试因当前进程未注入 `AGENTERA_TEST_*` 环境变量而按设计跳过，未发起外部网络请求。

长期 Goal 继续保持 active；P1.5 已完成，下一阶段进入 P1.6 DLC 派生指标与聚合公式，P2 仍未完成。

## v0.29.0 发布说明

- 新增题材无关的 18 种因果效果契约与物化器，覆盖资源、状态、持续事件、统一锚值转移、实体生命周期、Agent 启停、关系、成员职位、Issue、玩家知识、Objective、Action 解锁和阶段切换；每批效果有明确上限、中文错误与安全 JSON 边界。
- `WorldState` 将因果效果与 Gameplay Effect Runtime 纳入同一原子事务，命令使用稳定 `causal:{event_id}:{command_index}` ID；失败整批回滚，重启恢复和重复提交保持幂等。SQLite 权威状态新增 Agent 启停、Actor 动态成员关系、Issue、Knowledge、Objective 进度、Action Unlock 与 Phase。
- Orchestrator 将权威效果状态投影到 Agent、Actor、Issue、Objective 和 Action Manager；停用 Agent 不再接收或执行任务。生产调度、Dry Run、快照恢复和 Generation Runtime 使用相同效果语义。
- Registry 支持同一规则按顺序验证 `create_entity` 后续引用，并检查 Agent、Actor、关系类型、Issue 类型、Objective、Action、Scope 与资源引用。通用条件新增第 18 种 `phase` 来源，可读取 `phase_id` 或 `metadata.xxx`。
- DLC Studio 用完整中文动态编辑器覆盖全部 18 种效果和 18 种事实来源，提供分类说明、字段约束、Catalog/datalist、JSON 编辑、审计原因、原子事务提示与同批动态实体引用。
- `example_frontier` 升级到 `1.8.0`，演示动态实体创建后同批建立关系/职位、创建 Issue、揭示知识、推进目标、解锁行动和两阶段切换；AgentEra 四处产品版本同步到 `0.29.0`。
- 发布门禁：全量 Python `620 passed, 2 skipped`，因果/运行时专项 `120 passed`，前端 `46 passed`；Python compile、ESLint、Vite production build、`git diff --check` 均通过，`example_frontier` Registry 为 `0 issues`。2 个外部 API 测试因当前进程未注入 `AGENTERA_TEST_*` 环境变量而按设计跳过，未发起外部网络请求。
- Studio 视觉 QA：桌面端完成两条示例规则的 8/32 与 4/32 效果回显验证；390px 移动端效果字段与工具栏切换为单列、删除按钮全宽，效果卡片及页面整体无水平溢出。

长期 Goal 继续保持 active；P1.4 已完成，下一阶段进入 P1.5 三层知识系统，P1.6 与 P2 仍未完成。

## v0.28.0 发布说明

- 新增 `multiagent_framework/causal_conditions.py` 通用因果条件契约，完整支持 17 种事实来源、12 种操作符、5 种聚合方法和 7 种聚合来源；Runtime、Orchestrator、Registry、静态分析与 Studio Dry Run 使用同一确定性求值器。
- 因果运行时增加有界、脱敏事件历史并升级快照 Schema 至 `2.0`；历史不保留 Payload。`player_knowledge` 只能通过显式 Provider 读取，没有 Provider 时 fail-closed，避免泄露世界真相。
- Registry 增加实体字段/标签/状态、Actor 属性/状态/Trait/成员关系、关系类型与端点、目标和聚合来源等跨引用检查；Dry Run 可投影实体 Metadata、Actor、关系、持续事件与初始历史。
- DLC Studio 将旧三类型单行条件表单替换为详细中文动态卡片编辑器，支持全部条件来源与操作符、数值/文本/布尔/JSON 列表、历史窗口、聚合和 Filter JSON；条件组、模板和规则统一走安全序列化。
- `example_frontier` 升级至 `1.7.0`，新增 `frontier_response_readiness`、`frontier_stabilization_ready`、`frontier_deterministic_follow_up` 与两阶段 `patrol_report → frontier_response_started → frontier_stabilization` 示例；AgentEra 四处产品版本同步至 `0.28.0`。
- 本地发布门禁通过 600 个 Python 测试（另有 2 个外部 API 环境门禁测试因当前进程未注入凭据而按设计跳过）、41 个前端测试、155 项因果 focused suite、Python 编译、ESLint、生产构建和 `example_frontier` Registry 零问题校验。外部 API 测试仅从 `AGENTERA_TEST_*` 环境变量读取临时配置，未注入时不会发起网络调用。

长期 Goal 继续保持 active；P1.3 已完成，下一阶段进入 P1.4 通用因果效果扩展，P1.5-P1.6 与 P2 仍未完成。
## v0.27.0 发布说明

- 新增 DLC 自定义关系契约：`content.json.relationship_types` 可声明稳定类型 ID、中文名称与说明、有向/无向语义、强度上下界与默认值、默认公开性、是否允许到期、多维数值、图样式和安全 Metadata。旧 `subordinate` / `affiliated` 类型继续兼容，并允许 DLC 用同 ID 显式覆盖展示契约。
- 关系边统一支持 `strength`、`visibility`、`starts_at`、`expires_at`、`values`、`note` 和 `metadata`；无向边规范化端点顺序，时间窗口决定 active/inactive，所有写入均由当前 Generation 的 `RelationshipCatalog` 校验。
- WorldState 新增 SQLite `relationship_type_catalog`，旧 `entity_relations` 表自动补齐新列；目录与关系边随 Generation archive、热重载、回滚和重启恢复，发布过程保持原子一致。世界状态导出 Schema 升级为 `2.1`。
- 新增 `GET /api/relationship-types`、关系类型详情、关系列表/实体关系查询以及关系新增、更新和删除接口；主界面关系图从动态目录读取颜色、标签、虚线、箭头和维度，不再写死朝堂关系类型。
- DLC Studio 新增详细中文“关系类型”编辑器，并扩展关系边表单；关系类型与边均进入导入导出、即时校验、Content Diff、历史、撤销/重做和高级 JSON 工作流，删除类型前会检查草稿中的边引用。
- `example_frontier` 升级至 `1.6.0`，新增 `command`、`trust`、`rivalry` 三类关系及有向、无向、到期、多维值、样式示例；AgentEra 四处产品版本同步至 `0.27.0`。
- 本地发布门禁通过 592 个 Python 测试（另有 2 个外部 API 环境门禁测试因当前进程未注入凭据而按设计跳过）、35 个前端测试、Python 编译、ESLint、生产构建、示例 DLC Registry 校验、编码与凭据扫描和 Git 差异检查。外部 API 连通测试仅在显式注入 `AGENTERA_TEST_*` 环境变量时运行。

长期 Goal 继续保持 active；P1.2 已完成，下一阶段进入 P1.3 因果条件与效果表达力。

## v0.26.0 发布说明

- 新增 Generation-bound 通用 Actor 契约，将角色数据从固定 `Official`/Agent 表达中解耦，覆盖属性、Trait、需求、目标、能力、身份、成员关系和状态。
- Registry 校验 Actor、实体、能力、组织与 Agent 引用以及跨 DLC ID 冲突；旧 Agent 继续自动投影同 ID Actor，显式未知 Actor 会被拒绝。
- Runtime、Generation archive、热重载、回滚与存档均携带 Actor 定义和动态状态；热重载只保留仍符合新 Schema 的状态值。
- 新增安全 Actor 列表/详情 API、Actor Prompt 变量和 DLC Studio 中文 Actor 编辑器；Agent 编辑器支持显式绑定 Actor，Prompt 不注入 Metadata。
- `example_frontier` 升级至 `1.5.0`，包含两个 Agent-bound Actor 和一个 Actor-only 角色。
- 发布版本统一升级为 `0.26.0`。本地门禁通过 576 个 Python 测试（另有 2 个外部 API 环境门禁测试因当前进程未注入凭据而按设计跳过）、34 个前端测试、Python 编译、ESLint、生产构建、示例 DLC Registry 校验、凭据扫描与 Git 差异检查。

长期 Goal 继续保持 active；P1.1 已完成；P1.2 的完成状态见 v0.27.0 发布说明。

## v0.25.0 发布说明

本版本完成长期玩法框架 Goal 的 P0.6，并完成 P0 阶段：

- 新增 Generation-bound DLC 能力注册表，`content.json.capabilities` 成为玩家、普通 Agent 与 Router 的共同权威来源；能力 ID、别名、受众、目标、参数和 Action 引用均由 Registry 验证。
- 普通 Agent 通过稳定工具 `invoke_capability` 调用能力，Router LLM 通过 `route_capability` 完成目标消歧；框架内部使用 `INVOKE_CAPABILITY` 协议，不允许 DLC 注入 Python、React、函数名或任意 handler。
- 支持 `noop`、`message`、`world_action`、`contract_action`、`broadcast` 五类白名单 handler；玩家消息目标会在入队前整体校验，未知能力进入独立 dead letter，不产生隐式旧工具回退。
- 新增 `GET /api/capabilities` 与 `POST /api/capabilities/{capability_id}/invoke`；主界面新增动态能力面板，根据当前 Generation 自动生成目标和参数表单。
- DLC Studio 新增详细中文“能力”编辑器，覆盖 handler、受众、目标选择、参数、Action 引用、别名和说明，并保留高级 JSON、即时校验、Diff、撤销/重做与导入导出工作流。
- 能力目录进入 Generation 发布、加密 archive、热重载、rollback 与 restart recovery；无能力目录的旧 DLC 保持兼容，有能力目录时不再恢复旧 Agent 写工具。
- `example_frontier` 升级到 v1.4.0，示范等待、报告、广播命令、世界操作和契约巡逻五类能力。
- 发布版本统一升级为 `0.25.0`。发布门禁通过 567 个 Python 测试（另有 2 个外部 API 环境门禁测试因当前进程未注入凭据而按设计跳过）、32 个前端测试、Python 编译、ESLint、生产构建、示例 DLC Registry 校验、凭据扫描与 Git 差异检查。

长期 Goal 继续保持 active；P0 已完成，后续进入 P1 通用 Actor、关系、规则表达和知识层建设。

## v0.24.0 发布说明

本版本完成长期玩法框架 Goal 的 P0.5：

- 新增题材无关 `UIManifest` Schema、默认值、归一化和安全验证，严格限制导航、Widget、布局、区域和字段能力，拒绝 URL、组件名、HTML、CSS、脚本、不安全文本、未知能力与重复 ID。
- UI Manifest 由主 DLC 独占，纳入首次加载、热重载、Generation 发布、加密归档、历史回滚、旧归档兼容和 Orchestrator 发布，并新增 `GET /api/ui-manifest`。
- React 主界面改为能力驱动：标题、短标识、术语、主/应用导航、Dashboard 布局与 Widget、Issue 字段和实体详情均读取当前 Generation；API 失败时使用安全默认界面。
- DLC Studio 新增详细中文 UI Manifest 编辑器，支持可视化白名单配置、高级 JSON、即时校验、Content Diff、导入导出以及撤销/重做。
- `example_frontier` 升级到 v1.3.0，并提供完整边境题材 Manifest 示例。
- 发布版本统一升级为 `0.24.0`；发布门禁通过 544 个 Python 测试（另有 2 个外部 Provider 环境门禁测试因当前进程未注入凭据而跳过）、31 个前端测试、Python 编译、ESLint、生产构建、示例 DLC Registry 校验、凭据扫描与 Git 差异检查。

长期 Goal 继续保持 active；下一阶段为 P0.6 DLC 行动/能力注册表，使玩家 UI、Router 与 Agent 工具由同一能力目录驱动。

## v0.23.0 发布说明

本版本完成 DLC 驱动玩法框架的 P0.4：

- 新增权威 `ObjectiveManager`、主/可选/失败目标、有序/独立里程碑、嵌套条件 DSL，以及胜利/失败/中立结局。
- 目标与结局绑定主 DLC 和 Generation，支持 Registry 引用校验、热重载、归档、回滚、运行时快照、手动存档与重启恢复。
- 新增 `GET /api/objectives`、`GET /api/endings`、主界面目标面板，以及 DLC Studio 目标/结局中文可视化编辑器。
- `example_frontier` 升级为带巡逻里程碑、15 天失败条件和优先级结局的可玩 Campaign 示例。
- 新增可选外部 OpenAI-compatible 连接 Smoke Test；模型可自动拉取，凭据只从临时环境变量读取且不落盘。
- 版本身份同步为 `0.23.0`；本地发布门禁通过 **535 个 Python 测试（另有 2 个未配置外部凭据时按设计跳过）**、**27 个前端测试**、Python 编译、前端 Lint/Build、示例 DLC Registry 校验、凭据模式扫描和 Git Diff 检查。

## v0.22.0 发布说明

本版本完成 DLC 驱动玩法框架的 P0.3，并将持续增加的运行参数纳入可视化管理：

- 新增 DLC `issue_types`、通用 Issue / Case / DecisionRequest 生命周期、动态选项、Action 决策执行、期限/过期、幂等和存档/Generation 生命周期。
- 新增详细中文设置中心及安全设置 API，覆盖 Provider、模型 Profile、三级路由、Agent 多轮预算、记忆压缩、世界模拟和只读运维状态。
- 新增原子配置保存与失败回滚、Generation 乐观锁、Provider 主机变更确认，以及 Session Key 不回传、不落盘的安全边界。
- 版本身份同步为 `0.22.0`，并补充后端、前端 Schema 工具和事务失败回归测试。
- 本地发布门禁通过 **520 个 Python 测试**、**26 个前端测试**、Python 编译、前端 Lint/Build、设置 API 密钥泄漏 Smoke 和 Git Diff 检查。

## Reusable causal templates (v0.17.0)

DLC bundle content can define package-local `causal_condition_groups` and `causal_rule_templates`. A causal rule may select one template and multiple condition groups. Runtime precedence is `engine defaults < template.rule < authored rule`; condition order is template conditions, referenced groups in author order, then inline conditions. References are expanded by one pure deterministic backend function before Registry validation, static analysis, draft Dry Run, and runtime materialization. Templates and groups are not recursive and never cross DLC package boundaries.

DLC Studio provides dedicated condition-group and causal-template tabs, keyed import/export Diff and history, template inheritance versus explicit override controls, multi-group selection, resolved counts, and unsaved-draft preview. Release `0.17.0` is verified with 446 Python tests and 15 frontend tests.

## Causal graph replay workbench (v0.18.0)

The DLC Studio causal-rule preview now includes a resolved edge inspector rather than a full node-layout canvas. Authors can filter edges by event type, rule ID, template ID, or condition-group ID, then inspect source-to-child routing, priority, delay, probability, target mode, resolved condition/effect counts, and reusable authoring provenance. Node summaries include incoming and effective outgoing rules, including wildcard routes.

Every deterministic Dry Run returns an `agentera-causal-replay-v1` fixture containing only the source event, bounded replay controls, analysis fingerprint, result digest, and summary baseline. The digest covers the resolved analysis summary, simulated nodes, event edges, pending work, and Dry Run summary; it deliberately excludes the fixture itself, full DLC content, Prompt text, API keys, and model configuration.

`POST /api/dlcs/{dlc_id}/causal-events/replay` compares an imported baseline with the current content or an unsaved Studio draft. It distinguishes resolved-rule drift (`analysis_fingerprint`) from seed-fact or execution drift (`result_digest` and summary fields). Import and replay do not save the fixture, write DLC files, reload a generation, or mutate live world state. Release `0.18.0` is verified with **451 Python tests**, **16 frontend tests**, frontend lint/build, Prompt contracts at **100%**, and deterministic trajectory replay at **100%**.

## Agent 经济重放场景 (v0.19.0)

ActionAgent 现在使用三个类型化经济工具：`produce_resource`、`consume_resource` 和 `transfer_anchored_value`。生产与消费只接受 DLC 资源目录中的资产资源；跨实体、跨资源调拨统一填写 `base_value`，运行时按双方 `anchor_rate` 换算借贷数量，并通过原子批次确保余额不足时整批回滚。旧 `update_inventory` 继续保留在全量工具集中以兼容 Tianji、调试接口和旧调用方，但不再暴露给 ActionAgent。

`request_action` 新增可选 `causal_event_type`。只有调用方显式提供合法事件类型时，Orchestrator 才会通过 `action_causal_bridge.py` 发布确定性、脱敏的行动结果摘要；默认行为不会自动扩展因果链。摘要只包含行动类型、执行者、目标、成功状态、耗时和经济变化聚合，不包含 Prompt、FC 参数、完整轨迹、叙事正文或凭据。

新的确定性长场景覆盖三轮 ActionAgent 工具调用、生产/消费、跨锚值守恒转移、多个因果分支、零延迟下游、ongoing event 到期、SQLite 与因果快照重启恢复、命令重放幂等，以及 Replay Fixture 对规则漂移和种子/执行漂移的分类。Release `0.19.0` 通过 **464 个 Python 测试**、**16 个前端测试**、Python 编译、前端 lint/build、Prompt contracts **100%**、确定性 trajectory replay **100%** 和 Git diff 检查。

## DLC player identity gameplay contract (v0.20.0)

Release `0.20.0` introduces a theme-neutral `GameplayContract` for the primary DLC. It validates player identity and controlled entity/Agent references, distinguishes direct control from command authority, excludes directly controlled Agents from autonomous LLM construction, and lets explicit gameplay metadata override legacy `role=player` markers. The contract is carried through initial load, generation snapshots, encrypted archive blueprints, hot reload, rollback, Orchestrator player-recipient routing, and `GET /api/gameplay`.

The bundled frontier example now declares a non-imperial station-commander contract while keeping its player-like Agent role set to `operator`, proving that runtime behavior no longer depends on a theme-specific role string. The React shell consumes the public contract for package title, identity, control count, and view labels. This is the first P0 gameplay-framework slice; generic actions, issues/objectives, permissions, and a complete DLC UI manifest remain future work. Release `0.20.0` is verified with **472 Python tests**, **16 frontend tests**, Python compile, frontend lint/build, Prompt contracts **100%**, deterministic trajectory replay **100%**, and Git diff checks.

## 跨题材 DLC 可玩性验收 (v0.36.0)

`multiagent_framework.dlc_playability` 提供题材无关的七步静态审计。`GET /api/dlcs` 与 `GET /api/dlcs/{id}` 的每个包响应都包含 `playability`：`passed_count / total_count`、中文摘要、缺口和定位到具体 Action、Agent、Issue、因果规则、反馈、目标或结局的证据。Registry `validate()` 仍只回答“配置是否合法”，`audit_playability()` 回答“配置是否声明了可信的玩家循环”，两者不会互相替代。

DLC Studio 默认打开“可玩性验收”页签，显示 7 项通过状态、中文补齐建议和配置证据。该面板用于作者快速发现“JSON 合法但玩法链断开”的问题；静态 7/7 仍必须配合真实运行测试。

新增默认禁用的 `dlcs/example_orbital` 作为第二题材参考包。它不修改 Python 或 React 核心，仅通过 DLC JSON 声明轨道站长、维护工程师、冷却环报告、维修额度锚定成本、耗时巡查、Agent 唤醒、延迟因果世界效果、结果反馈、目标与胜利结局。集成测试真实执行以下证据链：

```text
Agent 报告 → Issue / DecisionRequest → 锚定成本行动
→ wake_agent → 推进 1 游戏日 → causal world effect
→ outcome feedback → objective completed → victory ending
```

`example_frontier` v1.15.0 同步补齐 `frontier_patrol_started` 到后续响应事件的 Action→Causal 桥接；它与 `example_orbital` 均通过 Registry 校验和七项可玩性审计。
