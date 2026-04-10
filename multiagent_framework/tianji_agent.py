"""天玑 AI — 全流程测试对话 Agent.

独立于 Orchestrator，维护自己的对话历史，注入所有 FC 工具集：
- ActionAgent 工具（世界状态读写）
- Router 工具（路由指令模拟）
- Agent 工具（submit_action / request_action dry-run）

每轮对话通过 SSE 事件流返回：tool_call / tool_result / assistant_delta / done。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from .action_agent import WORLD_STATE_TOOLS, execute_world_state_tool
from .agent_runtime import REQUEST_ACTION_TOOL, SUBMIT_ACTION_TOOL
from .llm_client import LLMClient
from .router import ROUTER_TOOLS
from .timer_system import TimerSystem
from .types import ChainLogEntry, TimerEntry
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
        system_prompt_path: str = "daming_court/prompts/tianji.txt",
    ) -> None:
        self.llm = llm_client
        self.world_state = world_state
        self.court = court_graph
        self.timers = timer_system
        self.chain_log = chain_log
        self.action_agent = action_agent
        self.router = router
        self._prompt_path = system_prompt_path

        self.session_id: str = f"tianji_{uuid.uuid4().hex[:8]}"
        self.messages: list[dict[str, Any]] = []
        self._tools = self._build_tools()

        # 注册工具分发器（可插拔：新工具只需在此 dict 中注册）
        self._dispatch_table: dict[str, Any] = {
            # 世界状态工具 → 共享函数
            "query_entity": self._do_world_state,
            "query_inventory": self._do_world_state,
            "update_entity_status": self._do_world_state,
            "update_inventory": self._do_world_state,
            # complete_action → 天玑上下文仅 ack
            "complete_action": self._do_complete_action,
            # Router 工具
            "wake_agent": self._do_wake_agent,
            "create_timer": self._do_create_timer,
            "execute_policy": self._do_execute_policy,
            "relay_decree": self._do_relay_decree,
            "mark_withheld": self._do_mark_withheld,
            # Agent 双 FC
            "submit_action": self._do_submit_action,
            "request_action": self._do_request_action,
        }

        self.reset()

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
        """合并三套工具集：世界状态 + Router + Agent 双 FC."""
        return WORLD_STATE_TOOLS + [
            {
                "type": "function",
                "function": {
                    "name": "complete_action",
                    "description": "（在天玑测试模式下）标记一次行动模拟结束，返回总结。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "result_description": {"type": "string"},
                            "duration_days": {"type": "number"},
                            "success": {"type": "boolean"},
                        },
                        "required": ["result_description", "duration_days"],
                    },
                },
            }
        ] + ROUTER_TOOLS + [SUBMIT_ACTION_TOOL, REQUEST_ACTION_TOOL]

    # ── 工具分发 ──

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = self._dispatch_table.get(name)
        if handler is None:
            return {"ok": False, "error": f"未知工具：{name}"}
        # 部分处理器是 async
        import inspect
        if inspect.iscoroutinefunction(handler):
            result = await handler(name, args)
        else:
            result = handler(name, args)
        # 所有工具调用都记录到 chain_log（上帝视角可见）
        self._append_chain_log(
            fc_called=name,
            transmitted_text=json.dumps(
                {"args": args, "result_summary": _summarize_result(result)},
                ensure_ascii=False,
            ),
        )
        return result

    # ── 世界状态工具 ──

    def _do_world_state(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return execute_world_state_tool(self.world_state, name, args, changes_made=None)

    # ── complete_action（天玑仅 ack）──

    def _do_complete_action(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "tianji_ack",
            "result_description": args.get("result_description", ""),
            "duration_days": args.get("duration_days", 1.0),
            "success": args.get("success", True),
        }

    # ── Router 工具 ──

    def _do_wake_agent(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        target = args.get("target", "")
        return {
            "ok": True,
            "mode": "dry_run",
            "note": f"天玑模式：wake_agent 不真实唤醒 Agent。target={target!r}",
        }

    def _do_create_timer(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        import time
        target = args.get("target", "")
        delay_days = float(args.get("delay_days", 1.0))
        message = args.get("message", "")
        # 真实写入定时器表（fire_at 取当前时间 + 延迟，用真实秒占位 sim_time 未知）
        entry = TimerEntry(
            fire_at=time.time() + delay_days * 86400,
            target_agent=target,
            payload={"message": message, "from": "tianji_test"},
            chain_id=f"{_CHAIN_PREFIX}{self.session_id}",
        )
        timer_id = self.timers.schedule(entry)
        return {
            "ok": True,
            "timer_id": timer_id,
            "note": f"已写入定时器表，fire_at 按真实时间偏移 {delay_days} 天（仅供测试，主模拟运行时会触发）",
        }

    async def _do_execute_policy(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        policy = args.get("policy", "")
        location = args.get("location", "")
        executor = args.get("executor", "tianji_test")
        payload = {
            "action_type": "policy",
            "executor_id": executor,
            "target_location": location,
            "description": policy,
            "resources": "（天玑测试）",
        }
        try:
            result = await self.action_agent.process(payload)
            return {
                "ok": True,
                "action_type": result.action_type,
                "result_description": result.result_description,
                "duration_days": result.duration_days,
                "success": result.success,
                "changes_made": result.changes_made,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _do_relay_decree(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        targets = args.get("targets", [])
        return {
            "ok": True,
            "mode": "dry_run",
            "note": f"天玑模式：relay_decree 不真实传达。targets={targets}",
        }

    def _do_mark_withheld(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "mode": "dry_run", "note": "天玑模式：mark_withheld 已记录日志"}

    # ── Agent 双 FC ──

    async def _do_submit_action(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        action = args.get("action", "do_nothing")
        content = args.get("content", "")
        original_intent = args.get("original_intent", "")
        is_distorting = args.get("is_distorting", False)
        urgency = args.get("urgency", "normal")
        agent_id = args.get("agent_id", "tianji_test")

        if self.router is None:
            return {
                "ok": False,
                "error": "Router 未挂载，无法路由行动",
            }

        from .types import AgentAction
        agent_action = AgentAction(
            agent_id=agent_id,
            action=action,
            content=content,
            original_intent=original_intent,
            is_distorting=is_distorting,
            urgency=urgency,
        )
        try:
            commands = await self.router.process_batch([agent_action])
            return {
                "ok": True,
                "routed": True,
                "commands": [
                    {
                        "type": c.command_type.value if hasattr(c.command_type, "value") else str(c.command_type),
                        "target": c.target_agent,
                        "message": c.message,
                    }
                    for c in commands
                ],
                "note": f"已真实进入 Router，生成 {len(commands)} 条路由指令",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def _do_request_action(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        action_type = args.get("action_type", "unknown")
        target_location = args.get("target_location", "")
        description = args.get("description", "")
        resources = args.get("resources", "（未指定）")
        payload = {
            "action_type": action_type,
            "executor_id": "tianji_test",
            "target_location": target_location,
            "description": description,
            "resources": resources,
        }
        try:
            result = await self.action_agent.process(payload)
            return {
                "ok": True,
                "action_type": result.action_type,
                "result_description": result.result_description,
                "duration_days": result.duration_days,
                "success": result.success,
                "changes_made": result.changes_made,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

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

**路由指令工具**（模拟 Router 行为，dry-run 模式不真实唤醒 Agent）：
- `wake_agent(target, message)` — 记录"唤醒"意图
- `create_timer(target, delay_days, message)` — 真实写入定时器表
- `execute_policy(policy, location, executor)` — 调用 ActionAgent 推演结果（真实执行）
- `relay_decree(decree, targets)` — 记录"传达圣旨"意图
- `mark_withheld(reason)` — 记录"压下不报"意图

**官员行为工具**（模拟 Agent 的 FC 调用，dry-run）：
- `submit_action(action, content, original_intent, is_distorting, urgency)` — 模拟官员表达意图
- `request_action(action_type, target_location, description)` — 调用 ActionAgent 推演行动（真实执行）

## 工作原则

1. 不确定时，先用 `query_entity` / `query_inventory` 了解现状
2. 对于写操作，在执行前告知用户你将要做什么
3. `execute_policy` 和 `request_action` 会**真实修改世界状态**，请谨慎调用
4. `wake_agent` / `relay_decree` / `mark_withheld` / `submit_action` 是 dry-run，仅记录日志，不影响实际模拟
5. 每次工具调用结果会反馈给你，请整合后用自然语言告知用户
6. 保持古风叙事口吻，符合大明朝廷背景
"""
