from __future__ import annotations

from multiagent_framework.actor_contract import ActorManager
from multiagent_framework.gameplay_effect_runtime import (
    GameplayEffectRuntime,
    normalize_gameplay_effect_state,
    reduce_gameplay_effect_state,
)
from multiagent_framework.world_commands import WorldCommand, WorldCommandType
from multiagent_framework.world_state import Entity, WorldState


def _lifecycle_payload() -> dict:
    return {
        "transition_id": "weekly_recruitment",
        "receipt_id": "weekly_recruitment:week:4",
        "kind": "birth",
        "dynamic_actor_upserts": [
            {
                "actor_id": "巡防员_1",
                "display_name": "巡防员一号",
                "actor_type": "person",
                "attributes": {"merit": 8},
                "memberships": [{"organization_id": "巡防营", "role": "guard"}],
            }
        ],
        "dynamic_agent_upserts": [
            {
                "agent_id": "巡防智能体_1",
                "actor_id": "巡防员_1",
                "source_agent_id": "哨站守备官",
                "display_name": "巡防智能体一号",
            }
        ],
        "position_updates": [
            {
                "position_id": "patrol_captain",
                "display_name": "巡防队长",
                "organization_id": "巡防营",
                "role": "captain",
                "capacity": 1,
                "incumbent_actor_ids": ["巡防员_1"],
            }
        ],
        "receipt": {"marker_id": "week:4", "sequence": 1},
        "feedback": {"message": "巡防员一号加入巡防营。"},
        "feedback_limit": 20,
    }


def test_v2_state_normalizes_to_v3_with_empty_lifecycle_bucket():
    state = normalize_gameplay_effect_state({
        "schema_version": "2.0",
        "actors": {"captain": {"actor_id": "captain"}},
    })

    assert state["schema_version"] == "3.0"
    assert state["actors"]["captain"]["actor_id"] == "captain"
    assert state["lifecycle"] == {
        "dynamic_actors": {},
        "dynamic_agents": {},
        "positions": {},
        "receipts": {},
        "recent_feedback": [],
    }


def test_modify_membership_persists_full_actor_membership_shape():
    state, _ = reduce_gameplay_effect_state(
        None,
        command_type="modify_membership",
        payload={
            "actor_id": "captain",
            "organization_id": "fleet",
            "operation": "upsert",
            "role": "commander",
            "status": "active",
            "rank": 12.5,
            "primary": True,
            "description": "Primary fleet command.",
            "metadata": {"appointed_by": "council"},
        },
        sim_time=25,
        command_id="membership-1",
    )

    membership = state["actors"]["captain"]["memberships"]["fleet"]
    assert membership == {
        "organization_id": "fleet",
        "role": "commander",
        "status": "active",
        "rank": 12.5,
        "primary": True,
        "description": "Primary fleet command.",
        "metadata": {"appointed_by": "council"},
        "updated_at": 25.0,
        "command_id": "membership-1",
    }


def test_lifecycle_transition_reducer_writes_all_authoritative_buckets_and_receipt():
    state, result = reduce_gameplay_effect_state(
        None,
        command_type="apply_lifecycle_transition",
        payload=_lifecycle_payload(),
        sim_time=400,
        command_id="lifecycle-1",
    )

    lifecycle = state["lifecycle"]
    assert result["already_applied"] is False
    assert lifecycle["dynamic_actors"]["巡防员_1"]["active"] is True
    assert lifecycle["dynamic_agents"]["巡防智能体_1"]["source_agent_id"] == "哨站守备官"
    assert lifecycle["positions"]["patrol_captain"]["vacant"] is False
    assert lifecycle["receipts"]["weekly_recruitment:week:4"]["sequence"] == 1
    assert lifecycle["recent_feedback"][-1]["message"] == "巡防员一号加入巡防营。"

    replay_state, replay_result = reduce_gameplay_effect_state(
        state,
        command_type="apply_lifecycle_transition",
        payload={**_lifecycle_payload(), "feedback": {"message": "不得重复"}},
        sim_time=401,
        command_id="lifecycle-duplicate-receipt",
    )
    assert replay_result["already_applied"] is True
    assert replay_state == state
    assert len(replay_state["lifecycle"]["recent_feedback"]) == 1


def test_lifecycle_retirement_creates_tombstones_for_actor_and_agent():
    initial, _ = reduce_gameplay_effect_state(
        None,
        command_type="apply_lifecycle_transition",
        payload=_lifecycle_payload(),
        sim_time=400,
        command_id="lifecycle-birth",
    )
    payload = {
        "transition_id": "retire_guard",
        "receipt_id": "retire_guard:巡防员_1",
        "kind": "death",
        "dynamic_actor_retirements": [{"actor_id": "巡防员_1", "reason": "fallen"}],
        "dynamic_agent_retirements": [{"agent_id": "巡防智能体_1", "reason": "actor_retired"}],
        "position_updates": [{
            "position_id": "patrol_captain",
            "capacity": 1,
            "incumbent_actor_ids": [],
        }],
        "receipt": {},
        "feedback": [{"message": "巡防队长职位空缺。"}],
    }

    state, _ = reduce_gameplay_effect_state(
        initial,
        command_type="apply_lifecycle_transition",
        payload=payload,
        sim_time=500,
        command_id="lifecycle-retire",
    )

    assert state["lifecycle"]["dynamic_actors"]["巡防员_1"]["retired"] is True
    assert state["lifecycle"]["dynamic_agents"]["巡防智能体_1"]["enabled"] is False
    assert state["lifecycle"]["positions"]["patrol_captain"]["vacant"] is True


def test_world_command_routes_lifecycle_state_and_roundtrips_export(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    result = world.command_bus.execute(WorldCommand(
        command_type=WorldCommandType.APPLY_LIFECYCLE_TRANSITION,
        command_id="lifecycle-world-1",
        sim_time=400,
        payload=_lifecycle_payload(),
    ))

    assert result.ok
    assert result.result["receipt_id"] == "weekly_recruitment:week:4"
    exported = world.export_all()
    restored = WorldState(str(tmp_path / "restored.db"))
    restored.restore_all(exported)
    assert restored.get_gameplay_effect_state()["lifecycle"] == exported["gameplay_effect_state"]["lifecycle"]

    runtime = GameplayEffectRuntime()
    runtime.refresh(restored)
    assert runtime.public_state()["lifecycle"]["positions"]["patrol_captain"]["vacant"] is False


def test_invalid_lifecycle_finalizer_rolls_back_prior_atomic_world_mutation(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    batch = world.command_bus.execute_batch([
        WorldCommand(
            command_type=WorldCommandType.CREATE_ENTITY,
            command_id="create-before-invalid-lifecycle",
            entity_id="new_guard_entity",
            payload={"entity_type": "person", "name": "New Guard"},
        ),
        WorldCommand(
            command_type=WorldCommandType.APPLY_LIFECYCLE_TRANSITION,
            command_id="invalid-lifecycle-finalizer",
            payload={"transition_id": "birth", "receipt_id": ""},
        ),
    ], atomic=True)

    assert not batch.ok
    assert not batch.committed
    assert world.get_entity("new_guard_entity") is None
    assert world.get_gameplay_effect_state()["lifecycle"]["receipts"] == {}


def test_gameplay_runtime_projects_dynamic_actor_and_membership_overlay(tmp_path):
    world = WorldState(str(tmp_path / "dynamic-actor.db"))
    world.command_bus.execute(WorldCommand(
        command_type=WorldCommandType.APPLY_LIFECYCLE_TRANSITION,
        command_id="spawn-dynamic-actor",
        sim_time=10,
        payload=_lifecycle_payload(),
    )).require_ok()
    world.command_bus.execute(WorldCommand(
        command_type=WorldCommandType.MODIFY_MEMBERSHIP,
        command_id="promote-dynamic-actor",
        sim_time=11,
        payload={
            "actor_id": "巡防员_1",
            "organization_id": "巡防营",
            "operation": "upsert",
            "role": "captain",
            "rank": 10,
            "primary": True,
            "description": "Lifecycle appointment",
        },
    )).require_ok()

    actors = ActorManager()
    actors.configure([{
        "actor_id": "static_actor",
        "display_name": "Static",
        "actor_type": "person",
    }], generation=2)
    runtime = GameplayEffectRuntime()
    runtime.refresh(world)
    runtime.sync_managers(
        actor_manager=actors,
        issue_manager=None,
        objective_manager=None,
        action_catalog=None,
        sim_time=11,
        project_issues=False,
    )

    dynamic = actors.get("巡防员_1")
    assert dynamic is not None
    assert dynamic["display_name"] == "巡防员一号"
    assert dynamic["memberships"][0]["role"] == "captain"
    assert dynamic["memberships"][0]["rank"] == 10
    assert dynamic["memberships"][0]["primary"] is True
    assert {item["actor_id"] for item in actors.list()} == {"static_actor", "巡防员_1"}


def test_dynamic_actor_survives_static_generation_reload_until_lifecycle_tombstone():
    actors = ActorManager()
    actors.configure([{
        "actor_id": "static_actor",
        "display_name": "Static v1",
        "actor_type": "person",
    }], generation=1)
    actors.set_lifecycle_definitions({
        "dynamic_actor": {
            "actor_id": "dynamic_actor",
            "display_name": "Dynamic",
            "actor_type": "person",
            "active": True,
            "retired": False,
            "attributes": {"merit": 1},
        }
    })
    actors.configure([{
        "actor_id": "static_actor",
        "display_name": "Static v2",
        "actor_type": "person",
    }], generation=2, preserve_state=True)

    assert actors.get("dynamic_actor") is not None
    assert actors.get("static_actor")["display_name"] == "Static v2"
    assert actors.generation == 2

    actors.set_lifecycle_definitions({
        "dynamic_actor": {
            "actor_id": "dynamic_actor",
            "retired": True,
            "active": False,
        }
    })
    assert actors.get("dynamic_actor") is None
    assert [item["actor_id"] for item in actors.list()] == ["static_actor"]

def test_lifecycle_tombstone_hides_static_actor_across_generation_reload():
    actors = ActorManager()
    actors.configure([{
        "actor_id": "retired_founder",
        "display_name": "Founder v1",
        "actor_type": "person",
    }], generation=1)

    actors.set_lifecycle_definitions({
        "retired_founder": {
            "actor_id": "retired_founder",
            "retired": True,
            "active": False,
        }
    })

    assert actors.get("retired_founder") is None
    assert actors.list() == []
    assert actors.configured is False

    actors.configure([{
        "actor_id": "retired_founder",
        "display_name": "Founder v2",
        "actor_type": "person",
    }], generation=2, preserve_state=True)

    assert actors.get("retired_founder") is None
    assert actors.list() == []
    assert actors.configured is False
