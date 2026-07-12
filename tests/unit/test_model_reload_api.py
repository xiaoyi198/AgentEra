from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.model_gateway import ModelGateway, TraceStore
from routers import _state as state
from routers.debug import router
from tests.unit.test_model_gateway import make_config, _replacement_config


def _app_with_gateway(tmp_path):
    config = make_config(tmp_path)
    config.save_to_file()
    gateway = ModelGateway(
        config,
        trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
    )
    app = FastAPI()
    app.include_router(router)
    return app, gateway


def _install_gateway(gateway):
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(model_gateway=gateway)
    return previous


def test_model_reload_admin_api_is_hidden_when_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("WORLD_SIM_ENABLE_ADMIN_API", raising=False)
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    app, gateway = _app_with_gateway(tmp_path)
    previous = _install_gateway(gateway)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debug/model-config/reload",
                headers={"X-Admin-Token": "admin-secret"},
            )
        assert response.status_code == 404
        assert gateway.generation == 1
    finally:
        state.orchestrator = previous


def test_model_reload_admin_api_rejects_wrong_token(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    app, gateway = _app_with_gateway(tmp_path)
    previous = _install_gateway(gateway)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debug/model-config/reload",
                headers={"X-Admin-Token": "wrong"},
            )
        assert response.status_code == 403
        assert gateway.generation == 1
    finally:
        state.orchestrator = previous


def test_model_reload_preview_and_reload_are_safe_and_atomic(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "true")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("TEST_GATEWAY_KEY", "provider-secret")
    app, gateway = _app_with_gateway(tmp_path)
    candidate = _replacement_config(tmp_path)
    candidate.save_to_file()
    previous = _install_gateway(gateway)
    try:
        with TestClient(app) as client:
            preview_response = client.post(
                "/api/debug/model-config/preview",
                headers={"X-Admin-Token": "admin-secret"},
            )
            reload_response = client.post(
                "/api/debug/model-config/reload",
                headers={"X-Admin-Token": "admin-secret"},
            )
            public_response = client.get("/api/debug/model-config")

        assert preview_response.status_code == 200
        assert preview_response.json()["profiles"]["changed"] == ["fast"]
        assert reload_response.status_code == 200
        assert reload_response.json()["generation"] == 2
        assert gateway.generation == 2
        assert gateway.config.models["fast"].model == "replacement-model"
        assert public_response.status_code == 200

        serialized = json.dumps(
            {
                "preview": preview_response.json(),
                "reload": reload_response.json(),
                "public": public_response.json(),
            }
        ).lower()
        assert "provider-secret" not in serialized
        assert "admin-secret" not in serialized
        assert "test_gateway_key" not in serialized
        assert "example.invalid" not in serialized
        assert "base_url" not in serialized
        assert "api_key_env" not in serialized
        assert "authorization" not in serialized
    finally:
        state.orchestrator = previous


def test_model_reload_rejects_stale_expected_generation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    app, gateway = _app_with_gateway(tmp_path)
    previous = _install_gateway(gateway)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debug/model-config/reload",
                headers={"X-Admin-Token": "admin-secret"},
                json={"expected_generation": 2},
            )
        assert response.status_code == 400
        assert gateway.generation == 1
    finally:
        state.orchestrator = previous


def test_invalid_reload_keeps_previous_generation(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    app, gateway = _app_with_gateway(tmp_path)
    source_path = Path(gateway.config.source_path)
    source_path.write_text(
        json.dumps(
            {
                "providers": {
                    "primary": {"base_url": "https://example.invalid/v1"}
                },
                "models": {
                    "fast": {"provider": "primary", "model": "fast-model"}
                },
                "task_policies": {"agent.turn": ["missing"]},
            }
        ),
        encoding="utf-8",
    )
    config_before = gateway.config
    clients_before = gateway._clients
    previous = _install_gateway(gateway)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debug/model-config/reload",
                headers={"X-Admin-Token": "admin-secret"},
            )
        assert response.status_code == 400
        assert response.json() == {"detail": "Model configuration reload failed"}
        assert gateway.generation == 1
        assert gateway.config is config_before
        assert gateway._clients is clients_before
    finally:
        state.orchestrator = previous
