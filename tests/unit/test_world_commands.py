from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from multiagent_framework.fc_handlers import FCContext, handle_add_ongoing_event
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.world_commands import (
    WorldCommand,
    WorldCommandError,
    WorldCommandType,
)
from multiagent_framework.world_state import (
    Entity,
    OngoingEvent,
    ResourceDefinition,
    WorldState,
)

MONEY = "\u91d1\u94b1"
GRAIN = "\u7cae\u8349"
SATISFACTION = "\u6ee1\u610f\u5ea6"
TAX_RATE = "\u7a0e\u7387"


@pytest.fixture
def world(tmp_path):
    state = WorldState(str(tmp_path / "world.db"))
    state.upsert_entity(Entity(id="e1", type="location", name="E1"))
    return state


def command(state, command_id, command_type, payload, *, entity_id="e1", expected=None, sim_time=0.0):
    return state.command_bus.execute(WorldCommand(
        command_id=command_id,
        command_type=command_type,
        entity_id=entity_id,
        expected_version=expected,
        sim_time=sim_time,
        payload=payload,
    ))


def quantity(state, name):
    return next(item.quantity for item in state.get_inventory("e1") if item.item_name == name)


def value(state, name):
    return next(item.value for item in state.get_entity_values("e1") if item.name == name)


def test_same_command_replays_without_duplicate_balance_or_ledger(world):
    payload = {"resource_name": MONEY, "quantity": 100}
    first = command(world, "set-1", WorldCommandType.SET_RESOURCE, payload)
    second = command(world, "set-1", WorldCommandType.SET_RESOURCE, payload)
    assert first.ok and not first.replayed
    assert second.ok and second.replayed
    assert quantity(world, MONEY) == 100
    assert len(world.get_inventory_ledger(command_id="set-1")) == 1


def test_command_id_payload_conflict_is_rejected(world):
    command(world, "set-conflict", WorldCommandType.SET_RESOURCE, {
        "resource_name": MONEY, "quantity": 100,
    })
    conflict = command(world, "set-conflict", WorldCommandType.SET_RESOURCE, {
        "resource_name": MONEY, "quantity": 200,
    })
    assert not conflict.ok
    assert conflict.status == "idempotency_conflict"
    assert conflict.error_code == "idempotency_conflict"
    assert quantity(world, MONEY) == 100


def test_insufficient_balance_rolls_back_and_records_no_ledger(world):
    rejected = command(world, "spend-too-much", WorldCommandType.ADJUST_RESOURCE, {
        "resource_name": MONEY, "quantity_delta": -1,
    })
    assert not rejected.ok
    assert rejected.error_code == "insufficient_balance"
    assert quantity(world, MONEY) == 0
    assert world.get_inventory_ledger(command_id="spend-too-much") == []
    assert world.get_world_command("spend-too-much")["status"] == "rejected"


def test_entity_expected_version_conflict(world):
    applied = command(
        world, "status-1", WorldCommandType.UPDATE_STATUS,
        {"new_status": "ready"}, expected=0,
    )
    assert applied.ok and applied.result["version"] == 1
    rejected = command(
        world, "status-2", WorldCommandType.UPDATE_STATUS,
        {"new_status": "stale"}, expected=0,
    )
    assert not rejected.ok and rejected.error_code == "version_conflict"
    assert world.get_entity("e1").current_status == "ready"


def test_inventory_expected_version_conflict(world):
    command(world, "set-version", WorldCommandType.SET_RESOURCE, {
        "resource_name": MONEY, "quantity": 10,
    }, expected=0)
    rejected = command(world, "adjust-stale", WorldCommandType.ADJUST_RESOURCE, {
        "resource_name": MONEY, "quantity_delta": 1,
    }, expected=0)
    assert not rejected.ok and rejected.error_code == "version_conflict"
    assert quantity(world, MONEY) == 10


def test_metrics_and_parameters_are_not_inventory(world):
    names = {item.item_name for item in world.get_inventory("e1")}
    assert SATISFACTION not in names
    assert TAX_RATE not in names
    values = {item.name: item.kind for item in world.get_entity_values("e1")}
    assert values[SATISFACTION] == "metric"
    assert values[TAX_RATE] == "parameter"
    world.update_inventory("e1", SATISFACTION, SATISFACTION, 10)
    assert value(world, SATISFACTION) == 60
    assert SATISFACTION not in {item.item_name for item in world.get_inventory("e1")}


def test_base_value_uses_anchor_rate(world):
    world.upsert_resource_definition(ResourceDefinition(
        name="silver", unit="bar", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=2.5,
    ))
    world.set_inventory("e1", "silver", "silver", 4, command_id="silver-set")
    item = next(item for item in world.get_inventory("e1") if item.item_name == "silver")
    assert item.base_value == 10
    ledger = world.get_inventory_ledger(command_id="silver-set")[0]
    assert ledger["base_value_delta"] == 10


def test_different_assets_compare_through_same_base_value_anchor(world):
    world.upsert_resource_definition(ResourceDefinition(
        name="token-a", kind="asset", min_quantity=0, anchor_rate=2,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="token-b", kind="asset", min_quantity=0, anchor_rate=0.5,
    ))
    world.set_inventory("e1", "token-a", "token-a", 5)
    world.set_inventory("e1", "token-b", "token-b", 20)
    valuation = world.get_inventory_valuation("e1")
    by_name = {item["name"]: item["base_value"] for item in valuation["items"]}
    assert by_name["token-a"] == by_name["token-b"] == 10


def test_matured_event_failure_rolls_back_every_change_and_keeps_event(world):
    world.set_inventory("e1", MONEY, MONEY, 100, command_id="seed-money")
    event = OngoingEvent(
        event_id="evt-fail", entity_id="e1", end_time=10,
        expected_result={
            "new_status": "should-not-stick",
            "inventory_deltas": [
                {"resource": MONEY, "delta": -20},
                {"resource": GRAIN, "delta": -1},
            ],
        },
    )
    world.add_ongoing_event(event)
    result = world.apply_matured_event("evt-fail", 10)
    assert not result.ok and result.error_code == "insufficient_balance"
    assert world.get_entity("e1").current_status == ""
    assert quantity(world, MONEY) == 100
    assert quantity(world, GRAIN) == 0
    assert world.get_ongoing_event("evt-fail") is not None
    assert world.get_inventory_ledger(command_id="mature:evt-fail") == []


def test_matured_event_applies_once(world):
    world.set_inventory("e1", MONEY, MONEY, 100, command_id="seed-success")
    world.add_ongoing_event(OngoingEvent(
        event_id="evt-ok", entity_id="e1", end_time=10,
        expected_result={
            "new_status": "done",
            "inventory_deltas": [{"resource": MONEY, "delta": -20}],
        },
    ))
    first = world.apply_matured_event("evt-ok", 10)
    second = world.apply_matured_event("evt-ok", 10)
    assert first.ok and not first.replayed
    assert second.ok and second.replayed
    assert quantity(world, MONEY) == 80
    assert world.get_entity("e1").current_status == "done"
    assert world.get_ongoing_event("evt-ok") is None
    assert len(world.get_inventory_ledger(command_id="mature:evt-ok")) == 1


def test_concurrent_unique_commands_do_not_lose_updates(world):
    world.set_inventory("e1", MONEY, MONEY, 1000, command_id="seed-concurrent")

    def spend(index):
        return command(world, f"spend-{index}", WorldCommandType.ADJUST_RESOURCE, {
            "resource_name": MONEY, "quantity_delta": -1,
        })

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(spend, range(100)))
    assert all(result.ok for result in results)
    assert quantity(world, MONEY) == 900
    assert sum(len(world.get_inventory_ledger(command_id=f"spend-{i}")) for i in range(100)) == 100


def test_restore_failure_rolls_back_complete_previous_world(world):
    world.set_inventory("e1", MONEY, MONEY, 42, command_id="seed-restore")
    before = world.export_all()
    broken = copy.deepcopy(before)
    broken["inventory"].append({
        "entity_id": "missing", "category": MONEY,
        "item_name": MONEY, "quantity": 999,
    })
    with pytest.raises(ValueError, match="missing entity"):
        world.restore_all(broken)
    assert world.get_entity("e1") is not None
    assert quantity(world, MONEY) == 42

def batch_command(command_id, command_type, payload, *, entity_id="e1", expected=None):
    return WorldCommand(
        command_id=command_id,
        command_type=command_type,
        entity_id=entity_id,
        expected_version=expected,
        payload=payload,
    )


def test_atomic_batch_commits_and_replays_without_duplicate_ledger(world):
    commands = [
        batch_command(
            f"batch-add-{index}",
            WorldCommandType.ADJUST_RESOURCE,
            {"resource_name": MONEY, "quantity_delta": 1},
        )
        for index in range(50)
    ]
    first = world.command_bus.execute_batch(commands)
    second = world.command_bus.execute_batch(commands)

    assert first.ok and first.committed and first.applied_count == 50
    assert second.ok and second.committed and second.replayed_count == 50
    assert quantity(world, MONEY) == 50
    assert sum(len(world.get_inventory_ledger(command_id=item.command_id)) for item in commands) == 50


def test_atomic_batch_rejection_rolls_back_prior_mutations(world):
    result = world.command_bus.execute_batch([
        batch_command("atomic-set", WorldCommandType.SET_RESOURCE, {
            "resource_name": MONEY, "quantity": 10,
        }),
        batch_command("atomic-fail", WorldCommandType.ADJUST_RESOURCE, {
            "resource_name": MONEY, "quantity_delta": -20,
        }),
        batch_command("atomic-skipped", WorldCommandType.ADJUST_RESOURCE, {
            "resource_name": MONEY, "quantity_delta": 5,
        }),
    ])

    assert not result.ok and not result.committed and result.atomic
    assert [item.status for item in result.results] == [
        "rolled_back", "rejected", "not_executed",
    ]
    assert quantity(world, MONEY) == 0
    assert world.get_world_command("atomic-set") is None
    assert world.get_world_command("atomic-fail")["status"] == "rejected"
    assert world.get_world_command("atomic-skipped") is None
    assert world.get_inventory_ledger(command_id="atomic-set") == []


def test_non_atomic_batch_uses_savepoints_and_keeps_successful_siblings(world):
    result = world.command_bus.execute_batch([
        batch_command("partial-set", WorldCommandType.SET_RESOURCE, {
            "resource_name": MONEY, "quantity": 10,
        }),
        batch_command("partial-fail", WorldCommandType.ADJUST_RESOURCE, {
            "resource_name": MONEY, "quantity_delta": -20,
        }),
        batch_command("partial-add", WorldCommandType.ADJUST_RESOURCE, {
            "resource_name": MONEY, "quantity_delta": 5,
        }),
    ], atomic=False)

    assert not result.ok and result.committed and not result.atomic
    assert [item.status for item in result.results] == [
        "applied", "rejected", "applied",
    ]
    assert quantity(world, MONEY) == 15
    assert world.get_world_command("partial-fail")["status"] == "rejected"
    assert world.get_inventory_ledger(command_id="partial-fail") == []
    assert len(world.get_inventory_ledger(command_id="partial-set")) == 1
    assert len(world.get_inventory_ledger(command_id="partial-add")) == 1


def test_atomic_batch_payload_conflict_rolls_back_new_commands(world):
    command(world, "existing-command", WorldCommandType.SET_RESOURCE, {
        "resource_name": MONEY, "quantity": 3,
    })
    result = world.command_bus.execute_batch([
        batch_command("before-conflict", WorldCommandType.ADJUST_RESOURCE, {
            "resource_name": MONEY, "quantity_delta": 1,
        }),
        batch_command("existing-command", WorldCommandType.SET_RESOURCE, {
            "resource_name": MONEY, "quantity": 99,
        }),
    ])

    assert not result.ok and not result.committed
    assert result.results[0].status == "rolled_back"
    assert result.results[1].status == "idempotency_conflict"
    assert result.results[1].replayed
    assert quantity(world, MONEY) == 3
    assert world.get_world_command("before-conflict") is None


def test_concurrent_batches_do_not_lose_updates(world):
    world.set_inventory("e1", MONEY, MONEY, 1000, command_id="seed-batches")

    def spend_batch(batch_index):
        return world.command_bus.execute_batch([
            batch_command(
                f"batch-{batch_index}-{item_index}",
                WorldCommandType.ADJUST_RESOURCE,
                {"resource_name": MONEY, "quantity_delta": -1},
            )
            for item_index in range(25)
        ])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(spend_batch, range(8)))
    assert all(result.ok and result.committed for result in results)
    assert quantity(world, MONEY) == 800

def test_matured_event_batch_isolates_failures_and_commits_successes(world):
    world.add_ongoing_event(OngoingEvent(
        event_id="batch-event-fail", entity_id="e1", end_time=10,
        expected_result={
            "new_status": "must-not-stick",
            "inventory_deltas": [{"resource": GRAIN, "delta": -1}],
        },
    ))
    world.add_ongoing_event(OngoingEvent(
        event_id="batch-event-ok", entity_id="e1", end_time=10,
        expected_result={
            "new_status": "batch-done",
            "inventory_deltas": [{"resource": MONEY, "delta": 10}],
        },
    ))

    results = world.apply_matured_events(10)
    by_id = {result.command_id: result for result in results}

    assert not by_id["mature:batch-event-fail"].ok
    assert by_id["mature:batch-event-fail"].error_code == "insufficient_balance"
    assert by_id["mature:batch-event-ok"].ok
    assert world.get_entity("e1").current_status == "batch-done"
    assert quantity(world, MONEY) == 10
    assert quantity(world, GRAIN) == 0
    assert world.get_ongoing_event("batch-event-fail") is not None
    assert world.get_ongoing_event("batch-event-ok") is None



def test_ongoing_event_duration_is_authoritatively_estimated(world):
    simulator = TimeSimulator([{
        "rule_id": "construction", "action_types": ["construction"],
        "base_days": 2, "workload_factor": 0, "min_days": 1, "max_days": 10,
    }])
    ctx = FCContext(
        world_state=world, caller_id="official", sim_time=100,
        seconds_per_day=86400,
        duration_estimator=lambda **kwargs: simulator.estimate(
            action_type=kwargs["action_type"], proposed_days=kwargs["proposed_days"],
        ),
        changes_made=[],
    )
    result = handle_add_ongoing_event(ctx, {
        "entity_id": "e1", "event_type": "construction",
        "description": "build", "duration_days": 999,
        "expected_result": {},
    })
    assert result["ok"] is True
    event = world.get_ongoing_event(result["event_id"])
    assert event.end_time == 100 + 2 * 86400
    assert ctx.changes_made[0]["duration_rule_id"] == "construction"
