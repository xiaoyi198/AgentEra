"""FC 处理函数集中注册表.

每个 FC 工具有且仅有一个处理函数。所有需要执行 FC 的调用方
（Orchestrator / ActionAgent / TianjiAgent）组装 FCContext 后
调用 dispatch_fc(name, ctx, args)，行为完全一致。

submit_action / request_action 是解析器（产出 AgentAction 而非 dict），
所以不在 FC_HANDLERS 中，单独走 parse_agent_fc_response。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .llm_client import LLMResponse
from .types import (
    AgentAction,
    ChainLogEntry,
    RouterCommand,
    RouterCommandType,
    TimerEntry,
)

if TYPE_CHECKING:
    from .chain_log import ChainLog
    from .court_graph import CourtGraph
    from .timer_system import TimerSystem
    from .world_state import WorldState

logger = logging.getLogger(__name__)


# ── 执行上下文 ────────────────────────────────────────────────────────────────

@dataclass
class FCContext:
    """FC 处理函数的执行上下文.

    任何需要执行 FC 的调用方（Orchestrator / ActionAgent / TianjiAgent）
    都负责组装这个对象。处理函数通过鸭子类型访问字段，未提供的依赖会退化
    为等价行为（例如 pending_wakeups=None 时 wake_agent 不入队但仍记日志）。
    """
    world_state: Any = None                 # WorldState
    court: Any = None                       # CourtGraph
    timers: Any = None                      # TimerSystem
    chain_log: Any = None                   # ChainLog
    action_agent: Any = None                # ActionAgent
    pending_wakeups: list[dict[str, Any]] | None = None  # orchestrator 队列引用
    run_action: Callable | None = None      # orchestrator._run_action 绑定方法
    agent_tasks: set[asyncio.Task] | None = None  # orchestrator._agent_tasks 引用
    sim_time: float = 0.0
    seconds_per_day: float = 86400.0
    caller_id: str = ""
    chain_id: str = ""
    source_action: AgentAction | None = None
    changes_made: list[dict[str, Any]] | None = None  # ActionAgent 收集变更用


# ── 世界状态辅助 ─────────────────────────────────────────────────────────────

def _resolve_entity_id(world_state: Any, entity_id: str) -> tuple[str, bool]:
    """精确匹配失败时回退到模糊匹配.

    返回 (resolved_id, is_fuzzy_matched)。模糊匹配使用 search_entities
    （在 id/name/description 中 LIKE 搜索），取第一个结果的 id。
    若仍找不到，返回原始 entity_id 和 False。
    """
    if world_state.get_entity(entity_id):
        return entity_id, False
    candidates = world_state.search_entities(entity_id)
    if candidates:
        return candidates[0].id, True
    return entity_id, False


# ── 世界状态 handlers ─────────────────────────────────────────────────────────

def handle_query_entity(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_id = args.get("entity_id", "")
        resolved_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        ent = ctx.world_state.get_entity(resolved_id)
        if not ent:
            return {"found": False, "message": "实体不存在"}
        result: dict[str, Any] = {
            "found": True,
            "id": ent.id,
            "type": ent.type,
            "name": ent.name,
            "description": ent.description,
            "current_status": ent.current_status,
        }
        if fuzzy:
            result["fuzzy_matched"] = True
            result["original_query"] = raw_id
        return result
    except Exception as e:
        logger.exception("handle_query_entity failed: %s", e)
        return {"ok": False, "error": str(e)}


def handle_query_inventory(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_id = args.get("entity_id", "")
        resolved_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        items = ctx.world_state.get_inventory(resolved_id)
        result: dict[str, Any] = {
            "entity_id": resolved_id,
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
        if fuzzy:
            result["fuzzy_matched"] = True
            result["original_query"] = raw_id
        return result
    except Exception as e:
        logger.exception("handle_query_inventory failed: %s", e)
        return {"ok": False, "error": str(e)}


def handle_list_entities(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """列出实体 ID/类型/名称。可按 entity_type 精确筛选."""
    try:
        entity_type = (args.get("entity_type") or "").strip()
        if entity_type:
            entities = ctx.world_state.get_entities_by_type(entity_type)
        else:
            entities = ctx.world_state.get_all_entities()
        result: dict[str, Any] = {
            "entities": [
                {"id": e.id, "type": e.type, "name": e.name}
                for e in entities
            ],
            "count": len(entities),
        }
        if entity_type:
            result["filtered_by"] = {"entity_type": entity_type}
        return result
    except Exception as e:
        logger.exception("handle_list_entities failed: %s", e)
        return {"ok": False, "error": str(e)}


def handle_update_entity_status(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_id = args.get("entity_id", "")
        entity_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        new_status = args.get("new_status", "")
        ctx.world_state.update_status(entity_id, new_status, 0.0)
        if ctx.changes_made is not None:
            ctx.changes_made.append({
                "type": "status_update",
                "entity_id": entity_id,
                "new_status": new_status,
            })
        result: dict[str, Any] = {"ok": True, "entity_id": entity_id}
        if fuzzy:
            result["fuzzy_matched"] = True
            result["original_query"] = raw_id
        return result
    except Exception as e:
        logger.exception("handle_update_entity_status failed: %s", e)
        return {"ok": False, "error": str(e)}


def handle_update_inventory(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_id = args.get("entity_id", "")
        entity_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        category = args.get("category", "")
        item_name = args.get("item_name", "")
        delta = float(args.get("quantity_delta", 0))
        reason = args.get("reason", "")
        new_qty = ctx.world_state.update_inventory(
            entity_id, category, item_name, delta, reason,
        )
        if ctx.changes_made is not None:
            ctx.changes_made.append({
                "type": "inventory_update",
                "entity_id": entity_id,
                "category": category,
                "item_name": item_name,
                "delta": delta,
                "new_quantity": new_qty,
                "reason": reason,
            })
        result: dict[str, Any] = {
            "ok": True,
            "new_quantity": new_qty,
            "entity_id": entity_id,
        }
        if fuzzy:
            result["fuzzy_matched"] = True
            result["original_query"] = raw_id
        return result
    except Exception as e:
        logger.exception("handle_update_inventory failed: %s", e)
        return {"ok": False, "error": str(e)}


# ── complete_action ──────────────────────────────────────────────────────────

def handle_complete_action(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """解析 complete_action 参数为结构化结果.

    不产生副作用 —— 调用方（ActionAgent 主循环）根据返回值自行终止。
    """
    return {
        "ok": True,
        "result_description": args.get("result_description", ""),
        "duration_days": float(args.get("duration_days", 1.0)),
        "success": bool(args.get("success", True)),
    }


# ── Router handlers（真实执行）──────────────────────────────────────────────

async def handle_wake_agent(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """唤醒指定 Agent，立即投递消息（追加到 pending_wakeups 队列）."""
    target = args.get("target", "")
    message = args.get("message", "")
    from_agent = args.get("from_agent", "") or args.get("from", "")

    if ctx.pending_wakeups is None:
        logger.warning("handle_wake_agent: no pending_wakeups queue, target=%s", target)
        return {"ok": False, "error": "无待唤醒队列", "target": target}

    ctx.pending_wakeups.append({
        "target": target,
        "payload": {"message": message, "from": from_agent},
        "chain_id": ctx.chain_id or f"chain_{uuid.uuid4().hex[:8]}",
    })
    return {"ok": True, "target": target, "queued": True}


async def handle_create_timer(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """创建延迟定时器 —— 始终以 ctx.sim_time 为时间基准."""
    target = args.get("target", "")
    delay_days = float(args.get("delay_days", 1.0))
    message = args.get("message", "")
    from_agent = args.get("from_agent", "") or args.get("from", "")
    urgency = args.get("urgency", "normal")

    if ctx.timers is None:
        return {"ok": False, "error": "timers 未注入"}

    delay_seconds = delay_days * ctx.seconds_per_day
    entry = TimerEntry(
        fire_at=ctx.sim_time + delay_seconds,
        target_agent=target,
        payload={
            "message": message,
            "from": from_agent,
            "urgency": urgency,
        },
        chain_id=ctx.chain_id or f"chain_{uuid.uuid4().hex[:8]}",
        created_at=ctx.sim_time,
    )
    timer_id = ctx.timers.schedule(entry)
    logger.info(
        "Timer created: → %s in %.0fs (%.1f days)",
        target, delay_seconds, delay_days,
    )
    return {
        "ok": True,
        "timer_id": timer_id,
        "target": target,
        "fire_at": entry.fire_at,
        "delay_days": delay_days,
    }


async def handle_execute_policy(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """调用 ActionAgent 执行政策.

    两条路径：
    - Orchestrator 路径（ctx.run_action 存在）：把任务交给 _run_action，
      走完整 chain_log + action_log 流程，异步执行。返回"已调度"。
    - 直接路径（ctx.action_agent 存在）：同步 await process()，
      返回完整结果（供天玑等需要立即看到结果的场景）。
    """
    policy = args.get("policy", "") or args.get("description", "")
    location = args.get("location", "") or args.get("target_location", "")
    source_id = (
        ctx.source_action.agent_id if ctx.source_action else ""
    ) or args.get("from_agent", "") or ""
    executor = args.get("executor") or source_id

    payload = {
        "action_type": args.get("action_type") or args.get("policy", "execute_policy"),
        "executor_id": executor,
        "target_location": location,
        "description": args.get("description") or policy or (
            ctx.source_action.content if ctx.source_action else ""
        ),
        "resources": args.get("resources", "（未指定）"),
    }

    # Orchestrator 路径 —— 通过 _run_action 异步调度
    if ctx.run_action is not None:
        task = asyncio.create_task(
            ctx.run_action(
                payload=payload,
                source_agent_id=source_id,
                chain_id=ctx.chain_id or f"chain_{uuid.uuid4().hex[:8]}",
                event_id="",
            )
        )
        if ctx.agent_tasks is not None:
            ctx.agent_tasks.add(task)
            task.add_done_callback(ctx.agent_tasks.discard)
        return {"ok": True, "scheduled": True, "executor": executor, "location": location}

    # 直接路径 —— 同步 await（天玑等场景）
    if ctx.action_agent is not None:
        try:
            result = await ctx.action_agent.process(payload)
            return {
                "ok": True,
                "action_type": result.action_type,
                "executor_id": result.executor_id,
                "target_location": result.target_location,
                "result_description": result.result_description,
                "duration_days": result.duration_days,
                "success": result.success,
                "changes_made": result.changes_made,
            }
        except Exception as e:
            logger.exception("handle_execute_policy direct path failed: %s", e)
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "既无 run_action 也无 action_agent，无法执行政策"}


async def handle_relay_decree(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """传递圣旨给指定官员列表 —— 每人一条 wake_agent."""
    targets = args.get("targets", []) or []
    decree = args.get("decree", "")
    urgency = args.get("urgency", "normal")

    if ctx.pending_wakeups is None:
        return {"ok": False, "error": "无待唤醒队列", "targets": targets}

    chain_id = ctx.chain_id or f"chain_{uuid.uuid4().hex[:8]}"
    for target_id in targets:
        ctx.pending_wakeups.append({
            "target": target_id,
            "payload": {
                "message": f"圣旨：{decree}",
                "from": "皇帝",
                "urgency": urgency,
            },
            "chain_id": chain_id,
        })
    return {"ok": True, "delivered_to": list(targets), "count": len(targets)}


def handle_list_agents(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """列出可唤醒 Agent。可按 location_id 精确筛选所在地."""
    try:
        location_id = (args.get("location_id") or "").strip()
        if location_id:
            officials = ctx.court.get_officials_at_location(location_id)
        else:
            officials = ctx.court.get_all_officials()
        result: dict[str, Any] = {
            "agents": [
                {
                    "id": o.id,
                    "name": o.name,
                    "rank": o.rank,
                    "location_id": o.location_id,
                    "role": o.role,
                    "enabled": o.enabled,
                }
                for o in officials
            ],
            "count": len(officials),
        }
        if location_id:
            result["filtered_by"] = {"location_id": location_id}
        return result
    except Exception as e:
        logger.exception("handle_list_agents failed: %s", e)
        return {"ok": False, "error": str(e)}


# ── 分发注册表 ────────────────────────────────────────────────────────────────

FC_HANDLERS: dict[str, tuple[Callable, bool]] = {
    # (handler, is_async)
    "list_entities":        (handle_list_entities, False),
    "query_entity":         (handle_query_entity, False),
    "query_inventory":      (handle_query_inventory, False),
    "update_entity_status": (handle_update_entity_status, False),
    "update_inventory":     (handle_update_inventory, False),
    "complete_action":      (handle_complete_action, False),
    "list_agents":          (handle_list_agents, False),
    "wake_agent":           (handle_wake_agent, True),
    "create_timer":         (handle_create_timer, True),
    "execute_policy":       (handle_execute_policy, True),
    "relay_decree":         (handle_relay_decree, True),
}


async def dispatch_fc(
    name: str, ctx: FCContext, args: dict[str, Any],
) -> dict[str, Any]:
    """按名称分发 FC 调用到对应 handler.

    submit_action / request_action 不在此表中 —— 它们是解析器，
    请使用 parse_agent_fc_response 或 parse_submit_action / parse_request_action。
    """
    entry = FC_HANDLERS.get(name)
    if entry is None:
        return {"ok": False, "error": f"未知 FC：{name}"}
    fn, is_async = entry
    if is_async:
        return await fn(ctx, args)
    return fn(ctx, args)


# ── RouterCommand ↔ FC 参数 桥接 ─────────────────────────────────────────────

def command_type_to_fc_name(cmd_type: RouterCommandType) -> str:
    """RouterCommandType 枚举 → FC 工具名."""
    return cmd_type.value


def command_to_fc_args(cmd: RouterCommand) -> dict[str, Any]:
    """RouterCommand → FC 调用参数 dict.

    把 RouterCommand 各字段还原成对应 FC 工具的 arguments，供 dispatch_fc 使用。
    """
    ct = cmd.command_type
    payload = cmd.payload or {}

    if ct == RouterCommandType.WAKE_AGENT:
        return {
            "target": cmd.target or "",
            "message": payload.get("message", ""),
            "from_agent": payload.get("from", ""),
        }

    if ct == RouterCommandType.CREATE_TIMER:
        return {
            "target": cmd.target or "",
            "delay_days": (cmd.delay_seconds / 86400.0) if cmd.delay_seconds else 1.0,
            "message": payload.get("message", ""),
            "from_agent": payload.get("from", ""),
            "urgency": payload.get("urgency", "normal"),
        }

    if ct == RouterCommandType.EXECUTE_POLICY:
        return {
            "policy": payload.get("policy", ""),
            "location": payload.get("location", ""),
            "executor": cmd.target or "",
            "description": payload.get("description", ""),
        }

    if ct == RouterCommandType.RELAY_DECREE:
        return {
            "targets": list(cmd.targets or []),
            "decree": payload.get("decree", ""),
            "urgency": payload.get("urgency", "normal"),
        }

    return dict(payload)


# ── Agent 双 FC 解析器（submit_action / request_action）──────────────────────

def parse_submit_action(agent_id: str, args: dict[str, Any]) -> AgentAction:
    """把 submit_action 的 FC 参数解析为 AgentAction."""
    return AgentAction(
        agent_id=agent_id,
        action=args.get("action", "do_nothing"),
        content=args.get("content", ""),
        original_intent=args.get("original_intent", ""),
        is_distorting=args.get("is_distorting", False),
        urgency=args.get("urgency", "normal"),
        metadata=args.get("metadata", {}),
    )


def parse_request_action(agent_id: str, args: dict[str, Any]) -> AgentAction:
    """把 request_action 的 FC 参数解析为 AgentAction（带 fc_type=request_action 标记）."""
    return AgentAction(
        agent_id=agent_id,
        action="request_action",
        content=args.get("description", ""),
        original_intent=args.get("description", ""),
        is_distorting=False,
        urgency="normal",
        metadata={
            "fc_type": "request_action",
            "action_type": args.get("action_type", "unknown"),
            "target_location": args.get("target_location", ""),
            "description": args.get("description", ""),
            "resources": args.get("resources", ""),
        },
    )


def parse_agent_fc_response(
    response: LLMResponse,
    agent_id: str,
) -> list[AgentAction]:
    """解析 LLM 响应 — 支持同时调用 submit_action + request_action.

    返回所有识别到的 AgentAction 列表。
    回退：没有合法 FC 调用 → 返回单个 do_nothing AgentAction。
    """
    actions: list[AgentAction] = []
    if response.tool_calls:
        for tc in response.tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}") or "{}")
            except json.JSONDecodeError:
                logger.warning("Agent %s: invalid FC JSON for %s", agent_id, name)
                continue

            if name == "submit_action":
                actions.append(parse_submit_action(agent_id, args))
            elif name == "request_action":
                actions.append(parse_request_action(agent_id, args))

    if not actions:
        actions.append(AgentAction(
            agent_id=agent_id,
            action="do_nothing",
            content=response.content,
            original_intent="未使用FC",
            is_distorting=False,
        ))

    return actions
