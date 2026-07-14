# AgentEra 世界模拟器

> 当前版本：**v0.22.0**

AgentEra 是一个基于 **FastAPI + React + SQLite + LLM Function Calling** 的多 Agent 世界模拟器。内置“大明朝堂”内容包，玩家可以扮演皇帝处理奏折、下达旨意，并观察官员、机构、资源、关系和延迟事件在持续世界中的演化。

项目不仅包含游戏运行时，也提供 DLC Studio、模型路由、多轮记忆、真实模型轨迹评测和安全晋级门禁，便于快速创建新的世界与 Agent 群体。

## 核心能力

- **多轮 Agent**：支持多轮 Function Calling、工具预算、终止条件、异常降级和调用轨迹。
- **多层记忆**：支持短期记忆、长期摘要、承诺状态、压缩恢复和跨进程持久化。
- **世界模拟器**：实体、关系、库存、数值、随机事件、延迟行动和 SQLite 持久化。
- **统一价值锚**：资产库存使用 `quantity × anchor_rate` 计算统一基础价值。
- **类型化 Agent 经济动作**：ActionAgent 使用生产、消费和统一锚值转移工具；跨资源调拨按基础价值换算并原子提交。
- **行动因果桥接**：`request_action` 可显式选择把有界、脱敏的 `ActionResult` 摘要发布为因果源事件。
- **经济不变量监控**：汇总当前价值、累计发行/销毁、转移守恒与因果效果拒绝/故障，提供安全 API、Prometheus 指标和世界状态仪表盘。
- **DLC Studio**：编辑 DLC、实体、Agent、关系、事件、资源、人格、Prompt、模型绑定、时间规则和通用行动。
- **DLC 玩法契约**：DLC 可声明题材无关的玩家身份、演员和直接/命令控制对象；运行时、热重载、回滚、API 与主界面按同一 Generation 切换。
- **DLC 通用行动契约**：DLC 可声明参数、目标、控制权限、前置条件、锚定成本、权威耗时、冷却和组合结果；预览无副作用，执行采用原子世界命令与 invocation 幂等回放。
- **Causal rule workbench**: build typed conditions with entity/resource selectors, diagnose conflicts, and deterministically preview unsaved draft chains without changing live world state.
- **Reusable causal authoring**: define package-local condition groups and rule templates, retain references or materialize explicit overrides in DLC Studio, and use identical expansion semantics for validation, Dry Run, reload, rollback, and production runtime.
- **Causal graph replay workbench**: inspect resolved rule edges and authoring provenance, export secret-free deterministic replay fixtures, and compare old baselines against the current unsaved Studio draft without mutating saved content or live runtime.
- **事务化因果世界效果**：因果规则可原子调整资源、状态、延迟事件和跨实体锚定价值转移；失败时整体回滚，重启重放保持幂等。
- **晋级工作台**：导入已批准 Promotion Gate，快速生成 DLC/Agent cohort mapping，执行 generation-bound Preview/Apply。
- **Generation 观测与安全回滚**：加密保留模型配置历史，按代聚合调用质量，并将历史配置作为新的递增 Generation 原子恢复。
- **Generation 热重载**：DLC、Prompt、模型与时间规则按代发布；在途工作继续使用开始时的版本语义。
- **任务模型路由**：可按全局任务、DLC、Agent 和显式 Profile 选择不同模型。
- **详细中文设置中心**：在主界面可视化管理 Provider、模型 Profile、三级任务路由、Agent 多轮预算、记忆压缩、世界 Tick 与安全运维状态；支持后端校验、变更预览、Generation 乐观锁和热更新。
- **通用 Issue / Case / DecisionRequest**：DLC 可声明题型、证据、期限、紧迫度和动态选项，运行时支持创建、决策、执行 Action、关闭、过期、幂等与存档恢复。
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
- 多轮记忆：控制短期记忆容量、压缩阈值、保留条数、结构化记忆容量/检索/字符预算和压缩温度。
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
- 通用行动与 Issue / Case / DecisionRequest 题型
- DLC/Agent 显式模型绑定
- 因果规则的结构化条件与事务化世界效果（资源、状态、ongoing event、锚值守恒转移）

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

发布时三处版本必须保持一致。


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
