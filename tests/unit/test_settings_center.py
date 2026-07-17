from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.model_gateway import ModelGateway, TraceStore
from multiagent_framework.runtime_settings import RuntimeSettings, RuntimeSettingsError
from routers import _state as state
from routers.settings import router
from tests.unit.test_model_gateway import make_config


class FakeOrchestrator:
    def __init__(self, gateway, runtime):
        self.model_gateway = gateway
        self.runtime_settings = runtime
        self._seconds_per_day = runtime.simulation.seconds_per_day
        self._tick_interval = runtime.simulation.tick_interval
        self.is_running = False
        self.is_paused = True
        self.agents = {}
        self.action_agent = SimpleNamespace(**runtime.action_agent.__dict__)
        self._memory_compressor = SimpleNamespace(
            _temperature=runtime.memory.compression_temperature
        )
        self.router = SimpleNamespace(update_time_scale=lambda value: None)
        self.dlc_loader = SimpleNamespace(
            generation=3,
            agent_factory=SimpleNamespace(agent_defaults={}),
        )

    def set_time_scale(self, value):
        self._seconds_per_day = value
        self.router.update_time_scale(value)


def _client(tmp_path):
    config = make_config(tmp_path)
    config.save_to_file()
    gateway = ModelGateway(config, trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    runtime = RuntimeSettings.from_dict(
        {"simulation": {"seconds_per_day": 120, "tick_interval": 0.25}},
        source_path=str(tmp_path / "runtime.json"),
    )
    runtime.save_to_file()
    orchestrator = FakeOrchestrator(gateway, runtime)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), orchestrator, gateway


def _payload(client):
    document = client.get("/api/settings").json()
    model = document["model_gateway"]
    for key in ("source_path", "legacy_mode", "generation"):
        model.pop(key, None)
    return {
        "model_gateway": model,
        "runtime": document["runtime"],
        "expected_generation": document["generation"],
    }


def test_runtime_settings_migrates_legacy_time_and_validates_memory(tmp_path):
    (tmp_path / "time.json").write_text(
        json.dumps({"seconds_per_day": 90, "tick_interval": 0.5}), encoding="utf-8"
    )
    settings = RuntimeSettings.from_file(str(tmp_path / "runtime.json"))
    assert settings.simulation.seconds_per_day == 90
    assert settings.simulation.tick_interval == 0.5

    with pytest.raises(RuntimeSettingsError, match="keep_recent"):
        RuntimeSettings.from_dict({
            "agent_defaults": {"compress_threshold": 20, "keep_recent": 20}
        })


def test_settings_schema_and_document_return_local_plaintext_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "never-return-this-secret")
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            schema = client.get("/api/settings/schema")
            document = client.get("/api/settings")
        assert schema.status_code == 200
        assert schema.json()["categories"][0]["label"] == "配置总览"
        assert any(item["path"] == "agent_defaults.max_rounds" for item in schema.json()["fields"])
        assert any(item["path"] == "providers.*.api_key" for item in schema.json()["fields"])
        assert any(item["path"] == "providers.*.retry_delay_s" for item in schema.json()["fields"])
        assert any(item["path"] == "providers.*.max_concurrent" for item in schema.json()["fields"])
        assert any(item["path"] == "providers.*.queue_interval_s" for item in schema.json()["fields"])
        assert not any(item["path"] == "providers.*.api_key_env" for item in schema.json()["fields"])
        assert document.status_code == 200
        provider = document.json()["model_gateway"]["providers"]["primary"]
        assert provider["api_key"] == "never-return-this-secret"
        assert provider["api_key_configured"] is True
    finally:
        state.orchestrator = previous


def test_settings_validate_rejects_unknown_profile(tmp_path):
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["task_policies"]["agent.turn"] = ["missing"]
            response = client.post("/api/settings/validate", json=payload)
        assert response.status_code == 422
        assert "unknown profiles" in response.json()["detail"]
    finally:
        state.orchestrator = previous


def test_settings_save_persists_plaintext_key_and_hot_reloads(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_GATEWAY_KEY", raising=False)
    client, orchestrator, gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["models"]["fast"]["model"] = "fast-model-v2"
            payload["model_gateway"]["models"]["fast"]["max_retries"] = 5
            payload["runtime"]["simulation"] = {"seconds_per_day": 75, "tick_interval": 0.4}
            payload["runtime"]["agent_defaults"]["max_rounds"] = 7
            payload["runtime"]["action_agent"]["max_tool_calls"] = 55
            payload["model_gateway"]["providers"]["primary"]["api_key"] = "local-hot-key"
            payload["model_gateway"]["providers"]["primary"]["retry_delay_s"] = 6.5
            payload["model_gateway"]["providers"]["primary"]["max_concurrent"] = 1
            payload["model_gateway"]["providers"]["primary"]["queue_interval_s"] = 1.25
            response = client.post("/api/settings", json=payload)
            refreshed = client.get("/api/settings")

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "saved"
        assert gateway.generation == 2
        assert gateway.config.models["fast"].model == "fast-model-v2"
        assert orchestrator._seconds_per_day == 75
        assert orchestrator._tick_interval == 0.4
        assert orchestrator.action_agent.max_tool_calls == 55
        assert orchestrator.dlc_loader.agent_factory.agent_defaults["max_rounds"] == 7
        saved_models = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
        assert saved_models["providers"]["primary"]["api_key"] == "local-hot-key"
        assert refreshed.json()["model_gateway"]["providers"]["primary"]["api_key"] == "local-hot-key"
        assert gateway._runtime_keys["primary"] == "local-hot-key"
        assert gateway._clients["fast"].config.api_key == "local-hot-key"
        assert gateway.config.providers["primary"].retry_delay_s == 6.5
        assert gateway._clients["fast"].config.retry_delay_s == 6.5
        assert saved_models["providers"]["primary"]["retry_delay_s"] == 6.5
        assert gateway.config.providers["primary"].max_concurrent == 1
        assert gateway.config.providers["primary"].queue_interval_s == 1.25
        assert gateway._clients["fast"]._request_queue is gateway._clients["deep"]._request_queue
        assert gateway._provider_queues["primary"].max_concurrent == 1
        assert gateway._provider_queues["primary"].queue_interval_s == 1.25
        assert saved_models["providers"]["primary"]["max_concurrent"] == 1
        assert saved_models["providers"]["primary"]["queue_interval_s"] == 1.25
        saved_runtime = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8-sig"))
        assert saved_runtime["agent_defaults"]["max_rounds"] == 7
    finally:
        state.orchestrator = previous


def test_settings_host_change_no_longer_requires_extra_confirmation(tmp_path):
    client, orchestrator, _gateway = _client(tmp_path)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["providers"]["primary"]["base_url"] = "https://new-host.invalid/v1"
            response = client.post("/api/settings/validate", json=payload)
        assert response.status_code == 200
        assert response.json()["valid"] is True
    finally:
        state.orchestrator = previous

def test_settings_save_rolls_back_all_files_when_activation_fails(monkeypatch, tmp_path):
    client, orchestrator, gateway = _client(tmp_path)
    time_path = tmp_path / "time.json"
    time_path.write_text('{"seconds_per_day": 120, "tick_interval": 0.25}', encoding="utf-8")
    model_before = (tmp_path / "models.json").read_bytes()
    runtime_before = (tmp_path / "runtime.json").read_bytes()
    time_before = time_path.read_bytes()
    previous = state.orchestrator
    state.orchestrator = orchestrator

    async def fail_replace_config(_candidate, **_kwargs):
        raise RuntimeError("activation failed")

    monkeypatch.setattr(gateway, "replace_config", fail_replace_config)
    try:
        with client:
            payload = _payload(client)
            payload["model_gateway"]["models"]["fast"]["model"] = "must-not-persist"
            payload["runtime"]["simulation"] = {
                "seconds_per_day": 45,
                "tick_interval": 0.1,
            }
            response = client.post("/api/settings", json=payload)

        assert response.status_code == 409
        assert "activation failed" in response.json()["detail"]
        assert (tmp_path / "models.json").read_bytes() == model_before
        assert (tmp_path / "runtime.json").read_bytes() == runtime_before
        assert time_path.read_bytes() == time_before
        assert gateway.generation == 1
        assert orchestrator._seconds_per_day == 120
        assert orchestrator._tick_interval == 0.25
    finally:
        state.orchestrator = previous


def test_provider_fields_keep_frontend_labels_when_schema_is_stale():
    source = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "SettingsCenter.jsx"
    ).read_text(encoding="utf-8")
    assert "const PROVIDER_FIELD_FALLBACKS" in source
    assert "Provider \u5171\u4eab\u5e76\u53d1\u4e0a\u9650" in source
    assert "API \u51fa\u961f\u95f4\u9694\uff08\u79d2\uff09" in source
    assert "\u5931\u8d25\u91cd\u8bd5\u51b7\u5374\uff08\u79d2\uff09" in source
    assert "providerFieldMeta(fieldMap, 'providers.*.max_concurrent')" in source
    assert "providerFieldMeta(fieldMap, 'providers.*.queue_interval_s')" in source
