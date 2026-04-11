"""行动 Agent — 模拟官员行动的过程和结果，读写世界状态数据库.

与普通 Agent 不同，ActionAgent：
- 不使用 memory（每次行动都是独立会话）
- 有自己的 FC 工具集（query_entity / query_inventory / update_entity_status /
  update_inventory / complete_action）
- 支持多轮 FC 循环，直到调用 complete_action 或达到最大轮次
- 返回 ActionResult（而非 AgentAction）

所有 FC 执行逻辑统一走 fc_handlers.dispatch_fc，不再有本地实现。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .fc_handlers import FCContext, dispatch_fc
from .fc_tools import ACTION_AGENT_TOOLS
from .llm_client import LLMClient
from .types import ActionResult
from .world_state import WorldState

logger = logging.getLogger(__name__)


# ── ActionAgent ──

class ActionAgent:
    """行动模拟 Agent.

    独立于 BaseAgent 类层次 —— ActionAgent 不是官员/机构，而是"世界法则"的执行者。
    每次 process() 都是独立的多轮 FC 会话，无跨会话记忆。
    max_rounds 参数限制每次行动的最大 FC 轮数，防止过度模拟导致资源耗尽推荐7轮就成最好别超过10轮。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        world_state: WorldState,
        prompt_template_path: str = "daming_court/prompts/action_agent.txt",
        max_rounds: int = 5,
    ) -> None:
        self.llm = llm_client
        self.world_state = world_state
        self._prompt_template_path = prompt_template_path
        self.max_rounds = max_rounds

    @property
    def agent_id(self) -> str:
        return "action_agent"

    @property
    def agent_type(self) -> str:
        return "world_sim"

    # ── 主入口 ──

    async def process(self, payload: dict[str, Any]) -> ActionResult:
        """模拟一次行动并返回结果。

        payload 期待字段：
          - action_type: 行动类型
          - executor_id: 执行者 ID
          - target_location: 目标地点 ID
          - description: 行动描述
          - resources: 投入资源描述（可选）
        """
        action_type = payload.get("action_type", "unknown")
        executor_id = payload.get("executor_id", "")
        target_location = payload.get("target_location", "")
        description = payload.get("description", "")
        resources = payload.get("resources", "（未指定）")

        # 构建动态 system prompt
        system_prompt = self._build_system_prompt(
            action_type=action_type,
            executor_id=executor_id,
            target_location=target_location,
            description=description,
            resources=resources,
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"请模拟这次行动：{action_type} @ {target_location}。\n"
                    f"执行者：{executor_id}\n"
                    f"描述：{description}\n"
                    "按工作流程调用工具推进，最后用 complete_action 结束。"
                ),
            },
        ]

        changes_made: list[dict[str, Any]] = []
        fc_trace: list[dict[str, Any]] = []
        result_description = ""
        duration_days = 1.0
        success = True
        error: str | None = None

        for round_idx in range(self.max_rounds):
            try:
                response = await self.llm.chat(
                    messages=messages,
                    tools=ACTION_AGENT_TOOLS,
                    temperature=0.7,
                )
            except Exception as e:
                logger.error("ActionAgent LLM call failed at round %d: %s", round_idx, e)
                error = f"LLM 调用失败: {e}"
                success = False
                result_description = f"行动因系统故障中断: {e}"
                break

            if not response.tool_calls:
                # 没有工具调用，强制收尾
                logger.warning("ActionAgent round %d: no tool call, content=%r",
                               round_idx, response.content[:200])
                result_description = response.content or "行动未产生明确结果"
                fc_trace.append({
                    "round": round_idx,
                    "assistant_content": response.content or "",
                    "calls": [],
                })
                break

            # 追加 assistant 消息（含 tool_calls）
            messages.append({
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

            round_calls: list[dict[str, Any]] = []
            complete_called = False
            ctx = FCContext(
                world_state=self.world_state,
                changes_made=changes_made,
                caller_id=executor_id,
            )
            for tc in response.tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_result = await dispatch_fc(name, ctx, args)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })

                round_calls.append({
                    "tool": name,
                    "args": args,
                    "result": tool_result,
                })

                if name == "complete_action":
                    result_description = tool_result.get("result_description", "")
                    duration_days = float(tool_result.get("duration_days", 1.0))
                    success = bool(tool_result.get("success", True))
                    complete_called = True

            fc_trace.append({
                "round": round_idx,
                "assistant_content": response.content or "",
                "calls": round_calls,
            })

            if complete_called:
                break
        else:
            # max_rounds 用完仍未 complete
            logger.warning("ActionAgent exhausted %d rounds without complete_action", self.max_rounds)
            if not result_description:
                result_description = "行动模拟超过最大轮次，未能产出明确结论"
                success = False

        return ActionResult(
            action_type=action_type,
            executor_id=executor_id,
            target_location=target_location,
            result_description=result_description,
            duration_days=duration_days,
            changes_made=changes_made,
            success=success,
            error=error,
            fc_trace=fc_trace,
        )

    # ── 内部：prompt 构建 ──

    def _build_system_prompt(
        self,
        action_type: str,
        executor_id: str,
        target_location: str,
        description: str,
        resources: str,
    ) -> str:
        """加载 prompt 模板，填充字段并注入世界状态上下文."""
        template = self._load_template()

        executor_name = executor_id
        exec_ent = self.world_state.get_entity(executor_id) if executor_id else None
        if exec_ent:
            executor_name = exec_ent.name

        world_context = self.world_state.get_context_for_action(
            location_id=target_location,
            executor_id=executor_id or None,
        )

        return template.format(
            executor_name=executor_name,
            action_type=action_type,
            target_location=target_location,
            action_description=description,
            resources=resources,
            world_context=world_context,
        )

    def _load_template(self) -> str:
        path = Path(self._prompt_template_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        # 退化模板（文件缺失时兜底）
        return (
            "你是世界模拟器。\n"
            "行动：{action_type}\n执行者：{executor_name}\n"
            "目标：{target_location}\n描述：{action_description}\n"
            "资源：{resources}\n\n{world_context}\n\n"
            "请通过工具调用模拟行动，最后用 complete_action 结束。"
        )
