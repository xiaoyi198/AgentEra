# AgentEra 世界模拟器

> 当前版本：**v0.10.0**

AgentEra 是一个基于 **FastAPI + React + SQLite + LLM Function Calling** 的多 Agent 世界模拟器。内置“大明朝堂”内容包，玩家可以扮演皇帝处理奏折、下达旨意，并观察官员、机构、资源、关系和延迟事件在持续世界中的演化。

项目不仅包含游戏运行时，也提供 DLC Studio、模型路由、多轮记忆、真实模型轨迹评测和安全晋级门禁，便于快速创建新的世界与 Agent 群体。

## 核心能力

- **多轮 Agent**：支持多轮 Function Calling、工具预算、终止条件、异常降级和调用轨迹。
- **多层记忆**：支持短期记忆、长期摘要、承诺状态、压缩恢复和跨进程持久化。
- **世界模拟器**：实体、关系、库存、数值、随机事件、延迟行动和 SQLite 持久化。
- **统一价值锚**：资产库存使用 `quantity × anchor_rate` 计算统一基础价值。
- **DLC Studio**：编辑 DLC、实体、Agent、关系、事件、资源、人格、Prompt、模型绑定和时间规则。
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

## DLC 开发

DLC 可以声明：

- 实体与初始库存
- Agent、机构和上下级关系
- 人格与 Prompt 模板
- 初始事件、随机事件和延迟事件
- 资源定义与统一价值锚
- 行动耗时规则
- DLC/Agent 显式模型绑定

DLC Studio 支持草稿校验、发布、Generation 归档、热重载和回滚。运行时不会因为新版本发布而重算已经承诺的延迟结果。

## 模型策略晋级

生产模型策略采用分阶段流程：

```text
真实模型轨迹 → 自动检查 → 独立人工评审 → cohort 排名
→ 环境规模/运行门禁 → Promotion Gate
→ Preview → 指纹确认 → 原子 Apply → Generation 热重载
```

管理 API 默认关闭。启用时必须同时配置管理开关与管理令牌。预览和应用结果只返回安全标识、计数与 SHA-256 指纹，不返回 Provider 地址、环境变量名、凭据、Prompt 或消息正文。

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
