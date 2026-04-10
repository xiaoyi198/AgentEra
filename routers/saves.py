"""存档管理 — 存/读/列表/重命名/删除."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import sqlite3
import time as _time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from . import _state as state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["saves"])

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


@router.get("/saves")
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


@router.post("/save")
async def save_game(req: SaveRequest):
    if not state.orchestrator:
        return {"error": "not initialized"}
    if not state.orchestrator.is_paused:
        return {"error": "must pause before saving"}

    sim_day = int(state.orchestrator.sim_time / 86400)
    raw_name = req.save_name.strip() or f"第{sim_day}天-{random.randint(1000, 9999)}"
    name = _sanitize(raw_name)

    save_path = _SAVE_DIR / name
    _SAVE_DIR.mkdir(exist_ok=True)

    if save_path.exists():
        shutil.rmtree(save_path)
    save_path.mkdir(parents=True)

    state.orchestrator.save_state()
    _backup_sqlite(_DB_PATH, save_path / "daming_court.db")

    if Path(_CHAIN_LOG_PATH).exists():
        shutil.copy2(_CHAIN_LOG_PATH, save_path / "chain_log.jsonl")

    runtime = {
        "agent_memories": {
            aid: agent.serialize_memory()
            for aid, agent in state.orchestrator.agents.items()
        },
        "pending_memorials": req.memorials,
        "seconds_per_day": state.orchestrator._seconds_per_day,
    }
    (save_path / "runtime.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    saved_at = _time.time()
    meta = {
        "save_name": name,
        "saved_at": saved_at,
        "sim_time": state.orchestrator.sim_time,
        "sim_day": sim_day,
        "seconds_per_day": state.orchestrator._seconds_per_day,
        "agent_count": len(state.orchestrator.agents),
        "timer_count": state.orchestrator.timers.count(),
        "memorial_count": len(req.memorials),
    }
    (save_path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info("Game saved to saves/%s: sim_time=%.1f, %d agents, %d timers",
                name, state.orchestrator.sim_time, len(state.orchestrator.agents),
                state.orchestrator.timers.count())
    return {"status": "saved", "save_name": name, "saved_at": saved_at, "sim_day": sim_day}


@router.post("/load")
async def load_game(req: LoadRequest):
    from main import init_orchestrator

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
    if state.orchestrator:
        # 必须先 drain 后台 agent 任务，否则它们在文件覆盖后仍会写 chain_log.jsonl，
        # 导致读档后出现不属于存档的幽灵对话时间线。
        await state.orchestrator.drain_agent_tasks()
    if state._orch_task and not state._orch_task.done():
        state._orch_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(state._orch_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        state._orch_task = None
    if state.orchestrator:
        state.orchestrator.stop()

    # 覆盖工作目录的 SQLite 和 chain log
    shutil.copy2(save_path / "daming_court.db", _DB_PATH)
    chain_src = save_path / "chain_log.jsonl"
    if chain_src.exists():
        shutil.copy2(chain_src, _CHAIN_LOG_PATH)
    elif Path(_CHAIN_LOG_PATH).exists():
        Path(_CHAIN_LOG_PATH).unlink()

    # 重新初始化
    state.orchestrator = init_orchestrator(load_from_save=True)

    # 恢复运行时状态
    s = {
        "sim_time": meta["sim_time"],
        "seconds_per_day": runtime.get("seconds_per_day", meta.get("seconds_per_day", 600.0)),
        "agent_memories": runtime.get("agent_memories", {}),
    }
    state.orchestrator.load_state(s)

    logger.info("Game loaded from saves/%s: sim_time=%.1f, %d memorials",
                name, meta["sim_time"], len(runtime.get("pending_memorials", [])))
    return {
        "status": "loaded",
        "save_name": name,
        "sim_time": meta["sim_time"],
        "seconds_per_day": s["seconds_per_day"],
        "pending_memorials": runtime.get("pending_memorials", []),
        "saved_at": meta.get("saved_at"),
    }


@router.post("/saves/rename")
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
    meta = _read_meta(new_path)
    if meta:
        meta["save_name"] = new
        (new_path / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return {"status": "renamed", "save_name": new}


@router.delete("/saves/{name}")
async def delete_save(name: str):
    safe = _sanitize(name)
    save_path = _SAVE_DIR / safe
    if not save_path.exists():
        return {"error": f"存档不存在：{safe}"}
    shutil.rmtree(save_path)
    return {"status": "deleted", "save_name": safe}
