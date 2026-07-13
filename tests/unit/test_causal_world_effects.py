from __future__ import annotations

import copy

import pytest

from multiagent_framework.causal_events import CausalEventEngine, CausalEventError
from multiagent_framework.causal_world_effects import execute_causal_world_commands
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.types import EventObject
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState


def make_world(tmp_path, *, grain=100.0):
    world = WorldState(str(tmp_path / "causal-world.db"))
    world.upsert_entity(Entity(id="warehouse", type="location", name="Warehouse"))
    world.upsert_entity(Entity(id="treasury", type="location", name="Treasury"))
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", kind="asset", default_quantity=0, min_quantity=0,
        anchor_rate=2.0,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="coin", kind="asset", default_quantity=0, min_quantity=0,
        anchor_rate=0.5,
    ))
    world.set_inventory("warehouse", "grain", "grain", grain, command_id="seed-grain")
    world.set_inventory("treasury", "coin", "coin", 0, command_id="seed-coin")
    return world


def source():
    return EventObject(
        event_id="source-1", event_type="shortage", severity=5,
        target_agents=["governor"], initial_payload={}, generated_at=0,
    )


def engine_with_effects(effects):
    return CausalEventEngine([{
        "rule_id": "shortage_response",
        "source_event_types": ["shortage"],
        "child_event_type": "relief",
        "delay_days": 2,
        "probability": 1,
        "world_effects": effects,
    }])


def inventory_quantity(world, entity_id, resource):
    return next(item.quantity for item in world.get_inventory(entity_id) if item.item_name == resource)


def test_materializes_and_executes_anchored_transfer_with_conserved_value(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "transfer_anchored_value",
        "from_entity_id": "warehouse", "from_resource": "grain",
        "to_entity_id": "treasury", "to_resource": "coin",
        "base_value": 20, "reason": "relief",
    }])

    assert engine.observe(source(), sim_time=10, world_state=world) == 1
    event = engine.poll(10 + 2 * GAME_SECONDS_PER_DAY).events[0]
    assert [item["payload"]["quantity_delta"] for item in event.world_commands] == [-10, 40]

    result = execute_causal_world_commands(world, event, sim_time=event.generated_at)
    assert result is not None and result.ok and result.applied_count == 2
    assert inventory_quantity(world, "warehouse", "grain") == 90
    assert inventory_quantity(world, "treasury", "coin") == 40

    ledger = [
        *world.get_inventory_ledger(command_id=f"causal:{event.event_id}:0"),
        *world.get_inventory_ledger(command_id=f"causal:{event.event_id}:1"),
    ]
    assert sum(item["base_value_delta"] for item in ledger) == pytest.approx(0)
    assert {item["metadata"]["transfer_side"] for item in ledger} == {"debit", "credit"}
    assert {item["metadata"]["anchored_base_value"] for item in ledger} == {20}
    transfer_ids = {item["metadata"]["transfer_id"] for item in ledger}
    assert len(transfer_ids) == 1
    assert next(iter(transfer_ids)).startswith("transfer_")


def test_causal_batch_replay_is_idempotent(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "adjust_resource", "entity_id": "warehouse",
        "resource_name": "grain", "quantity_delta": -5,
    }])
    engine.observe(source(), sim_time=0, world_state=world)
    event = engine.poll(2 * GAME_SECONDS_PER_DAY).events[0]

    first = execute_causal_world_commands(world, event, sim_time=event.generated_at)
    second = execute_causal_world_commands(world, event, sim_time=event.generated_at)
    assert first is not None and first.ok and first.applied_count == 1
    assert second is not None and second.ok and second.replayed_count == 1
    assert inventory_quantity(world, "warehouse", "grain") == 95


def test_atomic_rejection_rolls_back_sibling_effects(tmp_path):
    world = make_world(tmp_path, grain=5)
    engine = engine_with_effects([
        {"effect_type": "update_status", "entity_id": "warehouse", "new_status": "dispatching"},
        {
            "effect_type": "transfer_anchored_value",
            "from_entity_id": "warehouse", "from_resource": "grain",
            "to_entity_id": "treasury", "to_resource": "coin", "base_value": 20,
        },
    ])
    engine.observe(source(), sim_time=0, world_state=world)
    event = engine.poll(2 * GAME_SECONDS_PER_DAY).events[0]

    result = execute_causal_world_commands(world, event, sim_time=event.generated_at)
    assert result is not None and not result.ok and not result.committed
    assert result.results[0].error_code == "batch_rolled_back"
    assert result.results[1].error_code == "insufficient_balance"
    assert result.results[2].error_code == "batch_aborted"
    assert world.get_entity("warehouse").current_status == ""
    assert inventory_quantity(world, "warehouse", "grain") == 5
    assert inventory_quantity(world, "treasury", "coin") == 0


def test_ongoing_event_plan_freezes_times_and_stable_id(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "create_ongoing_event", "entity_id": "warehouse",
        "event_type": "construction", "description": "Build granary",
        "duration_days": 3,
        "expected_result": {"new_status": "expanded", "inventory_deltas": [{"resource": "grain", "delta": 5}]},
    }])
    engine.observe(source(), sim_time=100, world_state=world)
    snapshot = engine.snapshot()
    event = engine.poll(100 + 2 * GAME_SECONDS_PER_DAY).events[0]
    command = event.world_commands[0]
    payload = command["payload"]
    assert payload["start_time"] == event.generated_at
    assert payload["end_time"] == event.generated_at + 3 * GAME_SECONDS_PER_DAY

    restored = engine_with_effects([])
    restored.restore(copy.deepcopy(snapshot), sim_time=0)
    restored_event = restored.poll(event.generated_at).events[0]
    assert restored_event.world_commands == event.world_commands

    result = execute_causal_world_commands(world, event, sim_time=event.generated_at)
    assert result is not None and result.ok
    ongoing = world.get_ongoing_event(payload["event_id"])
    assert ongoing is not None
    assert ongoing.end_time == payload["end_time"]
    assert ongoing.expected_result["new_status"] == "expanded"


def test_snapshot_rejects_malformed_materialized_command(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "adjust_resource", "entity_id": "warehouse",
        "resource_name": "grain", "quantity_delta": -5,
    }])
    engine.observe(source(), sim_time=0, world_state=world)
    snapshot = engine.snapshot()
    snapshot["pending"][0]["event"]["world_commands"][0]["command_type"] = "delete_database"

    with pytest.raises(CausalEventError, match="causal_snapshot_invalid"):
        engine.restore(snapshot, sim_time=0)


def test_transfer_quantities_remain_frozen_after_anchor_change(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "transfer_anchored_value",
        "from_entity_id": "warehouse", "from_resource": "grain",
        "to_entity_id": "treasury", "to_resource": "coin", "base_value": 20,
    }])
    engine.observe(source(), sim_time=0, world_state=world)
    event = engine.poll(2 * GAME_SECONDS_PER_DAY).events[0]
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", kind="asset", default_quantity=0, min_quantity=0, anchor_rate=4.0,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="coin", kind="asset", default_quantity=0, min_quantity=0, anchor_rate=1.0,
    ))

    assert event.world_commands[0]["payload"]["quantity_delta"] == -10
    assert event.world_commands[1]["payload"]["quantity_delta"] == 40


def test_legacy_transfer_snapshot_without_transfer_id_restores_and_reconciles(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "transfer_anchored_value",
        "from_entity_id": "warehouse", "from_resource": "grain",
        "to_entity_id": "treasury", "to_resource": "coin", "base_value": 20,
    }])
    assert engine.observe(source(), sim_time=0, world_state=world) == 1
    snapshot = engine.snapshot()
    commands = snapshot["pending"][0]["event"]["world_commands"]
    for command in commands:
        command["payload"].pop("transfer_id")

    restored = CausalEventEngine()
    restored.restore(snapshot, sim_time=0)
    event = restored.poll(2 * GAME_SECONDS_PER_DAY).events[0]
    result = execute_causal_world_commands(world, event, sim_time=event.generated_at)

    assert result is not None and result.ok
    invariants = world.get_economic_invariants()
    assert invariants["transfers"] == {
        "batch_count": 1,
        "entry_count": 2,
        "debit_base_value": 20.0,
        "credit_base_value": 20.0,
        "net_base_value": 0.0,
        "mismatch_batch_count": 0,
        "incomplete_batch_count": 0,
        "max_abs_mismatch": 0.0,
        "conserved": True,
    }
    ledger = world.get_inventory_ledger()
    chain_id = event.initial_payload["causal_chain_id"]
    ledger = [row for row in ledger if row["chain_id"] == chain_id]
    assert all("transfer_id" not in item["metadata"] for item in ledger if item["metadata"].get("transfer_side"))


def test_snapshot_rejects_overlong_transfer_id(tmp_path):
    world = make_world(tmp_path)
    engine = engine_with_effects([{
        "effect_type": "transfer_anchored_value",
        "from_entity_id": "warehouse", "from_resource": "grain",
        "to_entity_id": "treasury", "to_resource": "coin", "base_value": 20,
    }])
    assert engine.observe(source(), sim_time=0, world_state=world) == 1
    snapshot = engine.snapshot()
    snapshot["pending"][0]["event"]["world_commands"][0]["payload"]["transfer_id"] = "x" * 65

    with pytest.raises(CausalEventError, match="causal_snapshot_invalid"):
        CausalEventEngine().restore(snapshot, sim_time=0)
