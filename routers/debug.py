"""调试接口 — timeline / timers / router-log / action-log."""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter

from . import _state as state

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/timeline")
async def debug_timeline(after: int = 0, limit: int = 50):
    """chain log 条目，支持增量拉取. after=已有条数, limit=最多返回."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    entries = state.orchestrator.chain_log.all_entries()
    total = len(entries)
    new_entries = entries[after:after + limit]
    return {
        "total": total,
        "offset": after,
        "entries": [dataclasses.asdict(e) for e in new_entries],
    }


@router.get("/timers")
async def debug_timers():
    """待触发定时器 + 最近20条已触发."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    pending = [
        {
            "timer_id": t.timer_id,
            "target": t.target_agent,
            "from": t.payload.get("from", ""),
            "message": t.payload.get("message", "")[:100],
            "created_at": t.created_at,
            "fire_at": t.fire_at,
            "remaining_seconds": round(max(0, t.fire_at - state.orchestrator.sim_time), 1),
        }
        for t in state.orchestrator.timers.get_in_transit()
    ]
    return {"pending": pending, "fired": list(state.orchestrator.fired_timers)[-20:]}


@router.get("/router-log")
async def debug_router_log(after: int = 0):
    """Router 决策日志，增量拉取."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    log = list(state.orchestrator.router_log)
    total = len(log)
    return {"total": total, "entries": log[after:]}


@router.get("/action-log")
async def debug_action_log(after: int = 0):
    """ActionAgent 执行日志（含多轮 FC trace），增量拉取."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    log = list(state.orchestrator.action_log)
    total = len(log)
    return {"total": total, "entries": log[after:]}
