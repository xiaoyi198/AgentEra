from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import _state as state
from routers.simulation import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_objective_api_returns_explicit_sandbox_without_orchestrator():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            objectives = client.get("/api/objectives")
            endings = client.get("/api/endings")
        assert objectives.status_code == 200
        assert objectives.json() == {
            "configured": False,
            "sandbox": True,
            "generation": 0,
            "status": "sandbox",
            "objectives": [],
            "endings": [],
            "current_ending": None,
            "recent_transitions": [],
        }
        assert endings.status_code == 200
        assert endings.json() == {
            "generation": 0,
            "status": "sandbox",
            "sandbox": True,
            "endings": [],
            "current_ending": None,
        }
    finally:
        state.orchestrator = previous


def test_objective_api_forwards_authoritative_state_and_projects_endings():
    calls = []
    objective_state = {
        "configured": True,
        "sandbox": False,
        "generation": 8,
        "status": "ended",
        "objectives": [{"objective_id": "secure", "status": "completed"}],
        "endings": [{"ending_id": "safe", "reached": True}],
        "current_ending": {"ending_id": "safe", "outcome": "victory"},
        "recent_transitions": [{"type": "ending_reached", "ending_id": "safe"}],
    }
    orchestrator = SimpleNamespace(
        objectives_public_state=lambda: calls.append("state") or objective_state,
    )
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with _client() as client:
            objectives = client.get("/api/objectives")
            endings = client.get("/api/endings")
        assert objectives.json() == objective_state
        assert endings.json() == {
            "generation": 8,
            "status": "ended",
            "sandbox": False,
            "endings": objective_state["endings"],
            "current_ending": objective_state["current_ending"],
        }
        assert calls == ["state", "state"]
    finally:
        state.orchestrator = previous
