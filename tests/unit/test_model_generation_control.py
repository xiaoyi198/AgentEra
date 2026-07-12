from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.model_generation_archive import ModelGenerationArchiveStore
from multiagent_framework.model_generation_control import (
    ModelGenerationController,
    ModelGenerationControlError,
)
from multiagent_framework.model_gateway import ModelGateway, TraceStore
from routers import _state as state
from routers.debug import router
from tests.unit.test_model_gateway import make_config, _replacement_config


def _gateway(tmp_path):
    config = make_config(tmp_path)
    config.save_to_file()
    archive = ModelGenerationArchiveStore(
        tmp_path / "model-generations",
        key=ModelGenerationArchiveStore.generate_key(),
    )
    return ModelGateway(
        config,
        trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
        generation_archive=archive,
    )


def _advance(gateway, tmp_path):
    candidate = _replacement_config(tmp_path)
    candidate.save_to_file()
    asyncio.run(gateway.replace_config(candidate, expected_generation=1))
    return candidate


def test_gateway_recovers_monotonic_generation_across_processes(tmp_path):
    gateway = _gateway(tmp_path)
    _advance(gateway, tmp_path)
    archive = gateway._generation_archive

    restored = ModelGateway(
        _replacement_config(tmp_path),
        trace_store=TraceStore(str(tmp_path / "trace-2.jsonl")),
        generation_archive=archive,
    )
    assert restored.generation == 2
    assert [item["generation"] for item in restored.generation_status()["generations"]] == [2, 1]

    external = _replacement_config(tmp_path)
    external.task_policies["agent.turn"] = ["deep"]
    external.save_to_file()
    changed = ModelGateway(
        external,
        trace_store=TraceStore(str(tmp_path / "trace-3.jsonl")),
        generation_archive=archive,
    )
    assert changed.generation == 3


def test_corrupt_archive_does_not_block_gateway_startup(tmp_path):
    first = _gateway(tmp_path)
    archive = first._generation_archive
    (tmp_path / "model-generations" / "generation-00000001.json").write_text(
        "not-json", encoding="utf-8"
    )
    restarted = ModelGateway(
        make_config(tmp_path),
        trace_store=TraceStore(str(tmp_path / "trace-2.jsonl")),
        generation_archive=archive,
    )
    status = restarted.generation_status()
    assert restarted.generation == 1
    assert status["archive"]["issue_count"] == 1
    assert status["archive"]["issues"][0]["code"] == "archive_decryption_failed"


def test_generation_status_aggregates_traces_without_sensitive_fields(tmp_path):
    gateway = _gateway(tmp_path)
    gateway.trace_store.append({
        "config_generation": 1, "success": False, "fallback": False,
        "input_tokens": 4, "output_tokens": 0, "latency_ms": 10,
        "error": "provider secret detail", "trace_id": "private-id",
    })
    gateway.trace_store.append({
        "config_generation": 1, "success": True, "fallback": True,
        "input_tokens": 5, "output_tokens": 3, "latency_ms": 30,
    })

    payload = gateway.generation_status()
    generation = payload["generations"][0]
    assert generation["calls"] == 2
    assert generation["successes"] == 1
    assert generation["errors"] == 1
    assert generation["fallback_attempts"] == 1
    assert generation["total_tokens"] == 12
    assert generation["mean_latency_ms"] == 20.0
    assert generation["max_latency_ms"] == 30.0
    serialized = json.dumps(payload).lower()
    assert "provider secret" not in serialized
    assert "private-id" not in serialized
    assert "base_url" not in serialized
    assert "api_key_env" not in serialized
    assert "source_path" not in serialized


def test_rollback_preview_is_stable_and_apply_creates_new_generation(tmp_path):
    gateway = _gateway(tmp_path)
    _advance(gateway, tmp_path)
    controller = ModelGenerationController(gateway)

    first = controller.preview_rollback(1, 2)
    second = controller.preview_rollback(1, 2)
    assert first == second
    assert first["source_generation"] == 1
    assert first["next_generation"] == 3
    assert first["profiles_changed_count"] == 1

    result = asyncio.run(controller.apply_rollback(1, 2, first["plan_fingerprint"]))
    assert result["generation"] == 3
    assert result["source_generation"] == 1
    assert gateway.config.models["fast"].model == "fast-model"
    disk = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    assert disk["models"]["fast"]["model"] == "fast-model"


def test_rollback_rejects_stale_unknown_fingerprint_and_external_edit(tmp_path):
    gateway = _gateway(tmp_path)
    _advance(gateway, tmp_path)
    controller = ModelGenerationController(gateway)
    plan = controller.preview_rollback(1, 2)

    with pytest.raises(ModelGenerationControlError, match="current_generation_stale"):
        controller.preview_rollback(1, 1)
    with pytest.raises(ModelGenerationControlError, match="source_generation_unavailable"):
        controller.preview_rollback(99, 2)
    with pytest.raises(ModelGenerationControlError, match="plan_fingerprint_mismatch"):
        asyncio.run(controller.apply_rollback(1, 2, "0" * 64))

    disk = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    disk["task_policies"]["agent.turn"] = ["deep"]
    (tmp_path / "models.json").write_text(json.dumps(disk), encoding="utf-8")
    with pytest.raises(ModelGenerationControlError, match="source_configuration_changed"):
        asyncio.run(controller.apply_rollback(1, 2, plan["plan_fingerprint"]))


def test_rollback_restores_disk_when_gateway_activation_fails(monkeypatch, tmp_path):
    gateway = _gateway(tmp_path)
    _advance(gateway, tmp_path)
    controller = ModelGenerationController(gateway)
    plan = controller.preview_rollback(1, 2)
    before = (tmp_path / "models.json").read_bytes()

    async def fail_replace(*args, **kwargs):
        raise RuntimeError("private failure")

    monkeypatch.setattr(gateway, "replace_config", fail_replace)
    with pytest.raises(ModelGenerationControlError, match="configuration_reload_failed"):
        asyncio.run(controller.apply_rollback(1, 2, plan["plan_fingerprint"]))
    assert (tmp_path / "models.json").read_bytes() == before
    assert gateway.generation == 2


def test_generation_admin_api_observes_previews_and_applies(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    gateway = _gateway(tmp_path)
    _advance(gateway, tmp_path)
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(model_gateway=gateway)
    headers = {"X-Admin-Token": "admin-secret"}
    try:
        with TestClient(app) as client:
            forbidden = client.get(
                "/api/debug/model-generations", headers={"X-Admin-Token": "wrong"}
            )
            observed = client.get("/api/debug/model-generations", headers=headers)
            preview = client.post(
                "/api/debug/model-config/rollback/preview",
                headers=headers,
                json={"source_generation": 1, "expected_current_generation": 2},
            )
            applied = client.post(
                "/api/debug/model-config/rollback/apply",
                headers=headers,
                json={
                    "source_generation": 1,
                    "expected_current_generation": 2,
                    "expected_plan_fingerprint": preview.json()["plan_fingerprint"],
                },
            )
        assert forbidden.status_code == 403
        assert observed.status_code == 200
        assert preview.status_code == 200
        assert applied.status_code == 200
        assert applied.json()["generation"] == 3
        serialized = json.dumps({
            "observed": observed.json(), "preview": preview.json(), "applied": applied.json()
        }).lower()
        assert "base_url" not in serialized
        assert "api_key_env" not in serialized
        assert "admin-secret" not in serialized
        assert "source_path" not in serialized
    finally:
        state.orchestrator = previous
