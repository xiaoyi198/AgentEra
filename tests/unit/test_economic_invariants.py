from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.world_commands import WorldCommand, WorldCommandType
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState
from routers import _state as router_state
from routers.worldstate import router as worldstate_router


def make_world(tmp_path) -> WorldState:
    world = WorldState(str(tmp_path / "economy.db"))
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", unit="sack", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=2.0,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="coin", unit="piece", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=0.5,
    ))
    world.upsert_entity(Entity(id="warehouse", type="place", name="Warehouse"))
    world.upsert_entity(Entity(id="treasury", type="place", name="Treasury"))
    world.set_inventory(
        "warehouse", "grain", "grain", 100, "sack",
        command_id="seed-grain", reason="seed",
    )
    world.set_inventory(
        "treasury", "coin", "coin", 0, "piece",
        command_id="seed-coin", reason="seed",
    )
    return world


def execute_transfer(world: WorldState) -> None:
    result = world.command_bus.execute_batch([
        WorldCommand(
            command_type=WorldCommandType.ADJUST_RESOURCE,
            command_id="transfer-1-debit", entity_id="warehouse",
            payload={"resource_name": "grain", "quantity_delta": -10},
            metadata={
                "source": "test", "transfer_id": "transfer-1",
                "transfer_side": "debit", "anchored_base_value": 20,
            },
        ),
        WorldCommand(
            command_type=WorldCommandType.ADJUST_RESOURCE,
            command_id="transfer-1-credit", entity_id="treasury",
            payload={"resource_name": "coin", "quantity_delta": 40},
            metadata={
                "source": "test", "transfer_id": "transfer-1",
                "transfer_side": "credit", "anchored_base_value": 20,
            },
        ),
    ], atomic=True)
    assert result.ok and result.committed


def test_economic_snapshot_reconciles_issuance_destruction_and_transfers(tmp_path):
    world = make_world(tmp_path)
    execute_transfer(world)
    world.update_inventory(
        "treasury", "coin", "coin", -10, "tax burn",
        command_id="destroy-coin",
    )

    snapshot = world.get_economic_invariants()

    assert snapshot["schema_version"] == "1.0"
    assert snapshot["inventory"]["total_base_value"] == 195
    assert snapshot["inventory"]["holder_count"] == 2
    resources = {
        item["resource_name"]: item
        for item in snapshot["inventory"]["by_resource"]
    }
    assert resources["grain"]["quantity"] == 90
    assert resources["grain"]["base_value"] == 180
    assert resources["grain"]["holder_count"] == 1
    assert resources["coin"]["quantity"] == 30
    assert resources["coin"]["base_value"] == 15
    assert resources["coin"]["holder_count"] == 1
    assert snapshot["ledger"] == {
        "entries_total": 5,
        "issued_base_value": 200,
        "destroyed_base_value": 5,
        "net_non_transfer_base_value": 195,
    }
    assert snapshot["transfers"] == {
        "batch_count": 1,
        "entry_count": 2,
        "debit_base_value": 20,
        "credit_base_value": 20,
        "net_base_value": 0,
        "mismatch_batch_count": 0,
        "incomplete_batch_count": 0,
        "max_abs_mismatch": 0,
        "conserved": True,
    }


def test_economic_snapshot_detects_incomplete_transfer_without_exposing_ids(tmp_path):
    world = make_world(tmp_path)
    result = world.command_bus.execute(WorldCommand(
        command_type=WorldCommandType.ADJUST_RESOURCE,
        command_id="broken-credit", entity_id="treasury",
        payload={"resource_name": "coin", "quantity_delta": 4},
        metadata={
            "transfer_id": "private-transfer-id",
            "transfer_side": "credit",
        },
    ))
    assert result.ok

    snapshot = world.get_economic_invariants()

    assert snapshot["transfers"]["batch_count"] == 1
    assert snapshot["transfers"]["incomplete_batch_count"] == 1
    assert snapshot["transfers"]["mismatch_batch_count"] == 1
    assert snapshot["transfers"]["max_abs_mismatch"] == 2
    assert snapshot["transfers"]["conserved"] is False
    assert "private-transfer-id" not in str(snapshot)
    assert "broken-credit" not in str(snapshot)


def test_world_economy_api_is_static_secret_free_and_includes_causal_counters(tmp_path):
    world = make_world(tmp_path)
    execute_transfer(world)
    app = FastAPI()
    app.include_router(worldstate_router)
    previous = router_state.orchestrator
    router_state.orchestrator = SimpleNamespace(
        world_state=world,
        sim_time=86400.0,
        operational_metrics_snapshot=lambda: {
            "causal_effect_batches": 5,
            "causal_effect_commands": 8,
            "causal_effect_rejections": 1,
            "causal_effect_failures": 1,
            "prompt": "private prompt",
        },
    )
    try:
        response = TestClient(app).get("/api/world-state/economy")
    finally:
        router_state.orchestrator = previous

    assert response.status_code == 200
    body = response.json()
    assert body["sim_time"] == 86400
    assert body["inventory"]["total_base_value"] == 200
    assert body["causal_effects"] == {
        "batches": 5,
        "commands": 8,
        "rejections": 1,
        "failures": 1,
        "rejection_rate": 0.4,
    }
    serialized = response.text.lower()
    assert "private prompt" not in serialized
    assert "entity_id" not in serialized
    assert "command_id" not in serialized
