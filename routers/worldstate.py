"""世界状态 — 实体 + 库存 + 关系 CRUD."""

from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from . import _state as state
from multiagent_framework.time_simulator import TimeSimulator

router = APIRouter(prefix="/api", tags=["worldstate"])


def _active_time_simulator() -> TimeSimulator:
    """Return the published simulator or a deterministic safe fallback."""
    orchestrator = state.orchestrator
    simulator = getattr(orchestrator, "time_simulator", None) if orchestrator else None
    return simulator if isinstance(simulator, TimeSimulator) else TimeSimulator()


def _ws():
    """取运行中的 WorldState，没有则返回 None。"""
    if not state.orchestrator:
        return None
    return getattr(state.orchestrator, "world_state", None)


@router.get("/world-state")
async def get_world_state():
    """返回所有实体 + 库存."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    return ws.export_all()


@router.get("/world-state/economy")
async def get_world_economy():
    """Return a secret-free anchored-value invariant dashboard snapshot."""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    snapshot = ws.get_economic_invariants()
    runtime = (
        state.orchestrator.operational_metrics_snapshot()
        if state.orchestrator and hasattr(state.orchestrator, "operational_metrics_snapshot")
        else {}
    )
    batches = max(0, int(runtime.get("causal_effect_batches", 0) or 0))
    rejections = max(0, int(runtime.get("causal_effect_rejections", 0) or 0))
    failures = max(0, int(runtime.get("causal_effect_failures", 0) or 0))
    snapshot["causal_effects"] = {
        "batches": batches,
        "commands": max(0, int(runtime.get("causal_effect_commands", 0) or 0)),
        "rejections": rejections,
        "failures": failures,
        "rejection_rate": round((rejections + failures) / batches, 6) if batches else 0.0,
    }
    snapshot["sim_time"] = round(float(getattr(state.orchestrator, "sim_time", 0.0)), 3)
    return snapshot


@router.get("/world-state/{entity_id}")
async def get_world_entity(entity_id: str):
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    ent = ws.get_entity(entity_id)
    if not ent:
        return {"error": "not found", "entity_id": entity_id}
    sim_time = state.orchestrator.sim_time if state.orchestrator else 0.0
    return {
        "entity": {
            "id": ent.id, "type": ent.type, "name": ent.name,
            "description": ent.description, "current_status": ent.current_status,
            "updated_at": ent.updated_at, "metadata": ent.metadata,
            "version": ent.version,
        },
        "inventory": [{
            "category": item.category, "item_name": item.item_name,
            "quantity": item.quantity, "description": item.description,
            "version": item.version, "anchor_rate": item.anchor_rate,
            "base_value": item.base_value,
        } for item in ws.get_inventory(entity_id)],
        "metrics": [{
            "name": value.name, "value": value.value,
            "description": value.description, "version": value.version,
        } for value in ws.get_entity_values(entity_id, "metric") if value.value != -1],
        "parameters": [{
            "name": value.name, "value": value.value,
            "description": value.description, "version": value.version,
        } for value in ws.get_entity_values(entity_id, "parameter") if value.value != -1],
        "inventory_valuation": ws.get_inventory_valuation(entity_id),
        "relations": {
            "parents": ws.get_parents(entity_id),
            "children": ws.get_children(entity_id),
            "siblings": ws.get_siblings(entity_id),
        },
        "ongoing_events": [{
            "event_id": event.event_id, "event_type": event.event_type,
            "description": event.description, "start_time": event.start_time,
            "end_time": event.end_time,
            "days_remaining": max(0.0, (event.end_time - sim_time) / 86400.0),
            "expected_result": event.expected_result,
            "source_action_id": event.source_action_id,
        } for event in ws.list_ongoing_events(entity_id)],
    }


class WorldStateStatusRequest(BaseModel):
    new_status: str
    expected_version: int | None = None


@router.post("/world-state/{entity_id}/status")
async def update_world_entity_status(
    entity_id: str, req: WorldStateStatusRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    if not ws.get_entity(entity_id):
        return {"error": "not found", "entity_id": entity_id}
    changed = ws.update_status(
        entity_id, req.new_status,
        state.orchestrator.sim_time if state.orchestrator else 0.0,
        command_id=idempotency_key, expected_version=req.expected_version,
        chain_id="api:world-state",
    )
    return {"status": "ok", **changed}


class WorldStateEntityRequest(BaseModel):
    type: str
    name: str
    description: str = ""
    current_status: str = ""
    metadata: dict = {}


@router.put("/world-state/{entity_id}")
async def upsert_world_entity(entity_id: str, req: WorldStateEntityRequest):
    """创建或更新实体（调试用）."""
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    from multiagent_framework.world_state import Entity
    ent = Entity(
        id=entity_id,
        type=req.type,
        name=req.name,
        description=req.description,
        current_status=req.current_status,
        updated_at=state.orchestrator.sim_time if state.orchestrator else 0.0,
        metadata=req.metadata,
    )
    ws.upsert_entity(ent)
    return {"status": "ok", "entity_id": entity_id}


@router.delete("/world-state/{entity_id}")
async def delete_world_entity(entity_id: str):
    """删除实体及其全部库存（调试用）."""
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    ok = ws.delete_entity(entity_id)
    return {"status": "ok" if ok else "not_found", **duration_metadata}


class InventoryItemRequest(BaseModel):
    resource_name: str
    quantity: float
    expected_version: int | None = None
    reason: str = "api_set"


@router.put("/world-state/{entity_id}/inventory")
async def upsert_inventory_item(
    entity_id: str, req: InventoryItemRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    definition = ws.get_resource_definition(req.resource_name)
    if not definition:
        return {"error": f"resource not registered: {req.resource_name}"}
    value = ws.set_inventory(
        entity_id, req.resource_name, req.resource_name, req.quantity, definition.unit,
        command_id=idempotency_key, expected_version=req.expected_version,
        chain_id="api:world-state",
        sim_time=state.orchestrator.sim_time if state.orchestrator else 0.0,
        reason=req.reason,
    )
    return {
        "status": "ok", "resource_name": req.resource_name,
        "kind": definition.kind, "value": value,
        "inventory_valuation": ws.get_inventory_valuation(entity_id),
    }


@router.delete("/world-state/{entity_id}/inventory/{category}/{item_name}")
async def delete_inventory_item_api(entity_id: str, category: str, item_name: str):
    """标准资源代币不可删除。"""
    return {"error": "标准资源代币不可删除", "status": "forbidden"}


# ── 关系图 ──

@router.get("/relations")
async def list_relations():
    """返回整张关系图：所有实体（轻量）+ 所有边。"""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    entities = ws.get_all_entities()
    return {
        "entities": [
            {"id": e.id, "type": e.type, "name": e.name}
            for e in entities
        ],
        "relations": ws.get_all_relations(),
    }


@router.get("/world-state/{entity_id}/relations")
async def get_entity_relations(entity_id: str):
    """返回单个实体的 parents / children / siblings."""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    return {
        "parents": ws.get_parents(entity_id),
        "children": ws.get_children(entity_id),
        "siblings": ws.get_siblings(entity_id),
    }


class RelationRequest(BaseModel):
    parent_id: str
    child_id: str
    rel_type: str
    note: str = ""


@router.post("/relations")
async def upsert_relation(req: RelationRequest):
    """创建或覆盖一条边。"""
    from multiagent_framework.world_state import RELATION_TYPES
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    if req.rel_type not in RELATION_TYPES:
        return {"error": f"非法关系类型: {req.rel_type}", "valid": sorted(RELATION_TYPES)}
    if req.parent_id == req.child_id:
        return {"error": "parent_id 与 child_id 不能相同"}
    ws.add_relation(req.parent_id, req.child_id, req.rel_type, req.note)
    return {"status": "ok"}


@router.delete("/relations")
async def delete_relation(parent_id: str, child_id: str, rel_type: str):
    """删除一条边。通过查询参数 ?parent_id=...&child_id=...&rel_type=... 传入。"""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    ok = ws.remove_relation(parent_id, child_id, rel_type)
    return {"status": "ok" if ok else "not_found"}


# ── 正在发生的事情（OngoingEvent） ──

@router.get("/world-state/{entity_id}/events")
async def list_entity_events(entity_id: str):
    """返回某实体的所有 ongoing events，附带 days_remaining。"""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    sim_time = state.orchestrator.sim_time if state.orchestrator else 0.0
    seconds_per_day = 86400.0
    events = ws.list_ongoing_events(entity_id)
    return {
        "entity_id": entity_id,
        "events": [
            {
                "event_id": ev.event_id,
                "event_type": ev.event_type,
                "description": ev.description,
                "start_time": ev.start_time,
                "end_time": ev.end_time,
                "days_remaining": max(0.0, (ev.end_time - sim_time) / seconds_per_day),
                "expected_result": ev.expected_result,
                "source_action_id": ev.source_action_id,
            }
            for ev in events
        ],
    }


class OngoingEventCreateRequest(BaseModel):
    event_type: str = "state_change"
    description: str = ""
    duration_days: float = 1.0
    duration_mode: Literal["estimate", "override"] = "estimate"
    expected_result: dict = Field(default_factory=dict)
    source_action_id: str = ""


@router.post("/world-state/{entity_id}/events")
async def create_entity_event(
    entity_id: str, req: OngoingEventCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """手动为实体添加一条 ongoing event（调试/天玑视角用）。"""
    import uuid as _uuid

    from multiagent_framework.world_state import OngoingEvent

    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    if not ws.get_entity(entity_id):
        return {"error": "entity not found", "entity_id": entity_id}
    sim_time = state.orchestrator.sim_time if state.orchestrator else 0.0
    seconds_per_day = 86400.0
    if not math.isfinite(req.duration_days) or req.duration_days < 0:
        return {"error": "duration_days must be a finite non-negative number"}
    if req.duration_mode == "override":
        duration_days = req.duration_days
        duration_metadata = {"source": "manual_override", "rule_id": "manual.override"}
    else:
        estimate = _active_time_simulator().estimate(
            action_type=req.event_type, proposed_days=req.duration_days,
        )
        duration_days = estimate.duration_days
        duration_metadata = {"source": estimate.source, "rule_id": estimate.rule_id}
    ev = OngoingEvent(
        event_id=(
            f"evt_{_uuid.uuid5(_uuid.NAMESPACE_URL, idempotency_key).hex[:10]}"
            if idempotency_key else f"evt_{_uuid.uuid4().hex[:10]}"
        ),
        entity_id=entity_id,
        event_type=req.event_type,
        description=req.description,
        start_time=sim_time,
        end_time=sim_time + duration_days * seconds_per_day,
        expected_result=req.expected_result or {},
        source_action_id=req.source_action_id,
    )
    ws.add_ongoing_event(
        ev, command_id=idempotency_key, chain_id="api:world-state",
    )
    return {"status": "ok", "event_id": ev.event_id, "duration_days": duration_days, **duration_metadata}


class OngoingEventPatchRequest(BaseModel):
    description: str | None = None
    duration_days: float | None = None
    duration_mode: Literal["estimate", "override"] = "estimate"
    expected_result: dict | None = None
    event_type: str | None = None


@router.patch("/world-state/events/{event_id}")
async def patch_entity_event(
    event_id: str, req: OngoingEventPatchRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """修改一条 ongoing event 的字段。"""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    kwargs: dict = {}
    if req.description is not None:
        kwargs["description"] = req.description
    duration_metadata = {}
    if req.duration_days is not None:
        if not math.isfinite(req.duration_days) or req.duration_days < 0:
            return {"error": "duration_days must be a finite non-negative number"}
        sim_time = state.orchestrator.sim_time if state.orchestrator else 0.0
        if req.duration_mode == "override":
            duration_days = req.duration_days
            duration_metadata = {"source": "manual_override", "rule_id": "manual.override"}
        else:
            existing = ws.get_ongoing_event(event_id)
            action_type = req.event_type or (existing.event_type if existing else "state_change")
            estimate = _active_time_simulator().estimate(
                action_type=action_type, proposed_days=req.duration_days,
            )
            duration_days = estimate.duration_days
            duration_metadata = {"source": estimate.source, "rule_id": estimate.rule_id}
        kwargs["end_time"] = sim_time + duration_days * 86400.0
    if req.expected_result is not None:
        kwargs["expected_result"] = req.expected_result
    if req.event_type is not None:
        kwargs["event_type"] = req.event_type
    ok = ws.update_ongoing_event(
        event_id, **kwargs, command_id=idempotency_key,
        chain_id="api:world-state",
        sim_time=state.orchestrator.sim_time if state.orchestrator else 0.0,
    )
    response = {"status": "ok" if ok else "not_found"}
    if ok and duration_metadata:
        response.update(duration_metadata)
        response["duration_days"] = duration_days
    return response


@router.delete("/world-state/events/{event_id}")
async def delete_entity_event(
    event_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """取消一条 ongoing event（不会触发 expected_result 的应用）。"""
    ws = _ws()
    if ws is None:
        return {"error": "world_state not initialized"}
    ok = ws.delete_ongoing_event(
        event_id, command_id=idempotency_key, chain_id="api:world-state",
        sim_time=state.orchestrator.sim_time if state.orchestrator else 0.0,
    )
    return {"status": "ok" if ok else "not_found"}
