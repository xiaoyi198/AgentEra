"""大明王朝·朝堂风云 — 后端入口."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.chain_log import ChainLog
from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.llm_client import LLMClient, LLMConfig
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.router import RouterAgent
from multiagent_framework.tianji_agent import TianJiAgent
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.types import TickResult
from multiagent_framework.world_state import WorldState

from routers import _state as state
from routers.simulation import router as simulation_router, ws_router
from routers.saves import router as saves_router
from routers.editor import router as editor_router
from routers.worldstate import router as worldstate_router
from routers.debug import router as debug_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ── WebSocket 广播 ──

async def broadcast_tick(tick: TickResult) -> None:
    """将 tick 结果推送给所有 WebSocket 客户端."""
    data = tick.to_dict()
    dead: set[WebSocket] = set()
    for ws in state.ws_clients:
        try:
            await ws.send_json({"type": "tick", "data": data})
        except Exception:
            dead.add(ws)
    for ws in dead:
        state.ws_clients.discard(ws)


# ── 初始化 ──

def init_orchestrator(load_from_save: bool = False) -> Orchestrator:
    """从配置文件初始化 Orchestrator."""
    llm_config_path = "config/llm.json"
    if Path(llm_config_path).exists():
        llm_config = LLMConfig.from_file(llm_config_path)
    else:
        logger.warning("No LLM config found, using defaults (will fail on real calls)")
        llm_config = LLMConfig()

    llm_client = LLMClient(llm_config)

    time_config_path = "config/time.json"
    seconds_per_day = 600.0
    if Path(time_config_path).exists():
        with open(time_config_path, encoding="utf-8") as f:
            tc = json.load(f)
            seconds_per_day = tc.get("seconds_per_day", 600.0)

    db_path = "daming_court.db"
    court = CourtGraph(db_path)
    timers = TimerSystem(db_path)
    chain_log = ChainLog("chain_log.jsonl")
    # 每次初始化时清空旧的 chain_log，避免跨会话/跨存档的日志累积
    chain_log.clear()

    router_prompt = ""
    router_prompt_path = "daming_court/prompts/router.txt"
    if Path(router_prompt_path).exists():
        router_prompt = Path(router_prompt_path).read_text(encoding="utf-8")

    router = RouterAgent(llm_client, court, router_prompt, seconds_per_day)

    world_state = WorldState(db_path)
    action_agent = ActionAgent(llm_client, world_state)

    from multiagent_framework.memory_compressor import LLMMemoryCompressor
    memory_compressor = LLMMemoryCompressor(llm_client)

    orch = Orchestrator(
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        router=router,
        seconds_per_day=seconds_per_day,
        on_tick=broadcast_tick,
        action_agent=action_agent,
        memory_compressor=memory_compressor,
    )
    orch.world_state = world_state  # type: ignore[attr-defined]

    state.tianji_agent = TianJiAgent(
        llm_client=llm_client,
        world_state=world_state,
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        action_agent=action_agent,
        router=router,
    )

    _load_dlc(orch, court, llm_client, world_state, load_from_save=load_from_save)

    return orch


def _load_dlc(
    orch: Orchestrator,
    court: CourtGraph,
    llm: LLMClient,
    world_state: WorldState,
    load_from_save: bool = False,
) -> None:
    """加载大明朝廷 DLC 数据."""
    from daming_court.loader import (
        create_agents,
        load_events,
        load_locations,
        load_officials,
        load_world_state,
    )

    officials_path = "daming_court/config/officials.json"
    prompts_dir = "daming_court/prompts"
    world_state_path = "daming_court/config/world_state.json"

    if not load_from_save:
        if Path(world_state_path).exists():
            ent_cnt, inv_cnt = load_world_state(world_state_path, world_state)
            logger.info("Loaded world state: %d entities, %d inventory items",
                        ent_cnt, inv_cnt)
        else:
            logger.info("No world_state.json at %s, skipped", world_state_path)

    if Path(officials_path).exists():
        officials = load_officials(officials_path)
        court.bulk_load_officials(officials)
        locations = load_locations(officials_path)
        for loc in locations:
            court.add_location(loc)
        if locations:
            logger.info("Loaded %d locations into court_graph", len(locations))
        agents = create_agents(officials, prompts_dir, llm)
        for agent in agents:
            orch.register_agent(agent)
        logger.info("Loaded %d officials, %d agents", len(officials), len(agents))
    else:
        logger.warning("No officials config found at %s", officials_path)

    if not load_from_save:
        events = load_events()
        for event in events:
            orch.inject_event(event)
        if events:
            logger.info("Injected %d initial events", len(events))


# ── FastAPI ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    state.orchestrator = init_orchestrator()
    yield
    if state._orch_task and not state._orch_task.done():
        state._orch_task.cancel()
    if state.orchestrator:
        state.orchestrator.stop()


app = FastAPI(title="大明王朝·朝堂风云", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(simulation_router)
app.include_router(saves_router)
app.include_router(editor_router)
app.include_router(worldstate_router)
app.include_router(debug_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
