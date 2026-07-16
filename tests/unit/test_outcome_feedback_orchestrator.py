from __future__ import annotations

import asyncio
from types import SimpleNamespace

from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.outcome_feedback_contract import DAY, OutcomeFeedbackContract
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.types import EventObject
from multiagent_framework.world_state import Entity, EntityValue, ResourceDefinition, WorldState


class _Court:
    def get_official(self, _agent_id):
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


def _orchestrator(tmp_path, name="feedback") -> Orchestrator:
    world = WorldState(str(tmp_path / f"{name}.db"))
    action_agent = SimpleNamespace(world_state=world, time_simulator=TimeSimulator(), llm=None, _prompt_template="")
    orchestrator = Orchestrator(
        court_graph=_Court(), timer_system=_Timers(), chain_log=_ChainLog(),
        router=_Router(), action_agent=action_agent,
    )
    gameplay = GameplayContract.from_content({
        "player": {
            "identity_id": "commander", "display_name": "Commander", "actor_id": "player_actor",
            "controlled_objects": [{"kind": "entity", "id": "station", "mode": "direct"}],
        }
    }, [], package_id="test")
    orchestrator.configure_gameplay_contract(gameplay, generation=1)
    world.apply_dlc_schema_update(
        entities=[Entity(id="station", type="location", name="Station", current_status="ready")],
        inventory=[],
        resource_definitions=[ResourceDefinition(name="stability", kind="metric", min_quantity=0, max_quantity=100)],
        entity_values=[EntityValue(entity_id="station", name="stability", kind="metric", value=10)],
    )
    orchestrator.configure_derived_metric_catalog([{
        "metric_id": "station_stability", "display_name": "Station stability", "unit": "%", "audience": "player",
        "formula": {"op": "source", "source": "metric", "entity_id": "station", "name": "stability"},
    }], generation=1)
    orchestrator.configure_action_catalog([{
        "action_id": "reinforce", "display_name": "Reinforce station", "description": "Raise stability.",
        "parameters": [],
        "target": {"kind": "entity", "required": True, "allowed_ids": ["station"], "controlled_only": True},
        "permissions": {"control_modes": ["direct"], "control_kinds": ["entity"], "control_ids": ["station"], "actor_ids": ["player_actor"]},
        "result": {"mode": "world_commands", "effects": [{"effect_type": "adjust_resource", "entity_id": "$target", "resource_name": "stability", "quantity_delta": 15, "reason": "reinforcement"}]},
        "enabled": True,
    }], generation=1, gameplay=gameplay)
    orchestrator.configure_outcome_feedback(OutcomeFeedbackContract.from_mapping({
        "enabled": True,
        "rules": [{
            "rule_id": "reinforcement_report", "display_name": "Reinforcement report",
            "source_kinds": ["action"], "delivery": "delayed", "action_ids": ["reinforce"],
            "delay_days": 1, "title": "Follow-up", "summary": "The follow-up is ready.",
        }],
    }), generation=1, preserve_state=False)
    return orchestrator


def test_action_returns_immediate_metric_reason_and_replay_is_idempotent(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    invocation = {"invocation_id": "reinforce-1", "expected_generation": 1, "actor_id": "player_actor", "control_object_id": "station", "target_id": "station", "parameters": {}}
    result = orchestrator.execute_dlc_action("reinforce", invocation)
    replay = orchestrator.execute_dlc_action("reinforce", invocation)

    immediate = result["outcome_feedback"]["delivered"][0]
    assert immediate["kind"] == "immediate"
    assert immediate["metric_changes"][0]["metric_id"] == "station_stability"
    assert immediate["metric_changes"][0]["delta"] == 15
    assert immediate["metric_changes"][0]["reason"] == "Reinforce station"
    assert replay["replayed"] is True
    assert replay["outcome_feedback"]["delivered"] == []
    assert orchestrator.outcome_feedback_public_state()["pending_count"] == 1


def test_save_load_preserves_delayed_report_and_delivers_once(tmp_path):
    current = _orchestrator(tmp_path, "current")
    current.execute_dlc_action("reinforce", {"invocation_id": "reinforce-2", "expected_generation": 1, "actor_id": "player_actor", "control_object_id": "station", "target_id": "station", "parameters": {}})
    current._paused = True
    saved = current.save_state()

    restored = _orchestrator(tmp_path, "restored")
    restored.load_state(saved)
    first = restored.outcome_feedback_runtime.poll(sim_time=DAY, condition_context=restored._feedback_condition_context())
    second = restored.outcome_feedback_runtime.poll(sim_time=DAY * 2, condition_context=restored._feedback_condition_context())
    assert [item["kind"] for item in first["delivered"]] == ["delayed_report"]
    assert second["delivered"] == []



def test_historical_causal_feedback_delay_is_already_due_in_catch_up_tick(tmp_path):
    orchestrator = _orchestrator(tmp_path, "causal-catch-up")
    orchestrator.configure_outcome_feedback(OutcomeFeedbackContract.from_mapping({
        "enabled": True,
        "immediate": {"enabled": False},
        "rules": [{
            "rule_id": "causal_follow_up",
            "display_name": "Causal follow-up",
            "source_kinds": ["causal_event"],
            "event_types": ["relief"],
            "delivery": "delayed",
            "delay_days": 1,
            "title": "Relief follow-up",
            "summary": "Relief consequences are now visible.",
        }],
    }), generation=2, preserve_state=False)
    orchestrator.configure_causal_events([{
        "rule_id": "shortage_to_relief",
        "source_event_types": ["shortage"],
        "child_event_type": "relief",
        "target_agents": [],
        "delay_days": 1,
        "probability": 1,
    }])
    orchestrator._sim_time = 5 * DAY
    orchestrator.inject_event(EventObject(
        event_id="historical-shortage",
        event_type="shortage",
        severity=5,
        target_agents=[],
        initial_payload={},
        generated_at=DAY,
    ))

    result = asyncio.run(orchestrator._tick())

    delayed = next(
        item for item in result.outcome_feedback["delivered"]
        if item["kind"] == "delayed_report"
    )
    assert delayed["created_at"] == 2 * DAY
    assert delayed["due_at"] == 3 * DAY
    assert delayed["delivered_at"] == 5 * DAY
    assert result.outcome_feedback["pending_count"] == 0
