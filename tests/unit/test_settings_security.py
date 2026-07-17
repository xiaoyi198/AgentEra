"""Regression tests for local plaintext provider keys and secret-free public surfaces."""

import json

from routers import _state as state
from tests.unit.test_settings_center import _client, _payload


def test_provider_host_change_needs_no_extra_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "local-environment-secret")
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["providers"]["primary"]["base_url"] = (
                "https://new-provider.example/v1"
            )
            validated = client.post("/api/settings/validate", json=payload)
            saved = client.post("/api/settings", json=payload)

        assert validated.status_code == 200
        assert saved.status_code == 200, saved.text
        assert _gateway.config.providers["primary"].base_url == "https://new-provider.example/v1"
    finally:
        state.orchestrator = previous


def test_plaintext_key_is_returned_persisted_and_hot_applied(tmp_path):
    client, orchestrator, gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            provider = payload["model_gateway"]["providers"]["primary"]
            provider["api_key"] = "local-settings-secret"
            response = client.post("/api/settings", json=payload)
            refreshed = client.get("/api/settings")

        assert response.status_code == 200, response.text
        saved = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
        assert saved["providers"]["primary"]["api_key"] == "local-settings-secret"
        assert "api_key_env" not in saved["providers"]["primary"]
        assert refreshed.json()["model_gateway"]["providers"]["primary"]["api_key"] == "local-settings-secret"
        assert gateway._runtime_keys["primary"] == "local-settings-secret"
        assert gateway._clients["fast"].config.api_key == "local-settings-secret"
        assert "local-settings-secret" not in json.dumps(gateway.public_config())
    finally:
        state.orchestrator = previous


def test_model_only_update_preserves_local_plaintext_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "preserved-local-key")
    client, orchestrator, gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["models"]["fast"]["model"] = "new-model"
            response = client.post("/api/settings", json=payload)

        assert response.status_code == 200, response.text
        provider = gateway.config.providers["primary"]
        assert provider.base_url == "https://example.invalid/v1"
        assert provider.api_key == "preserved-local-key"
        assert provider.api_key_env == ""
        assert gateway.config.models["fast"].model == "new-model"
        saved = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
        assert saved["providers"]["primary"]["api_key"] == "preserved-local-key"
    finally:
        state.orchestrator = previous


def test_clearing_plaintext_key_persists_and_hot_applies(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "key-to-clear")
    client, orchestrator, gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["providers"]["primary"]["api_key"] = ""
            saved = client.post("/api/settings", json=payload)
            refreshed = client.get("/api/settings")

        assert saved.status_code == 200, saved.text
        raw = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
        assert raw["providers"]["primary"]["api_key"] == ""
        assert refreshed.json()["model_gateway"]["providers"]["primary"]["api_key"] == ""
        assert "primary" not in gateway._runtime_keys
        assert gateway._clients["fast"].config.api_key == ""
    finally:
        state.orchestrator = previous
