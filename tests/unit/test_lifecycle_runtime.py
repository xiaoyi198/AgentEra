from __future__ import annotations

from multiagent_framework.actor_contract import ActorManager
from multiagent_framework.causal_conditions import CausalConditionContext
from multiagent_framework.gameplay_effect_runtime import GameplayEffectRuntime
from multiagent_framework.lifecycle_contract import LifecycleContract
from multiagent_framework.lifecycle_runtime import LifecycleRuntime
from multiagent_framework.world_commands import WorldCommand, WorldCommandType
from multiagent_framework.world_state import Entity, WorldState

DAY = 86400.0


def _spawn_contract(*, cooldown_days: float = 0) -> LifecycleContract:
    return LifecycleContract.from_mapping({
        "enabled": True,
        "recent_feedback_limit": 10,
        "actor_templates": [{
            "template_id": "guard",
            "display_name": "巡防员",
            "actor": {
                "display_name": "新巡防员",
                "actor_type": "person",
                "attributes": {"merit": 8},
                "states": {"alive": True},
            },
            "entity": {
                "entity_type": "person",
                "name": "巡防员实体",
                "location_id": "frontier",
                "metadata": {"tags": ["guard"]},
            },
            "agent": {
                "source_agent_id": "captain_agent",
                "display_name": "巡防智能体",
                "system_prompt": "守卫边境。",
            },
        }],
        "positions": [{
            "position_id": "captain",
            "display_name": "巡防队长",
            "organization_id": "patrol",
            "role": "captain",
            "rank": 10,
            "capacity": 2,
        }],
        "transitions": [{
            "transition_id": "weekly_recruitment",
            "display_name": "每周征募",
            "kind": "birth",
            "trigger": "week_start",
            "cooldown_days": cooldown_days,
            "operations": [
                {
                    "operation": "spawn_actor",
                    "template_id": "guard",
                    "actor_id_pattern": "guard_{sequence}",
                    "entity_id_pattern": "entity_{actor_id}",
                    "agent_id_pattern": "agent_{actor_id}",
                },
                {
                    "operation": "appoint_position",
                    "position_id": "captain",
                    "actor_id": "{actor_id}",
                    "primary": True,
                },
            ],
            "feedback": {"success": "{actor_id} 已加入巡防营。"},
        }],
    })


def test_marker_transition_spawns_actor_entity_agent_and_position_atomically(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    runtime = LifecycleRuntime(_spawn_contract(), generation=3)
    marker = {
        "trigger": "week_start",
        "sim_time": 7 * DAY,
        "day_index": 7,
        "date": {"day": 8, "day_of_year": 8, "month_id": "month_01"},
    }

    first = runtime.poll(7 * DAY, markers=[marker], world_state=world)

    assert first.applied_count == 1
    assert first.failed_count == 0
    assert first.applied[0]["actor_id"] == "guard_1"
    assert world.get_entity("entity_guard_1") is not None
    state = world.get_gameplay_effect_state()
    lifecycle = state["lifecycle"]
    assert lifecycle["dynamic_actors"]["guard_1"]["entity_id"] == "entity_guard_1"
    assert lifecycle["dynamic_agents"]["agent_guard_1"]["source_agent_id"] == "captain_agent"
    assert lifecycle["positions"]["captain"]["incumbent_actor_ids"] == ["guard_1"]
    assert state["actors"]["guard_1"]["memberships"]["patrol"]["role"] == "captain"
    assert lifecycle["recent_feedback"][-1]["message"] == "guard_1 已加入巡防营。"

    replay = runtime.poll(7 * DAY, markers=[marker], world_state=world)
    assert replay.applied_count == 0
    assert replay.skipped[0]["reason"] == "already_applied"
    assert len(world.get_gameplay_effect_state()["lifecycle"]["receipts"]) == 1


def test_restart_after_lifecycle_receipt_commit_before_cursor_ack_is_idempotent(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    contract = _spawn_contract()
    runtime = LifecycleRuntime(contract, generation=3)
    before_ack = runtime.snapshot()
    marker = {
        "trigger": "week_start",
        "sim_time": 7 * DAY,
        "day_index": 7,
        "date": {"day": 8, "day_of_year": 8, "month_id": "month_01"},
    }

    committed = runtime.poll(7 * DAY, markers=[marker], world_state=world)
    assert committed.applied_count == 1
    assert world.get_entity("entity_guard_1") is not None

    restarted = LifecycleRuntime(contract, generation=3)
    restarted.restore(before_ack)
    replayed = restarted.poll(7 * DAY, markers=[marker], world_state=world)

    assert replayed.applied_count == 0
    assert replayed.skipped[0]["reason"] == "already_applied"
    lifecycle = world.get_gameplay_effect_state()["lifecycle"]
    assert list(lifecycle["dynamic_actors"]) == ["guard_1"]
    assert len(lifecycle["receipts"]) == 1


def test_cooldown_uses_sqlite_receipts_across_runtime_recreation(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    contract = _spawn_contract(cooldown_days=7)
    first_runtime = LifecycleRuntime(contract, generation=1)
    first_runtime.poll(
        7 * DAY,
        markers=[{"trigger": "week_start", "sim_time": 7 * DAY, "day_index": 7}],
        world_state=world,
    )

    recreated = LifecycleRuntime(contract, generation=1)
    blocked = recreated.poll(
        10 * DAY,
        markers=[{"trigger": "week_start", "sim_time": 10 * DAY, "day_index": 10}],
        world_state=world,
    )
    assert blocked.skipped[0]["reason"] == "cooldown"

    allowed = recreated.poll(
        14 * DAY,
        markers=[{"trigger": "week_start", "sim_time": 14 * DAY, "day_index": 14}],
        world_state=world,
    )
    assert allowed.applied_count == 1
    assert allowed.applied[0]["actor_id"] == "guard_2"


def test_condition_edge_latch_snapshot_prevents_repeat_until_false(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    world.upsert_entity(Entity(id="fort", type="location", name="Fort", current_status="open"))
    contract = LifecycleContract.from_mapping({
        "enabled": True,
        "transitions": [{
            "transition_id": "fort_closes",
            "display_name": "关闭堡垒",
            "kind": "entity_transform",
            "trigger": "condition",
            "conditions": [{
                "kind": "time",
                "name": "days",
                "operator": "gte",
                "value": 1,
            }],
            "edge_triggered": True,
            "operations": [{
                "operation": "transform_entity",
                "entity_id": "fort",
                "new_status": "closed",
            }],
        }],
    })
    runtime = LifecycleRuntime(contract, generation=1)

    first = runtime.poll(DAY, world_state=world)
    assert first.applied_count == 1
    assert world.get_entity("fort").current_status == "closed"
    version = world.get_entity("fort").version

    restored = LifecycleRuntime(contract, generation=1)
    restored.restore(runtime.snapshot())
    second = restored.poll(2 * DAY, world_state=world)
    assert second.applied_count == 0
    assert world.get_entity("fort").version == version


def test_marker_poll_can_defer_condition_evaluation_without_mutating_latch(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    world.upsert_entity(Entity(id="marker_target", type="location", name="Marker", current_status="open"))
    world.upsert_entity(Entity(id="condition_target", type="location", name="Condition", current_status="open"))
    contract = LifecycleContract.from_mapping({
        "enabled": True,
        "transitions": [
            {
                "transition_id": "historical_marker",
                "display_name": "Historical marker",
                "kind": "entity_transform",
                "trigger": "day_start",
                "day": 2,
                "operations": [{
                    "operation": "transform_entity",
                    "entity_id": "marker_target",
                    "new_status": "marked",
                }],
            },
            {
                "transition_id": "current_condition",
                "display_name": "Current condition",
                "kind": "entity_transform",
                "trigger": "condition",
                "conditions": [{
                    "kind": "time",
                    "name": "days",
                    "operator": "gte",
                    "value": 1,
                }],
                "edge_triggered": True,
                "operations": [{
                    "operation": "transform_entity",
                    "entity_id": "condition_target",
                    "new_status": "condition_applied",
                }],
            },
        ],
    })
    runtime = LifecycleRuntime(contract, generation=1)
    marker = {
        "trigger": "day_start",
        "sim_time": DAY,
        "day_index": 1,
        "date": {"day": 2, "day_of_year": 2, "month_id": "month_01"},
    }

    historical = runtime.poll(
        DAY, markers=[marker], world_state=world, evaluate_conditions=False,
    )

    assert historical.applied_count == 1
    assert historical.applied[0]["transition_id"] == "historical_marker"
    assert world.get_entity("marker_target").current_status == "marked"
    assert world.get_entity("condition_target").current_status == "open"
    assert runtime.snapshot()["condition_state"] == {}

    current = runtime.poll(DAY, world_state=world, evaluate_conditions=True)

    assert current.applied_count == 1
    assert current.applied[0]["transition_id"] == "current_condition"
    assert world.get_entity("condition_target").current_status == "condition_applied"
    assert runtime.snapshot()["condition_state"]["current_condition"] == {
        "latch": True,
        "last_evaluated_at": DAY,
    }


def test_deterministic_position_candidate_uses_scores_then_actor_id(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    actors = ActorManager()
    actors.configure([
        {"actor_id": "candidate_b", "display_name": "B", "actor_type": "person", "attributes": {"merit": 9}},
        {"actor_id": "candidate_a", "display_name": "A", "actor_type": "person", "attributes": {"merit": 9}},
        {"actor_id": "candidate_c", "display_name": "C", "actor_type": "person", "attributes": {"merit": 5}},
    ], generation=1)
    contract = LifecycleContract.from_mapping({
        "enabled": True,
        "positions": [{
            "position_id": "governor",
            "display_name": "总督",
            "organization_id": "court",
            "role": "governor",
            "capacity": 1,
            "candidate_actor_ids": ["candidate_b", "candidate_c", "candidate_a"],
            "scores": [{"source": "attribute", "name": "merit", "direction": "desc", "weight": 1}],
        }],
        "transitions": [{
            "transition_id": "appoint_governor",
            "display_name": "任命总督",
            "kind": "succession",
            "trigger": "manual",
            "operations": [{"operation": "appoint_position", "position_id": "governor"}],
        }],
    })
    runtime = LifecycleRuntime(contract, generation=2)

    result = runtime.execute_manual(
        "appoint_governor",
        world_state=world,
        sim_time=20,
        request_id="appointment-1",
        actor_manager=actors,
    )

    assert result["status"] == "applied"
    assert world.get_gameplay_effect_state()["lifecycle"]["positions"]["governor"]["incumbent_actor_ids"] == ["candidate_a"]


def test_retire_actor_tombstones_actor_agent_vacates_position_and_removes_entity(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    runtime = LifecycleRuntime(_spawn_contract(), generation=1)
    runtime.poll(
        DAY,
        markers=[{"trigger": "week_start", "sim_time": DAY, "day_index": 1}],
        world_state=world,
    )
    retire_contract = LifecycleContract.from_mapping({
        "enabled": True,
        "positions": [{
            "position_id": "captain",
            "display_name": "巡防队长",
            "organization_id": "patrol",
            "role": "captain",
            "capacity": 2,
        }],
        "transitions": [{
            "transition_id": "retire_guard",
            "display_name": "巡防员退役",
            "kind": "death",
            "trigger": "manual",
            "operations": [{
                "operation": "retire_actor",
                "actor_id": "guard_1",
                "remove_entity": True,
            }],
        }],
    })
    retire = LifecycleRuntime(retire_contract, generation=2)

    result = retire.execute_manual(
        "retire_guard",
        world_state=world,
        sim_time=2 * DAY,
        request_id="retire-1",
    )

    assert result["status"] == "applied"
    state = world.get_gameplay_effect_state()
    assert state["lifecycle"]["dynamic_actors"]["guard_1"]["retired"] is True
    assert state["lifecycle"]["dynamic_agents"]["agent_guard_1"]["enabled"] is False
    assert state["lifecycle"]["positions"]["captain"]["vacant"] is True
    assert state["actors"]["guard_1"]["memberships"]["patrol"]["removed"] is True
    assert world.get_entity("entity_guard_1") is None


def test_atomic_batch_rolls_back_spawn_when_later_operation_fails(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    document = _spawn_contract().to_dict()
    document["transitions"][0]["operations"].append({
        "operation": "transform_entity",
        "entity_id": "missing_entity",
        "new_status": "invalid",
    })
    runtime = LifecycleRuntime(LifecycleContract.from_mapping(document), generation=1)

    result = runtime.poll(
        DAY,
        markers=[{"trigger": "week_start", "sim_time": DAY, "day_index": 1}],
        world_state=world,
    )

    assert result.failed_count == 1
    assert world.get_entity("entity_guard_1") is None
    state = world.get_gameplay_effect_state()
    assert state["lifecycle"]["dynamic_actors"] == {}
    assert state["lifecycle"]["receipts"] == {}


def test_split_and_merge_organizations_expand_membership_and_entity_commands(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    world.upsert_entity(Entity(id="old_order", type="organization", name="Old Order", current_status="active"))
    actors = ActorManager()
    actors.configure([{
        "actor_id": "member_1",
        "display_name": "Member",
        "actor_type": "person",
        "memberships": [{"organization_id": "old_order", "role": "member"}],
    }], generation=1)
    split_contract = LifecycleContract.from_mapping({
        "enabled": True,
        "transitions": [{
            "transition_id": "split_order",
            "display_name": "组织分裂",
            "kind": "organization_split",
            "trigger": "manual",
            "operations": [{
                "operation": "split_organization",
                "source_organization_id": "old_order",
                "destinations": [{
                    "organization_id": "new_order_a",
                    "name": "New Order A",
                    "actor_ids": ["member_1"],
                    "role": "founder",
                }],
            }],
        }],
    })
    split_runtime = LifecycleRuntime(split_contract, generation=1)
    split = split_runtime.execute_manual(
        "split_order",
        world_state=world,
        sim_time=10,
        request_id="split-1",
        actor_manager=actors,
    )
    assert split["status"] == "applied"
    assert world.get_entity("old_order").current_status == "split"
    assert world.get_entity("new_order_a") is not None
    memberships = world.get_gameplay_effect_state()["actors"]["member_1"]["memberships"]
    assert memberships["old_order"]["removed"] is True
    assert memberships["new_order_a"]["role"] == "founder"

    merge_contract = LifecycleContract.from_mapping({
        "enabled": True,
        "transitions": [{
            "transition_id": "merge_order",
            "display_name": "组织合并",
            "kind": "organization_merge",
            "trigger": "manual",
            "operations": [{
                "operation": "merge_organizations",
                "source_organization_ids": ["new_order_a"],
                "target_organization_id": "united_order",
                "target": {"name": "United Order"},
                "actor_ids": ["member_1"],
                "role": "member",
            }],
        }],
    })
    merge_runtime = LifecycleRuntime(merge_contract, generation=2)
    merged = merge_runtime.execute_manual(
        "merge_order",
        world_state=world,
        sim_time=20,
        request_id="merge-1",
        actor_manager=actors,
    )
    assert merged["status"] == "applied"
    assert world.get_entity("new_order_a").current_status == "merged"
    assert world.get_entity("united_order") is not None
    memberships = world.get_gameplay_effect_state()["actors"]["member_1"]["memberships"]
    assert memberships["new_order_a"]["removed"] is True
    assert memberships["united_order"]["role"] == "member"
