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


def test_rhythm_api_returns_stable_uninitialized_shape():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            response = client.get("/api/rhythm")
        assert response.status_code == 200
        assert response.json() == {
            "configured": False,
            "enabled": False,
            "generation": 0,
            "attention_available": 0.0,
            "attention_used": 0.0,
            "active_issue_count": 0,
            "queued_issue_count": 0,
            "rules": [],
            "recent_feedback": [],
            "recent_suppressed_events": [],
        }
    finally:
        state.orchestrator = previous


def test_rhythm_api_forwards_safe_runtime_projection():
    payload = {
        "configured": True,
        "enabled": True,
        "generation": 9,
        "attention_available": 3.5,
        "attention_used": 4.5,
        "active_issue_count": 2,
        "queued_issue_count": 1,
        "rules": [{"rule_id": "frontier_pressure"}],
        "recent_feedback": [{"kind": "escalation"}],
        "recent_suppressed_events": [{"event_type": "minor_noise"}],
    }
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(rhythm_public_state=lambda: payload)
    try:
        with _client() as client:
            response = client.get("/api/rhythm")
        assert response.status_code == 200
        assert response.json() == payload
    finally:
        state.orchestrator = previous
