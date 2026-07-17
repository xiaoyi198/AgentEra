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


def test_timeline_api_returns_stable_uninitialized_shape():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            response = client.get("/api/timeline")
        assert response.status_code == 200
        assert response.json() == {
            "pending_count": 0, "capacity": 0, "utilization": 0.0,
            "oldest_occurrence_time": None, "oldest_age_seconds": 0.0,
            "archive_count": 0, "archive_capacity": 0,
            "backpressure_count": 0, "restore_failure_count": 0,
            "last_restore_error": "",
            "last_snapshot": {
                "entry_count": 0, "build_ms": 0.0,
                "serialization_ms": 0.0, "serialized_bytes": 0,
            },
        }
    finally:
        state.orchestrator = previous


def test_timeline_api_forwards_safe_runtime_projection():
    payload = {
        "pending_count": 7, "capacity": 100, "utilization": 0.07,
        "oldest_occurrence_time": 10.0, "oldest_age_seconds": 25.0,
        "archive_count": 2, "archive_capacity": 64,
        "backpressure_count": 3, "restore_failure_count": 1,
        "last_restore_error": "deferred_timeline.snapshot_invalid",
        "last_snapshot": {
            "entry_count": 7, "build_ms": 1.5,
            "serialization_ms": 2.5, "serialized_bytes": 4096,
        },
    }
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(deferred_timeline_status=lambda: payload)
    try:
        with _client() as client:
            response = client.get("/api/timeline")
        assert response.status_code == 200
        assert response.json() == payload
    finally:
        state.orchestrator = previous
