from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy

import pytest

from multiagent_framework.action_contract import (
    ActionCatalog,
    ActionContractError,
    ActionDefinition,
)
from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.world_state import (
    Entity,
    EntityValue,
    InventoryItem,
    ResourceDefinition,
    WorldState,
)


def _gameplay() -> GameplayContract:
    return GameplayContract.from_content(
        {
            "player": {
                "identity_id": "commander",
                "display_name": "Commander",
                "actor_id": "player_actor",
                "controlled_objects": [
                    {"kind": "entity", "id": "station", "mode": "direct"},
                    {"kind": "entity", "id": "outpost", "mode": "command"},
                    {"kind": "agent", "id": "aide", "mode": "command"},
                ],
            }
        },
        [{"id": "aide", "role": "autonomous"}, {"id": "scout", "role": "autonomous"}],
        package_id="test_world",
    )


def _world(tmp_path) -> WorldState:
    world = WorldState(str(tmp_path / "world.db"))
    world.apply_dlc_schema_update(
        entities=[
            Entity(id="station", type="location", name="Station", current_status="ready"),
            Entity(id="outpost", type="location", name="Outpost", current_status="ready"),
            Entity(id="remote", type="location", name="Remote", current_status="closed"),
        ],
        inventory=[
            InventoryItem(entity_id="station", category="credits", item_name="credits", quantity=10),
            InventoryItem(entity_id="station", category="badge", item_name="badge", quantity=1),
            InventoryItem(entity_id="outpost", category="credits", item_name="credits", quantity=10),
        ],
        entity_values=[
            EntityValue(entity_id="station", name="alert", kind="metric", value=40),
            EntityValue(entity_id="outpost", name="alert", kind="metric", value=10),
        ],
        resource_definitions=[
            ResourceDefinition(name="credits", kind="asset", anchor_rate=2, min_quantity=0),
            ResourceDefinition(name="badge", kind="asset", anchor_rate=1, min_quantity=0, max_quantity=1),
            ResourceDefinition(name="alert", kind="metric", min_quantity=0, max_quantity=100),
        ],
    )
    return world


def _action(**overrides):
    action = {
        "action_id": "patrol",
        "display_name": "Patrol",
        "description": "A deterministic test action",
        "parameters": [
            {
                "name": "gain",
                "type": "number",
                "required": False,
                "default": 5,
                "enum": [5, 10],
                "minimum": 1,
                "maximum": 10,
            },
            {"name": "note", "type": "string", "required": True},
        ],
        "target": {
            "kind": "entity",
            "required": True,
            "allowed_ids": ["station", "outpost"],
            "entity_types": ["location"],
            "controlled_only": True,
        },
        "permissions": {
            "control_modes": ["direct"],
            "control_kinds": ["entity"],
            "control_ids": ["station"],
            "actor_ids": ["player_actor"],
        },
        "preconditions": [
            {
                "kind": "anchored_inventory",
                "entity_id": "$control",
                "name": "credits",
                "operator": "gte",
                "base_value": 8,
            },
            {
                "kind": "metric",
                "entity_id": "$target",
                "name": "alert",
                "operator": "lte",
                "value": 80,
            },
            {
                "kind": "entity_status",
                "entity_id": "$target",
                "operator": "eq",
                "value": "ready",
            },
        ],
        "costs": [
            {
                "entity_id": "$control",
                "resource_name": "credits",
                "base_value": 8,
                "reason": "patrol cost",
            }
        ],
        "duration": {
            "action_type": "patrol",
            "proposed_days": 99,
            "severity": 4,
            "resource_value": 8,
        },
        "cooldown": {"days": 3, "scope": "control_target"},
        "result": {
            "mode": "composite",
            "results": [
                {
                    "mode": "world_commands",
                    "effects": [
                        {
                            "effect_type": "adjust_resource",
                            "entity_id": "$target",
                            "resource_name": "alert",
                            "quantity_delta": "$param.gain",
                            "reason": "$param.note",
                        }
                    ],
                },
                {
                    "mode": "ongoing_event",
                    "effect": {
                        "effect_type": "create_ongoing_event",
                        "entity_id": "$target",
                        "event_type": "patrol",
                        "description": "$param.note",
                        "duration_days": "$duration_days",
                        "expected_result": {"new_status": "done"},
                    },
                },
                {
                    "mode": "wake_agent",
                    "agent_id": "aide",
                    "payload": {"target": "$target", "gain": "$param.gain"},
                },
                {
                    "mode": "emit_event",
                    "event": {
                        "event_type": "patrol_started",
                        "severity": 3,
                        "target_agents": ["aide"],
                        "initial_payload": {"target": "$target"},
                    },
                },
            ],
        },
        "enabled": True,
    }
    action.update(overrides)
    return action


def _catalog(action=None, generation=7) -> ActionCatalog:
    catalog = ActionCatalog()
    catalog.configure([action or _action()], generation=generation, gameplay=_gameplay())
    return catalog


def _timer() -> TimeSimulator:
    return TimeSimulator(
        [
            {
                "rule_id": "patrol_rule",
                "action_types": ["patrol"],
                "base_days": 2,
                "workload_factor": 0,
                "min_days": 2,
                "max_days": 2,
            }
        ]
    )


def _request(invocation_id="", **overrides):
    request = {
        "invocation_id": invocation_id,
        "expected_generation": 7,
        "actor_id": "player_actor",
        "control_object_id": "station",
        "target_id": "station",
        "parameters": {"note": "check perimeter"},
    }
    request.update(overrides)
    return request


def _quantity(world: WorldState, entity_id: str, resource: str) -> float:
    return next(item.quantity for item in world.get_inventory(entity_id) if item.item_name == resource)


def _value(world: WorldState, entity_id: str, resource: str) -> float:
    return next(item.value for item in world.get_entity_values(entity_id) if item.name == resource)


def test_schema_rejects_duplicate_ids_and_invalid_parameter_defaults():
    catalog = ActionCatalog()
    with pytest.raises(ActionContractError) as duplicate:
        catalog.configure([_action(), _action()])
    assert duplicate.value.code == "actions.duplicate"

    invalid = _action(parameters=[{"name": "count", "type": "integer", "default": 1.5}])
    with pytest.raises(ActionContractError) as default_error:
        ActionDefinition.parse(invalid)
    assert default_error.value.code == "action.parameter.default_type"


def test_preview_normalizes_parameters_permissions_targets_costs_and_duration(tmp_path):
    world = _world(tmp_path)
    catalog = _catalog()
    preview = catalog.preview("patrol", _request(), world_state=world, time_simulator=_timer(), sim_time=10)

    assert preview["ok"] is True
    assert preview["invocation"]["parameters"] == {"gain": 5.0, "note": "check perimeter"}
    assert preview["costs"][0] == {
        "index": 0,
        "entity_id": "station",
        "resource_name": "credits",
        "base_value": 8.0,
        "anchor_rate": 2.0,
        "quantity": 4.0,
        "available_quantity": 10.0,
        "available_base_value": 20.0,
        "reason": "patrol cost",
        "ok": True,
    }
    assert preview["duration"]["duration_days"] == 2
    assert preview["duration"]["rule_id"] == "patrol_rule"
    assert len(preview["world_commands"]) == 3  # cost, metric update, ongoing event
    assert [item["kind"] for item in preview["side_effects"]] == ["wake_agent", "emit_event"]

    with pytest.raises(ActionContractError, match="action.actor_forbidden"):
        catalog.preview("patrol", _request(actor_id="impersonator"), world_state=world, time_simulator=_timer())
    with pytest.raises(ActionContractError, match="action.control_forbidden"):
        catalog.preview("patrol", _request(control_object_id="outpost"), world_state=world, time_simulator=_timer())
    target_action = _action()
    target_action["target"]["allowed_ids"].append("remote")
    target_catalog = _catalog(target_action)
    with pytest.raises(ActionContractError, match="action.target.not_controlled"):
        target_catalog.preview("patrol", _request(target_id="remote"), world_state=world, time_simulator=_timer())


def test_parameter_validation_and_precondition_failure_are_structured(tmp_path):
    world = _world(tmp_path)
    catalog = _catalog()

    with pytest.raises(ActionContractError) as missing:
        catalog.preview("patrol", _request(parameters={}), world_state=world, time_simulator=_timer())
    assert missing.value.code == "action.parameter.invalid_value"
    assert missing.value.details["parameters"] == {"note": "required"}

    with pytest.raises(ActionContractError) as enum_error:
        catalog.preview(
            "patrol",
            _request(parameters={"gain": 7, "note": "x"}),
            world_state=world,
            time_simulator=_timer(),
        )
    assert enum_error.value.details["parameters"] == {"gain": "enum"}

    world.update_inventory("station", "credits", "credits", -7, sim_time=1)
    preview = catalog.preview("patrol", _request(), world_state=world, time_simulator=_timer())
    assert preview["ok"] is False
    assert preview["preconditions"][0]["ok"] is False
    assert preview["costs"][0]["ok"] is False


def test_execute_is_atomic_idempotent_and_runs_sidecars(tmp_path):
    world = _world(tmp_path)
    catalog = _catalog()
    wakes = []
    events = []
    request = _request("invoke-1")

    result = catalog.execute(
        "patrol",
        request,
        world_state=world,
        time_simulator=_timer(),
        sim_time=100,
        available_agent_ids={"aide", "scout"},
        wake_callback=lambda agent_id, payload, metadata: wakes.append((agent_id, payload, metadata)),
        emit_callback=events.append,
    )

    assert result["status"] == "executed"
    assert result["replayed"] is False
    assert _quantity(world, "station", "credits") == 6
    assert _value(world, "station", "alert") == 45
    assert len(world.list_ongoing_events()) == 1
    assert wakes[0][0] == "aide"
    assert wakes[0][1] == {"target": "station", "gain": 5.0}
    assert events[0].event_type == "patrol_started"

    replay = catalog.execute("patrol", request, world_state=world, time_simulator=_timer(), sim_time=100)
    assert replay["replayed"] is True
    assert _quantity(world, "station", "credits") == 6
    assert _value(world, "station", "alert") == 45

    changed = copy.deepcopy(request)
    changed["parameters"]["gain"] = 10
    with pytest.raises(ActionContractError, match="action.invocation.conflict"):
        catalog.execute("patrol", changed, world_state=world, time_simulator=_timer(), sim_time=100)


def test_world_command_failure_rolls_back_cost(tmp_path):
    world = _world(tmp_path)
    action = _action(
        result={
            "mode": "world_commands",
            "effects": [
                {
                    "effect_type": "adjust_resource",
                    "entity_id": "$control",
                    "resource_name": "badge",
                    "quantity_delta": 1,
                    "reason": "must exceed max",
                }
            ],
        },
        cooldown={"days": 0, "scope": "action"},
    )
    catalog = _catalog(action)

    with pytest.raises(ActionContractError) as failed:
        catalog.execute("patrol", _request("atomic-failure"), world_state=world, time_simulator=_timer())
    assert failed.value.code == "action.world_command_failed"
    assert _quantity(world, "station", "credits") == 10
    assert _quantity(world, "station", "badge") == 1


def test_cooldown_generation_snapshot_restore_and_reconfigure(tmp_path):
    world = _world(tmp_path)
    catalog = _catalog()
    catalog.execute("patrol", _request("cooldown-1"), world_state=world, time_simulator=_timer(), sim_time=100)

    with pytest.raises(ActionContractError) as cooldown:
        catalog.execute("patrol", _request("cooldown-2"), world_state=world, time_simulator=_timer(), sim_time=101)
    assert cooldown.value.code == "action.not_ready"
    assert cooldown.value.details["cooldown"]["remaining_days"] > 2.9

    snapshot = catalog.snapshot()
    restored = _catalog()
    restored.restore(snapshot, sim_time=101)
    restored.configure([_action()], generation=7, gameplay=_gameplay())
    with pytest.raises(ActionContractError, match="action.not_ready"):
        restored.execute("patrol", _request("cooldown-3"), world_state=world, time_simulator=_timer(), sim_time=101)

    replay = restored.execute("patrol", _request("cooldown-1"), world_state=world, time_simulator=_timer(), sim_time=101)
    assert replay["replayed"] is True

    with pytest.raises(ActionContractError, match="action.generation_conflict"):
        restored.preview("patrol", _request(expected_generation=6), world_state=world, time_simulator=_timer())


def test_concurrent_same_invocation_commits_once(tmp_path):
    world = _world(tmp_path)
    catalog = _catalog(_action(cooldown={"days": 0, "scope": "action"}))
    request = _request("parallel-once")

    def run():
        return catalog.execute("patrol", request, world_state=world, time_simulator=_timer(), sim_time=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))

    assert sorted(item["replayed"] for item in results) == [False, True]
    assert _quantity(world, "station", "credits") == 6
    assert _value(world, "station", "alert") == 45
