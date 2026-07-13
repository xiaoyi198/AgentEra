from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.action_contract import ActionContractError
from routers import _state as state
from routers.simulation import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_actions_api_uninitialized_catalog_and_service_unavailable():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            catalog = client.get("/api/actions")
            preview = client.post("/api/actions/patrol/preview", json={})
            execute = client.post("/api/actions/patrol/execute", json={"invocation_id": "i-1"})
        assert catalog.status_code == 200
        assert catalog.json() == {
            "configured": False,
            "generation": 0,
            "fingerprint": "",
            "actions": [],
        }
        assert preview.status_code == 503
        assert preview.json()["detail"]["code"] == "action.orchestrator_unavailable"
        assert execute.status_code == 503
    finally:
        state.orchestrator = previous


def test_actions_api_forwards_normalized_requests():
    calls = []
    orchestrator = SimpleNamespace(
        actions_public_state=lambda: {
            "configured": True,
            "generation": 4,
            "fingerprint": "abc",
            "actions": [{"action_id": "patrol"}],
        },
        preview_dlc_action=lambda action_id, request: calls.append(("preview", action_id, request)) or {"ok": True},
        execute_dlc_action=lambda action_id, request: calls.append(("execute", action_id, request)) or {"status": "executed"},
    )
    previous = state.orchestrator
    state.orchestrator = orchestrator
    payload = {
        "invocation_id": "i-1",
        "expected_generation": 4,
        "actor_id": "actor",
        "control_object_id": "station",
        "target_id": "station",
        "parameters": {"gain": 5},
    }
    try:
        with _client() as client:
            catalog = client.get("/api/actions")
            preview = client.post("/api/actions/patrol/preview", json=payload)
            execute = client.post("/api/actions/patrol/execute", json=payload)
        assert catalog.json()["actions"] == [{"action_id": "patrol"}]
        assert preview.json() == {"ok": True}
        assert execute.json() == {"status": "executed"}
        assert calls == [
            ("preview", "patrol", payload),
            ("execute", "patrol", payload),
        ]
    finally:
        state.orchestrator = previous


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("action.not_found", 404),
        ("action.generation_conflict", 409),
        ("action.invocation.conflict", 409),
        ("action.not_ready", 409),
        ("action.world_command_failed", 409),
        ("action.actor_forbidden", 400),
    ],
)
def test_actions_api_maps_contract_errors(code, status):
    def fail(*_args, **_kwargs):
        raise ActionContractError(code, "contract failed", path="actions[0]", details={"marker": 1})

    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(preview_dlc_action=fail, execute_dlc_action=fail)
    try:
        with _client() as client:
            response = client.post("/api/actions/patrol/preview", json={})
        assert response.status_code == status
        assert response.json()["detail"] == {
            "code": code,
            "message": "contract failed",
            "path": "actions[0]",
            "details": {"marker": 1},
        }
    finally:
        state.orchestrator = previous
