from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import _state as state
from routers.simulation import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_derived_metric_list_has_safe_empty_fallback():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            response = client.get("/api/derived-metrics")
            single = client.get("/api/derived-metrics/secret")
        assert response.status_code == 200
        assert response.json() == {
            "configured": False, "generation": 0, "evaluated_at": 0.0,
            "metric_count": 0, "metrics": [],
        }
        assert single.status_code == 503
        assert single.json()["detail"]["code"] == "derived_metric.orchestrator_unavailable"
    finally:
        state.orchestrator = previous


def test_derived_metric_api_forwards_only_public_projection():
    public = {
        "configured": True, "generation": 9, "evaluated_at": 120.0, "metric_count": 1,
        "metrics": [{
            "metric_id": "readiness", "display_name": "Readiness", "description": "",
            "unit": "pts", "precision": 1, "minimum": 0, "maximum": 100,
            "dependencies": [], "generation": 9, "evaluated_at": 120.0,
            "status": "ok", "value": 72.5, "error_code": "",
        }],
    }
    single = dict(public["metrics"][0])
    calls = []
    orchestrator = SimpleNamespace(
        derived_metrics_public_state=lambda: calls.append("list") or public,
        derived_metric_public_state=lambda metric_id: calls.append(metric_id) or single,
    )
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with _client() as client:
            listed = client.get("/api/derived-metrics")
            item = client.get("/api/derived-metrics/readiness")
        assert listed.status_code == 200
        assert listed.json() == public
        assert item.status_code == 200
        assert item.json() == single
        serialized = item.text + listed.text
        assert "formula" not in serialized
        assert "metadata" not in serialized
        assert calls == ["list", "readiness"]
    finally:
        state.orchestrator = previous


def test_hidden_derived_metric_is_indistinguishable_from_missing():
    orchestrator = SimpleNamespace(
        derived_metric_public_state=lambda metric_id: {
            "metric_id": metric_id, "status": "hidden", "error_code": "",
            "display_name": "Private Name", "metadata": {"secret": True},
        },
    )
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with _client() as client:
            response = client.get("/api/derived-metrics/private")
        assert response.status_code == 404
        assert response.json() == {"detail": {"code": "derived_metric.not_found"}}
        assert "Private Name" not in response.text
        assert "secret" not in response.text
    finally:
        state.orchestrator = previous
