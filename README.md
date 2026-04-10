# 世界模拟器

基于 `FastAPI + React + SQLite + LLM Function Calling` 的多智能体朝堂模拟器。玩家扮演皇帝，基层官员接收灾情后逐级上报、隐瞒、越级、执行行动，Router 决定信息如何在朝廷体系中流转，ActionAgent 把行动落到世界状态上。

## 核心链路

1. 初始事件注入基层官员
2. 官员 Agent 调用 `submit_action`（上报/隐瞒/越级）或 `request_action`（触发行动）
3. `RouterAgent` 解析意图，生成路由指令
4. `TimerSystem` 延时送达，模拟古代文书传递
5. 消息逐级上传，形成奏折送达皇帝
6. 玩家批复或下旨，旨意通过太监逐级下达
7. `ActionAgent` 执行实际操作，写回 `WorldState`

## 当前场景

皇帝、司礼监太监王振、内阁杨士奇、通政司、户部、江南布政使周忱、苏州知府况钟、吴县知县张三、昆山知县李四。默认初始事件为苏州府蝗灾。

## 技术栈

`Python 3.11+` / `FastAPI` / `uvicorn` / `React 19` / `Vite` / `SQLite` / OpenAI 兼容接口 + Function Calling

## 快速开始

### 1. 安装依赖

```bash
python -m pip install -r requirements.txt
cd frontend && npm install
```

### 2. 配置 LLM

编辑 `config/llm.json`，填入 OpenAI 兼容接口地址和密钥。`api_key` 支持明文或 `${ENV_VAR_NAME}` 格式。

### 3. 启动

```bash
# 后端
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# 前端
cd frontend && npm run dev -- --host
# 或一键启动
start.bat      # Windows
sh start.sh    # macOS/Linux
```

### 4. 打开页面

- 皇帝视角：`http://localhost:3000`
- 上帝视角：同页面 Tab 切换
- Agent 编辑器：`http://localhost:3000/editor.html`
- 天玑 AI：`http://localhost:3000/tianji.html`

## 基本玩法

1. 点击"开始模拟"，蝗灾事件唤醒基层官员
2. 等待奏折逐级传到皇帝视角
3. 对奏折执行批准 / 驳回 / 留中，或直接下旨
4. 切换上帝视角查看完整链路和信息失真

---

更详细的文档：

- **[PROJECT_DOC.md](PROJECT_DOC.md)** — 架构详解、数据流、核心模块、数据库结构、API 接口、配置说明
- **[DEV_GUIDE.md](DEV_GUIDE.md)** — 开发指南、关键文件说明、代码级细节、如何添加官员/事件/世界状态实体、测试
