"""行动 Agent — 模拟官员行动的过程和结果，读写世界状态数据库.

与普通 Agent 不同，ActionAgent：
- 不使用 memory（每次行动都是独立会话）
- 有自己的 FC 工具集（query_entity / query_inventory / update_entity_status /
  update_inventory / complete_action）
- 支持多轮 FC 循环，直到调用 complete_action 或达到最大轮次
- 返回 ActionResult（而非 AgentAction）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .llm_client import LLMClient
from .types import ActionResult
from .world_state import WorldState

logger = logging.getLogger(__name__)


# ── FC 工具定义 ──

QUERY_ENTITY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_entity",
        "description": "查询一个实体（地点/人物/机构/组织）的描述与当前状况。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
            },
            "required": ["entity_id"],
        },
    },
}

QUERY_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_inventory",
        "description": "查询一个实体的库存（粮食、货币、兵力、物资等）。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
            },
            "required": ["entity_id"],
        },
    },
}

UPDATE_ENTITY_STATUS_TOOL = {
    "type": "function",
    "function": {
        "name": "update_entity_status",
        "description": "更新一个实体的当前状况（current_status 字段），用一句话描述该实体目前发生了什么。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
                "new_status": {"type": "string", "description": "新的当前状况描述"},
            },
            "required": ["entity_id", "new_status"],
        },
    },
}

UPDATE_INVENTORY_TOOL = {
    "type": "function",
    "function": {
        "name": "update_inventory",
        "description": "相对增减某实体的库存条目。quantity_delta 正数为增加，负数为减少。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "category": {"type": "string", "description": "类别：粮食/货币/兵力/物资"},
                "item_name": {"type": "string", "description": "条目名：稻米/白银/官兵 等"},
                "quantity_delta": {"type": "number", "description": "变化量，正增负减"},
                "reason": {"type": "string", "description": "变动原因（用于日志）"},
            },
            "required": ["entity_id", "category", "item_name", "quantity_delta"],
        },
    },
}

COMPLETE_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "complete_action",
        "description": "结束本次行动模拟，返回结果、耗时和对执行者的重要发现。必须作为最后一次工具调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "result_description": {
                    "type": "string",
                    "description": "行动结果描述，将作为消息送回执行者。简明扼要、聚焦关键发现。",
                },
                "duration_days": {
                    "type": "number",
                    "description": "行动耗时（模拟天数）",
                },
                "success": {
                    "type": "boolean",
                    "description": "行动是否成功达成目标",
                },
            },
            "required": ["result_description", "duration_days"],
        },
    },
}

ACTION_AGENT_TOOLS = [
    QUERY_ENTITY_TOOL,
    QUERY_INVENTORY_TOOL,
    UPDATE_ENTITY_STATUS_TOOL,
    UPDATE_INVENTORY_TOOL,
    COMPLETE_ACTION_TOOL,
]

# 世界状态工具集（不含 complete_action），供 TianJiAgent 等复用
WORLD_STATE_TOOLS = [
    QUERY_ENTITY_TOOL,
    QUERY_INVENTORY_TOOL,
    UPDATE_ENTITY_STATUS_TOOL,
    UPDATE_INVENTORY_TOOL,
]


def execute_world_state_tool(
    world_state: WorldState,
    name: str,
    args: dict[str, Any],
    changes_made: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """执行单次世界状态类 FC 工具调用，返回 JSON 可序列化结果.

    供 ActionAgent 和 TianJiAgent 共用，避免逻辑重复。
    支持的工具：query_entity / query_inventory / update_entity_status / update_inventory.
    `changes_made` 可为 None（天玑调试时不强制记录）。
    """
    try:
        if name == "query_entity":
            ent = world_state.get_entity(args.get("entity_id", ""))
            if not ent:
                return {"found": False, "message": "实体不存在"}
            return {
                "found": True,
                "id": ent.id,
                "type": ent.type,
                "name": ent.name,
                "description": ent.description,
                "current_status": ent.current_status,
            }

        if name == "query_inventory":
            items = world_state.get_inventory(args.get("entity_id", ""))
            return {
                "entity_id": args.get("entity_id", ""),
                "items": [
                    {
                        "category": it.category,
                        "item_name": it.item_name,
                        "quantity": it.quantity,
                        "description": it.description,
                    }
                    for it in items
                ],
            }

        if name == "update_entity_status":
            entity_id = args.get("entity_id", "")
            new_status = args.get("new_status", "")
            world_state.update_status(entity_id, new_status, 0.0)
            if changes_made is not None:
                changes_made.append({
                    "type": "status_update",
                    "entity_id": entity_id,
                    "new_status": new_status,
                })
            return {"ok": True}

        if name == "update_inventory":
            entity_id = args.get("entity_id", "")
            category = args.get("category", "")
            item_name = args.get("item_name", "")
            delta = float(args.get("quantity_delta", 0))
            reason = args.get("reason", "")
            new_qty = world_state.update_inventory(
                entity_id, category, item_name, delta, reason,
            )
            if changes_made is not None:
                changes_made.append({
                    "type": "inventory_update",
                    "entity_id": entity_id,
                    "category": category,
                    "item_name": item_name,
                    "delta": delta,
                    "new_quantity": new_qty,
                    "reason": reason,
                })
            return {"ok": True, "new_quantity": new_qty}

        return {"ok": False, "error": f"未知世界状态工具：{name}"}

    except Exception as e:
        logger.exception("world_state tool %s failed: %s", name, e)
        return {"ok": False, "error": str(e)}


# ── ActionAgent ──

class ActionAgent:
    """行动模拟 Agent.

    独立于 BaseAgent 类层次 —— ActionAgent 不是官员/机构，而是"世界法则"的执行者。
    每次 process() 都是独立的多轮 FC 会话，无跨会话记忆。
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

            complete_called = False
            for tc in response.tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_result = self._dispatch_tool(name, args, changes_made)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })

                if name == "complete_action":
                    result_description = args.get("result_description", "")
                    duration_days = float(args.get("duration_days", 1.0))
                    success = bool(args.get("success", True))
                    complete_called = True

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
        )

    # ── 内部：工具调度 ──

    def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        changes_made: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """执行一次 FC 工具调用，返回 JSON 可序列化的结果."""
        if name == "complete_action":
            # 主循环会解析 args，本函数只回显一个 ack
            return {"ok": True}
        return execute_world_state_tool(self.world_state, name, args, changes_made)

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
