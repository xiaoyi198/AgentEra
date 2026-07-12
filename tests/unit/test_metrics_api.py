from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import _state as state
from routers.metrics import router


def _app():
    app = FastAPI()
    app.include_router(router)
    return app


def _orchestrator():
    return SimpleNamespace(
        agents={}, agent_generation=0, world_state=None,
        model_gateway=None, dlc_loader=None,
        is_running=False, is_paused=False, sim_time=0,
        _agent_tasks=set(), _pending_wakeups=[],
        operational_metrics_snapshot=lambda: {},
    )


def test_metrics_api_is_hidden_when_disabled(monkeypatch):
    monkeypatch.delenv("WORLD_SIM_ENABLE_METRICS_API", raising=False)
    monkeypatch.setenv("WORLD_SIM_METRICS_TOKEN", "metrics-secret")
    previous = state.orchestrator
    state.orchestrator = _orchestrator()
    try:
        with TestClient(_app()) as client:
            response = client.get(
                "/api/metrics", headers={"X-Metrics-Token": "metrics-secret"}
            )
        assert response.status_code == 404
    finally:
        state.orchestrator = previous


def test_metrics_api_requires_independent_token(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_METRICS_API", "1")
    monkeypatch.setenv("WORLD_SIM_METRICS_TOKEN", "metrics-secret")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    previous = state.orchestrator
    state.orchestrator = _orchestrator()
    try:
        with TestClient(_app()) as client:
            missing = client.get("/api/metrics")
            admin = client.get(
                "/api/metrics", headers={"X-Metrics-Token": "admin-secret"}
            )
            valid = client.get(
                "/api/metrics", headers={"X-Metrics-Token": "metrics-secret"}
            )
        assert missing.status_code == 403
        assert admin.status_code == 403
        assert valid.status_code == 200
        serialized = json.dumps(valid.json()).lower()
        assert "metrics-secret" not in serialized
        assert "admin-secret" not in serialized
        assert "world_sim_metrics_token" not in serialized
    finally:
        state.orchestrator = previous


def test_prometheus_endpoint_returns_text_and_no_secrets(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_METRICS_API", "true")
    monkeypatch.setenv("WORLD_SIM_METRICS_TOKEN", "metrics-secret")
    previous = state.orchestrator
    state.orchestrator = _orchestrator()
    try:
        with TestClient(_app()) as client:
            response = client.get(
                "/api/metrics/prometheus",
                headers={"X-Metrics-Token": "metrics-secret"},
            )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "text/plain; version=0.0.4"
        )
        assert "world_sim_up 1" in response.text
        assert "world_sim_health_status 1" in response.text
        assert "metrics-secret" not in response.text
    finally:
        state.orchestrator = previous


def test_metrics_api_returns_503_before_simulation_init(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_METRICS_API", "yes")
    monkeypatch.setenv("WORLD_SIM_METRICS_TOKEN", "metrics-secret")
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with TestClient(_app()) as client:
            response = client.get(
                "/api/metrics", headers={"X-Metrics-Token": "metrics-secret"}
            )
        assert response.status_code == 503
    finally:
        state.orchestrator = previous
