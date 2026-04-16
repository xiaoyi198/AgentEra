"""世界状态 — 实体 + 库存 CRUD."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from . import _state as state

router = APIRouter(prefix="/api", tags=["worldstate"])


@router.get("/world-state")
async def get_world_state():
    """返回所有实体 + 库存."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    return ws.export_all()


@router.get("/world-state/{entity_id}")
async def get_world_entity(entity_id: str):
    """返回单个实体详情 + 库存."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    ent = ws.get_entity(entity_id)
    if not ent:
        return {"error": "not found", "entity_id": entity_id}
    inventory = ws.get_inventory(entity_id)
    return {
        "entity": {
            "id": ent.id,
            "type": ent.type,
            "name": ent.name,
            "description": ent.description,
            "current_status": ent.current_status,
            "updated_at": ent.updated_at,
            "metadata": ent.metadata,
        },
        "inventory": [
            {
                "category": it.category,
                "item_name": it.item_name,
                "quantity": it.quantity,
                "description": it.description,
            }
            for it in inventory
        ],
    }


class WorldStateStatusRequest(BaseModel):
    new_status: str


@router.post("/world-state/{entity_id}/status")
async def update_world_entity_status(entity_id: str, req: WorldStateStatusRequest):
    """手动修改实体的 current_status（调试用）."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    if not ws.get_entity(entity_id):
        return {"error": "not found", "entity_id": entity_id}
    ws.update_status(entity_id, req.new_status, state.orchestrator.sim_time)
    return {"status": "ok", "entity_id": entity_id, "new_status": req.new_status}


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
    return {"status": "ok" if ok else "not_found"}


class InventoryItemRequest(BaseModel):
    resource_name: str
    quantity: float


@router.put("/world-state/{entity_id}/inventory")
async def upsert_inventory_item(entity_id: str, req: InventoryItemRequest):
    """设置实体的某项资源代币（绝对赋值，调试用）。仅允许 7 种标准代币。"""
    ws = getattr(state.orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    from multiagent_framework.world_state import RESOURCE_META
    meta = RESOURCE_META.get(req.resource_name)
    if not meta:
        return {"error": f"非标准资源代币: {req.resource_name}"}
    ws.set_inventory(entity_id, req.resource_name, req.resource_name, req.quantity, meta["unit"])
    return {"status": "ok"}


@router.delete("/world-state/{entity_id}/inventory/{category}/{item_name}")
async def delete_inventory_item_api(entity_id: str, category: str, item_name: str):
    """标准资源代币不可删除。"""
    return {"error": "标准资源代币不可删除", "status": "forbidden"}
