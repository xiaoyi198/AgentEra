from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.types import EventObject
from multiagent_framework.event_generator import (
    GAME_SECONDS_PER_DAY,
    RandomEventGenerator,
    RandomEventGeneratorError,
)


def template(**overrides):
    value = {
        "template_id": "locust",
        "event_type": "locust_plague",
        "target_agents": ["county-a", "county-b"],
        "initial_payload": {"area": "south"},
        "severity_min": 4,
        "severity_max": 8,
        "min_interval_days": 2,
        "max_interval_days": 2,
        "probability": 1.0,
        "max_occurrences": 0,
        "enabled": True,
    }
    value.update(overrides)
    return value


def test_deterministic_schedule_emits_stable_events_and_catches_up():
    first = RandomEventGenerator([template()], sim_time=0)
    second = RandomEventGenerator([template()], sim_time=0)

    assert first.poll(GAME_SECONDS_PER_DAY).events == ()
    left = first.poll(5 * GAME_SECONDS_PER_DAY)
    right = second.poll(5 * GAME_SECONDS_PER_DAY)

    assert left == right
    assert left.attempts == 2
    assert [event.event_id for event in left.events] == [
        "rnd_locust_000001", "rnd_locust_000002"
    ]
    assert all(4 <= event.severity <= 8 for event in left.events)
    assert all(event.initial_payload["random_event_template_id"] == "locust" for event in left.events)


def test_probability_skip_advances_schedule_and_max_occurrences_stops():
    skipped = RandomEventGenerator([template(probability=0.0)], sim_time=0)
    result = skipped.poll(6 * GAME_SECONDS_PER_DAY)
    assert result.attempts == 3
    assert result.events == ()
    assert skipped.snapshot()["templates"]["locust"]["next_due"] == 8 * GAME_SECONDS_PER_DAY

    limited = RandomEventGenerator([template(max_occurrences=1)], sim_time=0)
    result = limited.poll(10 * GAME_SECONDS_PER_DAY)
    assert len(result.events) == 1
    assert limited.poll(20 * GAME_SECONDS_PER_DAY).attempts == 0


def test_snapshot_restore_prevents_duplicate_events_after_restart():
    generator = RandomEventGenerator([template()], sim_time=0)
    emitted = generator.poll(2 * GAME_SECONDS_PER_DAY)
    assert len(emitted.events) == 1
    snapshot = copy.deepcopy(generator.snapshot())

    restored = RandomEventGenerator([template()], sim_time=0)
    restored.restore(snapshot, sim_time=2 * GAME_SECONDS_PER_DAY)
    assert restored.poll(3 * GAME_SECONDS_PER_DAY).events == ()
    next_result = restored.poll(4 * GAME_SECONDS_PER_DAY)
    assert [event.event_id for event in next_result.events] == ["rnd_locust_000002"]


def test_reconfigure_preserves_unchanged_state_and_resets_changed_template():
    generator = RandomEventGenerator([template()], sim_time=0)
    generator.poll(2 * GAME_SECONDS_PER_DAY)
    generator.configure([template()], sim_time=2 * GAME_SECONDS_PER_DAY)
    assert generator.poll(3 * GAME_SECONDS_PER_DAY).events == ()

    generator.configure(
        [template(min_interval_days=5, max_interval_days=5)],
        sim_time=3 * GAME_SECONDS_PER_DAY,
    )
    state = generator.snapshot()["templates"]["locust"]
    assert state["attempts"] == 0
    assert state["next_due"] == 8 * GAME_SECONDS_PER_DAY


def test_invalid_templates_are_rejected():
    cases = [
        template(template_id=""),
        template(target_agents=[]),
        template(severity_min=9, severity_max=3),
        template(min_interval_days=0),
        template(probability=1.5),
        template(max_occurrences=-1),
        template(template_id="bad id"),
        template(event_type="bad/type"),
        template(initial_payload=[]),
        template(target_agents="county-a"),
        template(enabled="yes"),
        template(probability=True),
    ]
    for value in cases:
        with pytest.raises(RandomEventGeneratorError):
            RandomEventGenerator([value], sim_time=0)
    with pytest.raises(RandomEventGeneratorError, match="duplicate"):
        RandomEventGenerator([template(), template()], sim_time=0)


def test_poll_budget_bounds_large_time_jump():
    generator = RandomEventGenerator([template(min_interval_days=1, max_interval_days=1)], sim_time=0)
    result = generator.poll(1000 * GAME_SECONDS_PER_DAY, max_attempts=7)
    assert result.attempts == 7
    assert len(result.events) == 7
    assert generator.snapshot()["templates"]["locust"]["attempts"] == 7


def test_orchestrator_tick_injects_due_event_and_exposes_metadata_only():
    class Court:
        def get_official(self, agent_id):
            if agent_id in {"county-a", "county-b"}:
                return SimpleNamespace(id=agent_id, role="player")
            return None

    class Timers:
        def pop_due(self, _sim_time):
            return []

        def get_in_transit(self):
            return []

    class Router:
        def __init__(self):
            self.llm = SimpleNamespace(
                token_counter=SimpleNamespace(to_dict=lambda: {})
            )

    orch = Orchestrator(
        court_graph=Court(),
        timer_system=Timers(),
        chain_log=SimpleNamespace(),
        router=Router(),
    )
    orch.configure_random_events([
        template(min_interval_days=1, max_interval_days=1)
    ], preserve_state=False)
    orch._sim_time = GAME_SECONDS_PER_DAY

    result = asyncio.run(orch._tick())

    assert len(result.generated_events) == 1
    assert len(result.new_memorials) == 2
    metadata = result.generated_events[0]
    assert metadata["event_id"] == "rnd_locust_000001"
    assert metadata["target_count"] == 2
    assert "initial_payload" not in metadata
    assert "target_agents" not in metadata
    assert "area" not in metadata


def test_poll_budget_is_shared_chronologically_across_templates():
    generator = RandomEventGenerator([
        template(template_id="alpha", min_interval_days=1, max_interval_days=1),
        template(template_id="beta", min_interval_days=1, max_interval_days=1),
    ], sim_time=0)
    result = generator.poll(100 * GAME_SECONDS_PER_DAY, max_attempts=4)
    assert [event.event_id for event in result.events] == [
        "rnd_alpha_000001",
        "rnd_beta_000001",
        "rnd_alpha_000002",
        "rnd_beta_000002",
    ]
    states = generator.snapshot()["templates"]
    assert states["alpha"]["attempts"] == 2
    assert states["beta"]["attempts"] == 2


def test_poll_rejects_non_positive_or_boolean_budget():
    generator = RandomEventGenerator([template()], sim_time=0)
    for budget in (0, -1, True):
        with pytest.raises(RandomEventGeneratorError, match="budget"):
            generator.poll(GAME_SECONDS_PER_DAY, max_attempts=budget)



def make_orchestrator_for_events():
    class Court:
        def get_official(self, agent_id):
            if agent_id in {"county-a", "county-b"}:
                return SimpleNamespace(id=agent_id, role="player")
            return None

    class Timers:
        def pop_due(self, _sim_time):
            return []

        def get_in_transit(self):
            return []

    class Router:
        def __init__(self):
            self.llm = SimpleNamespace(token_counter=SimpleNamespace(to_dict=lambda: {}))

    return Orchestrator(
        court_graph=Court(), timer_system=Timers(),
        chain_log=SimpleNamespace(), router=Router(),
    )


def test_orchestrator_random_event_triggers_zero_delay_causal_event_without_payload_leak():
    orch = make_orchestrator_for_events()
    orch.configure_random_events([
        template(min_interval_days=1, max_interval_days=1)
    ], preserve_state=False)
    orch.configure_causal_events([{
        "rule_id": "locust_to_famine",
        "source_event_types": ["locust_plague"],
        "child_event_type": "famine_risk",
        "delay_days": 0,
        "probability": 1,
        "inherit_payload_keys": ["area"],
        "initial_payload": {"private_note": "never-expose"},
        "max_depth": 4,
        "max_fires_per_chain": 1,
    }])
    orch._sim_time = GAME_SECONDS_PER_DAY

    result = asyncio.run(orch._tick())

    assert [item["event_type"] for item in result.generated_events] == [
        "locust_plague", "famine_risk",
    ]
    causal = result.generated_events[1]
    assert causal["source"] == "causal"
    assert "initial_payload" not in causal
    assert "target_agents" not in causal
    assert "private_note" not in str(causal)
    assert len(result.new_memorials) == 4


def test_orchestrator_causal_tick_budget_leaves_excess_due_events_pending():
    orch = make_orchestrator_for_events()
    rules = [{
        "rule_id": f"fanout-{index}",
        "source_event_types": ["*"],
        "child_event_type": f"echo-{index}",
        "target_agents": ["county-a"],
        "delay_days": 0,
        "probability": 1,
        "max_depth": 16,
        "max_fires_per_chain": 64,
    } for index in range(16)]
    orch.configure_causal_events(rules)
    orch.inject_event(EventObject(
        event_id="source-fanout", event_type="start", severity=5,
        target_agents=["county-a"], initial_payload={}, generated_at=0,
    ))

    result = asyncio.run(orch._tick())

    assert len(result.generated_events) == 128
    assert all(item["source"] == "causal" for item in result.generated_events)
    assert orch.causal_event_status()["pending_count"] > 0
