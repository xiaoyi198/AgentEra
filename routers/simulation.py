"""模拟控制 + 设置 + 天玑 AI + WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from multiagent_framework.action_contract import ActionContractError
from multiagent_framework.capability_contract import CapabilityContractError
from multiagent_framework.issue_contract import IssueContractError
from multiagent_framework.types import PlayerAction, PlayerActionType

from . import _state as state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["simulation"])

# ── 模拟控制 ──


class PlayerActionRequest(BaseModel):
    action_type: str  # approve / reject / hold / issue_decree
    memorial_id: str = ""
    content: str = ""
    note: str = ""


@router.post("/player-action")
async def player_action(req: PlayerActionRequest):
    if not state.orchestrator:
        return {"error": "Orchestrator not initialized"}
    action = PlayerAction(
        action_type=PlayerActionType(req.action_type),
        memorial_id=req.memorial_id,
        content=req.content,
        note=req.note,
    )
    await state.orchestrator.inject_player_action(action)
    return {"status": "ok"}


@router.post("/start")
async def start_simulation():
    if not state.orchestrator:
        return {"error": "not initialized"}
    if state.orchestrator.is_running and not state.orchestrator.is_paused:
        return {"status": "already_running"}
    if state.orchestrator.is_paused:
        state.orchestrator.resume()
        return {"status": "resumed"}
    state._orch_task = asyncio.create_task(state.orchestrator.run())
    return {"status": "started"}


@router.post("/pause")
async def pause_simulation():
    if not state.orchestrator:
        return {"error": "not initialized"}
    await state.orchestrator.pause()
    return {"status": "pausing"}


@router.post("/resume")
async def resume_simulation():
    if not state.orchestrator:
        return {"error": "not initialized"}
    state.orchestrator.resume()
    return {"status": "resumed"}


# ── 时间倍率 ──


@router.get("/time-scale")
async def get_time_scale():
    if not state.orchestrator:
        return {"error": "not initialized"}
    return {
        "seconds_per_day": state.orchestrator._seconds_per_day,
        "sim_time": state.orchestrator.sim_time,
        "sim_day": state.orchestrator.sim_day,
    }


class TimeScaleRequest(BaseModel):
    seconds_per_day: float


@router.post("/time-scale")
async def set_time_scale(req: TimeScaleRequest):
    if not state.orchestrator:
        return {"error": "not initialized"}
    if state.orchestrator.is_running and not state.orchestrator.is_paused:
        return {"error": "must_pause_first"}
    state.orchestrator.set_time_scale(req.seconds_per_day)
    return {"status": "ok", "seconds_per_day": req.seconds_per_day}


# ── 杂项查询 ──


@router.get("/chain-log/{chain_id}")
async def get_chain_log(chain_id: str):
    if not state.orchestrator:
        return {"error": "not initialized"}
    return state.orchestrator.chain_log.to_god_view(chain_id)


@router.get("/token-usage")
async def get_token_usage():
    if not state.orchestrator:
        return {"error": "not initialized"}
    return state.orchestrator.router.llm.token_counter.to_dict()


@router.get("/in-transit")
async def get_in_transit():
    if not state.orchestrator:
        return {"error": "not initialized"}
    timers = state.orchestrator.timers.get_in_transit()
    return [
        {
            "timer_id": t.timer_id,
            "target": t.target_agent,
            "from": t.payload.get("from", ""),
            "remaining_seconds": max(0, t.fire_at - state.orchestrator.sim_time),
            "fire_at": t.fire_at,
        }
        for t in timers
    ]


# ── 奏折列表（持久化） ──


@router.get("/gameplay")
async def get_gameplay():
    """Return the active DLC-authored player identity and control boundary."""
    if not state.orchestrator:
        return {
            "configured": False,
            "generation": 0,
            "package_id": "",
            "package_name": "",
            "source": "none",
            "player": None,
        }
    return state.orchestrator.gameplay_public_state()

@router.get("/ui-manifest")
async def get_ui_manifest():
    """Return the current generation's capability-safe DLC UI manifest."""
    if not state.orchestrator:
        from multiagent_framework.ui_manifest import UIManifest
        return {**UIManifest.from_content({}).to_dict(), "generation": 0}
    return state.orchestrator.ui_manifest_public_state()

class CapabilityInvocationRequest(BaseModel):
    invocation_id: str = ""
    expected_generation: int | None = None
    source_id: str = ""
    content: str = ""
    target_id: str = ""
    target_ids: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    urgency: str = ""
    actor_id: str = ""
    control_object_id: str = ""
    executor_id: str = ""
    location: str = ""


def _capability_http_error(exc: Exception) -> HTTPException:
    code = str(getattr(exc, "code", "capability.execution_failed"))
    status_code = 400
    if code in {"capability.not_found", "action.not_found"}:
        status_code = 404
    elif code in {
        "capability.generation_conflict", "capability.invocation.conflict",
        "action.generation_conflict", "action.invocation.conflict",
        "action.not_ready", "action.world_command_failed",
    }:
        status_code = 409
    return HTTPException(status_code=status_code, detail={
        "code": code,
        "message": str(exc),
        "path": str(getattr(exc, "path", "") or ""),
        "details": dict(getattr(exc, "details", {}) or {}),
    })


@router.get("/capabilities")
async def get_capabilities(audience: str = "player"):
    """Return the active generation's DLC-authored capability registry."""
    if not state.orchestrator:
        return {
            "configured": False, "generation": 0, "fingerprint": "",
            "audience": audience or "player", "capabilities": [],
        }
    selected_audience = audience if audience in {"player", "agent", "router"} else "player"
    return state.orchestrator.capabilities_public_state(selected_audience)


@router.post("/capabilities/{capability_id}/invoke")
async def invoke_capability(capability_id: str, req: CapabilityInvocationRequest):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "capability.orchestrator_unavailable"})
    payload = req.model_dump()
    try:
        return await state.orchestrator.invoke_capability(
            capability_id,
            payload,
            audience="player",
            source_id=req.source_id,
        )
    except (CapabilityContractError, ActionContractError) as exc:
        raise _capability_http_error(exc) from exc


class DLCActionInvocationRequest(BaseModel):
    invocation_id: str = ""
    expected_generation: int | None = None
    actor_id: str = ""
    control_object_id: str = ""
    target_id: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


def _action_http_error(exc: ActionContractError) -> HTTPException:
    status_code = 400
    if exc.code == "action.not_found":
        status_code = 404
    elif exc.code in {
        "action.generation_conflict", "action.invocation.conflict",
        "action.not_ready", "action.world_command_failed",
    }:
        status_code = 409
    return HTTPException(status_code=status_code, detail={
        "code": exc.code, "message": str(exc), "path": exc.path,
        "details": exc.details,
    })


@router.get("/actions")
async def get_actions():
    """Return the current generation's DLC-authored action catalog."""
    if not state.orchestrator:
        return {"configured": False, "generation": 0, "fingerprint": "", "actions": []}
    return state.orchestrator.actions_public_state()


@router.post("/actions/{action_id}/preview")
async def preview_action(action_id: str, req: DLCActionInvocationRequest):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "action.orchestrator_unavailable"})
    try:
        return state.orchestrator.preview_dlc_action(action_id, req.model_dump())
    except ActionContractError as exc:
        raise _action_http_error(exc) from exc


@router.post("/actions/{action_id}/execute")
async def execute_action(action_id: str, req: DLCActionInvocationRequest):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "action.orchestrator_unavailable"})
    try:
        return state.orchestrator.execute_dlc_action(action_id, req.model_dump())
    except ActionContractError as exc:
        raise _action_http_error(exc) from exc


def _issue_http_error(exc: IssueContractError) -> HTTPException:
    status_code = 400
    if exc.code in {"issue.not_found", "case.not_found", "decision.not_found", "issue.type_not_found"}:
        status_code = 404
    elif exc.code in {
        "issue.generation_conflict", "issue.external_id_conflict", "issue.id_conflict",
        "case.closed", "issue.closed", "issue.evidence.id_conflict",
        "decision.id_conflict", "decision.not_pending", "decision.expired",
    }:
        status_code = 409
    return HTTPException(status_code=status_code, detail={
        "code": exc.code, "message": str(exc), "path": exc.path,
        "details": exc.details,
    })


@router.get("/issue-types")
async def get_issue_types():
    if not state.orchestrator:
        return {"configured": False, "generation": 0, "issue_types": [], "counts": {}}
    return state.orchestrator.issues_public_state()


@router.get("/objectives")
async def get_objectives():
    if not state.orchestrator:
        return {
            "configured": False, "sandbox": True, "generation": 0,
            "status": "sandbox", "objectives": [], "endings": [],
            "current_ending": None, "recent_transitions": [],
        }
    return state.orchestrator.objectives_public_state()


@router.get("/endings")
async def get_endings():
    if not state.orchestrator:
        return {"generation": 0, "status": "sandbox", "sandbox": True, "endings": [], "current_ending": None}
    objective_state = state.orchestrator.objectives_public_state()
    return {
        "generation": objective_state["generation"],
        "status": objective_state["status"],
        "sandbox": objective_state["sandbox"],
        "endings": objective_state["endings"],
        "current_ending": objective_state["current_ending"],
    }


@router.get("/issues")
async def get_issues(status: str = ""):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    return state.orchestrator.list_issues(status=status)


@router.post("/issues")
async def open_issue(payload: dict[str, Any]):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    try:
        return state.orchestrator.open_issue(payload)
    except IssueContractError as exc:
        raise _issue_http_error(exc) from exc


@router.post("/issues/{issue_id}/evidence")
async def append_issue_evidence(issue_id: str, payload: dict[str, Any]):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    try:
        return state.orchestrator.append_issue_evidence(issue_id, payload)
    except IssueContractError as exc:
        raise _issue_http_error(exc) from exc


@router.get("/cases")
async def get_cases(status: str = ""):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    return state.orchestrator.list_cases(status=status)


@router.post("/cases/{case_id}/close")
async def close_case(case_id: str, payload: dict[str, Any]):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    try:
        return state.orchestrator.close_case(case_id, payload)
    except IssueContractError as exc:
        raise _issue_http_error(exc) from exc


@router.get("/decision-requests")
async def get_decision_requests(status: str = ""):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    return state.orchestrator.list_decision_requests(status=status)


@router.post("/decision-requests/{request_id}/preview")
async def preview_decision(request_id: str, payload: dict[str, Any]):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    try:
        return state.orchestrator.preview_decision(request_id, payload)
    except (IssueContractError, ActionContractError) as exc:
        if isinstance(exc, ActionContractError):
            raise _action_http_error(exc) from exc
        raise _issue_http_error(exc) from exc


@router.post("/decision-requests/{request_id}/resolve")
async def resolve_decision(request_id: str, payload: dict[str, Any]):
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail={"code": "issue.orchestrator_unavailable"})
    try:
        return state.orchestrator.resolve_decision(request_id, payload)
    except (IssueContractError, ActionContractError) as exc:
        if isinstance(exc, ActionContractError):
            raise _action_http_error(exc) from exc
        raise _issue_http_error(exc) from exc


@router.get("/memorials")
async def get_memorials():
    """返回当前所有待处理奏折（后端持久化，刷新不丢）."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    state.orchestrator._sync_legacy_memorials()
    return state.orchestrator.memorials


# ── 新游戏 ──


@router.post("/new-game")
async def new_game():
    """清空 runtime/ 数据，从 seed 重新初始化."""
    from main import init_orchestrator
    import gc
    import sqlite3

    if state.orchestrator:
        await state.orchestrator.pause()
        await state.orchestrator.drain_agent_tasks()
        state.orchestrator.stop()
    if state._orch_task and not state._orch_task.done():
        state._orch_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(state._orch_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        state._orch_task = None

    state.orchestrator = None
    gc.collect()

    from multiagent_framework.runtime_paths import (
        WORLD_DB, CHAIN_LOG, STATE_FILE, AGENTS_DIR,
    )

    # 用空数据库覆盖 world.db，绕过 Windows 文件锁
    if WORLD_DB.exists():
        src = sqlite3.connect(":memory:")
        dst = sqlite3.connect(str(WORLD_DB))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    # 清理其他 runtime 文件
    if CHAIN_LOG.exists():
        CHAIN_LOG.unlink(missing_ok=True)
    if STATE_FILE.exists():
        STATE_FILE.unlink(missing_ok=True)
    if AGENTS_DIR.exists():
        for f in AGENTS_DIR.glob("*.json"):
            f.unlink(missing_ok=True)

    # force_fresh=True 确保重新 seed
    state.orchestrator = init_orchestrator(force_fresh=True)
    return {"status": "new_game_started"}


# Settings center routes live in routers/settings.py.


# ── 天玑 AI ──


class TianJiChatRequest(BaseModel):
    message: str


@router.post("/tianji/chat")
async def tianji_chat(req: TianJiChatRequest):
    """天玑 AI 对话接口，流式 SSE 响应."""
    if not state.tianji_agent:
        return {"error": "TianJiAgent not initialized"}

    async def event_stream():
        try:
            async for event in state.tianji_agent.chat(req.message):
                data = json.dumps(event, ensure_ascii=False)
                yield f"data: {data}\n\n"
        except Exception as e:
            logger.exception("tianji_chat SSE error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tianji/reset")
async def tianji_reset():
    """重置天玑 AI 对话历史."""
    if not state.tianji_agent:
        return {"error": "TianJiAgent not initialized"}
    state.tianji_agent.reset()
    return {"status": "ok", "session_id": state.tianji_agent.session_id}


@router.get("/tianji/history")
async def tianji_history():
    """返回当前对话历史（供前端刷新后恢复展示）."""
    if not state.tianji_agent:
        return {"error": "TianJiAgent not initialized"}
    history = [
        m for m in state.tianji_agent.get_history()
        if m.get("role") in ("user", "assistant", "tool")
    ]
    return {"messages": history, "session_id": state.tianji_agent.session_id}


@router.get("/tianji/tools")
async def tianji_tools():
    """返回天玑 AI 的完整工具 schema（前端侧边栏展示用）."""
    if not state.tianji_agent:
        return {"error": "TianJiAgent not initialized"}
    tools = state.tianji_agent._build_tools()
    world_state_names = {"list_entities", "query_entity", "update_entity_status",
                         "update_inventory", "add_ongoing_event", "modify_ongoing_event",
                         "delete_ongoing_event", "complete_action"}
    router_names = {"wake_agent", "execute_policy", "relay_decree"}
    agent_names = {"submit_action", "request_action"}

    categorized = {"世界状态": [], "路由指令": [], "官员行为": [], "其他": []}
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "")
        item = {"name": name, "description": fn.get("description", ""), "parameters": fn.get("parameters", {})}
        if name in world_state_names:
            categorized["世界状态"].append(item)
        elif name in router_names:
            categorized["路由指令"].append(item)
        elif name in agent_names:
            categorized["官员行为"].append(item)
        else:
            categorized["其他"].append(item)
    return {"categories": categorized}


# ── WebSocket ──

ws_router = APIRouter(tags=["websocket"])


@ws_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state.ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(state.ws_clients))
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "player_action":
                action = PlayerAction(
                    action_type=PlayerActionType(data["action_type"]),
                    memorial_id=data.get("memorial_id", ""),
                    content=data.get("content", ""),
                    note=data.get("note", ""),
                )
                await state.orchestrator.inject_player_action(action)
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(state.ws_clients))
