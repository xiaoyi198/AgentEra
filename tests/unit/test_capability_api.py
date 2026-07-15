from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.action_contract import ActionContractError
from multiagent_framework.capability_contract import CapabilityContractError
from routers import _state as state
from routers.simulation import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_capability_api_uninitialized_catalog_and_service_unavailable():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            catalog = client.get("/api/capabilities?audience=agent")
            invoke = client.post("/api/capabilities/frontier_report/invoke", json={})
        assert catalog.status_code == 200
        assert catalog.json() == {
            "configured": False,
            "generation": 0,
            "fingerprint": "",
            "audience": "agent",
            "capabilities": [],
        }
        assert invoke.status_code == 503
        assert invoke.json()["detail"]["code"] == "capability.orchestrator_unavailable"
    finally:
        state.orchestrator = previous


def test_capability_api_filters_audience_and_forwards_player_invocation():
    invoke = AsyncMock(return_value={"ok": True, "invocation_id": "ui-1"})
    calls = []
    orchestrator = SimpleNamespace(
        capabilities_public_state=lambda audience: calls.append(audience) or {
            "configured": True,
            "generation": 9,
            "fingerprint": "abc",
            "audience": audience,
            "capabilities": [{"capability_id": "frontier_report"}],
        },
        invoke_capability=invoke,
    )
    previous = state.orchestrator
    state.orchestrator = orchestrator
    payload = {
        "invocation_id": "ui-1",
        "expected_generation": 9,
        "source_id": "player_actor",
        "content": "Inspect the northern road.",
        "target_id": "guard",
        "target_ids": ["guard"],
        "parameters": {"severity": 4},
        "urgency": "urgent",
        "actor_id": "player_actor",
        "control_object_id": "station",
        "executor_id": "guard",
        "location": "frontier",
    }
    try:
        with _client() as client:
            catalog = client.get("/api/capabilities?audience=router")
            invalid_audience = client.get("/api/capabilities?audience=admin")
            response = client.post("/api/capabilities/frontier_report/invoke", json=payload)
        assert catalog.json()["audience"] == "router"
        assert invalid_audience.json()["audience"] == "player"
        assert calls == ["router", "player"]
        assert response.status_code == 200
        assert response.json() == {"ok": True, "invocation_id": "ui-1"}
        invoke.assert_awaited_once_with(
            "frontier_report",
            payload,
            audience="player",
            source_id="player_actor",
        )
    finally:
        state.orchestrator = previous


@pytest.mark.parametrize(
    ("error_type", "code", "status"),
    [
        (CapabilityContractError, "capability.not_found", 404),
        (CapabilityContractError, "capability.generation_conflict", 409),
        (CapabilityContractError, "capability.invocation.conflict", 409),
        (CapabilityContractError, "capability.audience_forbidden", 400),
        (ActionContractError, "action.not_found", 404),
        (ActionContractError, "action.not_ready", 409),
        (ActionContractError, "action.world_command_failed", 409),
    ],
)
def test_capability_api_maps_contract_errors(error_type, code, status):
    async def fail(*_args, **_kwargs):
        raise error_type(code, "contract failed", path="capabilities[0]", details={"marker": 1})

    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(invoke_capability=fail)
    try:
        with _client() as client:
            response = client.post("/api/capabilities/frontier_report/invoke", json={})
        assert response.status_code == status
        assert response.json()["detail"] == {
            "code": code,
            "message": "contract failed",
            "path": "capabilities[0]",
            "details": {"marker": 1},
        }
    finally:
        state.orchestrator = previous
