from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import main
import multiagent_framework.runtime_persistor as runtime_persistor
from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.runtime_persistor import RuntimePersistor
from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.issue_contract import IssueManager
from multiagent_framework.objective_contract import ObjectiveManager


class UnusedLLM:
    async def chat(self, *args, **kwargs):
        raise AssertionError("LLM must not be called during persistence tests")


class FakeRouter:
    def __init__(self) -> None:
        self.time_scale = None

    def update_time_scale(self, value: float) -> None:
        self.time_scale = value


class FakeCausalEngine:
    def __init__(self, snapshot=None) -> None:
        self.value = snapshot or {}
        self.restored = None

    def snapshot(self):
        return self.value

    def restore(self, value, *, sim_time):
        self.restored = (value, sim_time)


class FakeLoader:
    def load_from_db(self) -> int:
        return 0

    def load_from_file(self) -> int:
        return 0


def make_agent(agent_id: str = "keeper") -> OfficialAgent:
    return OfficialAgent(
        AgentConfig(agent_id=agent_id, display_name="Keeper", system_prompt="test"),
        UnusedLLM(),
    )


def test_runtime_persistor_and_startup_loader_round_trip_memory_v3(tmp_path, monkeypatch):
    state_file = tmp_path / "runtime" / "state.json"
    agents_dir = tmp_path / "runtime" / "agents"
    agents_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_persistor, "STATE_FILE", state_file)
    monkeypatch.setattr(runtime_persistor, "AGENTS_DIR", agents_dir)

    source_agent = make_agent()
    source_agent.memory.append({"role": "user", "content": "Inspect the northern granary"})
    source_agent.structured_memory.remember(
        kind="commitment",
        content="Deliver grain to the northern district",
        entities=("north", "grain"),
        importance=0.95,
        source_chain_id="intent-42",
        status="unresolved",
        metadata={"dedupe_key": "intent-42"},
    )
    source_orch = SimpleNamespace(
        _sim_time=123.5,
        _seconds_per_day=720.0,
        memorials=[{"id": "m1"}],
        agents={"keeper": source_agent},
        causal_event_engine=FakeCausalEngine({
            "schema_version": "1.0",
            "observed_event_ids": ["evt-1"],
            "chain_counts": {},
            "chain_order": [],
            "dropped_count": 0,
            "pending": [],
        }),
    )
    persistor = RuntimePersistor(source_orch)
    persistor.mark_state_dirty()
    persistor.mark_agent_dirty("keeper")

    asyncio.run(persistor.flush())

    stored = json.loads((agents_dir / "keeper.json").read_text(encoding="utf-8"))
    assert stored["version"] == 3
    assert stored["structured_memory"]["schema_version"] == "1.0"
    assert stored["structured_memory"]["records"][0]["status"] == "unresolved"
    assert not list(agents_dir.glob("*.tmp"))

    restored_agent = make_agent()
    restored_orch = SimpleNamespace(
        _sim_time=0.0,
        _seconds_per_day=600.0,
        memorials=[],
        agents={"keeper": restored_agent},
        router=FakeRouter(),
        timers=FakeLoader(),
        chain_log=FakeLoader(),
        causal_event_engine=FakeCausalEngine(),
    )
    monkeypatch.setattr(main, "STATE_FILE", state_file)
    monkeypatch.setattr(main, "AGENTS_DIR", agents_dir)

    main._load_runtime_state(restored_orch)

    restored_snapshot = restored_agent.serialize_memory()
    records = restored_snapshot["structured_memory"]["records"]
    assert restored_orch._sim_time == 123.5
    assert restored_orch._seconds_per_day == 720.0
    assert restored_orch.router.time_scale == 720.0
    assert restored_orch.memorials == [{"id": "m1"}]
    assert restored_orch.causal_event_engine.restored == (
        {
            "schema_version": "1.0",
            "observed_event_ids": ["evt-1"],
            "chain_counts": {},
            "chain_order": [],
            "dropped_count": 0,
            "pending": [],
        },
        123.5,
    )
    assert restored_snapshot["messages"] == [
        {"role": "user", "content": "Inspect the northern granary"}
    ]
    assert len(records) == 1
    assert records[0]["kind"] == "commitment"
    assert records[0]["source_chain_id"] == "intent-42"
    assert records[0]["status"] == "unresolved"


def test_runtime_persistor_round_trips_issue_runtime_and_legacy_projection(tmp_path, monkeypatch):
    state_file = tmp_path / "runtime" / "state.json"
    agents_dir = tmp_path / "runtime" / "agents"
    agents_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_persistor, "STATE_FILE", state_file)
    monkeypatch.setattr(runtime_persistor, "AGENTS_DIR", agents_dir)

    source_manager = IssueManager()
    source_manager.configure([], generation=1, gameplay=GameplayContract())
    opened = source_manager.open_issue({
        "external_id": "runtime-report-1",
        "source": {"kind": "system", "id": "runtime"},
        "summary": "Persist this generic report",
        "details": {"marker": 1},
    }, sim_time=12)
    source_orch = SimpleNamespace(
        _sim_time=12.0,
        _seconds_per_day=600.0,
        memorials=[],
        agents={},
        issue_manager=source_manager,
        causal_event_engine=FakeCausalEngine(),
    )
    persistor = RuntimePersistor(source_orch)
    persistor.mark_state_dirty()
    asyncio.run(persistor.flush())

    stored = json.loads(state_file.read_text(encoding="utf-8"))
    issue_id = opened["issue"]["issue_id"]
    assert stored["issue_runtime"]["issues"][issue_id]["summary"] == "Persist this generic report"
    assert stored["memorials"][0]["memorial_id"] == issue_id

    restored_manager = IssueManager()
    restored_manager.configure([], generation=2, gameplay=GameplayContract())
    restored_orch = SimpleNamespace(
        _sim_time=0.0,
        _seconds_per_day=600.0,
        memorials=[],
        agents={},
        issue_manager=restored_manager,
        router=FakeRouter(),
        timers=FakeLoader(),
        chain_log=FakeLoader(),
        causal_event_engine=FakeCausalEngine(),
    )
    restored_orch._sync_legacy_memorials = lambda: setattr(
        restored_orch, "memorials",
        restored_manager.legacy_memorials(sim_time=restored_orch._sim_time),
    )
    monkeypatch.setattr(main, "STATE_FILE", state_file)
    monkeypatch.setattr(main, "AGENTS_DIR", agents_dir)

    main._load_runtime_state(restored_orch)

    assert restored_manager.list_issues(sim_time=12)[0]["issue_id"] == issue_id
    assert restored_manager.public_state(sim_time=12)["generation"] == 2
    assert restored_orch.memorials[0]["content"] == "Persist this generic report"


def test_runtime_persistor_round_trips_objective_receipts_and_ending(tmp_path, monkeypatch):
    state_file = tmp_path / "runtime" / "state.json"
    agents_dir = tmp_path / "runtime" / "agents"
    agents_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_persistor, "STATE_FILE", state_file)
    monkeypatch.setattr(runtime_persistor, "AGENTS_DIR", agents_dir)

    definitions = [{
        "objective_id": "persist_goal",
        "display_name": "Persist goal",
        "completion": {"kind": "sim_day", "operator": "gte", "value": 1},
    }]
    endings = [{
        "ending_id": "persist_ending",
        "display_name": "Persist ending",
        "outcome": "victory",
        "trigger": {"objective_ids": ["persist_goal"]},
    }]
    source_manager = ObjectiveManager()
    source_manager.configure(definitions, endings, generation=3)
    source_manager.evaluate(world_state=None, sim_time=86400)
    source_orch = SimpleNamespace(
        _sim_time=86400.0,
        _seconds_per_day=600.0,
        memorials=[],
        agents={},
        objective_manager=source_manager,
        causal_event_engine=FakeCausalEngine(),
    )
    persistor = RuntimePersistor(source_orch)
    persistor.mark_state_dirty()
    asyncio.run(persistor.flush())

    stored = json.loads(state_file.read_text(encoding="utf-8"))
    assert stored["objective_runtime"]["objective_receipts"]["persist_goal"]["generation"] == 3
    assert stored["objective_runtime"]["ending_receipt"]["ending_id"] == "persist_ending"

    restored_manager = ObjectiveManager()
    restored_manager.configure(definitions, endings, generation=4)
    restored_orch = SimpleNamespace(
        _sim_time=0.0,
        _seconds_per_day=600.0,
        memorials=[],
        agents={},
        objective_manager=restored_manager,
        router=FakeRouter(),
        timers=FakeLoader(),
        chain_log=FakeLoader(),
        causal_event_engine=FakeCausalEngine(),
    )
    monkeypatch.setattr(main, "STATE_FILE", state_file)
    monkeypatch.setattr(main, "AGENTS_DIR", agents_dir)

    main._load_runtime_state(restored_orch)

    restored_state = restored_manager.public_state(
        world_state=None, sim_time=restored_orch._sim_time
    )
    assert restored_state["current_ending"]["ending_id"] == "persist_ending"
    assert restored_state["current_ending"]["reached_at"] == 86400
    assert restored_state["objectives"][0]["status"] == "completed"
