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
from multiagent_framework.dlc_registry import DLCRegistry, DLCRegistryError
from multiagent_framework.dlc_runtime import DLCBundleLoader, ModelBindingResolver
from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.model_gateway import ModelGateway, TaskLLMClient
from multiagent_framework.model_generation_archive import ModelGenerationArchiveStore
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.router import RouterAgent
from multiagent_framework.runtime_paths import (
    RUNTIME_DIR, WORLD_DB, CHAIN_LOG, STATE_FILE, AGENTS_DIR,
    ensure_runtime_dir, runtime_exists,
)
from multiagent_framework.runtime_persistor import RuntimePersistor
from multiagent_framework.runtime_settings import RuntimeSettings
from multiagent_framework.tianji_agent import TianJiAgent
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.types import TickResult
from multiagent_framework.world_state import WorldState
from multiagent_framework.version import __version__

from routers import _state as state
from routers.simulation import router as simulation_router, ws_router
from routers.saves import router as saves_router
from routers.editor import router as editor_router
from routers.worldstate import router as worldstate_router
from routers.debug import router as debug_router
from routers.metrics import router as metrics_router
from routers.settings import router as settings_router
from routers.dlc import router as dlc_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _task_client_for_primary_dlc(
    gateway: ModelGateway,
    primary_dlc: Any,
    task_type: str,
    *,
    agent_id: str,
) -> TaskLLMClient:
    """Bind world-global LLM tasks to the primary DLC policy when configured."""
    client = gateway.for_task(task_type, agent_id=agent_id)
    if primary_dlc is None:
        return client
    package_id = str(getattr(primary_dlc.manifest, "id", ""))
    if package_id and hasattr(client, "for_dlc"):
        client = client.for_dlc(package_id)
    resolver = ModelBindingResolver(client, primary_dlc.manifest.model_bindings)
    profile = resolver.resolve("", task_type=task_type)
    return resolver.bind(agent_id, profile)


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

    project_root = Path(__file__).resolve().parent
    dlc_registry = DLCRegistry(project_root)
    primary_dlc = dlc_registry.primary_package()
    primary_prompts: dict[str, str] = {}
    if primary_dlc is not None:
        primary_prompts = dlc_registry.load_content(primary_dlc.manifest.id).get("prompts", {})

    runtime_settings = RuntimeSettings.from_file("config/runtime.json")

    model_gateway = ModelGateway.from_files(
        models_path="config/models.json",
        legacy_path="config/llm.json",
        generation_archive=ModelGenerationArchiveStore(
            RUNTIME_DIR / "model_generations"
        ),
    )
    router_llm = _task_client_for_primary_dlc(
        model_gateway, primary_dlc, "router.ambiguous", agent_id="router"
    )
    action_llm = _task_client_for_primary_dlc(
        model_gateway, primary_dlc, "action.simulation", agent_id="action_agent"
    )
    memory_llm = _task_client_for_primary_dlc(
        model_gateway, primary_dlc, "memory.compress", agent_id="memory_compressor"
    )
    tianji_llm = _task_client_for_primary_dlc(
        model_gateway, primary_dlc, "tianji.chat", agent_id="tianji"
    )
    # Agent clients are bound per package/agent by DLCBundleLoader. Applying the
    # primary profile here would incorrectly leak it into secondary DLC agents.
    agent_llm = model_gateway.for_task("agent.turn")

    seconds_per_day = runtime_settings.simulation.seconds_per_day
    tick_interval = runtime_settings.simulation.tick_interval

    db_path = str(WORLD_DB)              # runtime/world.db
    is_fresh = force_fresh or (not runtime_exists() and not load_from_save)

    court = CourtGraph(db_path)
    timers = TimerSystem(db_path)
    chain_log = ChainLog(str(CHAIN_LOG)) # runtime/chain_log.jsonl

    router_prompt = str(primary_prompts.get("router", ""))
    router = RouterAgent(router_llm, court, router_prompt, seconds_per_day)

    world_state = WorldState(db_path)
    court.set_world_state(world_state)
    action_agent = ActionAgent(
        action_llm,
        world_state,
        court=court,
        prompt_template=primary_prompts.get("action_agent"),
        **runtime_settings.action_agent.__dict__,
    )

    from multiagent_framework.memory_compressor import LLMMemoryCompressor
    memory_compressor = LLMMemoryCompressor(
        memory_llm, temperature=runtime_settings.memory.compression_temperature
    )

    orch = Orchestrator(
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        router=router,
        seconds_per_day=seconds_per_day,
        tick_interval=tick_interval,
        on_tick=broadcast_tick,
        action_agent=action_agent,
        memory_compressor=memory_compressor,
    )
    orch.persistor = RuntimePersistor(orch)
    orch.runtime_settings = runtime_settings
    orch.model_gateway = model_gateway
    orch.dlc_registry = dlc_registry

    state.tianji_agent = TianJiAgent(
        llm_client=tianji_llm,
        world_state=world_state,
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        action_agent=action_agent,
        router=router,
        system_prompt=primary_prompts.get("tianji"),
    )
    orch.tianji_agent = state.tianji_agent

    _load_dlcs(
        orch, court, agent_llm, world_state, dlc_registry,
        load_from_save=not is_fresh,  # 有 runtime → 不再 seed entity/event
    )

    if not is_fresh:
        _load_runtime_state(orch)

    return orch


def _load_dlcs(
    orch: Orchestrator,
    court: CourtGraph,
    llm: TaskLLMClient,
    world_state: WorldState,
    registry: DLCRegistry,
    load_from_save: bool = False,
) -> None:
    """Load all enabled, published DLCs in dependency order."""
    try:
        packages = registry.ordered_active_packages()
        if not packages:
            logger.warning("No active published DLC found; using legacy fallback")
            _load_legacy_dlc(orch, court, llm, world_state, load_from_save)
            return
        defaults = getattr(getattr(orch, "runtime_settings", None), "agent_defaults", None)
        loader = DLCBundleLoader(
            registry, world_state, court, orch, llm,
            agent_defaults=(defaults.__dict__ if defaults is not None else None),
        )
        reports = loader.load_active(seed_world=not load_from_save)
        orch.dlc_loader = loader
        for report in reports:
            logger.info(
                "Loaded DLC %s: resources=%d entities=%d inventory=%d values=%d officials=%d agents=%d events=%d",
                report.package_id, report.resources, report.entities, report.inventory,
                report.entity_values, report.officials, report.agents, report.initial_events,
            )
    except DLCRegistryError:
        logger.exception("DLC Registry load failed")
        raise


def _load_legacy_dlc(
    orch: Orchestrator,
    court: CourtGraph,
    llm: TaskLLMClient,
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
        defaults = getattr(getattr(orch, "runtime_settings", None), "agent_defaults", None)
        if defaults is not None:
            for agent in agents:
                for key, value in defaults.__dict__.items():
                    setattr(agent.config, key, value)
        for agent in agents:
            orch.register_agent(agent)
        orch.configure_gameplay_contract(
            GameplayContract.from_content(
                {},
                [
                    {
                        "id": item.id,
                        "name": item.name,
                        "role": item.role,
                    }
                    for item in officials
                ],
                package_id="legacy_fallback",
                package_name="Legacy World",
            ),
            generation=0,
        )
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
            legacy_memorials = s.get("memorials", [])
            if hasattr(orch, "issue_manager"):
                orch.issue_manager.restore(
                    s.get("issue_runtime", {}), sim_time=orch._sim_time,
                    legacy_memorials=legacy_memorials,
                )
                orch._sync_legacy_memorials()
            else:
                orch.memorials = legacy_memorials
            orch.router.update_time_scale(orch._seconds_per_day)
            if hasattr(orch, "random_event_generator"):
                orch.random_event_generator.restore(
                    s.get("random_events", {}), sim_time=orch._sim_time
                )
            if hasattr(orch, "causal_event_engine"):
                orch.causal_event_engine.restore(
                    s.get("causal_events", {}), sim_time=orch._sim_time
                )
            if hasattr(orch, "action_catalog"):
                orch.action_catalog.restore(
                    s.get("action_runtime", {}), sim_time=orch._sim_time
                )
            if hasattr(orch, "objective_manager"):
                orch.objective_manager.restore(
                    s.get("objective_runtime", {}), sim_time=orch._sim_time
                )
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
        model_gateway = getattr(state.orchestrator, "model_gateway", None)
        if model_gateway:
            await model_gateway.close()


app = FastAPI(title="大明王朝·朝堂风云", version=__version__, lifespan=lifespan)
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
app.include_router(metrics_router)
app.include_router(settings_router)
app.include_router(dlc_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
