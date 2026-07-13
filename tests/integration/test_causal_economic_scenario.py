from __future__ import annotations

import pytest

from multiagent_framework.causal_events import CausalEventEngine
from multiagent_framework.causal_world_effects import execute_causal_world_commands
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.types import EventObject
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState


RULES = [
    {
        "rule_id": "harvest_distribution",
        "source_event_types": ["harvest"],
        "child_event_type": "distribution",
        "delay_days": 1,
        "probability": 1,
        "world_effects": [
            {
                "effect_type": "transfer_anchored_value",
                "from_entity_id": "warehouse",
                "from_resource": "grain",
                "to_entity_id": "treasury",
                "to_resource": "coin",
                "base_value": 20,
                "reason": "harvest distribution",
            },
            {
                "effect_type": "create_ongoing_event",
                "entity_id": "warehouse",
                "event_type": "settlement",
                "description": "settlement completes",
                "duration_days": 2,
                "expected_result": {
                    "new_status": "settled",
                    "inventory_deltas": [
                        {"resource": "grain", "delta": 3, "reason": "settlement yield"},
                    ],
                },
            },
        ],
    },
    {
        "rule_id": "distribution_tax",
        "source_event_types": ["distribution"],
        "child_event_type": "tax",
        "delay_days": 0,
        "probability": 1,
        "conditions": [
            {
                "kind": "anchored_inventory",
                "entity_id": "treasury",
                "name": "coin",
                "operator": "gte",
                "value": 20,
            },
        ],
        "world_effects": [
            {
                "effect_type": "adjust_resource",
                "entity_id": "treasury",
                "resource_name": "coin",
                "quantity_delta": -10,
                "reason": "tax administration cost",
            },
        ],
    },
    {
        "rule_id": "tax_replenishment",
        "source_event_types": ["tax"],
        "child_event_type": "replenishment",
        "delay_days": 1,
        "probability": 1,
        "world_effects": [
            {
                "effect_type": "adjust_resource",
                "entity_id": "warehouse",
                "resource_name": "grain",
                "quantity_delta": 5,
                "reason": "replenishment",
            },
        ],
    },
]


def _quantity(world: WorldState, entity_id: str, resource_name: str) -> float:
    return next(
        item.quantity
        for item in world.get_inventory(entity_id)
        if item.item_name == resource_name
    )


def _execute_due(engine: CausalEventEngine, world: WorldState, sim_time: float):
    events = engine.poll(sim_time).events
    assert len(events) == 1
    event = events[0]
    result = execute_causal_world_commands(world, event, sim_time=sim_time)
    assert result is not None and result.ok
    return event, result


def test_causal_economy_chain_survives_restart_without_value_or_ledger_drift(tmp_path):
    db_path = tmp_path / "causal-economy.db"
    world = WorldState(str(db_path))
    world.upsert_entity(Entity(id="warehouse", type="location", name="Warehouse"))
    world.upsert_entity(Entity(id="treasury", type="location", name="Treasury"))
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", kind="asset", default_quantity=0, min_quantity=0, anchor_rate=2.0,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="coin", kind="asset", default_quantity=0, min_quantity=0, anchor_rate=0.5,
    ))
    world.set_inventory("warehouse", "grain", "grain", 100, command_id="seed-grain")
    world.set_inventory("treasury", "coin", "coin", 0, command_id="seed-coin")

    engine = CausalEventEngine(RULES)
    source = EventObject(
        event_id="harvest-1",
        event_type="harvest",
        severity=4,
        target_agents=["steward"],
        initial_payload={},
        generated_at=0,
    )
    assert engine.observe(source, sim_time=0, world_state=world) == 1

    day_one = GAME_SECONDS_PER_DAY
    distribution, distribution_result = _execute_due(engine, world, day_one)
    assert distribution.event_type == "distribution"
    assert distribution_result.applied_count == 3
    assert _quantity(world, "warehouse", "grain") == pytest.approx(90)
    assert _quantity(world, "treasury", "coin") == pytest.approx(40)
    assert len(world.list_ongoing_events("warehouse")) == 1

    # The zero-delay tax proves the downstream condition sees the preceding
    # transfer's committed anchored value, not the pre-chain world state.
    assert engine.observe(distribution, sim_time=day_one, world_state=world) == 1
    tax, _ = _execute_due(engine, world, day_one)
    assert tax.event_type == "tax"
    assert _quantity(world, "treasury", "coin") == pytest.approx(30)
    assert engine.observe(tax, sim_time=day_one, world_state=world) == 1

    engine_snapshot = engine.snapshot()
    ledger_before_restart = world.get_inventory_ledger()
    assert len(engine_snapshot["pending"]) == 1
    del world

    restarted_world = WorldState(str(db_path))
    restarted_engine = CausalEventEngine(RULES)
    restarted_engine.restore(engine_snapshot, sim_time=day_one)

    day_two = 2 * GAME_SECONDS_PER_DAY
    replenishment, replenishment_result = _execute_due(
        restarted_engine, restarted_world, day_two,
    )
    assert replenishment.event_type == "replenishment"
    assert replenishment_result.applied_count == 1
    assert _quantity(restarted_world, "warehouse", "grain") == pytest.approx(95)

    # Replaying the same materialized child after restart cannot duplicate its ledger row.
    replay = execute_causal_world_commands(restarted_world, replenishment, sim_time=day_two)
    assert replay is not None and replay.ok and replay.replayed_count == 1

    day_three = 3 * GAME_SECONDS_PER_DAY
    matured = restarted_world.apply_matured_events(day_three)
    assert len(matured) == 1 and matured[0].ok
    assert restarted_world.get_entity("warehouse").current_status == "settled"
    assert _quantity(restarted_world, "warehouse", "grain") == pytest.approx(98)
    assert restarted_world.list_ongoing_events("warehouse") == []

    invariants = restarted_world.get_economic_invariants()
    assert invariants["inventory"]["total_base_value"] == pytest.approx(211)
    assert invariants["ledger"]["issued_base_value"] == pytest.approx(216)
    assert invariants["ledger"]["destroyed_base_value"] == pytest.approx(5)
    assert invariants["ledger"]["net_non_transfer_base_value"] == pytest.approx(211)
    assert invariants["transfers"]["batch_count"] == 1
    assert invariants["transfers"]["entry_count"] == 2
    assert invariants["transfers"]["net_base_value"] == pytest.approx(0)
    assert invariants["transfers"]["conserved"] is True

    ledger_after_restart = restarted_world.get_inventory_ledger()
    assert len(ledger_after_restart) == len(ledger_before_restart) + 2
    assert len({row["entry_id"] for row in ledger_after_restart}) == len(ledger_after_restart)
    assert len(restarted_world.get_inventory_ledger(
        command_id=f"causal:{replenishment.event_id}:0",
    )) == 1
    assert len(restarted_world.get_inventory_ledger(
        command_id=f"mature:{matured[0].result['event_id']}",
    )) == 1
