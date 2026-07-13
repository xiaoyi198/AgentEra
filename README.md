# AgentEra 世界模拟器

> 当前版本：**v0.17.0**

AgentEra 是一个基于 **FastAPI + React + SQLite + LLM Function Calling** 的多 Agent 世界模拟器。内置“大明朝堂”内容包，玩家可以扮演皇帝处理奏折、下达旨意，并观察官员、机构、资源、关系和延迟事件在持续世界中的演化。

项目不仅包含游戏运行时，也提供 DLC Studio、模型路由、多轮记忆、真实模型轨迹评测和安全晋级门禁，便于快速创建新的世界与 Agent 群体。

## 核心能力

- **多轮 Agent**：支持多轮 Function Calling、工具预算、终止条件、异常降级和调用轨迹。
- **多层记忆**：支持短期记忆、长期摘要、承诺状态、压缩恢复和跨进程持久化。
- **世界模拟器**：实体、关系、库存、数值、随机事件、延迟行动和 SQLite 持久化。
- **统一价值锚**：资产库存使用 `quantity × anchor_rate` 计算统一基础价值。
- **经济不变量监控**：汇总当前价值、累计发行/销毁、转移守恒与因果效果拒绝/故障，提供安全 API、Prometheus 指标和世界状态仪表盘。
- **DLC Studio**：编辑 DLC、实体、Agent、关系、事件、资源、人格、Prompt、模型绑定和时间规则。
- **Causal rule workbench**: build typed conditions with entity/resource selectors, diagnose conflicts, and deterministically preview unsaved draft chains without changing live world state.
- **Reusable causal authoring**: define package-local condition groups and rule templates, retain references or materialize explicit overrides in DLC Studio, and use identical expansion semantics for validation, Dry Run, reload, rollback, and production runtime.
- **事务化因果世界效果**：因果规则可原子调整资源、状态、延迟事件和跨实体锚定价值转移；失败时整体回滚，重启重放保持幂等。
- **晋级工作台**：导入已批准 Promotion Gate，快速生成 DLC/Agent cohort mapping，执行 generation-bound Preview/Apply。
- **Generation 观测与安全回滚**：加密保留模型配置历史，按代聚合调用质量，并将历史配置作为新的递增 Generation 原子恢复。
- **Generation 热重载**：DLC、Prompt、模型与时间规则按代发布；在途工作继续使用开始时的版本语义。
- **任务模型路由**：可按全局任务、DLC、Agent 和显式 Profile 选择不同模型。
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


## Reusable causal templates (v0.17.0)

DLC bundle content can define package-local `causal_condition_groups` and `causal_rule_templates`. A causal rule may select one template and multiple condition groups. Runtime precedence is `engine defaults < template.rule < authored rule`; condition order is template conditions, referenced groups in author order, then inline conditions. References are expanded by one pure deterministic backend function before Registry validation, static analysis, draft Dry Run, and runtime materialization. Templates and groups are not recursive and never cross DLC package boundaries.

DLC Studio provides dedicated condition-group and causal-template tabs, keyed import/export Diff and history, template inheritance versus explicit override controls, multi-group selection, resolved counts, and unsaved-draft preview. Release `0.17.0` is verified with 446 Python tests and 15 frontend tests.
