"""模拟控制 + 设置 + 天玑 AI + WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from multiagent_framework.llm_client import LLMConfig
from multiagent_framework.runtime_paths import RUNTIME_DIR
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


@router.get("/memorials")
async def get_memorials():
    """返回当前所有待处理奏折（后端持久化，刷新不丢）."""
    if not state.orchestrator:
        return {"error": "not initialized"}
    return state.orchestrator.memorials


# ── 新游戏 ──


@router.post("/new-game")
async def new_game():
    """清空 runtime/ 目录，从 seed 重新初始化."""
    from main import init_orchestrator

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

    # 清空 runtime/ 目录
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)

    # 重新初始化（is_fresh=True 会触发 seed）
    state.orchestrator = init_orchestrator()
    return {"status": "new_game_started"}


# ── 设置 ──

from pathlib import Path


class SettingsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: int = 30
    max_concurrent: int = 4
    seconds_per_day: float = 600.0


@router.get("/settings")
async def get_settings():
    if not state.orchestrator:
        return {"error": "not initialized"}
    llm_display = state.orchestrator.router.llm.config.to_display_dict()
    llm_display["seconds_per_day"] = state.orchestrator._seconds_per_day
    return llm_display


@router.post("/settings")
async def save_settings(req: SettingsRequest):
    if not state.orchestrator:
        return {"error": "not initialized"}

    llm_client = state.orchestrator.router.llm
    old_config = llm_client.config

    new_config = LLMConfig(
        base_url=req.base_url or old_config.base_url,
        api_key=req.api_key if req.api_key and not req.api_key.startswith("****") else old_config.api_key,
        model=req.model or old_config.model,
        timeout_s=req.timeout_s,
        max_concurrent=req.max_concurrent,
    )

    await llm_client.update_config(new_config)
    new_config.save_to_file("config/llm.json")

    if req.seconds_per_day != state.orchestrator._seconds_per_day:
        if not (state.orchestrator.is_running and not state.orchestrator.is_paused):
            state.orchestrator.set_time_scale(req.seconds_per_day)

    time_data = {"seconds_per_day": req.seconds_per_day, "tick_interval": 1.0}
    Path("config").mkdir(parents=True, exist_ok=True)
    with open("config/time.json", "w", encoding="utf-8") as f:
        json.dump(time_data, f, indent=2)

    return {"status": "ok"}


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
    world_state_names = {"query_entity", "query_inventory", "update_entity_status",
                         "update_inventory", "complete_action"}
    router_names = {"wake_agent", "create_timer", "execute_policy", "relay_decree"}
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
