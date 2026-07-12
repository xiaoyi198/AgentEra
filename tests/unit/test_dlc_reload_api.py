from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import _state as state
from routers.debug import router


class FakeDLCLoader:
    def __init__(self, *, fail: bool = False) -> None:
        self.generation = 3
        self.fail = fail
        self.reload_calls = 0
        self.rollback_calls = 0

    def public_runtime(self):
        return {
            "generation": self.generation,
            "package_ids": ["sample"],
            "agent_count": 2,
            "official_count": 3,
            "retired_memory_archives": 0,
            "available_generations": [
                {
                    "generation": 1,
                    "package_ids": ["sample"],
                    "package_hashes": {"sample": "abcdef0123456789"},
                    "active": self.generation == 1,
                },
            ],
            "recent_events": [],
        }

    def preview_reload(self):
        if self.fail:
            raise ValueError("C:/private/dlc prompt-body provider-secret")
        return {
            "current_generation": self.generation,
            "target_generation": self.generation + 1,
            "package_ids": ["sample"],
            "package_hashes": {"sample": "abcdef0123456789"},
            "added_agents": ["scribe"],
            "removed_agents": [],
            "changed_agents": ["mayor"],
            "unchanged_agents": [],
            "world_changes": {"entities_added": 1},
            "warnings": ["initial_events_not_replayed"],
            "errors": [],
            "safe": True,
        }

    async def reload_active(self):
        if self.fail:
            raise RuntimeError("C:/private/dlc prompt-body provider-secret")
        self.reload_calls += 1
        self.generation += 1
        result = self.preview_reload()
        result["generation"] = self.generation
        return result

    def preview_rollback(self, source_generation: int):
        if self.fail:
            raise ValueError("C:/private/dlc prompt-body provider-secret")
        return {
            "current_generation": self.generation,
            "target_generation": self.generation + 1,
            "rollback_source_generation": source_generation,
            "activation_kind": "rollback",
            "package_ids": ["sample"],
            "package_hashes": {"sample": "abcdef0123456789"},
            "added_agents": ["office"],
            "removed_agents": ["scribe"],
            "changed_agents": ["mayor"],
            "unchanged_agents": [],
            "world_changes": {},
            "warnings": ["dynamic_world_state_preserved"],
            "errors": [],
            "safe": True,
        }

    async def rollback_active(self, source_generation: int):
        if self.fail:
            raise RuntimeError("C:/private/dlc prompt-body provider-secret")
        self.rollback_calls += 1
        self.generation += 1
        result = self.preview_rollback(source_generation)
        result["generation"] = self.generation
        result["source_generation"] = source_generation
        return result


def _install_loader(loader):
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(dlc_loader=loader)
    app = FastAPI()
    app.include_router(router)
    return app, previous


def test_dlc_runtime_admin_api_is_hidden_when_disabled(monkeypatch):
    monkeypatch.delenv("WORLD_SIM_ENABLE_ADMIN_API", raising=False)
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    loader = FakeDLCLoader()
    app, previous = _install_loader(loader)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debug/dlc-runtime/reload",
                headers={"X-Admin-Token": "admin-secret"},
            )
        assert response.status_code == 404
        assert loader.reload_calls == 0
    finally:
        state.orchestrator = previous


def test_dlc_runtime_admin_api_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    loader = FakeDLCLoader()
    app, previous = _install_loader(loader)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/debug/dlc-runtime/preview",
                headers={"X-Admin-Token": "wrong"},
            )
        assert response.status_code == 403
    finally:
        state.orchestrator = previous


def test_dlc_runtime_preview_reload_and_status_are_secret_free(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "true")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    loader = FakeDLCLoader()
    app, previous = _install_loader(loader)
    try:
        with TestClient(app) as client:
            preview = client.post(
                "/api/debug/dlc-runtime/preview",
                headers={"X-Admin-Token": "admin-secret"},
            )
            reload_response = client.post(
                "/api/debug/dlc-runtime/reload",
                headers={"X-Admin-Token": "admin-secret"},
            )
            status = client.get("/api/debug/dlc-runtime")
        assert preview.status_code == 200
        assert reload_response.status_code == 200
        assert reload_response.json()["generation"] == 4
        assert status.status_code == 200
        serialized = json.dumps({
            "preview": preview.json(),
            "reload": reload_response.json(),
            "status": status.json(),
        }).lower()
        for forbidden in (
            "admin-secret", "provider-secret", "prompt-body", "base_url",
            "api_key_env", "authorization", "c:/private",
        ):
            assert forbidden not in serialized
    finally:
        state.orchestrator = previous


def test_dlc_runtime_api_hides_internal_validation_errors(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    loader = FakeDLCLoader(fail=True)
    app, previous = _install_loader(loader)
    try:
        with TestClient(app) as client:
            preview = client.post(
                "/api/debug/dlc-runtime/preview",
                headers={"X-Admin-Token": "admin-secret"},
            )
            reload_response = client.post(
                "/api/debug/dlc-runtime/reload",
                headers={"X-Admin-Token": "admin-secret"},
            )
        assert preview.status_code == 400
        assert preview.json() == {"detail": "DLC runtime validation failed"}
        assert reload_response.status_code == 400
        assert reload_response.json() == {"detail": "DLC runtime reload failed"}
        assert "private" not in (preview.text + reload_response.text).lower()
    finally:
        state.orchestrator = previous


def test_dlc_runtime_rollback_preview_apply_and_auth_are_secret_free(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    loader = FakeDLCLoader()
    app, previous = _install_loader(loader)
    try:
        with TestClient(app) as client:
            forbidden = client.post(
                "/api/debug/dlc-runtime/rollback/preview",
                headers={"X-Admin-Token": "wrong"},
                json={"source_generation": 1},
            )
            preview = client.post(
                "/api/debug/dlc-runtime/rollback/preview",
                headers={"X-Admin-Token": "admin-secret"},
                json={"source_generation": 1},
            )
            rollback = client.post(
                "/api/debug/dlc-runtime/rollback",
                headers={"X-Admin-Token": "admin-secret"},
                json={"source_generation": 1},
            )
        assert forbidden.status_code == 403
        assert preview.status_code == 200
        assert preview.json()["rollback_source_generation"] == 1
        assert preview.json()["activation_kind"] == "rollback"
        assert rollback.status_code == 200
        assert rollback.json()["generation"] == 4
        assert rollback.json()["source_generation"] == 1
        assert loader.rollback_calls == 1
        serialized = json.dumps({
            "preview": preview.json(),
            "rollback": rollback.json(),
        }).lower()
        for forbidden_value in (
            "admin-secret", "provider-secret", "prompt-body", "base_url",
            "api_key_env", "authorization", "c:/private",
        ):
            assert forbidden_value not in serialized
    finally:
        state.orchestrator = previous


def test_dlc_runtime_rollback_api_hides_internal_errors(monkeypatch):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    loader = FakeDLCLoader(fail=True)
    app, previous = _install_loader(loader)
    try:
        with TestClient(app) as client:
            preview = client.post(
                "/api/debug/dlc-runtime/rollback/preview",
                headers={"X-Admin-Token": "admin-secret"},
                json={"source_generation": 1},
            )
            rollback = client.post(
                "/api/debug/dlc-runtime/rollback",
                headers={"X-Admin-Token": "admin-secret"},
                json={"source_generation": 1},
            )
        assert preview.status_code == 400
        assert preview.json() == {
            "detail": "DLC runtime rollback validation failed"
        }
        assert rollback.status_code == 400
        assert rollback.json() == {"detail": "DLC runtime rollback failed"}
        assert "private" not in (preview.text + rollback.text).lower()
    finally:
        state.orchestrator = previous
