"""大明王朝·朝堂风云 — 后端入口."""

from __future__ import annotations

import asyncio
import json
import logging
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
from multiagent_framework.runtime_paths import (
    RUNTIME_DIR, WORLD_DB, CHAIN_LOG, STATE_FILE, AGENTS_DIR,
    ensure_runtime_dir, runtime_exists,
)
from multiagent_framework.runtime_persistor import RuntimePersistor
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

def init_orchestrator(
    load_from_save: bool = False,
    force_fresh: bool = False,
) -> Orchestrator:
    """从配置文件初始化 Orchestrator.

    冷启动逻辑：
    - runtime/ 不存在 → is_fresh=True，从 daming_court/ seed 初始化
    - runtime/ 存在 → is_fresh=False，从 runtime/ 恢复状态，不再重新 seed
    - load_from_save=True → 读档流程，runtime/ 已由调用方替换好，按非 fresh 处理
    - force_fresh=True → 新游戏，强制按 fresh 处理（忽略 runtime/ 是否存在）
    """
    ensure_runtime_dir()

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

    db_path = str(WORLD_DB)              # runtime/world.db
    is_fresh = force_fresh or (not runtime_exists() and not load_from_save)

    court = CourtGraph(db_path)
    timers = TimerSystem(db_path)
    chain_log = ChainLog(str(CHAIN_LOG)) # runtime/chain_log.jsonl

    router_prompt = ""
    router_prompt_path = "daming_court/prompts/router.txt"
    if Path(router_prompt_path).exists():
        router_prompt = Path(router_prompt_path).read_text(encoding="utf-8")

    router = RouterAgent(llm_client, court, router_prompt, seconds_per_day)

    world_state = WorldState(db_path)
    court.set_world_state(world_state)
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
    orch.persistor = RuntimePersistor(orch)

    state.tianji_agent = TianJiAgent(
        llm_client=llm_client,
        world_state=world_state,
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        action_agent=action_agent,
        router=router,
    )

    _load_dlc(
        orch, court, llm_client, world_state,
        load_from_save=not is_fresh,  # 有 runtime → 不再 seed entity/event
    )

    if not is_fresh:
        _load_runtime_state(orch)

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
        load_officials,
        load_world_state,
    )

    officials_path = "daming_court/config/officials.json"
    prompts_dir = "daming_court/prompts"
    world_state_path = "daming_court/config/world_state.json"

    if not load_from_save:
        if Path(world_state_path).exists():
            ent_cnt, inv_cnt, rel_cnt = load_world_state(world_state_path, world_state)
            logger.info("Loaded world state: %d entities, %d inventory items, %d relations",
                        ent_cnt, inv_cnt, rel_cnt)
        else:
            logger.info("No world_state.json at %s, skipped", world_state_path)

    if Path(officials_path).exists():
        officials = load_officials(officials_path)
        court.bulk_load_officials(officials)
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


def _load_runtime_state(orch: Orchestrator) -> None:
    """从 runtime/ 目录恢复运行时状态（sim_time / agent 记忆 / chain_log）."""
    # state.json
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            orch._sim_time = s.get("sim_time", 0.0)
            orch._seconds_per_day = s.get("seconds_per_day", 600.0)
            orch.memorials = s.get("memorials", [])
            orch.router.update_time_scale(orch._seconds_per_day)
            logger.info("Restored state: sim_time=%.1f, %d memorials",
                        orch._sim_time, len(orch.memorials))
        except Exception as e:
            logger.warning("Failed to load state.json: %s", e)

    # 从 DB 恢复定时器
    timer_count = orch.timers.load_from_db()
    if timer_count:
        logger.info("Restored %d timers", timer_count)

    # chain_log 从 JSONL 文件恢复
    loaded = orch.chain_log.load_from_file()
    if loaded:
        logger.info("Restored %d chain log entries", loaded)

    # agent 记忆
    restored = 0
    for agent_id, agent in orch.agents.items():
        mem_path = AGENTS_DIR / f"{agent_id}.json"
        if mem_path.exists():
            try:
                data = json.loads(mem_path.read_text(encoding="utf-8"))
                agent.deserialize_memory(data)
                restored += 1
            except Exception as e:
                logger.warning("Failed to load memory for %s: %s", agent_id, e)
    if restored:
        logger.info("Restored memories for %d/%d agents", restored, len(orch.agents))


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
