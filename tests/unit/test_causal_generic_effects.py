from __future__ import annotations

from types import SimpleNamespace

import pytest

from multiagent_framework.action_contract import ActionCatalog
from multiagent_framework.actor_contract import ActorManager
from multiagent_framework.causal_conditions import (
    CausalCondition,
    CausalEventError,
    CausalConditionContext,
    compare_condition_value,
    resolve_condition_value,
)
from multiagent_framework.causal_world_effects import execute_causal_world_commands
from multiagent_framework.gameplay_effect_runtime import GameplayEffectRuntime
from multiagent_framework.gameplay_effects import (
    materialize_world_commands,
    normalize_world_effect,
    normalize_world_effects,
)
from multiagent_framework.issue_contract import IssueManager
from multiagent_framework.objective_contract import ObjectiveManager
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.types import EventObject
from multiagent_framework.world_commands import WorldCommand, WorldCommandType
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState


@pytest.mark.parametrize("effect", [
    {"effect_type": "create_entity", "entity_id": "new_port", "entity_type": "location", "name": "New Port"},
    {"effect_type": "move_entity", "entity_id": "ship", "x": 10, "y": 20, "region": "north"},
    {"effect_type": "transform_entity", "entity_id": "ship", "new_type": "fort", "new_name": "Sea Fort"},
    {"effect_type": "remove_entity", "entity_id": "ship"},
    {"effect_type": "enable_agent", "agent_id": "captain", "actor_id": "captain_actor"},
    {"effect_type": "disable_agent", "agent_id": "captain"},
    {"effect_type": "modify_relationship", "operation": "upsert", "parent_id": "a", "child_id": "b", "rel_type": "ally"},
    {"effect_type": "modify_membership", "operation": "upsert", "actor_id": "captain_actor", "organization_id": "fleet", "role": "captain"},
    {"effect_type": "create_issue", "issue_type_id": "security", "summary": "Raid reported"},
    {"effect_type": "reveal_information", "entity_id": "ship", "knowledge_id": "route", "value": "east"},
    {"effect_type": "advance_objective", "objective_id": "secure_sea", "mode": "adjust", "amount": 0.5},
    {"effect_type": "unlock_action", "action_id": "sail", "scope_id": "captain"},
    {"effect_type": "switch_phase", "phase_id": "war", "metadata": {"chapter": 2}},
])
def test_all_generic_effect_types_normalize(effect):
    normalized = normalize_world_effect(effect)
    assert normalized["effect_type"] == effect["effect_type"]


def test_investigation_result_policy_is_normalized_before_runtime():
    effect = normalize_world_effect({
        "effect_type": "start_investigation",
        "investigation_id": "verify_route",
        "entity_id": "station",
        "knowledge_id": "route",
        "investigator_layer": "player_knowledge",
        "investigator_id": "player",
        "result_policy": {
            "source_layer": "world_truth",
            "target_layer": "player_knowledge",
            "confidence_multiplier": 0.75,
            "secrecy_level": "restricted",
            "metadata": {"method": "survey"},
        },
    })

    assert effect["result_policy"] == {
        "source_layer": "world_truth",
        "target_layer": "player_knowledge",
        "source_holder_id": "",
        "target_holder_id": "player",
        "confidence_multiplier": 0.75,
        "secrecy_level": "restricted",
        "distortion": {},
        "metadata": {"method": "survey"},
    }


@pytest.mark.parametrize("policy", [
    {"source_layer": "invalid", "target_layer": "player_knowledge"},
    {"source_layer": "world_truth", "target_layer": "world_truth"},
    {"source_layer": "actor_belief", "target_layer": "player_knowledge"},
    {"source_layer": "world_truth", "target_layer": "player_knowledge", "confidence_multiplier": 1.1},
    {"source_layer": "world_truth", "target_layer": "player_knowledge", "secrecy_level": "unknown"},
])
def test_investigation_result_policy_rejects_unsafe_authored_values(policy):
    with pytest.raises(CausalEventError, match="causal_effect_investigation_result_invalid"):
        normalize_world_effect({
            "effect_type": "start_investigation",
            "investigation_id": "verify_route",
            "knowledge_id": "route",
            "investigator_id": "player",
            "result_policy": policy,
        })


def _world(tmp_path, name="world.db") -> WorldState:
    world = WorldState(str(tmp_path / name))
    world.upsert_resource_definition(ResourceDefinition(
        name="credits", kind="asset", default_quantity=0, min_quantity=0, anchor_rate=1,
    ))
    world.upsert_entity(Entity(id="station", type="location", name="Station"))
    return world


def _event(commands, *, event_id="generic-event") -> EventObject:
    return EventObject(
        event_id=event_id,
        event_type="generic",
        severity=5,
        target_agents=[],
        initial_payload={"causal_chain_id": "chain-1", "causal_parent_event_id": "source-1"},
        generated_at=100,
        world_commands=commands,
    )


def test_create_entity_resources_expand_and_atomic_failure_rolls_back(tmp_path):
    world = _world(tmp_path)
    effects = normalize_world_effects([{
        "effect_type": "create_entity",
        "entity_id": "new_port",
        "entity_type": "location",
        "name": "New Port",
        "resources": {"credits": 10, "missing_token": 1},
    }])
    commands = materialize_world_commands(
        effects, world_state=world, due_at=100, source_id="child-1", chain_id="chain-1",
    )
    assert [item["command_type"] for item in commands] == [
        "create_entity", "set_resource", "set_resource",
    ]

    result = execute_causal_world_commands(world, _event(commands), sim_time=100)

    assert result is not None and not result.ok and not result.committed
    assert world.get_entity("new_port") is None
    assert world.get_inventory("new_port") == []


def test_gameplay_effect_state_roundtrips_through_world_export(tmp_path):
    world = _world(tmp_path)
    effects = normalize_world_effects([
        {"effect_type": "disable_agent", "agent_id": "captain", "reason": "injured"},
        {"effect_type": "reveal_information", "entity_id": "station", "knowledge_id": "route", "value": "east"},
        {"effect_type": "advance_objective", "objective_id": "secure_sea", "mode": "set", "progress": 1, "status": "completed"},
        {"effect_type": "unlock_action", "action_id": "sail", "scope_id": "captain"},
        {"effect_type": "switch_phase", "phase_id": "war", "metadata": {"chapter": 2}},
    ])
    commands = materialize_world_commands(
        effects, world_state=world, due_at=100, source_id="child-state", chain_id="chain-state",
    )
    result = execute_causal_world_commands(world, _event(commands, event_id="state-event"), sim_time=100)
    assert result is not None and result.ok

    exported = world.export_all()
    restored = _world(tmp_path, "restored.db")
    restored.restore_all(exported)

    assert restored.get_gameplay_effect_state() == world.get_gameplay_effect_state()
    assert exported["gameplay_effect_state_version"] == len(commands)
    assert restored.export_all()["gameplay_effect_state_updated_at"] == 100


def test_runtime_projects_membership_issue_objective_action_and_phase(tmp_path):
    world = _world(tmp_path)
    actor_manager = ActorManager()
    actor_manager.configure([{
        "actor_id": "captain_actor",
        "display_name": "Captain",
        "actor_type": "person",
        "memberships": [{"organization_id": "fleet", "role": "captain"}],
    }], generation=1)
    action_catalog = ActionCatalog()
    action_catalog.configure([{
        "action_id": "sail",
        "display_name": "Sail",
        "initially_unlocked": False,
    }], generation=1)
    issue_manager = IssueManager()
    issue_manager.configure([{
        "issue_type_id": "security",
        "display_name": "Security",
        "source": {"allowed_kinds": ["system"], "allowed_ids": ["causal_event"]},
    }], generation=1, action_ids={"sail"})
    objective_manager = ObjectiveManager()
    objective_manager.configure([{
        "objective_id": "secure_sea",
        "display_name": "Secure Sea",
        "completion": {"kind": "sim_day", "operator": "gte", "value": 999},
    }], [], generation=1)

    effects = normalize_world_effects([
        {"effect_type": "modify_membership", "operation": "remove", "actor_id": "captain_actor", "organization_id": "fleet"},
        {"effect_type": "create_issue", "issue_type_id": "security", "summary": "Raid reported"},
        {"effect_type": "advance_objective", "objective_id": "secure_sea", "mode": "set", "progress": 1},
        {"effect_type": "unlock_action", "action_id": "sail", "scope_id": ""},
        {"effect_type": "switch_phase", "phase_id": "war", "metadata": {"chapter": 2}},
    ])
    commands = materialize_world_commands(
        effects, world_state=world, due_at=100, source_id="child-projection", chain_id="chain-projection",
    )
    result = execute_causal_world_commands(world, _event(commands, event_id="projection-event"), sim_time=100)
    assert result is not None and result.ok

    runtime = GameplayEffectRuntime()
    runtime.refresh(world)
    runtime.sync_managers(
        actor_manager=actor_manager,
        issue_manager=issue_manager,
        objective_manager=objective_manager,
        action_catalog=action_catalog,
        sim_time=100,
    )

    actor = actor_manager.get("captain_actor")
    assert actor is not None and actor["memberships"] == []
    assert action_catalog.public_state()["actions"][0]["unlocked"] is True
    assert len(issue_manager.list_issues(sim_time=100)) == 1
    objective = objective_manager.public_state(world_state=world, sim_time=100)["objectives"][0]
    assert objective["status"] == "completed"
    condition = CausalCondition.from_mapping({
        "kind": "phase", "field": "metadata.chapter", "operator": "eq", "value": 2,
    })
    actual = resolve_condition_value(condition, CausalConditionContext(
        world_state=world, knowledge_provider=runtime, sim_time=100,
    ))
    assert compare_condition_value(actual, condition.operator, condition.value)


class _Court:
    def get_official(self, _agent_id):
        return None


class _Router:
    def __init__(self):
        self.llm = SimpleNamespace(token_counter=SimpleNamespace(to_dict=lambda: {}))


class _Timers:
    def pop_due(self, _sim_time):
        return []

    def get_in_transit(self):
        return []


def test_orchestrator_refreshes_effect_state_and_blocks_disabled_agent(tmp_path):
    world = _world(tmp_path)
    action_agent = SimpleNamespace(
        world_state=world,
        time_simulator=TimeSimulator(),
        llm=None,
        _prompt_template="",
    )
    orchestrator = Orchestrator(
        court_graph=_Court(), timer_system=_Timers(), chain_log=SimpleNamespace(),
        router=_Router(), action_agent=action_agent,
    )
    orchestrator.agents["captain"] = SimpleNamespace(agent_id="captain")
    command = WorldCommand(
        command_type=WorldCommandType.SET_AGENT_ENABLED,
        command_id="disable-captain",
        entity_id="captain",
        sim_time=10,
        payload={"agent_id": "captain", "enabled": False},
    )
    assert world.command_bus.execute(command).ok

    orchestrator._sync_gameplay_effect_runtime()
    orchestrator.inject_event(EventObject(
        event_id="wake-disabled", event_type="alert", severity=5,
        target_agents=["captain"], initial_payload={}, generated_at=10,
    ))

    assert orchestrator.is_agent_enabled("captain") is False
    assert "captain" not in orchestrator._action_agent_ids()
    assert orchestrator._pending_wakeups == []
    assert orchestrator.gameplay_effects_public_state()["agents"]["captain"]["enabled"] is False
