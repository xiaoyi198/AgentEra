from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.rhythm_contract import RhythmContract
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.types import EventObject
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState


class _Court:
    def get_official(self, agent_id):
        if agent_id == "observer":
            return SimpleNamespace(id=agent_id, role="player")
        return None

    def get_officials_at_location(self, _location):
        return []

    def get_all_officials(self):
        return []


class _Timers:
    def pop_due(self, _sim_time):
        return []

    def get_in_transit(self):
        return []

    def save_all(self):
        return 0

    def load_from_db(self):
        return None


class _ChainLog:
    def load_from_file(self):
        return None


class _Router:
    def __init__(self):
        self.llm = SimpleNamespace(token_counter=SimpleNamespace(to_dict=lambda: {}))

    def update_time_scale(self, _seconds_per_day):
        return None


def _orchestrator(tmp_path) -> Orchestrator:
    world = WorldState(str(tmp_path / "rhythm-world.db"))
    action_agent = SimpleNamespace(
        world_state=world,
        time_simulator=TimeSimulator(),
        llm=None,
        _prompt_template="",
    )
    return Orchestrator(
        court_graph=_Court(),
        timer_system=_Timers(),
        chain_log=_ChainLog(),
        router=_Router(),
        action_agent=action_agent,
    )


def _contract(*, event_type: str, cost: float = 2) -> RhythmContract:
    return RhythmContract.from_mapping({
        "enabled": True,
        "max_active_issues": 4,
        "attention_budget": 6,
        "attention_recovery_per_day": 1,
        "urgency_costs": {"low": 1, "normal": cost, "high": 3, "critical": 5},
        "rules": [{
            "rule_id": "paced_event",
            "issue_type_ids": ["pulse_issue"],
            "event_types": [event_type],
            "max_active": 2,
        }],
    })


def test_tick_exposes_rhythm_and_suppresses_random_event_during_quiet_period(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    orchestrator.configure_rhythm_system(_contract(event_type="storm"), generation=4, preserve_state=False)
    orchestrator.rhythm_runtime.quiet_until = 2 * GAME_SECONDS_PER_DAY
    orchestrator.configure_random_events([{
        "template_id": "storm",
        "event_type": "storm",
        "target_agents": ["observer"],
        "severity_min": 4,
        "severity_max": 4,
        "min_interval_days": 1,
        "max_interval_days": 1,
        "probability": 1,
        "max_occurrences": 1,
        "enabled": True,
    }], preserve_state=False)
    orchestrator._sim_time = GAME_SECONDS_PER_DAY

    result = asyncio.run(orchestrator._tick())

    assert result.rhythm["queue_count"] == 0
    assert result.rhythm["active_count"] == 0
    assert result.generated_events == [{
        "event_id": "rnd_storm_000001",
        "event_type": "storm",
        "severity": 4,
        "target_count": 0,
        "generated_at": GAME_SECONDS_PER_DAY,
        "source": "random",
        "rhythm_status": "suppressed",
        "rhythm_reason": "quiet_period",
        "rhythm_rule_id": "paced_event",
    }]
    public = orchestrator.rhythm_public_state()
    assert public["generation"] == 4
    assert public["quiet_active"] is True
    assert public["recent_suppressed_events"][-1]["event_type"] == "storm"


def test_suppressed_causal_event_does_not_execute_world_commands(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    world = orchestrator.world_state
    world.upsert_entity(Entity(id="warehouse", type="location", name="Warehouse"))
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", kind="asset", anchor_rate=1, min_quantity=0, default_quantity=0,
    ))
    world.set_inventory("warehouse", "grain", "grain", 10, command_id="seed")
    orchestrator.configure_rhythm_system(_contract(event_type="relief"), generation=2, preserve_state=False)
    orchestrator.rhythm_runtime.quiet_until = GAME_SECONDS_PER_DAY
    orchestrator.configure_causal_events([{
        "rule_id": "shortage_to_relief",
        "source_event_types": ["shortage"],
        "child_event_type": "relief",
        "target_agents": [],
        "delay_days": 0,
        "probability": 1,
        "world_effects": [{
            "effect_type": "adjust_resource",
            "entity_id": "warehouse",
            "resource_name": "grain",
            "quantity_delta": -5,
        }],
    }])
    orchestrator.inject_event(EventObject(
        event_id="shortage-1",
        event_type="shortage",
        severity=5,
        target_agents=[],
        initial_payload={},
        generated_at=0,
    ))

    result = asyncio.run(orchestrator._tick())

    causal = next(item for item in result.generated_events if item["source"] == "causal")
    assert causal["event_type"] == "relief"
    assert causal["world_effect_count"] == 1
    assert causal["world_effect_status"] == "suppressed"
    assert causal["rhythm_status"] == "suppressed"
    quantity = next(item.quantity for item in world.get_inventory("warehouse") if item.item_name == "grain")
    assert quantity == pytest.approx(10)


def test_save_load_restores_rhythm_budget_quiet_period_and_event_history(tmp_path):
    contract = _contract(event_type="pulse", cost=2)
    current = _orchestrator(tmp_path)
    current.configure_rhythm_system(contract, generation=7, preserve_state=False)
    current.configure_issue_catalog([{
        "issue_type_id": "pulse_issue",
        "display_name": "Pulse issue",
        "default_urgency": "normal",
        "source": {"allowed_kinds": ["system"]},
    }], generation=7)
    current._sim_time = 100
    opened = current.open_issue({
        "external_id": "pulse-issue-1",
        "issue_type_id": "pulse_issue",
        "source": {"kind": "system", "id": "rhythm-test"},
        "summary": "Pulse issue",
    })
    assert opened["issue"]["status"] == "open"
    decision = current.rhythm_runtime.allow_event(
        "pulse", sim_time=100, issue_manager=current.issue_manager,
    )
    assert decision["allowed"] is True
    current.rhythm_runtime.quiet_until = 500
    current._sim_time = 100
    current._paused = True
    saved = current.save_state()

    restored = _orchestrator(tmp_path)
    restored.configure_rhythm_system(contract, generation=7, preserve_state=False)
    restored.load_state(saved)
    public = restored.rhythm_public_state()

    assert public["generation"] == 7
    assert public["attention_available"] == pytest.approx(4)
    assert public["quiet_until"] == 500
    assert public["quiet_active"] is True
    assert restored.rhythm_runtime.allow_event(
        "pulse", sim_time=100, issue_manager=restored.issue_manager,
    )["allowed"] is False
