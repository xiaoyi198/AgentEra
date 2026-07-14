from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import main
import multiagent_framework.runtime_paths as runtime_paths
from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.issue_contract import IssueManager
from routers import _state as state
from routers import saves


class SnapshotOnly:
    def __init__(self, value=None):
        self.value = value or {}

    def snapshot(self):
        return self.value


class Timers:
    def count(self):
        return 0


class PausedOrchestrator:
    def __init__(self, issue_manager):
        self.is_paused = True
        self.sim_time = 42.0
        self._seconds_per_day = 600.0
        self.issue_manager = issue_manager
        self.memorials = issue_manager.legacy_memorials(sim_time=self.sim_time)
        self.agents = {}
        self.random_event_generator = SnapshotOnly({"templates": {}})
        self.causal_event_engine = SnapshotOnly({"pending": []})
        self.action_catalog = SnapshotOnly({"receipts": {}})
        self.timers = Timers()
        self.saved = False
        self.stopped = False

    def save_state(self):
        self.saved = True

    def list_issues(self):
        return self.issue_manager.list_issues(sim_time=self.sim_time)

    async def drain_agent_tasks(self):
        return None

    def stop(self):
        self.stopped = True


def test_manual_save_and_load_preserve_issue_runtime(tmp_path, monkeypatch):
    save_dir = tmp_path / "saves"
    runtime_dir = tmp_path / "runtime"
    agents_dir = runtime_dir / "agents"
    agents_dir.mkdir(parents=True)
    world_db = runtime_dir / "world.db"
    chain_log = runtime_dir / "chain_log.jsonl"
    state_file = runtime_dir / "state.json"
    with sqlite3.connect(world_db) as conn:
        conn.execute("create table marker (id integer primary key)")

    monkeypatch.setattr(saves, "_SAVE_DIR", save_dir)
    monkeypatch.setattr(saves, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(saves, "WORLD_DB", world_db)
    monkeypatch.setattr(saves, "CHAIN_LOG", chain_log)
    monkeypatch.setattr(runtime_paths, "STATE_FILE", state_file)
    monkeypatch.setattr(runtime_paths, "AGENTS_DIR", agents_dir)

    manager = IssueManager()
    manager.configure([], generation=1, gameplay=GameplayContract())
    opened = manager.open_issue({
        "external_id": "manual-save-report",
        "source": {"kind": "system", "id": "save-test"},
        "summary": "Manual save issue",
    }, sim_time=42)
    current = PausedOrchestrator(manager)
    previous_orchestrator = state.orchestrator
    previous_task = state._orch_task
    state.orchestrator = current
    state._orch_task = None
    restored = SimpleNamespace(marker="restored")
    monkeypatch.setattr(main, "init_orchestrator", lambda **kwargs: restored)

    try:
        saved = asyncio.run(saves.save_game(saves.SaveRequest(save_name="issue-save")))
        assert saved["status"] == "saved"
        runtime_payload = json.loads((save_dir / "issue-save" / "runtime.json").read_text(encoding="utf-8"))
        issue_id = opened["issue"]["issue_id"]
        assert runtime_payload["issue_runtime"]["issues"][issue_id]["summary"] == "Manual save issue"
        meta = json.loads((save_dir / "issue-save" / "meta.json").read_text(encoding="utf-8"))
        assert meta["issue_count"] == 1

        loaded = asyncio.run(saves.load_game(saves.LoadRequest(save_name="issue-save")))
        assert loaded["status"] == "loaded"
        assert state.orchestrator is restored
        restored_state = json.loads(state_file.read_text(encoding="utf-8"))
        assert restored_state["issue_runtime"]["issues"][issue_id]["summary"] == "Manual save issue"
        assert restored_state["memorials"][0]["memorial_id"] == issue_id
        assert current.stopped is True
    finally:
        state.orchestrator = previous_orchestrator
        state._orch_task = previous_task
