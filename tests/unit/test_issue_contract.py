from __future__ import annotations

import copy

import pytest

from multiagent_framework.action_contract import ActionCatalog
from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.world_state import Entity, InventoryItem, ResourceDefinition, WorldState
from multiagent_framework.issue_contract import (
    IssueContractError,
    IssueManager,
    normalize_issue_document,
    validate_issue_definitions,
)


def gameplay() -> GameplayContract:
    return GameplayContract.from_content(
        {"player": {"identity_id": "commander", "actor_id": "player", "controlled_objects": [{"kind": "entity", "id": "station", "mode": "direct"}]}},
        [{"id": "guard", "role": "official"}],
        package_id="test",
    )


def definitions() -> list[dict]:
    return [{
        "issue_type_id": "security_report",
        "display_name": "Security report",
        "default_urgency": "high",
        "default_deadline_days": 3,
        "source": {"allowed_kinds": ["agent"], "allowed_ids": ["guard"]},
        "match": {"payload_types": ["security"], "source_ids": ["guard"], "default_for_player_reports": True},
        "evidence": [{"kind": "report", "display_name": "Report", "required": True}],
        "decision_requests": [{
            "request_id": "response",
            "title": "Choose response",
            "deadline_days": 2,
            "options": [
                {
                    "option_id": "patrol",
                    "display_name": "Patrol",
                    "action_id": "patrol",
                    "control_object_id": "station",
                    "target_id": "station",
                    "parameters": {"gain": 5},
                    "resolution": {"issue_status": "resolved", "case_status": "resolved", "summary": "Patrol ordered"},
                },
                {
                    "option_id": "monitor",
                    "display_name": "Monitor",
                    "resolution": {"issue_status": "monitoring", "case_status": "monitoring", "summary": "Monitoring"},
                },
            ],
        }],
    }]


def opened(manager: IssueManager, *, sim_time: float = 10) -> dict:
    return manager.open_issue({
        "external_id": "report-1",
        "type": "security",
        "source": {"kind": "agent", "id": "guard", "display_name": "Guard"},
        "summary": "Tracks found",
        "evidence": [{"kind": "report", "summary": "Fresh tracks", "confidence": 0.8}],
    }, sim_time=sim_time)


def test_normalizes_and_cross_validates_issue_definitions():
    normalized = normalize_issue_document(definitions())
    assert normalized[0]["issue_type_id"] == "security_report"
    assert normalized[0]["decision_requests"][0]["options"][0]["action_id"] == "patrol"
    assert not validate_issue_definitions(
        definitions(), action_ids={"patrol"}, entity_ids={"station"},
        agent_ids={"guard"}, gameplay=gameplay(),
    )
    broken = copy.deepcopy(definitions())
    broken[0]["decision_requests"][0]["options"][0]["action_id"] = "missing"
    broken.append(copy.deepcopy(broken[0]))
    codes = {item.code for item in validate_issue_definitions(
        broken, action_ids={"patrol"}, entity_ids={"station"},
        agent_ids={"guard"}, gameplay=gameplay(),
    )}
    assert "issue.option.unknown_action" in codes
    assert "issues.duplicate" in codes
    assert "issues.multiple_defaults" in codes


def test_open_issue_requires_evidence_and_is_external_id_idempotent():
    manager = IssueManager()
    manager.configure(definitions(), generation=4, action_ids={"patrol"}, gameplay=gameplay())
    with pytest.raises(IssueContractError) as exc:
        manager.open_issue({"source": {"kind": "agent", "id": "guard"}}, sim_time=10)
    assert exc.value.code == "issue.evidence.required"
    result = opened(manager)
    assert result["issue"]["urgency"] == "high"
    assert result["issue"]["deadline_at"] == 10 + 3 * 86400
    assert result["decision_requests"][0]["deadline_at"] == 10 + 2 * 86400
    assert manager.open_issue({
        "external_id": "report-1", "type": "security",
        "source": {"kind": "agent", "id": "guard", "display_name": "Guard"},
        "summary": "Tracks found",
        "evidence": [{"kind": "report", "summary": "Fresh tracks", "confidence": 0.8}],
    }, sim_time=99)["replayed"] is True
    with pytest.raises(IssueContractError) as exc:
        manager.open_issue({"external_id": "report-1", "source": {"kind": "agent", "id": "guard"}, "summary": "changed", "evidence": [{"kind": "report"}]}, sim_time=10)
    assert exc.value.code == "issue.external_id_conflict"


def test_decision_executes_action_and_is_idempotent():
    manager = IssueManager()
    manager.configure(definitions(), generation=4, action_ids={"patrol"}, gameplay=gameplay())
    request_id = opened(manager)["decision_requests"][0]["decision_request_id"]
    previews, executions = [], []
    def preview(action_id, invocation):
        previews.append((action_id, copy.deepcopy(invocation)))
        return {"ok": True, "duration": {"duration_days": 2}}
    def execute(action_id, invocation):
        executions.append((action_id, copy.deepcopy(invocation)))
        return {"ok": True, "invocation_id": invocation["invocation_id"]}
    payload = {"decision_id": "decision-client-1", "expected_generation": 4, "option_id": "patrol", "note": "Proceed"}
    result = manager.resolve_decision(request_id, payload, sim_time=20, action_preview=preview, action_execute=execute)
    assert result["issue"]["status"] == "resolved"
    assert result["case"]["status"] == "resolved"
    assert executions == [("patrol", {
        "invocation_id": "decision:decision-client-1", "expected_generation": 4,
        "actor_id": "player", "control_object_id": "station", "target_id": "station",
        "parameters": {"gain": 5},
    })]
    replay = manager.resolve_decision(request_id, payload, sim_time=30, action_preview=preview, action_execute=execute)
    assert replay["replayed"] is True
    assert len(executions) == 1
    with pytest.raises(IssueContractError) as exc:
        manager.resolve_decision(request_id, {**payload, "option_id": "monitor"}, sim_time=30, action_preview=preview, action_execute=execute)
    assert exc.value.code == "decision.id_conflict"


def test_no_action_decision_deadlines_evidence_and_close_case():
    manager = IssueManager()
    manager.configure(definitions(), generation=1, action_ids={"patrol"}, gameplay=gameplay())
    first = opened(manager, sim_time=0)
    issue_id = first["issue"]["issue_id"]
    case_id = first["case"]["case_id"]
    request_id = first["decision_requests"][0]["decision_request_id"]
    added = manager.append_evidence(issue_id, {"evidence_id": "extra", "kind": "photo", "summary": "Image"}, sim_time=5)
    assert len(added["issue"]["evidence"]) == 2
    result = manager.resolve_decision(
        request_id, {"decision_id": "monitor-1", "option_id": "monitor"}, sim_time=6,
        action_preview=lambda *_: pytest.fail("no action preview expected"),
        action_execute=lambda *_: pytest.fail("no action execute expected"),
    )
    assert result["case"]["status"] == "monitoring"
    closed = manager.close_case(case_id, {"status": "closed", "summary": "No threat"}, sim_time=7)
    assert closed["case"]["outcome"]["summary"] == "No threat"

    second = manager.open_issue({
        "external_id": "report-2", "source": {"kind": "agent", "id": "guard"},
        "summary": "Another", "evidence": [{"kind": "report"}],
    }, sim_time=0)
    second_request = second["decision_requests"][0]["decision_request_id"]
    assert manager.list_decision_requests(sim_time=3 * 86400, status="expired")[0]["decision_request_id"] == second_request
    assert manager.list_issues(sim_time=4 * 86400, status="overdue")


def test_snapshot_restore_and_legacy_memorial_migration():
    manager = IssueManager()
    manager.configure(definitions(), generation=2, action_ids={"patrol"}, gameplay=gameplay())
    first = opened(manager)
    snapshot = manager.snapshot()
    restored = IssueManager()
    restored.configure(definitions(), generation=3, action_ids={"patrol"}, gameplay=gameplay())
    restored.restore(snapshot, sim_time=10)
    assert restored.list_issues(sim_time=10)[0]["issue_id"] == first["issue"]["issue_id"]
    assert restored.public_state(sim_time=10)["generation"] == 3

    legacy = IssueManager()
    legacy.configure([], generation=0, gameplay=GameplayContract())
    legacy.restore({}, sim_time=5, legacy_memorials=[{"memorial_id": "m1", "from": "agent", "content": "Legacy report", "received_at": 2}])
    assert legacy.list_issues(sim_time=5)[0]["summary"] == "Legacy report"
    assert legacy.legacy_memorials(sim_time=5)[0]["content"] == "Legacy report"


def test_decision_option_executes_real_action_catalog_and_charges_anchored_cost_once(tmp_path):
    world = WorldState(str(tmp_path / "decision-action.db"))
    world.apply_dlc_schema_update(
        entities=[Entity(id="station", type="location", name="Station")],
        inventory=[InventoryItem(entity_id="station", category="credits", item_name="credits", quantity=10)],
        resource_definitions=[ResourceDefinition(name="credits", kind="asset", anchor_rate=2)],
    )
    contract = gameplay()
    catalog = ActionCatalog()
    catalog.configure([{
        "action_id": "patrol",
        "display_name": "Patrol",
        "parameters": [{"name": "gain", "type": "number", "required": False, "default": 5}],
        "target": {"kind": "none", "required": False},
        "permissions": {
            "control_modes": ["direct"],
            "control_kinds": ["entity"],
            "control_ids": ["station"],
            "actor_ids": ["player"],
        },
        "costs": [{
            "entity_id": "$control",
            "resource_name": "credits",
            "base_value": 8,
            "reason": "decision patrol",
        }],
        "duration": {"action_type": "patrol", "proposed_days": 1},
        "result": {"mode": "wake_agent", "agent_id": "guard", "payload": {"message": "patrol"}},
    }], generation=4, gameplay=contract)

    manager = IssueManager()
    manager.configure(definitions(), generation=4, action_ids=catalog.action_ids, gameplay=contract)
    request_id = opened(manager)["decision_requests"][0]["decision_request_id"]
    timer = TimeSimulator()
    wakeups = []

    def preview(action_id, invocation):
        return catalog.preview(
            action_id, invocation, world_state=world, time_simulator=timer,
            sim_time=20, available_agent_ids={"guard"},
        )

    def execute(action_id, invocation):
        return catalog.execute(
            action_id, invocation, world_state=world, time_simulator=timer,
            sim_time=20, available_agent_ids={"guard"},
            wake_callback=lambda target, payload, metadata: wakeups.append((target, payload, metadata)),
        )

    payload = {
        "decision_id": "real-action-1",
        "expected_generation": 4,
        "option_id": "patrol",
    }
    first = manager.resolve_decision(
        request_id, payload, sim_time=20, action_preview=preview, action_execute=execute,
    )
    quantity_after_first = world.get_inventory("station")[0].quantity
    replay = manager.resolve_decision(
        request_id, payload, sim_time=30, action_preview=preview, action_execute=execute,
    )

    assert first["action_result"]["status"] == "executed"
    assert quantity_after_first == 6
    assert world.get_inventory("station")[0].quantity == 6
    assert replay["replayed"] is True
    assert len(wakeups) == 1
