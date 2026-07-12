from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.world_state import Entity, WorldState
from routers import _state as state
from routers.worldstate import router


def _client(tmp_path: Path, *, simulator: TimeSimulator | None = None):
    world = WorldState(str(tmp_path / "world.db"))
    world.upsert_entity(Entity(id="city", type="location", name="City"))
    orchestrator = SimpleNamespace(
        world_state=world,
        sim_time=86400.0,
        time_simulator=simulator or TimeSimulator(),
    )
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    return TestClient(app), world, previous


def test_create_ongoing_event_estimate_uses_active_time_rule(tmp_path: Path):
    simulator = TimeSimulator([{
        "rule_id": "relief.rule",
        "action_types": ["relief"],
        "base_days": 2,
        "workload_factor": 2,
        "min_days": 1,
        "max_days": 30,
    }])
    client, world, previous = _client(tmp_path, simulator=simulator)
    try:
        with client:
            response = client.post("/api/world-state/city/events", json={
                "event_type": "relief",
                "duration_days": 3,
                "duration_mode": "estimate",
                "description": "public-safe-description",
            })
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "status": "ok",
            "event_id": body["event_id"],
            "duration_days": 8.0,
            "source": "deterministic",
            "rule_id": "relief.rule",
        }
        event = world.get_ongoing_event(body["event_id"])
        assert event is not None
        assert event.end_time == 86400.0 + 8.0 * 86400.0
    finally:
        state.orchestrator = previous


def test_create_ongoing_event_override_preserves_trusted_duration(tmp_path: Path):
    simulator = TimeSimulator([{
        "rule_id": "would.otherwise.apply",
        "action_types": ["*"],
        "base_days": 20,
        "workload_factor": 0,
    }])
    client, world, previous = _client(tmp_path, simulator=simulator)
    try:
        with client:
            response = client.post("/api/world-state/city/events", json={
                "event_type": "relief",
                "duration_days": 4.25,
                "duration_mode": "override",
            })
        body = response.json()
        assert body["duration_days"] == 4.25
        assert body["source"] == "manual_override"
        assert body["rule_id"] == "manual.override"
        event = world.get_ongoing_event(body["event_id"])
        assert event is not None
        assert event.end_time == 86400.0 + 4.25 * 86400.0
    finally:
        state.orchestrator = previous


def test_patch_ongoing_event_returns_authoritative_duration_metadata(tmp_path: Path):
    simulator = TimeSimulator([{
        "rule_id": "construction.rule",
        "action_types": ["construction"],
        "base_days": 5,
        "workload_factor": 1,
        "min_days": 1,
        "max_days": 30,
    }])
    client, world, previous = _client(tmp_path, simulator=simulator)
    try:
        with client:
            created = client.post("/api/world-state/city/events", json={
                "event_type": "construction",
                "duration_days": 1,
                "duration_mode": "override",
            }).json()
            response = client.patch(
                f"/api/world-state/events/{created['event_id']}",
                json={"duration_days": 2, "duration_mode": "estimate"},
            )
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "source": "deterministic",
            "rule_id": "construction.rule",
            "duration_days": 7.0,
        }
        event = world.get_ongoing_event(created["event_id"])
        assert event is not None
        assert event.end_time == 86400.0 + 7.0 * 86400.0
    finally:
        state.orchestrator = previous


def test_ongoing_event_api_rejects_negative_nan_and_infinity(tmp_path: Path):
    client, _world, previous = _client(tmp_path)
    try:
        with client:
            negative = client.post(
                "/api/world-state/city/events",
                json={"duration_days": -1, "duration_mode": "override"},
            )
            nan = client.post(
                "/api/world-state/city/events",
                content='{"duration_days": NaN, "duration_mode": "override"}',
                headers={"Content-Type": "application/json"},
            )
            infinity = client.post(
                "/api/world-state/city/events",
                content='{"duration_days": Infinity, "duration_mode": "estimate"}',
                headers={"Content-Type": "application/json"},
            )
        for response in (negative, nan, infinity):
            assert response.status_code in {200, 422}
            if response.status_code == 200:
                assert "finite non-negative" in response.json()["error"]
    finally:
        state.orchestrator = previous
