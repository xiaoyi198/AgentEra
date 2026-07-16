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


def test_outcome_feedback_api_returns_stable_uninitialized_shape():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            response = client.get("/api/outcome-feedback")
        assert response.status_code == 200
        assert response.json() == {
            "configured": False, "enabled": False, "generation": 0,
            "rules": [], "recent": [], "pending": [],
            "recent_count": 0, "pending_count": 0,
        }
    finally:
        state.orchestrator = previous


def test_outcome_feedback_api_forwards_only_runtime_projection():
    payload = {
        "configured": True, "enabled": True, "generation": 5,
        "rules": [{"rule_id": "follow_up", "delivery": "delayed"}],
        "recent": [{"feedback_id": "feedback_safe", "kind": "immediate"}],
        "pending": [{"feedback_id": "feedback_pending", "due_at": 100}],
        "recent_count": 1, "pending_count": 1,
    }
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(outcome_feedback_public_state=lambda: payload)
    try:
        with _client() as client:
            response = client.get("/api/outcome-feedback")
        assert response.status_code == 200
        assert response.json() == payload
    finally:
        state.orchestrator = previous
