"""天玑 AI — 全流程测试对话 Agent.

独立于 Orchestrator，维护自己的对话历史，注入所有 FC 工具集（ALL_TOOLS）。

与生产路径**行为完全一致**：天玑执行 FC 时也走 fc_handlers.dispatch_fc，
所以 wake_agent / create_timer / execute_policy / relay_decree
等命令在天玑会话中产生和主模拟一模一样的副作用（真实唤醒、真实排定时器、
用真实 sim_time 作为时间基准等）。

每轮对话通过 SSE 事件流返回：tool_call / tool_result / assistant_delta / done。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .fc_handlers import (
    FCContext,
    command_to_fc_args,
    command_type_to_fc_name,
    dispatch_fc,
    parse_submit_action,
)
from .fc_tools import ALL_TOOLS
from .llm_client import LLMClient
from .timer_system import TimerSystem
from .types import ChainLogEntry
from .world_state import WorldState

logger = logging.getLogger(__name__)

# 天玑会话 chain_id 前缀，与主模拟的日志分隔
_CHAIN_PREFIX = "tianji_"


def _summarize_result(result: dict) -> str:
    """将工具执行结果摘要为短字符串（供 chain_log 展示）."""
    if not isinstance(result, dict):
        return str(result)[:200]
    if result.get("error"):
        return f"错误: {result['error']}"
    if result.get("found") is False:
        return "实体不存在"
    if "result_description" in result:
        return result["result_description"][:200]
    if "new_quantity" in result:
        return f"库存更新 → {result['new_quantity']}"
    if result.get("mode") == "dry_run":
        return result.get("note", "dry-run")[:200]
    if result.get("items") is not None:
        return f"{len(result['items'])} 条库存"
    if result.get("name"):
        return f"{result.get('name')} | {result.get('current_status', '')}"[:200]
    return "ok"


class TianJiAgent:
    """天玑 AI — 全工具注入的调试对话 Agent."""

    def __init__(
        self,
        llm_client: LLMClient,
        world_state: WorldState,
        court_graph: Any,       # CourtGraph（避免循环导入用 Any）
        timer_system: TimerSystem,
        chain_log: Any,         # ChainLog
        action_agent: Any,      # ActionAgent
        router: Any | None = None,
        orchestrator: Any | None = None,  # 可选注入；有则共享 sim_time / pending_wakeups
        system_prompt_path: str = "daming_court/prompts/tianji.txt",
    ) -> None:
        self.llm = llm_client
        self.world_state = world_state
        self.court = court_graph
        self.timers = timer_system
        self.chain_log = chain_log
        self.action_agent = action_agent
        self.router = router
        self.orchestrator = orchestrator
        self._prompt_path = system_prompt_path

        self.session_id: str = f"tianji_{uuid.uuid4().hex[:8]}"
        self.messages: list[dict[str, Any]] = []
        self._tools = self._build_tools()

        self.reset()

    def attach_orchestrator(self, orchestrator: Any) -> None:
        """在 orchestrator 构造后反向挂载，便于天玑共享 sim_time / pending_wakeups."""
        self.orchestrator = orchestrator

    # ── 公开接口 ──

    def reset(self) -> None:
        """清空对话历史，重新注入 system prompt."""
        system_prompt = self._load_system_prompt()
        self.messages = [{"role": "system", "content": system_prompt}]
        self.session_id = f"tianji_{uuid.uuid4().hex[:8]}"
        logger.info("TianJiAgent reset, session_id=%s", self.session_id)

    def get_history(self) -> list[dict[str, Any]]:
        """返回当前 messages（供前端刷新后恢复展示）."""
        return self.messages

    async def chat(self, user_text: str) -> AsyncIterator[dict[str, Any]]:
        """主入口：处理一条用户消息，yield SSE 事件流.

        全部使用非流式 chat() 确保兼容性。
        工具执行后如果 LLM 返回空 content，强制追问直到得到文字回复。
        """
        self.messages.append({"role": "user", "content": user_text})

        had_tools = False        # 是否执行过工具
        empty_retries = 0        # 空回复重试计数

        for _round in range(10):
            logger.info("TianJi round %d: calling LLM (msgs=%d, had_tools=%s)",
                        _round, len(self.messages), had_tools)

            # 最后 2 轮不给工具，强制出文字
            use_tools = self._tools if _round < 8 else []

            try:
                response = await self.llm.chat(
                    messages=self.messages,
                    tools=use_tools,
                    temperature=0.7,
                )
            except Exception as e:
                logger.error("TianJiAgent LLM call failed round %d: %s", _round, e)
                yield {"type": "error", "text": f"LLM 调用失败：{e}"}
                yield {"type": "done"}
                return

            logger.info("TianJi round %d: content_len=%d, tool_calls=%d",
                        _round, len(response.content or ""), len(response.tool_calls))

            # ── 有 tool_calls → 执行工具 ──
            if response.tool_calls:
                had_tools = True
                empty_retries = 0
                # 如果 LLM 同时返回了文字 + tool_calls，先把文字发给前端
                if response.content and response.content.strip():
                    yield {"type": "assistant_delta", "text": response.content}
                yield {"type": "tool_round_start", "round": _round}

                self.messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": tc.get("function", {}),
                        }
                        for tc in response.tool_calls
                    ],
                })

                for tc in response.tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    yield {"type": "tool_call", "name": name, "arguments": args}

                    result = await self._dispatch_tool(name, args)

                    yield {"type": "tool_result", "name": name, "result": result}

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                # 工具执行完毕，循环继续调用 LLM
                continue

            # ── 没有 tool_calls → 检查是否有文字内容 ──
            content = (response.content or "").strip()

            if content:
                # 有内容 → yield 给前端并结束
                logger.info("TianJi round %d: yielding text (%d chars)", _round, len(content))
                yield {"type": "assistant_delta", "text": response.content}
                self.messages.append({
                    "role": "assistant",
                    "content": response.content,
                })
                break

            # ── 空内容 ──
            logger.warning("TianJi round %d: empty content (had_tools=%s, retries=%d)",
                           _round, had_tools, empty_retries)

            if had_tools and empty_retries < 2:
                # 工具执行过但 LLM 返回空 → 强制追问
                empty_retries += 1
                # 不追加空 assistant 消息（某些模型不接受空 content + 无 tool_calls）
                self.messages.append({
                    "role": "user",
                    "content": (
                        "（系统提示：你刚才调用工具获得了结果，但没有回复用户。"
                        "请根据工具返回的数据，用自然语言向用户说明结果。不要调用工具。）"
                    ),
                })
                continue

            # 没执行过工具或重试次数用完 → 兜底通知前端
            yield {"type": "assistant_delta", "text": "（模型未返回文字回复，请重新提问）"}
            self.messages.append({"role": "assistant", "content": "（模型未返回文字回复）"})
            break

        yield {"type": "done"}

    # ── 工具构建 ──

    def _build_tools(self) -> list[dict[str, Any]]:
        """天玑注入全量 FC 工具集（来自 fc_tools.ALL_TOOLS）."""
        return list(ALL_TOOLS)

    # ── 工具分发 —— 所有 FC 走 fc_handlers.dispatch_fc，与生产路径共享实现 ──

    def _build_ctx(self) -> FCContext:
        """组装天玑的 FCContext.

        若挂载了 orchestrator，共享其 sim_time / pending_wakeups / _agent_tasks
        以及 _run_action 绑定方法 —— 这样天玑调用 wake_agent / create_timer /
        execute_policy 等都产生与生产路径一致的真实副作用。
        """
        orch = self.orchestrator
        return FCContext(
            world_state=self.world_state,
            court=self.court,
            timers=self.timers,
            chain_log=self.chain_log,
            action_agent=self.action_agent,
            pending_wakeups=getattr(orch, "_pending_wakeups", None) if orch else None,
            run_action=getattr(orch, "_run_action", None) if orch else None,
            agent_tasks=getattr(orch, "_agent_tasks", None) if orch else None,
            sim_time=getattr(orch, "_sim_time", 0.0) if orch else 0.0,
            seconds_per_day=86400.0,
            caller_id="tianji",
            chain_id=f"{_CHAIN_PREFIX}{self.session_id}",
        )

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """天玑统一分发：submit_action / request_action 特殊路径，其余走 dispatch_fc."""
        ctx = self._build_ctx()

        # submit_action → 先解析成 AgentAction → 走 Router → Router 产生的命令
        # 再通过 dispatch_fc 真实执行（与 Orchestrator._execute_commands 同逻辑）
        if name == "submit_action":
            if self.router is None:
                result = {"ok": False, "error": "Router 未挂载，无法路由行动"}
            else:
                agent_id = args.get("agent_id", "tianji_test")
                agent_action = parse_submit_action(agent_id, args)
                try:
                    commands = await self.router.process_batch([agent_action])
                    executed: list[dict[str, Any]] = []
                    for cmd in commands:
                        ctx.chain_id = cmd.chain_id
                        ctx.source_action = cmd.source_action
                        fc_name = command_type_to_fc_name(cmd.command_type)
                        fc_args = command_to_fc_args(cmd)
                        exec_result = await dispatch_fc(fc_name, ctx, fc_args)
                        executed.append({
                            "type": fc_name,
                            "args": fc_args,
                            "result": exec_result,
                        })
                    result = {
                        "ok": True,
                        "routed": True,
                        "commands": executed,
                        "note": f"已进入 Router 并真实执行 {len(executed)} 条路由指令",
                    }
                except Exception as e:
                    logger.exception("tianji submit_action failed: %s", e)
                    result = {"ok": False, "error": str(e)}

        # request_action → 等价于 execute_policy 直接跑 ActionAgent
        elif name == "request_action":
            fc_args = {
                "action_type": args.get("action_type", "unknown"),
                "description": args.get("description", ""),
                "location": args.get("target_location", ""),
                "executor": "tianji_test",
                "resources": args.get("resources", "（未指定）"),
            }
            result = await dispatch_fc("execute_policy", ctx, fc_args)

        # 其余 FC 直接分发
        else:
            result = await dispatch_fc(name, ctx, args)

        # 所有工具调用都记录到 chain_log（上帝视角可见）
        self._append_chain_log(
            fc_called=name,
            transmitted_text=json.dumps(
                {"args": args, "result_summary": _summarize_result(result)},
                ensure_ascii=False,
            ),
        )
        return result

    # ── 内部工具 ──

    def _append_chain_log(
        self,
        fc_called: str,
        transmitted_text: str = "",
        original_text: str = "",
        is_distorted: bool = False,
        status: str = "delivered",
    ) -> None:
        try:
            entry = ChainLogEntry(
                chain_id=f"{_CHAIN_PREFIX}{self.session_id}",
                caller="tianji_agent",
                caller_agent_type="tianji",
                fc_called=fc_called,
                original_text=original_text,
                transmitted_text=transmitted_text,
                is_distorted=is_distorted,
                status=status,
            )
            self.chain_log.append(entry)
        except Exception as e:
            logger.warning("TianJiAgent chain_log append failed: %s", e)

    def _load_system_prompt(self) -> str:
        path = Path(self._prompt_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return _DEFAULT_SYSTEM_PROMPT


_DEFAULT_SYSTEM_PROMPT = """你是「天玑」，大明王朝·朝堂风云世界模拟器的调试 AI 助手。

你可以直接操作模拟器的底层数据，帮助开发者测试全流程是否正常工作。

## 你拥有的工具

**世界状态工具**（读写实体与库存）：
- `query_entity(entity_id)` — 查询地点/人物/机构的描述与当前状况
- `query_inventory(entity_id)` — 查询库存（粮食/货币/兵力/物资）
- `update_entity_status(entity_id, new_status)` — 更新实体当前状况
- `update_inventory(entity_id, category, item_name, quantity_delta)` — 增减库存
- `complete_action(result_description, duration_days)` — 标记行动模拟结束

**路由指令工具**（与生产路径行为一致，会产生真实副作用）：
- `wake_agent(target, message)` — 真实唤醒目标 Agent（下一 tick 送达）
- `create_timer(target, delay_days, message)` — 写入定时器表（使用模拟时间）
- `execute_policy(policy, location, executor)` — 调用 ActionAgent 推演结果（真实执行）
- `relay_decree(decree, targets)` — 真实传达圣旨给指定官员

**官员行为工具**：
- `submit_action(action, content, original_intent, is_distorting, urgency)` — 进入 Router 路由并真实执行
- `request_action(action_type, target_location, description)` — 调用 ActionAgent 推演行动（真实执行）

## 工作原则

1. 不确定时，先用 `query_entity` / `query_inventory` 了解现状
2. 对于写操作，在执行前告知用户你将要做什么
3. 所有工具调用都会产生**真实副作用**（修改世界状态、唤醒 Agent、创建定时器等），请谨慎调用
4. 每次工具调用结果会反馈给你，请整合后用自然语言告知用户
5. 保持古风叙事口吻，符合大明朝廷背景
"""
