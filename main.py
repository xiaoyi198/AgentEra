"""大明王朝·朝堂风云 — 后端入口."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import sqlite3
import time as _time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.chain_log import ChainLog
from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.llm_client import LLMClient, LLMConfig
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.router import RouterAgent
from multiagent_framework.tianji_agent import TianJiAgent
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.types import PlayerAction, PlayerActionType, TickResult
from multiagent_framework.world_state import WorldState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
# 压掉高频噪音日志：uvicorn 每个请求一行、httpx 每个 LLM 调用一行
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ── 全局状态 ──
orchestrator: Orchestrator | None = None
tianji_agent: TianJiAgent | None = None
ws_clients: set[WebSocket] = set()
_orch_task: asyncio.Task | None = None


# ── WebSocket 广播 ──

async def broadcast_tick(tick: TickResult) -> None:
    """将 tick 结果推送给所有 WebSocket 客户端."""
    data = tick.to_dict()
    dead: set[WebSocket] = set()
    for ws in ws_clients:
        try:
            await ws.send_json({"type": "tick", "data": data})
        except Exception:
            dead.add(ws)
    for ws in dead:
        ws_clients.discard(ws)


# ── 初始化 ──

def init_orchestrator(load_from_save: bool = False) -> Orchestrator:
    """从配置文件初始化 Orchestrator."""
    # 加载 LLM 配置
    llm_config_path = "config/llm.json"
    if Path(llm_config_path).exists():
        llm_config = LLMConfig.from_file(llm_config_path)
    else:
        logger.warning("No LLM config found, using defaults (will fail on real calls)")
        llm_config = LLMConfig()

    llm_client = LLMClient(llm_config)

    # 加载时间配置
    time_config_path = "config/time.json"
    seconds_per_day = 600.0
    if Path(time_config_path).exists():
        with open(time_config_path, encoding="utf-8") as f:
            tc = json.load(f)
            seconds_per_day = tc.get("seconds_per_day", 600.0)

    # 初始化组件
    db_path = "daming_court.db"
    court = CourtGraph(db_path)
    timers = TimerSystem(db_path)
    chain_log = ChainLog("chain_log.jsonl")

    # 加载 Router prompt
    router_prompt = ""
    router_prompt_path = "daming_court/prompts/router.txt"
    if Path(router_prompt_path).exists():
        router_prompt = Path(router_prompt_path).read_text(encoding="utf-8")

    router = RouterAgent(llm_client, court, router_prompt, seconds_per_day)

    # 世界状态 + 行动 Agent
    world_state = WorldState(db_path)
    action_agent = ActionAgent(llm_client, world_state)

    orch = Orchestrator(
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        router=router,
        seconds_per_day=seconds_per_day,
        on_tick=broadcast_tick,
        action_agent=action_agent,
    )
    # 暴露给 API 层使用
    orch.world_state = world_state  # type: ignore[attr-defined]

    # 初始化天玑 AI（全工具注入的调试对话 Agent）
    global tianji_agent
    tianji_agent = TianJiAgent(
        llm_client=llm_client,
        world_state=world_state,
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        action_agent=action_agent,
        router=router,
    )

    # 加载 DLC 数据（官员、Agent、初始世界状态等）
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

    # 加载初始世界状态（读档时 SQLite 已有最新数据，跳过以免覆盖）
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
        # 同步加载地理坐标到 CourtGraph（WorldStateView 坐标面板依赖此数据源）
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

    # 注入初始事件（读档时定时器已从 SQLite 恢复，跳过以免重复触发）
    if not load_from_save:
        events = load_events()
        for event in events:
            orch.inject_event(event)
        if events:
            logger.info("Injected %d initial events", len(events))


# ── FastAPI ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, _orch_task
    orchestrator = init_orchestrator()
    # 不自动启动 — 等前端连接后手动启动
    yield
    if _orch_task and not _orch_task.done():
        _orch_task.cancel()
    if orchestrator:
        orchestrator.stop()


app = FastAPI(title="大明王朝·朝堂风云", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── REST 接口 ──

class PlayerActionRequest(BaseModel):
    action_type: str  # approve / reject / hold / issue_decree
    memorial_id: str = ""
    content: str = ""
    note: str = ""


@app.post("/api/player-action")
async def player_action(req: PlayerActionRequest):
    if not orchestrator:
        return {"error": "Orchestrator not initialized"}
    action = PlayerAction(
        action_type=PlayerActionType(req.action_type),
        memorial_id=req.memorial_id,
        content=req.content,
        note=req.note,
    )
    await orchestrator.inject_player_action(action)
    return {"status": "ok"}


@app.post("/api/start")
async def start_simulation():
    global _orch_task
    if not orchestrator:
        return {"error": "not initialized"}
    if orchestrator.is_running and not orchestrator.is_paused:
        return {"status": "already_running"}
    if orchestrator.is_paused:
        orchestrator.resume()
        return {"status": "resumed"}
    _orch_task = asyncio.create_task(orchestrator.run())
    return {"status": "started"}


@app.post("/api/pause")
async def pause_simulation():
    if not orchestrator:
        return {"error": "not initialized"}
    await orchestrator.pause()
    return {"status": "pausing"}


@app.post("/api/resume")
async def resume_simulation():
    if not orchestrator:
        return {"error": "not initialized"}
    orchestrator.resume()
    return {"status": "resumed"}


_SAVE_DIR = Path("saves")
_DB_PATH = "daming_court.db"
_CHAIN_LOG_PATH = "chain_log.jsonl"
_BAD_CHARS = r'\/:*?"<>|'


def _sanitize(name: str) -> str:
    """将存档名净化为合法目录名."""
    cleaned = "".join(c for c in name if c not in _BAD_CHARS and ord(c) >= 32)
    cleaned = cleaned.strip(" .")
    return cleaned or f"save_{int(_time.time())}"


def _backup_sqlite(src: str, dest: Path) -> None:
    """用 SQLite backup API 热备份，不锁源文件."""
    with sqlite3.connect(src) as src_conn:
        with sqlite3.connect(str(dest)) as dst_conn:
            src_conn.backup(dst_conn)


def _read_meta(save_dir: Path) -> dict | None:
    meta_path = save_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None


class SaveRequest(BaseModel):
    memorials: list[dict] = []
    save_name: str = ""


class LoadRequest(BaseModel):
    save_name: str


class RenameRequest(BaseModel):
    old_name: str
    new_name: str


@app.get("/api/saves")
async def list_saves():
    """列出所有存档（扫描 saves/ 子目录）."""
    _SAVE_DIR.mkdir(exist_ok=True)
    saves = []
    for d in sorted(_SAVE_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = _read_meta(d)
        if meta:
            saves.append(meta)
    saves.sort(key=lambda m: m.get("saved_at", 0), reverse=True)
    return saves


@app.post("/api/save")
async def save_game(req: SaveRequest):
    if not orchestrator:
        return {"error": "not initialized"}
    if not orchestrator.is_paused:
        return {"error": "must pause before saving"}

    # 确定存档名
    sim_day = int(orchestrator.sim_time / 86400)
    raw_name = req.save_name.strip() or f"第{sim_day}天-{random.randint(1000, 9999)}"
    name = _sanitize(raw_name)

    save_path = _SAVE_DIR / name
    _SAVE_DIR.mkdir(exist_ok=True)

    # 如目录已存在则先清空（覆盖模式，前端已弹确认）
    if save_path.exists():
        shutil.rmtree(save_path)
    save_path.mkdir(parents=True)

    # 1. 把内存定时器刷到 SQLite
    orchestrator.save_state()  # 内部调用 timers.save_all()

    # 2. 热备份 SQLite
    _backup_sqlite(_DB_PATH, save_path / "daming_court.db")

    # 3. 拷贝 chain log（可能不存在）
    if Path(_CHAIN_LOG_PATH).exists():
        shutil.copy2(_CHAIN_LOG_PATH, save_path / "chain_log.jsonl")

    # 4. 写 runtime.json（agent 记忆 + 待处理奏折）
    runtime = {
        "agent_memories": {
            aid: agent.serialize_memory()
            for aid, agent in orchestrator.agents.items()
        },
        "pending_memorials": req.memorials,
        "seconds_per_day": orchestrator._seconds_per_day,
    }
    (save_path / "runtime.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 5. 写 meta.json
    saved_at = _time.time()
    meta = {
        "save_name": name,
        "saved_at": saved_at,
        "sim_time": orchestrator.sim_time,
        "sim_day": sim_day,
        "seconds_per_day": orchestrator._seconds_per_day,
        "agent_count": len(orchestrator.agents),
        "timer_count": orchestrator.timers.count(),
        "memorial_count": len(req.memorials),
    }
    (save_path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info("Game saved to saves/%s: sim_time=%.1f, %d agents, %d timers",
                name, orchestrator.sim_time, len(orchestrator.agents),
                orchestrator.timers.count())
    return {"status": "saved", "save_name": name, "saved_at": saved_at, "sim_day": sim_day}


@app.post("/api/load")
async def load_game(req: LoadRequest):
    global orchestrator, _orch_task
    name = _sanitize(req.save_name)
    save_path = _SAVE_DIR / name
    if not save_path.exists():
        return {"error": f"存档不存在：{name}"}
    meta = _read_meta(save_path)
    if not meta:
        return {"error": "存档 meta.json 损坏"}
    runtime_path = save_path / "runtime.json"
    if not runtime_path.exists():
        return {"error": "存档 runtime.json 损坏"}
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))

    # 停止当前模拟
    if _orch_task and not _orch_task.done():
        _orch_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(_orch_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        _orch_task = None
    if orchestrator:
        orchestrator.stop()

    # 覆盖工作目录的 SQLite 和 chain log
    shutil.copy2(save_path / "daming_court.db", _DB_PATH)
    chain_src = save_path / "chain_log.jsonl"
    if chain_src.exists():
        shutil.copy2(chain_src, _CHAIN_LOG_PATH)
    elif Path(_CHAIN_LOG_PATH).exists():
        Path(_CHAIN_LOG_PATH).unlink()

    # 重新初始化（跳过初始事件注入和世界状态重置，从 DB 读）
    orchestrator = init_orchestrator(load_from_save=True)

    # 恢复运行时状态
    state = {
        "sim_time": meta["sim_time"],
        "seconds_per_day": runtime.get("seconds_per_day", meta.get("seconds_per_day", 600.0)),
        "agent_memories": runtime.get("agent_memories", {}),
    }
    orchestrator.load_state(state)

    logger.info("Game loaded from saves/%s: sim_time=%.1f, %d memorials",
                name, meta["sim_time"], len(runtime.get("pending_memorials", [])))
    return {
        "status": "loaded",
        "save_name": name,
        "sim_time": meta["sim_time"],
        "seconds_per_day": state["seconds_per_day"],
        "pending_memorials": runtime.get("pending_memorials", []),
        "saved_at": meta.get("saved_at"),
    }


@app.post("/api/saves/rename")
async def rename_save(req: RenameRequest):
    old = _sanitize(req.old_name)
    new = _sanitize(req.new_name)
    old_path = _SAVE_DIR / old
    new_path = _SAVE_DIR / new
    if not old_path.exists():
        return {"error": f"存档不存在：{old}"}
    if new_path.exists():
        return {"error": f"目标名称已存在：{new}"}
    old_path.rename(new_path)
    # 更新 meta.json 内的 save_name
    meta = _read_meta(new_path)
    if meta:
        meta["save_name"] = new
        (new_path / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {"status": "renamed", "save_name": new}


@app.delete("/api/saves/{name}")
async def delete_save(name: str):
    safe = _sanitize(name)
    save_path = _SAVE_DIR / safe
    if not save_path.exists():
        return {"error": f"存档不存在：{safe}"}
    shutil.rmtree(save_path)
    return {"status": "deleted", "save_name": safe}


@app.get("/api/time-scale")
async def get_time_scale():
    if not orchestrator:
        return {"error": "not initialized"}
    return {
        "seconds_per_day": orchestrator._seconds_per_day,
        "sim_time": orchestrator.sim_time,
        "sim_day": orchestrator.sim_day,
    }


class TimeScaleRequest(BaseModel):
    seconds_per_day: float


@app.post("/api/time-scale")
async def set_time_scale(req: TimeScaleRequest):
    if not orchestrator:
        return {"error": "not initialized"}
    if orchestrator.is_running and not orchestrator.is_paused:
        return {"error": "must_pause_first"}
    orchestrator.set_time_scale(req.seconds_per_day)
    return {"status": "ok", "seconds_per_day": req.seconds_per_day}


@app.get("/api/chain-log/{chain_id}")
async def get_chain_log(chain_id: str):
    if not orchestrator:
        return {"error": "not initialized"}
    return orchestrator.chain_log.to_god_view(chain_id)


@app.get("/api/token-usage")
async def get_token_usage():
    if not orchestrator:
        return {"error": "not initialized"}
    return orchestrator.router.llm.token_counter.to_dict()


@app.get("/api/in-transit")
async def get_in_transit():
    if not orchestrator:
        return {"error": "not initialized"}
    timers = orchestrator.timers.get_in_transit()
    return [
        {
            "timer_id": t.timer_id,
            "target": t.target_agent,
            "from": t.payload.get("from", ""),
            "remaining_seconds": max(0, t.fire_at - orchestrator.sim_time),
            "fire_at": t.fire_at,
        }
        for t in timers
    ]


# ── 调试接口 ──

@app.get("/api/debug/timeline")
async def debug_timeline(after: int = 0, limit: int = 50):
    """chain log 条目，支持增量拉取. after=已有条数, limit=最多返回."""
    if not orchestrator:
        return {"error": "not initialized"}
    import dataclasses
    entries = orchestrator.chain_log.all_entries()
    total = len(entries)
    # 只返回 after 之后的新条目
    new_entries = entries[after:after + limit]
    return {
        "total": total,
        "offset": after,
        "entries": [dataclasses.asdict(e) for e in new_entries],
    }


@app.get("/api/debug/timers")
async def debug_timers():
    """待触发定时器 + 最近20条已触发."""
    if not orchestrator:
        return {"error": "not initialized"}
    pending = [
        {
            "timer_id": t.timer_id,
            "target": t.target_agent,
            "from": t.payload.get("from", ""),
            "message": t.payload.get("message", "")[:100],
            "created_at": t.created_at,
            "fire_at": t.fire_at,
            "remaining_seconds": round(max(0, t.fire_at - orchestrator.sim_time), 1),
        }
        for t in orchestrator.timers.get_in_transit()
    ]
    return {"pending": pending, "fired": orchestrator.fired_timers[-20:]}


@app.get("/api/debug/router-log")
async def debug_router_log(after: int = 0):
    """Router 决策日志，增量拉取."""
    if not orchestrator:
        return {"error": "not initialized"}
    log = orchestrator.router_log
    total = len(log)
    return {"total": total, "entries": log[after:]}


@app.get("/api/debug/action-log")
async def debug_action_log(after: int = 0):
    """ActionAgent 执行日志（含多轮 FC trace），增量拉取."""
    if not orchestrator:
        return {"error": "not initialized"}
    log = orchestrator.action_log
    total = len(log)
    return {"total": total, "entries": log[after:]}


@app.get("/api/world-state")
async def get_world_state():
    """返回所有实体 + 库存."""
    if not orchestrator:
        return {"error": "not initialized"}
    ws = getattr(orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    return ws.export_all()


@app.get("/api/world-state/{entity_id}")
async def get_world_entity(entity_id: str):
    """返回单个实体详情 + 库存."""
    if not orchestrator:
        return {"error": "not initialized"}
    ws = getattr(orchestrator, "world_state", None)
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


@app.post("/api/world-state/{entity_id}/status")
async def update_world_entity_status(entity_id: str, req: WorldStateStatusRequest):
    """手动修改实体的 current_status（调试用）."""
    if not orchestrator:
        return {"error": "not initialized"}
    ws = getattr(orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    if not ws.get_entity(entity_id):
        return {"error": "not found", "entity_id": entity_id}
    ws.update_status(entity_id, req.new_status, orchestrator.sim_time)
    return {"status": "ok", "entity_id": entity_id, "new_status": req.new_status}


class WorldStateEntityRequest(BaseModel):
    type: str
    name: str
    description: str = ""
    current_status: str = ""
    metadata: dict = {}


@app.put("/api/world-state/{entity_id}")
async def upsert_world_entity(entity_id: str, req: WorldStateEntityRequest):
    """创建或更新实体（调试用）."""
    ws = getattr(orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    from multiagent_framework.world_state import Entity
    ent = Entity(
        id=entity_id,
        type=req.type,
        name=req.name,
        description=req.description,
        current_status=req.current_status,
        updated_at=orchestrator.sim_time if orchestrator else 0.0,
        metadata=req.metadata,
    )
    ws.upsert_entity(ent)
    return {"status": "ok", "entity_id": entity_id}


@app.delete("/api/world-state/{entity_id}")
async def delete_world_entity(entity_id: str):
    """删除实体及其全部库存（调试用）."""
    ws = getattr(orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    ok = ws.delete_entity(entity_id)
    return {"status": "ok" if ok else "not_found"}


class InventoryItemRequest(BaseModel):
    category: str
    item_name: str
    quantity: float
    description: str = ""


@app.put("/api/world-state/{entity_id}/inventory")
async def upsert_inventory_item(entity_id: str, req: InventoryItemRequest):
    """设置实体的某项库存（绝对赋值，调试用）."""
    ws = getattr(orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    ws.set_inventory(entity_id, req.category, req.item_name, req.quantity, req.description)
    return {"status": "ok"}


@app.delete("/api/world-state/{entity_id}/inventory/{category}/{item_name}")
async def delete_inventory_item_api(entity_id: str, category: str, item_name: str):
    """删除实体的单条库存记录（调试用）."""
    ws = getattr(orchestrator, "world_state", None)
    if ws is None:
        return {"error": "world_state not initialized"}
    ok = ws.delete_inventory_item(entity_id, category, item_name)
    return {"status": "ok" if ok else "not_found"}


@app.get("/api/prompts")
async def get_prompts():
    """返回所有可编辑 prompt 文件."""
    result = {}
    for name, path in [
        ("official_template", "daming_court/prompts/official_template.txt"),
        ("router", "daming_court/prompts/router.txt"),
        ("personas", "daming_court/config/personas.json"),
    ]:
        p = Path(path)
        if p.exists():
            result[name] = p.read_text(encoding="utf-8")
        else:
            result[name] = ""
    return result


class PromptSaveRequest(BaseModel):
    name: str  # official_template / router / personas
    content: str


@app.post("/api/prompts")
async def save_prompt(req: PromptSaveRequest):
    """保存 prompt 文件."""
    paths = {
        "official_template": "daming_court/prompts/official_template.txt",
        "router": "daming_court/prompts/router.txt",
        "personas": "daming_court/config/personas.json",
    }
    path = paths.get(req.name)
    if not path:
        return {"error": f"unknown prompt: {req.name}"}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(req.content, encoding="utf-8")
    # 如果是 router prompt，热更新
    if req.name == "router" and orchestrator:
        orchestrator.router._system_prompt = req.content
    return {"status": "ok"}


# ── Agent 编辑器接口 ──

OFFICIALS_PATH = "daming_court/config/officials.json"
PROMPTS_DIR = "daming_court/prompts"


def _read_officials_json() -> dict:
    with open(OFFICIALS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_officials_json(data: dict) -> None:
    Path(OFFICIALS_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(OFFICIALS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/editor/officials")
async def editor_get_officials():
    """返回完整的 officials + locations 配置."""
    if not Path(OFFICIALS_PATH).exists():
        return {"officials": [], "locations": []}
    return _read_officials_json()


class OfficialUpsertRequest(BaseModel):
    id: str
    name: str
    rank: int = 1
    location_id: str = ""
    role: str = "official"
    agent_type: str = "official"
    superior_id: str | None = None
    personality_seed: str = "忠直"
    enabled: bool = True
    extra_prompt: str = ""


@app.put("/api/editor/officials/{official_id}")
async def editor_update_official(official_id: str, req: OfficialUpsertRequest):
    """更新或新增一个官员."""
    data = _read_officials_json()
    officials = data.get("officials", [])

    # 查找已有
    found = False
    for i, o in enumerate(officials):
        if o["id"] == official_id:
            officials[i] = req.model_dump()
            found = True
            break
    if not found:
        officials.append(req.model_dump())

    data["officials"] = officials
    _write_officials_json(data)
    return {"status": "ok", "created": not found}


@app.delete("/api/editor/officials/{official_id}")
async def editor_delete_official(official_id: str):
    """删除一个官员."""
    data = _read_officials_json()
    before = len(data.get("officials", []))
    data["officials"] = [o for o in data.get("officials", []) if o["id"] != official_id]
    _write_officials_json(data)
    return {"status": "ok", "deleted": len(data["officials"]) < before}


@app.post("/api/editor/officials/batch")
async def editor_batch_save(officials: list[OfficialUpsertRequest]):
    """批量保存（覆盖全部官员列表，保留 locations）."""
    data = _read_officials_json()
    data["officials"] = [o.model_dump() for o in officials]
    _write_officials_json(data)
    return {"status": "ok", "count": len(officials)}


class LocationUpsertRequest(BaseModel):
    id: str
    name: str
    region: str = ""
    x: float = 0.0
    y: float = 0.0


@app.post("/api/editor/locations/batch")
async def editor_batch_save_locations(locations: list[LocationUpsertRequest]):
    """批量保存地点（同步写入 officials.json 和 CourtGraph）."""
    data = _read_officials_json()
    data["locations"] = [loc.model_dump() for loc in locations]
    _write_officials_json(data)
    # 同步到 CourtGraph，避免数据源分叉
    if orchestrator:
        from multiagent_framework.court_graph import Location
        for loc in locations:
            orchestrator.court.add_location(
                Location(id=loc.id, name=loc.name, region=loc.region, x=loc.x, y=loc.y)
            )
    return {"status": "ok", "count": len(locations)}


@app.get("/api/editor/locations/{location_id}")
async def editor_get_location(location_id: str):
    """获取单个地点坐标."""
    if not orchestrator:
        return {"error": "not initialized"}
    loc = orchestrator.court.get_location(location_id)
    if not loc:
        return {"error": "not found", "location_id": location_id}
    return {"id": loc.id, "name": loc.name, "region": loc.region, "x": loc.x, "y": loc.y}


@app.put("/api/editor/locations/{location_id}")
async def editor_update_location(location_id: str, req: LocationUpsertRequest):
    """更新单个地点坐标，同步写入 DB 和 officials.json."""
    if not orchestrator:
        return {"error": "not initialized"}
    from multiagent_framework.court_graph import Location
    loc = Location(id=location_id, name=req.name, region=req.region, x=req.x, y=req.y)
    orchestrator.court.add_location(loc)
    # 同步更新 officials.json
    data = _read_officials_json()
    locations = data.get("locations", [])
    found = False
    for i, l in enumerate(locations):
        if l["id"] == location_id:
            locations[i] = {"id": location_id, "name": req.name, "region": req.region, "x": req.x, "y": req.y}
            found = True
            break
    if not found:
        locations.append({"id": location_id, "name": req.name, "region": req.region, "x": req.x, "y": req.y})
    data["locations"] = locations
    _write_officials_json(data)
    return {"status": "ok", "location_id": location_id}


class PromptPreviewRequest(BaseModel):
    id: str
    name: str
    rank: int = 1
    location_id: str = ""
    superior_id: str | None = None
    personality_seed: str = "忠直"
    extra_prompt: str = ""


@app.post("/api/editor/prompt-preview")
async def editor_prompt_preview(req: PromptPreviewRequest):
    """预览某官员的 system prompt 渲染结果."""
    from daming_court.loader import load_personas, _build_system_prompt
    from multiagent_framework.court_graph import Official

    official = Official(
        id=req.id, name=req.name, rank=req.rank,
        location_id=req.location_id, role="official",
        agent_type="official", superior_id=req.superior_id,
        personality_seed=req.personality_seed,
        extra_prompt=req.extra_prompt,
    )
    personas = load_personas()
    prompt = _build_system_prompt(official, personas, PROMPTS_DIR)
    return {"prompt": prompt}


PERSONAS_PATH = "daming_court/config/personas.json"


def _read_personas() -> dict[str, str]:
    if not Path(PERSONAS_PATH).exists():
        return {
            "贪腐": "你是一个贪腐官员，倾向于隐瞒灾情以避免追责，偶尔索取好处。上报时会淡化问题严重性，优先考虑自己的仕途和钱袋。",
            "忠直": "你是一个忠直官员，如实上报，不夸大不隐瞒，为百姓着想。哪怕可能得罪上级，也要把真实情况传递出去。",
            "圆滑": "你是一个圆滑官员，善于揣摩上意，措辞委婉，优先保护自己。你会根据接收者的喜好调整措辞，既不完全说谎也不完全说实话。",
        }
    with open(PERSONAS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _write_personas(data: dict[str, str]) -> None:
    Path(PERSONAS_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(PERSONAS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/editor/personas")
async def editor_get_personas():
    """返回所有人格种子."""
    return _read_personas()


class PersonaUpsertRequest(BaseModel):
    name: str
    description: str


@app.put("/api/editor/personas/{persona_name}")
async def editor_upsert_persona(persona_name: str, req: PersonaUpsertRequest):
    """新增或更新一个人格种子，保存后立即热重载."""
    personas = _read_personas()
    # 如果改名了，删旧的
    if persona_name != req.name and persona_name in personas:
        del personas[persona_name]
    personas[req.name] = req.description
    _write_personas(personas)
    return {"status": "ok"}


@app.delete("/api/editor/personas/{persona_name}")
async def editor_delete_persona(persona_name: str):
    """删除一个人格种子."""
    personas = _read_personas()
    deleted = persona_name in personas
    personas.pop(persona_name, None)
    _write_personas(personas)
    return {"status": "ok", "deleted": deleted}


@app.post("/api/editor/personas/batch")
async def editor_batch_save_personas(personas: dict[str, str]):
    """批量保存所有人格种子（覆盖），保存后热重载."""
    _write_personas(personas)
    return {"status": "ok", "count": len(personas)}


# ── 设置接口 ──

class SettingsRequest(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_s: int = 30
    max_concurrent: int = 4
    seconds_per_day: float = 600.0


@app.get("/api/settings")
async def get_settings():
    if not orchestrator:
        return {"error": "not initialized"}
    llm_display = orchestrator.router.llm.config.to_display_dict()
    llm_display["seconds_per_day"] = orchestrator._seconds_per_day
    return llm_display


@app.post("/api/settings")
async def save_settings(req: SettingsRequest):
    if not orchestrator:
        return {"error": "not initialized"}

    llm_client = orchestrator.router.llm
    old_config = llm_client.config

    new_config = LLMConfig(
        base_url=req.base_url or old_config.base_url,
        api_key=req.api_key if req.api_key and not req.api_key.startswith("****") else old_config.api_key,
        model=req.model or old_config.model,
        timeout_s=req.timeout_s,
        max_concurrent=req.max_concurrent,
    )

    # 热更新 LLM 客户端
    await llm_client.update_config(new_config)

    # 持久化 LLM 配置
    new_config.save_to_file("config/llm.json")

    # 更新时间倍率（运行中不允许改）
    if req.seconds_per_day != orchestrator._seconds_per_day:
        if orchestrator.is_running and not orchestrator.is_paused:
            pass  # 运行中跳过，不报错，仅持久化供下次启动用
        else:
            orchestrator.set_time_scale(req.seconds_per_day)

    # 持久化时间配置
    time_data = {"seconds_per_day": req.seconds_per_day, "tick_interval": 1.0}
    Path("config").mkdir(parents=True, exist_ok=True)
    with open("config/time.json", "w", encoding="utf-8") as f:
        json.dump(time_data, f, indent=2)

    return {"status": "ok"}


# ── 天玑 AI ──

class TianJiChatRequest(BaseModel):
    message: str


@app.post("/api/tianji/chat")
async def tianji_chat(req: TianJiChatRequest):
    """天玑 AI 对话接口，流式 SSE 响应."""
    from fastapi.responses import StreamingResponse

    if not tianji_agent:
        return {"error": "TianJiAgent not initialized"}

    async def event_stream():
        try:
            async for event in tianji_agent.chat(req.message):
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


@app.post("/api/tianji/reset")
async def tianji_reset():
    """重置天玑 AI 对话历史."""
    if not tianji_agent:
        return {"error": "TianJiAgent not initialized"}
    tianji_agent.reset()
    return {"status": "ok", "session_id": tianji_agent.session_id}


@app.get("/api/tianji/history")
async def tianji_history():
    """返回当前对话历史（供前端刷新后恢复展示）."""
    if not tianji_agent:
        return {"error": "TianJiAgent not initialized"}
    # 过滤掉 system prompt 和工具调用的内部消息，只返回 user/assistant 部分给前端
    history = [
        m for m in tianji_agent.get_history()
        if m.get("role") in ("user", "assistant", "tool")
    ]
    return {"messages": history, "session_id": tianji_agent.session_id}


@app.get("/api/tianji/tools")
async def tianji_tools():
    """返回天玑 AI 的完整工具 schema（前端侧边栏展示用）."""
    if not tianji_agent:
        return {"error": "TianJiAgent not initialized"}
    tools = tianji_agent._build_tools()
    # 按类别分组
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

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    logger.info("WebSocket client connected (%d total)", len(ws_clients))
    try:
        while True:
            data = await ws.receive_json()
            # 前端也可以通过 WS 发送操作
            if data.get("type") == "player_action":
                action = PlayerAction(
                    action_type=PlayerActionType(data["action_type"]),
                    memorial_id=data.get("memorial_id", ""),
                    content=data.get("content", ""),
                    note=data.get("note", ""),
                )
                await orchestrator.inject_player_action(action)
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(ws_clients))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
