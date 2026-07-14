from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.action_contract import ActionContractError
from multiagent_framework.issue_contract import IssueContractError
from routers import _state as state
from routers.simulation import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_issue_api_uninitialized_catalog_and_service_unavailable():
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with _client() as client:
            catalog = client.get("/api/issue-types")
            issues = client.get("/api/issues")
            decisions = client.get("/api/decision-requests")
        assert catalog.status_code == 200
        assert catalog.json() == {
            "configured": False,
            "generation": 0,
            "issue_types": [],
            "counts": {},
        }
        assert issues.status_code == 503
        assert issues.json()["detail"]["code"] == "issue.orchestrator_unavailable"
        assert decisions.status_code == 503
    finally:
        state.orchestrator = previous


def test_issue_api_forwards_lifecycle_requests():
    calls = []
    orchestrator = SimpleNamespace(
        issues_public_state=lambda: {"configured": True, "generation": 7, "issue_types": [{"issue_type_id": "report"}]},
        list_issues=lambda status="": calls.append(("list_issues", status)) or [{"issue_id": "i-1"}],
        list_cases=lambda status="": calls.append(("list_cases", status)) or [{"case_id": "c-1"}],
        list_decision_requests=lambda status="": calls.append(("list_decisions", status)) or [{"decision_request_id": "d-1"}],
        open_issue=lambda payload: calls.append(("open", payload)) or {"issue": {"issue_id": "i-1"}},
        append_issue_evidence=lambda issue_id, payload: calls.append(("evidence", issue_id, payload)) or {"issue": {"issue_id": issue_id}},
        close_case=lambda case_id, payload: calls.append(("close", case_id, payload)) or {"case": {"case_id": case_id}},
        preview_decision=lambda request_id, payload: calls.append(("preview", request_id, payload)) or {"ok": True},
        resolve_decision=lambda request_id, payload: calls.append(("resolve", request_id, payload)) or {"decision_request": {"decision_request_id": request_id}},
    )
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with _client() as client:
            assert client.get("/api/issue-types").json()["generation"] == 7
            assert client.get("/api/issues", params={"status": "open"}).json()[0]["issue_id"] == "i-1"
            assert client.get("/api/cases", params={"status": "monitoring"}).json()[0]["case_id"] == "c-1"
            assert client.get("/api/decision-requests", params={"status": "pending"}).json()[0]["decision_request_id"] == "d-1"
            assert client.post("/api/issues", json={"summary": "Report"}).status_code == 200
            assert client.post("/api/issues/i-1/evidence", json={"kind": "report"}).status_code == 200
            assert client.post("/api/cases/c-1/close", json={"status": "closed"}).status_code == 200
            assert client.post("/api/decision-requests/d-1/preview", json={"option_id": "patrol"}).json() == {"ok": True}
            assert client.post("/api/decision-requests/d-1/resolve", json={"decision_id": "client-1", "option_id": "patrol"}).status_code == 200
        assert calls == [
            ("list_issues", "open"),
            ("list_cases", "monitoring"),
            ("list_decisions", "pending"),
            ("open", {"summary": "Report"}),
            ("evidence", "i-1", {"kind": "report"}),
            ("close", "c-1", {"status": "closed"}),
            ("preview", "d-1", {"option_id": "patrol"}),
            ("resolve", "d-1", {"decision_id": "client-1", "option_id": "patrol"}),
        ]
    finally:
        state.orchestrator = previous


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("issue.not_found", 404),
        ("case.not_found", 404),
        ("decision.not_found", 404),
        ("issue.type_not_found", 404),
        ("issue.generation_conflict", 409),
        ("issue.external_id_conflict", 409),
        ("decision.id_conflict", 409),
        ("decision.not_pending", 409),
        ("decision.expired", 409),
        ("issue.evidence.required", 400),
    ],
)
def test_issue_api_maps_contract_errors(code, status):
    def fail(*_args, **_kwargs):
        raise IssueContractError(code, "contract failed", path="issue_types[0]", details={"marker": 1})

    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(open_issue=fail)
    try:
        with _client() as client:
            response = client.post("/api/issues", json={})
        assert response.status_code == status
        assert response.json()["detail"] == {
            "code": code,
            "message": "contract failed",
            "path": "issue_types[0]",
            "details": {"marker": 1},
        }
    finally:
        state.orchestrator = previous


def test_issue_decision_api_maps_action_errors():
    def fail(*_args, **_kwargs):
        raise ActionContractError("action.not_ready", "cooldown")

    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(preview_decision=fail, resolve_decision=fail)
    try:
        with _client() as client:
            preview = client.post("/api/decision-requests/d-1/preview", json={})
            resolve = client.post("/api/decision-requests/d-1/resolve", json={})
        assert preview.status_code == 409
        assert preview.json()["detail"]["code"] == "action.not_ready"
        assert resolve.status_code == 409
    finally:
        state.orchestrator = previous
