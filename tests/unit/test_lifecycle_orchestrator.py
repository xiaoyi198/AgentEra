from __future__ import annotations

import asyncio
from types import SimpleNamespace

from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.lifecycle_contract import LifecycleContract
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.temporal_contract import TemporalContract
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.types import EventObject
from multiagent_framework.world_state import ResourceDefinition, WorldState


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


def _time_contract() -> TemporalContract:
    return TemporalContract.from_mapping({
        "enabled": True,
        "calendar": {
            "calendar_id": "lifecycle_test",
            "display_name": "Lifecycle Calendar",
            "epoch_label": "Era",
            "start_year": 1,
            "start_month_id": "m1",
            "start_day": 1,
            "weekdays": ["w1", "w2", "w3", "w4", "w5", "w6", "w7"],
            "start_weekday": "w1",
            "months": [{"month_id": "m1", "display_name": "Month 1", "days": 28}],
        },
    })


def _lifecycle_contract() -> LifecycleContract:
    return LifecycleContract.from_mapping({
        "enabled": True,
        "actor_templates": [{
            "template_id": "guard",
            "display_name": "Guard",
            "actor": {
                "display_name": "New Guard",
                "actor_type": "person",
                "attributes": {"merit": 8},
                "states": {"alive": True},
            },
            "entity": {
                "entity_type": "person",
                "name": "Guard Entity",
                "location_id": "frontier",
            },
            "agent": {
                "source_agent_id": "captain_agent",
                "display_name": "Patrol Agent",
                "system_prompt": "Protect the frontier.",
            },
        }],
        "transitions": [
            {
                "transition_id": "weekly_recruitment",
                "display_name": "Weekly Recruitment",
                "kind": "birth",
                "trigger": "week_start",
                "once": True,
                "operations": [{
                    "operation": "spawn_actor",
                    "template_id": "guard",
                    "actor_id_pattern": "guard_{sequence}",
                    "entity_id_pattern": "entity_{actor_id}",
                    "agent_id_pattern": "agent_{actor_id}",
                }],
            },
            {
                "transition_id": "retire_guard",
                "display_name": "Retire Guard",
                "kind": "death",
                "trigger": "manual",
                "operations": [{
                    "operation": "retire_actor",
                    "actor_id": "guard_1",
                    "remove_entity": True,
                }],
            },
        ],
    })


def _orchestrator(tmp_path) -> Orchestrator:
    world = WorldState(str(tmp_path / "world.db"))
    action_agent = SimpleNamespace(
        world_state=world,
        time_simulator=TimeSimulator(),
        llm=None,
        _prompt_template="",
    )
    orchestrator = Orchestrator(
        court_graph=_Court(), timer_system=_Timers(), chain_log=_ChainLog(),
        router=_Router(), action_agent=action_agent,
    )
    source = OfficialAgent(
        AgentConfig(
            agent_id="captain_agent",
            actor_id="captain_actor",
            display_name="Captain",
            system_prompt="Keep order.",
            max_rounds=7,
        ),
        SimpleNamespace(),
    )
    orchestrator.register_agent(source)
    orchestrator.configure_time_system(_time_contract(), generation=4, preserve_state=False)
    orchestrator.configure_lifecycle_system(_lifecycle_contract(), generation=4, preserve_state=False)
    return orchestrator


def test_tick_applies_lifecycle_marker_and_projects_dynamic_agent(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    result = asyncio.run(orchestrator._tick())

    assert result.lifecycle["applied_count"] == 1
    assert result.lifecycle["failed_count"] == 0
    assert result.lifecycle["applied"][0]["transition_id"] == "weekly_recruitment"
    assert result.agent_errors == []
    projected = orchestrator.get_agent("agent_guard_1")
    assert isinstance(projected, OfficialAgent)
    assert projected is not orchestrator.get_agent("captain_agent")
    assert projected.config.actor_id == "guard_1"
    assert projected.config.display_name == "Patrol Agent"
    assert projected.config.system_prompt == "Protect the frontier."
    assert projected.config.max_rounds == 7
    assert projected.llm is orchestrator.get_agent("captain_agent").llm
    assert orchestrator.world_state.get_entity("entity_guard_1") is not None
    assert orchestrator.lifecycle_public_state()["counts"]["dynamic_agents"] == 1


def test_catch_up_runs_earlier_causal_effect_before_later_lifecycle_spawn(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    orchestrator.configure_lifecycle_system(LifecycleContract.from_mapping({
        "enabled": True,
        "actor_templates": [{
            "template_id": "guard",
            "display_name": "Guard",
            "actor": {
                "display_name": "New Guard",
                "actor_type": "person",
                "attributes": {},
                "states": {"alive": True},
            },
            "entity": {
                "entity_type": "person",
                "name": "Guard Entity",
                "location_id": "frontier",
            },
            "agent": {
                "source_agent_id": "captain_agent",
                "display_name": "Patrol Agent",
                "system_prompt": "Protect the frontier.",
            },
        }],
        "transitions": [{
            "transition_id": "day_four_recruitment",
            "display_name": "Day Four Recruitment",
            "kind": "birth",
            "trigger": "day_start",
            "day": 4,
            "once": True,
            "operations": [{
                "operation": "spawn_actor",
                "template_id": "guard",
                "actor_id_pattern": "guard_{sequence}",
                "entity_id_pattern": "entity_{actor_id}",
                "agent_id_pattern": "agent_{actor_id}",
            }],
        }],
    }), generation=5, preserve_state=False)
    orchestrator.world_state.upsert_resource_definition(ResourceDefinition(
        name="coin", kind="asset", default_quantity=0, min_quantity=0, anchor_rate=1,
    ))
    orchestrator.configure_causal_events([{
        "rule_id": "early_guard_payment",
        "source_event_types": ["payment_order"],
        "child_event_type": "guard_payment",
        "delay_days": 2,
        "probability": 1,
        "world_effects": [{
            "effect_type": "adjust_resource",
            "entity_id": "entity_guard_1",
            "resource_name": "coin",
            "quantity_delta": 1,
            "reason": "payment before recruitment",
        }],
    }])
    orchestrator.inject_event(EventObject(
        event_id="payment-order-1",
        event_type="payment_order",
        severity=1,
        target_agents=[],
        initial_payload={},
        generated_at=0,
    ))
    orchestrator._sim_time = 3 * GAME_SECONDS_PER_DAY

    result = asyncio.run(orchestrator._tick())

    causal = next(item for item in result.generated_events if item["source"] == "causal")
    assert causal["event_type"] == "guard_payment"
    assert causal["generated_at"] == 2 * GAME_SECONDS_PER_DAY
    assert causal["world_effect_status"] == "rejected"
    assert causal["error_code"] == "entity_not_found"
    assert result.lifecycle["applied_count"] == 1
    assert result.lifecycle["applied"][0]["transition_id"] == "day_four_recruitment"
    assert result.lifecycle["applied"][0]["sim_time"] == 3 * GAME_SECONDS_PER_DAY
    assert orchestrator.world_state.get_entity("entity_guard_1") is not None


def test_manual_retirement_removes_dynamic_agent_from_future_dispatch(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    asyncio.run(orchestrator._tick())
    dynamic = orchestrator.get_agent("agent_guard_1")
    dynamic.memory.append({"role": "user", "content": "retirement memory"})

    outcome = orchestrator.execute_lifecycle_transition(
        "retire_guard", request_id="retire-guard-1",
    )

    assert outcome["status"] == "applied"
    assert orchestrator.get_agent("agent_guard_1") is None
    assert "agent_guard_1" not in orchestrator._dynamic_agent_ids
    assert orchestrator.world_state.get_entity("entity_guard_1") is None
    row = orchestrator.world_state.get_gameplay_effect_state()["lifecycle"]["dynamic_agents"]["agent_guard_1"]
    assert row["retired"] is True
    assert row["enabled"] is False


def test_dynamic_agent_memory_survives_source_snapshot_refresh(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    asyncio.run(orchestrator._tick())
    dynamic = orchestrator.get_agent("agent_guard_1")
    dynamic.memory.append({"role": "user", "content": "persistent memory"})

    replacement = OfficialAgent(
        AgentConfig(
            agent_id="captain_agent",
            actor_id="captain_actor",
            display_name="Replacement Captain",
            system_prompt="Replacement captain prompt.",
            max_rounds=9,
        ),
        SimpleNamespace(),
    )
    orchestrator.activate_agent_snapshot(
        {"captain_agent": replacement}, replace_agent_ids={"captain_agent"},
    )

    refreshed = orchestrator.get_agent("agent_guard_1")
    assert refreshed is not dynamic
    assert list(refreshed.memory)[-1]["content"] == "persistent memory"
    assert refreshed.config.max_rounds == 9
    assert refreshed.config.system_prompt == "Protect the frontier."
    assert refreshed.llm is replacement.llm


def test_save_state_contains_lifecycle_latch_snapshot(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    orchestrator._paused = True

    state = orchestrator.save_state()

    assert state["lifecycle_runtime"]["generation"] == 4
    assert state["lifecycle_runtime"]["fingerprint"] == orchestrator.lifecycle_runtime.contract.fingerprint()



def test_lifecycle_marker_continues_after_transition_budget_without_replaying_prefix(tmp_path):
    orchestrator = _orchestrator(tmp_path)
    storm_contract = LifecycleContract.from_mapping({
        "enabled": True,
        "transitions": [{
            "transition_id": f"storm_{index:03d}",
            "display_name": f"Storm {index}",
            "kind": "custom",
            "trigger": "day_start",
            "operations": [{
                "operation": "transform_entity",
                "entity_id": f"missing_{index:03d}",
                "new_status": "processed",
            }],
        } for index in range(300)],
    })
    orchestrator.configure_lifecycle_system(
        storm_contract, generation=40, preserve_state=False,
    )

    first = asyncio.run(orchestrator._tick())

    assert first.lifecycle["failed_count"] == 256
    assert first.lifecycle["truncated"] is True
    assert first.lifecycle["deferred_count"] == 1
    assert orchestrator.deferred_timeline.peek().metadata["start_transition_index"] == 256

    # A DLC hot reload cannot rewrite already materialized marker semantics.
    orchestrator.configure_lifecycle_system(
        LifecycleContract.from_mapping({}), generation=41, preserve_state=True,
    )
    second = asyncio.run(orchestrator._tick())

    assert second.lifecycle["failed_count"] == 44
    assert second.lifecycle["deferred_count"] == 0
    assert [item["transition_id"] for item in second.lifecycle["failed"]] == [
        f"storm_{index:03d}" for index in range(256, 300)
    ]
