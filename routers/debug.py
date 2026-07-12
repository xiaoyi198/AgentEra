"""调试接口 — timeline / timers / router-log / action-log."""

from __future__ import annotations

import dataclasses
import hmac
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import _state as state

router = APIRouter(prefix="/api/debug", tags=["debug"])


class DLCRollbackRequest(BaseModel):
    source_generation: int = Field(ge=1)


def _require_model_admin(token: str | None) -> None:
    enabled = os.environ.get("WORLD_SIM_ENABLE_ADMIN_API", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not found")
    expected = os.environ.get("WORLD_SIM_ADMIN_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def _model_gateway():
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail="Simulation is not initialized")
    gateway = getattr(state.orchestrator, "model_gateway", None)
    if not gateway:
        raise HTTPException(status_code=503, detail="Model gateway is not initialized")
    return gateway


def _dlc_loader():
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail="Simulation is not initialized")
    loader = getattr(state.orchestrator, "dlc_loader", None)
    if not loader:
        raise HTTPException(status_code=503, detail="DLC runtime is not initialized")
    return loader


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


@router.get("/llm-metrics")
async def debug_llm_metrics():
    """Aggregated model metrics grouped by task/profile/provider/model."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    gateway = getattr(state.orchestrator, "model_gateway", None)
    if not gateway:
        return {"error": "model gateway not initialized"}
    return gateway.metrics_snapshot()


@router.get("/llm-traces")
async def debug_llm_traces(limit: int = 100, task_type: str = ""):
    """Recent redacted model spans; prompt and response bodies are never returned."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    gateway = getattr(state.orchestrator, "model_gateway", None)
    if not gateway:
        return {"error": "model gateway not initialized"}
    return {
        "entries": gateway.recent_traces(limit=max(1, min(limit, 1000)), task_type=task_type),
    }


@router.get("/model-config")
async def debug_model_config():
    """Return non-secret provider, model profile, and task policy metadata."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    gateway = getattr(state.orchestrator, "model_gateway", None)
    if not gateway:
        return {"error": "model gateway not initialized"}
    return gateway.public_config()


@router.post("/model-config/preview")
async def preview_model_config_reload(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Validate the configured local model file and return a secret-free diff."""
    _require_model_admin(x_admin_token)
    gateway = _model_gateway()
    try:
        return gateway.preview_from_file()
    except (OSError, ValueError, KeyError):
        raise HTTPException(
            status_code=400, detail="Model configuration validation failed"
        ) from None


@router.post("/model-config/reload")
async def reload_model_config(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Atomically reload the configured local model file after admin authentication."""
    _require_model_admin(x_admin_token)
    gateway = _model_gateway()
    try:
        return await gateway.reload_from_file()
    except (OSError, ValueError, KeyError, RuntimeError):
        raise HTTPException(
            status_code=400, detail="Model configuration reload failed"
        ) from None


@router.get("/dlc-runtime")
async def debug_dlc_runtime():
    """Return secret-free DLC runtime generation and operational counters."""
    return _dlc_loader().public_runtime()


@router.post("/dlc-runtime/preview")
async def preview_dlc_runtime_reload(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Validate local published DLCs and return hashes, IDs, counts, and diffs only."""
    _require_model_admin(x_admin_token)
    try:
        return _dlc_loader().preview_reload()
    except (OSError, ValueError, KeyError, RuntimeError):
        raise HTTPException(
            status_code=400, detail="DLC runtime validation failed"
        ) from None


@router.post("/dlc-runtime/reload")
async def reload_dlc_runtime(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Activate a locally prepared DLC runtime generation without replaying the world."""
    _require_model_admin(x_admin_token)
    try:
        return await _dlc_loader().reload_active()
    except (OSError, ValueError, KeyError, RuntimeError):
        raise HTTPException(
            status_code=400, detail="DLC runtime reload failed"
        ) from None


@router.post("/dlc-runtime/rollback/preview")
async def preview_dlc_runtime_rollback(
    request: DLCRollbackRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Preview a retained runtime generation as a non-destructive new activation."""
    _require_model_admin(x_admin_token)
    try:
        return _dlc_loader().preview_rollback(request.source_generation)
    except (OSError, ValueError, KeyError, RuntimeError):
        raise HTTPException(
            status_code=400, detail="DLC runtime rollback validation failed"
        ) from None


@router.post("/dlc-runtime/rollback")
async def rollback_dlc_runtime(
    request: DLCRollbackRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Activate a retained blueprint as a new monotonic runtime generation."""
    _require_model_admin(x_admin_token)
    try:
        return await _dlc_loader().rollback_active(request.source_generation)
    except (OSError, ValueError, KeyError, RuntimeError):
        raise HTTPException(
            status_code=400, detail="DLC runtime rollback failed"
        ) from None


@router.get("/action-log")
async def debug_action_log(after: int = 0):
    """ActionAgent 执行日志（含多轮 FC trace），增量拉取."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    log = list(state.orchestrator.action_log)
    total = len(log)
    return {"total": total, "entries": log[after:]}

@router.get("/agent-turns")
async def debug_agent_turns():
    """普通 Agent 最近一次多轮执行的脱敏状态与预算消耗。"""
    if not state.orchestrator:
        return {"error": "not initialized"}
    entries = []
    for agent_id, agent in state.orchestrator.agents.items():
        outcome = getattr(agent, "last_turn_outcome", None)
        last_turn = None
        if outcome is not None:
            last_turn = {
                "stop_reason": outcome.stop_reason,
                "completed": outcome.completed,
                "terminal_tool": outcome.terminal_tool,
                "rounds": outcome.rounds,
                "tool_calls": outcome.tool_calls,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "elapsed_ms": round(outcome.elapsed_ms, 2),
                "model_latency_ms": round(outcome.model_latency_ms, 2),
                "error": outcome.error,
            }
        entries.append({
            "agent_id": agent_id,
            "agent_type": agent.agent_type,
            "busy": bool(getattr(agent, "is_busy", False)),
            "inbox_sequence": int(getattr(agent, "inbox_sequence", 0)),
            "last_turn": last_turn,
        })
    return {"entries": entries, "count": len(entries)}

@router.get("/router-dead-letters")
async def debug_router_dead_letters(after: int = 0, limit: int = 100):
    """Recent bounded Router rejects; payload/prompt bodies are intentionally omitted."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    entries = list(getattr(state.orchestrator.router, "dead_letters", []))
    start = max(0, int(after))
    capped = max(1, min(int(limit), 1000))
    return {
        "total": len(entries),
        "offset": start,
        "entries": entries[start:start + capped],
        "router_stats": dict(getattr(state.orchestrator.router, "last_stats", {}) or {}),
    }
