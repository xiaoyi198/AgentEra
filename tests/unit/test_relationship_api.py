from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from multiagent_framework.relationship_contract import RelationshipContractError
from multiagent_framework.world_state import Entity, WorldState
from routers import _state as state
from routers.worldstate import router


def trust_type():
    return {
        "relationship_type_id": "trust",
        "display_name": "信任",
        "direction": "undirected",
        "minimum_strength": -100,
        "maximum_strength": 100,
        "default_strength": 0,
        "default_visibility": "private",
        "allow_expiry": True,
        "dimensions": [{
            "dimension_id": "personal", "display_name": "个人信任",
            "minimum": -100, "maximum": 100, "default": 0,
        }],
    }


def make_client(tmp_path: Path):
    world = WorldState(str(tmp_path / "relationships.db"))
    world.bulk_load([Entity("alpha", "person", "?"), Entity("beta", "person", "?")], [])
    world.configure_relationship_catalog([trust_type()], generation=3)
    orchestrator = SimpleNamespace(
        world_state=world, sim_time=12.0, dlc_loader=SimpleNamespace(generation=3),
    )
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    return TestClient(app), world, previous


def test_relationship_api_lists_types_and_round_trips_custom_edge(tmp_path: Path):
    client, world, previous = make_client(tmp_path)
    try:
        with client:
            types = client.get("/api/relationship-types")
            created = client.post("/api/relations", json={
                "parent_id": "beta",
                "child_id": "alpha",
                "rel_type": "trust",
                "strength": 65,
                "visibility": "limited",
                "starts_at": 10,
                "expires_at": 20,
                "values": {"personal": 70},
                "metadata": {"source": "test"},
            })
            graph = client.get("/api/relations")
            detail = client.get("/api/world-state/beta/relations")
        assert types.status_code == 200
        assert types.json()["generation"] == 3
        assert {item["relationship_type_id"] for item in types.json()["relationship_types"]} >= {"subordinate", "affiliated", "trust"}
        assert created.status_code == 200
        relation = created.json()["relation"]
        assert (relation["parent_id"], relation["child_id"]) == ("alpha", "beta")
        assert relation["active"] is True
        assert graph.json()["relations"][0]["strength"] == 65
        assert detail.json()["neighbors"][0]["neighbor_id"] == "alpha"
        assert world.get_all_relations()[0]["metadata"] == {"source": "test"}
    finally:
        state.orchestrator = previous


def test_relationship_api_rejects_unknown_and_out_of_range_values(tmp_path: Path):
    client, _world, previous = make_client(tmp_path)
    try:
        with client:
            unknown = client.post("/api/relations", json={
                "parent_id": "alpha", "child_id": "beta", "rel_type": "missing",
            })
            invalid = client.post("/api/relations", json={
                "parent_id": "alpha", "child_id": "beta", "rel_type": "trust", "strength": 101,
            })
        assert unknown.status_code == 422
        assert unknown.json()["detail"]["code"] == "relation.unknown_type"
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "relation.strength_out_of_range"
    finally:
        state.orchestrator = previous


def test_world_state_migrates_legacy_relation_table_and_persists_catalog(tmp_path: Path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE entity_relations(parent_id TEXT, child_id TEXT, rel_type TEXT, note TEXT, PRIMARY KEY(parent_id,child_id,rel_type))")
    conn.executemany(
        "INSERT INTO entity_relations(parent_id,child_id,rel_type,note) VALUES (?,?,?,?)",
        [("a", "b", "subordinate", ""), ("a", "c", "affiliated", "")],
    )
    conn.commit()
    conn.close()

    world = WorldState(str(db))
    columns = {row[1] for row in sqlite3.connect(db).execute("PRAGMA table_info(entity_relations)")}
    assert {"strength", "visibility", "starts_at", "expires_at", "dimension_values", "metadata"} <= columns
    migrated = {item["rel_type"]: item for item in world.get_all_relations()}
    assert migrated["subordinate"]["strength"] == 100
    assert migrated["affiliated"]["strength"] == 50

    subordinate_override = {
        "relationship_type_id": "subordinate",
        "display_name": "Command",
        "minimum_strength": 0,
        "maximum_strength": 10,
        "default_strength": 7,
    }
    world.configure_relationship_catalog([trust_type(), subordinate_override])
    restarted = WorldState(str(db))
    assert "trust" in restarted.relationship_catalog.ids
    assert restarted.relationship_catalog.get("subordinate").display_name == "Command"
    assert {
        item["relationship_type_id"]
        for item in restarted.relationship_catalog.definitions(include_legacy=False)
    } == {"trust", "subordinate"}


def test_failed_schema_update_does_not_publish_candidate_catalog(tmp_path: Path):
    world = WorldState(str(tmp_path / "atomic.db"))
    world.bulk_load([Entity("alpha", "person", "?"), Entity("beta", "person", "?")], [])
    with pytest.raises(RelationshipContractError):
        world.apply_dlc_schema_update(
            [], [], [], relationship_definitions=[{
                "relationship_type_id": "broken",
                "display_name": "错误类型",
                "minimum_strength": 10,
                "maximum_strength": 0,
            }],
        )
    assert "broken" not in world.relationship_catalog.ids
