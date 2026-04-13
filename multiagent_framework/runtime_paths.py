"""runtime/ 目录路径常量 — 所有运行时状态的持久化位置."""

from __future__ import annotations

from pathlib import Path

RUNTIME_DIR = Path("runtime")
WORLD_DB = RUNTIME_DIR / "world.db"
CHAIN_LOG = RUNTIME_DIR / "chain_log.jsonl"
STATE_FILE = RUNTIME_DIR / "state.json"
AGENTS_DIR = RUNTIME_DIR / "agents"


def ensure_runtime_dir() -> None:
    """确保 runtime/ 及子目录存在."""
    RUNTIME_DIR.mkdir(exist_ok=True)
    AGENTS_DIR.mkdir(exist_ok=True)


def runtime_exists() -> bool:
    """判断 runtime 是否已初始化（world.db 存在即可）."""
    return WORLD_DB.exists()
