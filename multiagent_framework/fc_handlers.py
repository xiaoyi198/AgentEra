"""FC 处理函数集中注册表.

每个 FC 工具有且仅有一个处理函数。所有需要执行 FC 的调用方
（Orchestrator / ActionAgent / TianjiAgent）组装 FCContext 后
调用 dispatch_fc(name, ctx, args)，行为完全一致。

submit_action / request_action 是解析器（产出 AgentAction 而非 dict），
所以不在 FC_HANDLERS 中，单独走 parse_agent_fc_response。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .llm_client import LLMResponse
from .time_simulator import TimeSimulator
from .types import (
    AgentAction,
    ChainLogEntry,
    RouterCommand,
    RouterCommandType,
    TimerEntry,
)
from .world_commands import WorldCommand, WorldCommandType
from .world_state import OngoingEvent, RESOURCE_META

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
    tool_call_id: str = ""
    source_action: AgentAction | None = None
    changes_made: list[dict[str, Any]] | None = None  # ActionAgent 收集变更用
    duration_estimator: Callable | None = None  # authoritative deterministic estimator


# ── 世界状态辅助 ─────────────────────────────────────────────────────────────


def _world_command_id(ctx: FCContext, operation: str) -> str | None:
    """Build a stable command id when the caller exposes a tool/route call id."""
    if not ctx.tool_call_id:
        return None
    scope = ctx.chain_id or ctx.caller_id or "world"
    return f"{scope}:{ctx.tool_call_id}:{operation}"

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
    """Return the split, versioned world-state read model for one entity."""
    try:
        raw_id = args.get("entity_id", "")
        resolved_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        ent = ctx.world_state.get_entity(resolved_id)
        if not ent:
            return {"found": False, "message": "entity not found"}
        raw_items = ctx.world_state.get_inventory(resolved_id)
        inventory = []
        legacy_values = []
        for item in raw_items:
            if item.quantity == -1:
                continue
            meta = RESOURCE_META.get(item.item_name, {})
            if not hasattr(ctx.world_state, "get_entity_values") and meta.get("kind") in {"metric", "parameter"}:
                legacy_values.append((item, meta.get("kind")))
                continue
            rate = float(getattr(item, "anchor_rate", 1.0))
            inventory.append({
                "name": item.item_name, "quantity": item.quantity,
                "unit": item.description, "version": int(getattr(item, "version", 0)),
                "anchor_rate": rate, "base_value": float(item.quantity) * rate,
            })
        get_values = getattr(ctx.world_state, "get_entity_values", None)
        values = get_values(resolved_id) if callable(get_values) else []
        metrics = [{
            "name": value.name, "value": value.value,
            "unit": value.description, "version": value.version,
        } for value in values if value.kind == "metric" and value.value != -1]
        parameters = [{
            "name": value.name, "value": value.value,
            "unit": value.description, "version": value.version,
        } for value in values if value.kind == "parameter" and value.value != -1]
        for item, kind in legacy_values:
            target = metrics if kind == "metric" else parameters
            target.append({
                "name": item.item_name, "value": item.quantity,
                "unit": item.description, "version": int(getattr(item, "version", 0)),
            })
        seconds_per_day = ctx.seconds_per_day or 86400.0
        ongoing = [{
            "event_id": event.event_id, "event_type": event.event_type,
            "description": event.description,
            "days_remaining": max(0.0, (event.end_time - ctx.sim_time) / seconds_per_day),
            "end_time": event.end_time, "expected_result": event.expected_result,
        } for event in ctx.world_state.list_ongoing_events(resolved_id)]
        result: dict[str, Any] = {
            "found": True, "id": ent.id, "type": ent.type, "name": ent.name,
            "description": ent.description, "current_status": ent.current_status,
            "version": int(getattr(ent, "version", 0)), "inventory": inventory, "metrics": metrics,
            "parameters": parameters,
            "inventory_valuation": (
                ctx.world_state.get_inventory_valuation(resolved_id)
                if callable(getattr(ctx.world_state, "get_inventory_valuation", None))
                else {
                    "entity_id": resolved_id,
                    "base_value": sum(item["base_value"] for item in inventory),
                    "items": inventory,
                }
            ),
            "ongoing_events": ongoing,
        }
        if fuzzy:
            result.update({"fuzzy_matched": True, "original_query": raw_id})
        return result
    except Exception as exc:
        logger.exception("handle_query_entity failed: %s", exc)
        return {"ok": False, "error": str(exc)}


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
        changed = ctx.world_state.update_status(
            entity_id, new_status, ctx.sim_time,
            command_id=_world_command_id(ctx, "update_status"),
            expected_version=args.get("expected_version"),
            chain_id=ctx.chain_id,
        )
        if ctx.changes_made is not None:
            ctx.changes_made.append({"type": "status_update", **changed})
        result: dict[str, Any] = {"ok": True, **changed}
        if fuzzy:
            result.update({"fuzzy_matched": True, "original_query": raw_id})
        return result
    except Exception as exc:
        logger.exception("handle_update_entity_status failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def handle_update_inventory(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        raw_id = args.get("entity_id", "")
        entity_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        resource_name = args.get("resource_name", "") or args.get("item_name", "")
        delta = float(args.get("quantity_delta", 0))
        reason = args.get("reason", "")
        new_value = ctx.world_state.update_inventory(
            entity_id, resource_name, resource_name, delta, reason,
            command_id=_world_command_id(ctx, "adjust_resource"),
            expected_version=args.get("expected_version"),
            chain_id=ctx.chain_id, sim_time=ctx.sim_time,
        )
        definition = ctx.world_state.get_resource_definition(resource_name)
        kind = definition.kind if definition else "asset"
        change = {
            "type": "inventory_update" if kind == "asset" else "entity_value_update",
            "entity_id": entity_id, "resource_name": resource_name,
            "delta": delta, "new_quantity": new_value, "reason": reason, "kind": kind,
        }
        if ctx.changes_made is not None:
            ctx.changes_made.append(change)
        result: dict[str, Any] = {"ok": True, **change}
        if fuzzy:
            result.update({"fuzzy_matched": True, "original_query": raw_id})
        return result
    except Exception as exc:
        logger.exception("handle_update_inventory failed: %s", exc)
        return {"ok": False, "error": str(exc)}



def _positive_finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name}_invalid")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name}_invalid") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field_name}_invalid")
    return number


def _asset_anchor(world_state: Any, resource_name: str) -> float:
    definition = world_state.get_resource_definition(resource_name)
    if definition is None:
        raise ValueError(f"resource_not_found: {resource_name}")
    kind = definition.get("kind") if isinstance(definition, dict) else getattr(definition, "kind", None)
    if kind != "asset":
        raise ValueError(f"resource_kind_invalid: {resource_name}")
    raw_rate = definition.get("anchor_rate") if isinstance(definition, dict) else getattr(definition, "anchor_rate", None)
    try:
        anchor_rate = float(raw_rate)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"resource_anchor_invalid: {resource_name}") from exc
    if not math.isfinite(anchor_rate) or anchor_rate <= 0:
        raise ValueError(f"resource_anchor_invalid: {resource_name}")
    return anchor_rate


def _record_economic_change(ctx: FCContext, change: dict[str, Any], *, replayed: bool) -> None:
    # An idempotent replay is observable in the tool result/trace, but it must not
    # claim a second world mutation in ActionResult.changes_made.
    if ctx.changes_made is not None and not replayed:
        ctx.changes_made.append(change)


def _handle_resource_flow(
    ctx: FCContext,
    args: dict[str, Any],
    *,
    operation: str,
) -> dict[str, Any]:
    try:
        raw_id = str(args.get("entity_id", "") or "")
        entity_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        resource_name = str(args.get("resource_name", "") or "").strip()
        quantity = _positive_finite(args.get("quantity"), "quantity")
        anchor_rate = _asset_anchor(ctx.world_state, resource_name)
        reason = str(args.get("reason", "") or "")
        sign = 1.0 if operation == "production" else -1.0
        command_id = _world_command_id(ctx, f"{operation}_resource") or f"wcmd_{uuid.uuid4().hex}"
        result = ctx.world_state.command_bus.execute(WorldCommand(
            command_type=WorldCommandType.ADJUST_RESOURCE,
            command_id=command_id,
            entity_id=entity_id,
            expected_version=args.get("expected_version"),
            chain_id=ctx.chain_id,
            sim_time=ctx.sim_time,
            payload={
                "resource_name": resource_name,
                "quantity_delta": sign * quantity,
                "reason": reason,
            },
            metadata={
                "source": "action_agent",
                "economic_operation": operation,
                "anchor_rate": anchor_rate,
            },
        ))
        if not result.ok:
            return {
                "ok": False,
                "operation": operation,
                "error_code": result.error_code or "world_command_rejected",
                "error": result.error or "world command rejected",
                "details": dict(result.result),
                "command_id": result.command_id,
                "status": result.status,
            }
        reduced = dict(result.result)
        change = {
            "type": f"resource_{operation}",
            "economic_operation": operation,
            "entity_id": entity_id,
            "resource_name": resource_name,
            "quantity_delta": reduced.get("quantity_delta", sign * quantity),
            "new_quantity": reduced.get("quantity"),
            "anchor_rate": reduced.get("anchor_rate", anchor_rate),
            "base_value_delta": reduced.get("base_value_delta", sign * quantity * anchor_rate),
            "reason": reason,
            "command_id": result.command_id,
        }
        _record_economic_change(ctx, change, replayed=result.replayed)
        response: dict[str, Any] = {
            "ok": True,
            **change,
            "replayed": result.replayed,
            "status": result.status,
        }
        if fuzzy:
            response.update({"fuzzy_matched": True, "original_query": raw_id})
        return response
    except Exception as exc:
        logger.exception("handle_%s_resource failed: %s", operation, exc)
        return {"ok": False, "operation": operation, "error": str(exc)}


def handle_produce_resource(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """Issue anchored asset value from an explicit production source."""
    return _handle_resource_flow(ctx, args, operation="production")


def handle_consume_resource(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """Destroy anchored asset value through explicit consumption or loss."""
    return _handle_resource_flow(ctx, args, operation="consumption")


def handle_transfer_anchored_value(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """Atomically move equal anchored base value between two asset balances."""
    try:
        raw_from = str(args.get("from_entity_id", "") or "")
        raw_to = str(args.get("to_entity_id", "") or "")
        from_entity_id, from_fuzzy = _resolve_entity_id(ctx.world_state, raw_from)
        to_entity_id, to_fuzzy = _resolve_entity_id(ctx.world_state, raw_to)
        from_resource = str(args.get("from_resource", "") or "").strip()
        to_resource = str(args.get("to_resource", "") or "").strip()
        if from_entity_id == to_entity_id and from_resource == to_resource:
            raise ValueError("transfer_same_account")
        base_value = _positive_finite(args.get("base_value"), "base_value")
        from_anchor = _asset_anchor(ctx.world_state, from_resource)
        to_anchor = _asset_anchor(ctx.world_state, to_resource)
        reason = str(args.get("reason", "") or "")
        command_scope = _world_command_id(ctx, "transfer_anchored_value") or f"agent-transfer:{uuid.uuid4().hex}"
        transfer_id = "transfer_" + hashlib.sha256(command_scope.encode("utf-8")).hexdigest()[:24]
        debit_quantity = -(base_value / from_anchor)
        credit_quantity = base_value / to_anchor

        def command(
            *, side: str, entity_id: str, resource_name: str, quantity_delta: float,
            anchor_rate: float, expected_version: Any,
        ) -> WorldCommand:
            audit = {
                "source": "action_agent",
                "economic_operation": "transfer",
                "anchored_base_value": base_value,
                "anchor_rate": anchor_rate,
                "transfer_side": side,
                "transfer_id": transfer_id,
            }
            return WorldCommand(
                command_type=WorldCommandType.ADJUST_RESOURCE,
                command_id=f"{command_scope}:{side}",
                entity_id=entity_id,
                expected_version=expected_version,
                chain_id=ctx.chain_id,
                sim_time=ctx.sim_time,
                payload={
                    "resource_name": resource_name,
                    "quantity_delta": quantity_delta,
                    "reason": reason,
                    "anchored_base_value": base_value,
                    "anchor_rate": anchor_rate,
                    "transfer_side": side,
                    "transfer_id": transfer_id,
                },
                metadata=audit,
            )

        batch = ctx.world_state.command_bus.execute_batch([
            command(
                side="debit", entity_id=from_entity_id, resource_name=from_resource,
                quantity_delta=debit_quantity, anchor_rate=from_anchor,
                expected_version=args.get("from_expected_version"),
            ),
            command(
                side="credit", entity_id=to_entity_id, resource_name=to_resource,
                quantity_delta=credit_quantity, anchor_rate=to_anchor,
                expected_version=args.get("to_expected_version"),
            ),
        ], atomic=True)
        if not batch.ok:
            failure = next((item for item in batch.results if item.error_code not in {"batch_rolled_back", "batch_aborted"}), None)
            failure = failure or next((item for item in batch.results if not item.ok), None)
            return {
                "ok": False,
                "operation": "transfer",
                "error_code": failure.error_code if failure else "transfer_rejected",
                "error": failure.error if failure else "anchored transfer rejected",
                "details": dict(failure.result) if failure else {},
                "transfer_id": transfer_id,
                "committed": batch.committed,
                "results": [item.to_dict() for item in batch.results],
            }
        replayed = bool(batch.results) and all(item.replayed for item in batch.results)
        debit_result = dict(batch.results[0].result)
        credit_result = dict(batch.results[1].result)
        change = {
            "type": "anchored_value_transfer",
            "economic_operation": "transfer",
            "transfer_id": transfer_id,
            "from_entity_id": from_entity_id,
            "from_resource": from_resource,
            "from_quantity_delta": debit_result.get("quantity_delta", debit_quantity),
            "from_anchor_rate": debit_result.get("anchor_rate", from_anchor),
            "from_new_quantity": debit_result.get("quantity"),
            "to_entity_id": to_entity_id,
            "to_resource": to_resource,
            "to_quantity_delta": credit_result.get("quantity_delta", credit_quantity),
            "to_anchor_rate": credit_result.get("anchor_rate", to_anchor),
            "to_new_quantity": credit_result.get("quantity"),
            "base_value": base_value,
            "base_value_delta": 0.0,
            "reason": reason,
            "command_ids": [item.command_id for item in batch.results],
        }
        _record_economic_change(ctx, change, replayed=replayed)
        response: dict[str, Any] = {
            "ok": True,
            **change,
            "replayed": replayed,
            "status": "replayed" if replayed else "applied",
            "committed": batch.committed,
        }
        if from_fuzzy or to_fuzzy:
            response["fuzzy_matches"] = {
                "from": from_entity_id if from_fuzzy else None,
                "to": to_entity_id if to_fuzzy else None,
            }
        return response
    except Exception as exc:
        logger.exception("handle_transfer_anchored_value failed: %s", exc)
        return {"ok": False, "operation": "transfer", "error": str(exc)}


# Ongoing event handlers.

_SAFE_TIME_SIMULATOR = TimeSimulator()


def _estimate_event_duration(
    ctx: FCContext,
    *,
    action_type: str,
    entity_id: str,
    proposed_days: Any,
) -> Any:
    if ctx.duration_estimator is not None:
        return ctx.duration_estimator(
            action_type=action_type,
            executor_id=ctx.caller_id,
            target_location=entity_id,
            proposed_days=proposed_days,
            severity=5,
            success=True,
        )
    return _SAFE_TIME_SIMULATOR.estimate(
        action_type=action_type,
        proposed_days=proposed_days,
    )

def handle_add_ongoing_event(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """在指定实体上排队一个事件，到期由 orchestrator 自动应用 expected_result."""
    try:
        raw_id = args.get("entity_id", "")
        entity_id, fuzzy = _resolve_entity_id(ctx.world_state, raw_id)
        if not ctx.world_state.get_entity(entity_id):
            return {"ok": False, "error": f"实体不存在：{raw_id}"}

        estimate = _estimate_event_duration(
            ctx,
            action_type=str(args.get("event_type", "state_change") or "state_change"),
            entity_id=entity_id,
            proposed_days=args.get("duration_days", 1.0),
        )
        duration_days = estimate.duration_days
        seconds_per_day = ctx.seconds_per_day or 86400.0
        end_time = ctx.sim_time + duration_days * seconds_per_day

        expected_result = args.get("expected_result") or {}
        if not isinstance(expected_result, dict):
            return {"ok": False, "error": "expected_result 必须是对象"}

        command_id = _world_command_id(ctx, "create_event")
        event_id = (
            f"evt_{uuid.uuid5(uuid.NAMESPACE_URL, command_id).hex[:10]}"
            if command_id else f"evt_{uuid.uuid4().hex[:10]}"
        )
        event = OngoingEvent(
            event_id=event_id,
            entity_id=entity_id,
            event_type=args.get("event_type", "state_change"),
            description=args.get("description", ""),
            start_time=ctx.sim_time,
            end_time=end_time,
            expected_result=expected_result,
            source_action_id=ctx.chain_id or "",
        )
        ctx.world_state.add_ongoing_event(
            event, command_id=command_id, chain_id=ctx.chain_id,
        )

        if ctx.changes_made is not None:
            ctx.changes_made.append({
                "type": "ongoing_event_add",
                "event_id": event.event_id,
                "entity_id": entity_id,
                "description": event.description,
                "duration_days": duration_days,
                "duration_rule_id": estimate.rule_id,
                "duration_source": estimate.source,
                "expected_result": expected_result,
            })

        result: dict[str, Any] = {
            "ok": True,
            "event_id": event.event_id,
            "entity_id": entity_id,
            "end_time": end_time,
        }
        if fuzzy:
            result["fuzzy_matched"] = True
            result["original_query"] = raw_id
        return result
    except Exception as e:
        logger.exception("handle_add_ongoing_event failed: %s", e)
        return {"ok": False, "error": str(e)}


def handle_modify_ongoing_event(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """修改一条已存在的 ongoing event。可改 description / duration_days / expected_result."""
    try:
        event_id = args.get("event_id", "")
        existing = ctx.world_state.get_ongoing_event(event_id)
        if not existing:
            return {"ok": False, "error": f"事件不存在：{event_id}"}

        kwargs: dict[str, Any] = {}
        if "description" in args and args["description"] is not None:
            kwargs["description"] = args["description"]
        duration_estimate = None
        if "duration_days" in args and args["duration_days"] is not None:
            duration_estimate = _estimate_event_duration(
                ctx,
                action_type=str(args.get("event_type") or existing.event_type or "state_change"),
                entity_id=existing.entity_id,
                proposed_days=args["duration_days"],
            )
            seconds_per_day = ctx.seconds_per_day or 86400.0
            kwargs["end_time"] = ctx.sim_time + duration_estimate.duration_days * seconds_per_day
        if "expected_result" in args and args["expected_result"] is not None:
            if not isinstance(args["expected_result"], dict):
                return {"ok": False, "error": "expected_result 必须是对象"}
            kwargs["expected_result"] = args["expected_result"]
        if "event_type" in args and args["event_type"]:
            kwargs["event_type"] = args["event_type"]

        ok = ctx.world_state.update_ongoing_event(
            event_id, **kwargs,
            command_id=_world_command_id(ctx, "update_event"),
            chain_id=ctx.chain_id, sim_time=ctx.sim_time,
        )
        if ok and ctx.changes_made is not None:
            ctx.changes_made.append({
                "type": "ongoing_event_modify",
                "event_id": event_id,
                "fields": list(kwargs.keys()),
            })
        return {"ok": ok, "event_id": event_id}
    except Exception as e:
        logger.exception("handle_modify_ongoing_event failed: %s", e)
        return {"ok": False, "error": str(e)}


def handle_delete_ongoing_event(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """取消一条进行中的事件。expected_result 不会被应用."""
    try:
        event_id = args.get("event_id", "")
        ok = ctx.world_state.delete_ongoing_event(
            event_id, command_id=_world_command_id(ctx, "delete_event"),
            chain_id=ctx.chain_id, sim_time=ctx.sim_time,
        )
        if ok and ctx.changes_made is not None:
            ctx.changes_made.append({
                "type": "ongoing_event_delete",
                "event_id": event_id,
            })
        return {"ok": ok, "event_id": event_id}
    except Exception as e:
        logger.exception("handle_delete_ongoing_event failed: %s", e)
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

# urgency → 速度倍率（越大越快）
_URGENCY_SPEED: dict[str, float] = {
    "urgent":  1.8,
    "express": 1.5,
    "normal":  1.0,
}


async def handle_wake_agent(ctx: FCContext, args: dict[str, Any]) -> dict[str, Any]:
    """向目标 Agent 投递消息。

    延迟由代码根据双方坐标（通过 CourtGraph → WorldState）和 urgency 速度倍率
    确定性计算：
      - 同城 / 无坐标 / 最短时间 → 立即入 pending_wakeups
      - 其他 → 走 timers.schedule 按天数延迟

    需要 ctx.court 才能查距离；ctx.timers / ctx.pending_wakeups 任一可用即可。
    """
    from .court_graph import MIN_TRAVEL_DAYS

    target = args.get("target", "")
    message = args.get("message", "")
    from_agent = args.get("from_agent", "") or args.get("from", "")
    urgency = args.get("urgency", "normal")
    speed_coeff = _URGENCY_SPEED.get(urgency, 1.0)

    # 计算延迟
    if ctx.court is not None and from_agent:
        try:
            delay_days = float(ctx.court.get_distance(from_agent, target, speed_coeff))
        except Exception as e:
            logger.warning("get_distance(%s → %s) failed: %s", from_agent, target, e)
            delay_days = 0.0
    else:
        if not from_agent:
            logger.warning("handle_wake_agent: from_agent missing, target=%s", target)
        delay_days = 0.0

    chain_id = ctx.chain_id or f"chain_{uuid.uuid4().hex[:8]}"

    # 同城 / 可忽略距离 → 立即投递
    if delay_days <= MIN_TRAVEL_DAYS and ctx.pending_wakeups is not None:
        ctx.pending_wakeups.append({
            "target": target,
            "payload": {
                "message": message,
                "from": from_agent,
                "urgency": urgency,
                "intent_id": args.get("intent_id", ""),
            },
            "chain_id": chain_id,
        })
        logger.info("wake_agent: %s → %s 立即投递（%s）", from_agent or "?", target, urgency)
        return {
            "ok": True, "target": target, "queued": True,
            "delay_days": delay_days,
        }

    # 否则写定时器
    if ctx.timers is None:
        logger.warning("handle_wake_agent: no timers and no pending_wakeups, target=%s", target)
        return {"ok": False, "error": "无投递通道", "target": target}

    delay_seconds = delay_days * ctx.seconds_per_day
    entry = TimerEntry(
        fire_at=ctx.sim_time + delay_seconds,
        target_agent=target,
        payload={
            "message": message,
            "from": from_agent,
            "urgency": urgency,
            "intent_id": args.get("intent_id", ""),
        },
        chain_id=chain_id,
        created_at=ctx.sim_time,
    )
    timer_id = ctx.timers.schedule(entry)
    logger.info(
        "wake_agent: %s → %s 延迟 %.2f 天（%s, coeff=%.1f）",
        from_agent or "?", target, delay_days, urgency, speed_coeff,
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
        "severity": args.get("severity", 5),
        "intent_id": args.get("intent_id", "") or (
            (ctx.source_action.metadata or {}).get("intent_id", "") if ctx.source_action else ""
        ),
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
            payload.setdefault("sim_time", ctx.sim_time)
            payload.setdefault("seconds_per_day", ctx.seconds_per_day)
            payload.setdefault("chain_id", ctx.chain_id)
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
    "list_entities":         (handle_list_entities, False),
    "query_entity":          (handle_query_entity, False),
    "update_entity_status":  (handle_update_entity_status, False),
    "update_inventory":      (handle_update_inventory, False),
    "produce_resource":      (handle_produce_resource, False),
    "consume_resource":      (handle_consume_resource, False),
    "transfer_anchored_value": (handle_transfer_anchored_value, False),
    "add_ongoing_event":     (handle_add_ongoing_event, False),
    "modify_ongoing_event":  (handle_modify_ongoing_event, False),
    "delete_ongoing_event":  (handle_delete_ongoing_event, False),
    "complete_action":       (handle_complete_action, False),
    "list_agents":           (handle_list_agents, False),
    "wake_agent":            (handle_wake_agent, True),
    "execute_policy":        (handle_execute_policy, True),
    "relay_decree":          (handle_relay_decree, True),
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
            "urgency": payload.get("urgency", "normal"),
            "intent_id": cmd.intent_id,
        }

    if ct == RouterCommandType.EXECUTE_POLICY:
        return {
            "policy": payload.get("policy", ""),
            "location": payload.get("location", ""),
            "executor": cmd.target or "",
            "description": payload.get("description", ""),
            "from_agent": payload.get("from", cmd.source_agent_id),
            "intent_id": cmd.intent_id,
        }

    if ct == RouterCommandType.RELAY_DECREE:
        return {
            "targets": list(cmd.targets or []),
            "decree": payload.get("decree", ""),
            "urgency": payload.get("urgency", "normal"),
            "intent_id": cmd.intent_id,
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
            "severity": args.get("severity", 5),
            "causal_event_type": args.get("causal_event_type", ""),
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
