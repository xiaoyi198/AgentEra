from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import main
import multiagent_framework.runtime_persistor as runtime_persistor
from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.runtime_persistor import RuntimePersistor


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
