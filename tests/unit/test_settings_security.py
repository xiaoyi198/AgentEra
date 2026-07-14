"""Security regression tests for the detailed settings center."""

import json

from routers import _state as state
from tests.unit.test_settings_center import _client, _payload


def test_configured_provider_host_change_requires_explicit_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "environment-secret")
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["providers"]["primary"]["base_url"] = (
                "https://attacker.example/v1"
            )
            rejected = client.post("/api/settings/validate", json=payload)
            payload["allow_secret_host_change"] = True
            confirmed = client.post("/api/settings/validate", json=payload)

        assert rejected.status_code == 422
        assert "安全确认" in rejected.json()["detail"]
        assert confirmed.status_code == 200
        assert confirmed.json()["valid"] is True
    finally:
        state.orchestrator = previous


def test_environment_variable_reference_is_editable_but_secret_is_never_returned(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("SETTINGS_SECURITY_NEW_KEY", "never-return-this-secret")
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            provider = payload["model_gateway"]["providers"]["primary"]
            provider["api_key_env"] = "SETTINGS_SECURITY_NEW_KEY"
            response = client.post("/api/settings", json=payload)
            refreshed = client.get("/api/settings")

        assert response.status_code == 200, response.text
        serialized = json.dumps(response.json(), ensure_ascii=False)
        serialized += json.dumps(refreshed.json(), ensure_ascii=False)
        assert "never-return-this-secret" not in serialized
        saved = (tmp_path / "models.json").read_text(encoding="utf-8")
        assert "SETTINGS_SECURITY_NEW_KEY" in saved
        assert "never-return-this-secret" not in saved
    finally:
        state.orchestrator = previous


def test_model_only_update_preserves_provider_configuration(tmp_path):
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
        assert provider.api_key_env == "TEST_GATEWAY_KEY"
        assert gateway.config.models["fast"].model == "new-model"
    finally:
        state.orchestrator = previous


def test_new_session_key_requires_confirmation_and_never_persists(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_GATEWAY_KEY", raising=False)
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["providers"]["primary"]["base_url"] = (
                "https://new-provider.example/v1"
            )
            payload["runtime_keys"] = {"primary": "session-only-secret"}
            rejected = client.post("/api/settings", json=payload)
            payload["allow_secret_host_change"] = True
            saved = client.post("/api/settings", json=payload)
            refreshed = client.get("/api/settings")

        assert rejected.status_code == 422
        assert saved.status_code == 200, saved.text
        assert "session-only-secret" not in (tmp_path / "models.json").read_text(
            encoding="utf-8"
        )
        assert "session-only-secret" not in json.dumps(saved.json(), ensure_ascii=False)
        assert "session-only-secret" not in json.dumps(refreshed.json(), ensure_ascii=False)
    finally:
        state.orchestrator = previous
