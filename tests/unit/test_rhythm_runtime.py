from __future__ import annotations

import pytest

from multiagent_framework.causal_conditions import CausalConditionContext
from multiagent_framework.issue_contract import IssueContractError, IssueManager
from multiagent_framework.rhythm_contract import DAY, RhythmContract
from multiagent_framework.rhythm_runtime import RhythmRuntime


def definitions(*type_ids: str) -> list[dict]:
    return [{
        "issue_type_id": type_id,
        "display_name": type_id,
        "default_urgency": "normal",
        "source": {"allowed_kinds": ["system"]},
        "decision_requests": [{
            "request_id": "review", "title": "Review",
            "options": [{
                "option_id": "done", "display_name": "Done",
                "resolution": {"issue_status": "resolved", "case_status": "resolved"},
            }],
        }],
    } for type_id in type_ids]


def manager_with(contract: RhythmContract, *type_ids: str) -> tuple[IssueManager, RhythmRuntime]:
    manager = IssueManager()
    manager.configure(definitions(*type_ids), generation=1)
    runtime = RhythmRuntime(contract, generation=1)
    manager.set_rhythm_controller(runtime)
    return manager, runtime


def open_issue(manager: IssueManager, type_id: str, external_id: str, *, at: float = 0, urgency: str = "normal") -> dict:
    return manager.open_issue({
        "external_id": external_id, "issue_type_id": type_id,
        "source": {"kind": "system", "id": "test"},
        "summary": external_id, "urgency": urgency,
    }, sim_time=at)


def test_global_cap_attention_queue_recovery_priority_and_idempotency():
    contract = RhythmContract.from_mapping({
        "enabled": True, "max_active_issues": 1,
        "attention_budget": 1, "attention_recovery_per_day": 1,
        "urgency_costs": {"low": 1, "normal": 1, "high": 1, "critical": 1},
        "rules": [
            {"rule_id": "low", "issue_type_ids": ["low"], "priority": 1, "max_active": 2},
            {"rule_id": "high", "issue_type_ids": ["high"], "priority": 10, "max_active": 2},
        ],
    })
    manager, runtime = manager_with(contract, "low", "high")
    first = open_issue(manager, "low", "first")
    assert first["issue"]["status"] == "open"
    assert runtime.attention_available == 0
    replay = open_issue(manager, "low", "first", at=100)
    assert replay["replayed"] is True
    assert runtime.attention_available == 0
    low = open_issue(manager, "low", "queued-low")
    high = open_issue(manager, "high", "queued-high")
    assert low["issue"]["status"] == high["issue"]["status"] == "queued"
    assert low["decision_requests"][0]["status"] == "queued"
    manager.rhythm_update_issue(first["issue"]["issue_id"], sim_time=1, status="resolved", case_status="resolved")
    poll = runtime.poll(DAY, issue_manager=manager)
    assert [item["issue_type_id"] for item in poll.activated] == ["high"]
    assert manager.list_issues(sim_time=DAY, status="queued")[0]["issue_type_id"] == "low"
    assert manager.public_state(sim_time=DAY)["counts"]["open_issues"] == 1


def test_rule_limit_cooldown_mutual_exclusion_and_event_suppression():
    contract = RhythmContract.from_mapping({
        "enabled": True, "max_active_issues": 8, "attention_budget": 20,
        "rules": [
            {"rule_id": "storm", "issue_type_ids": ["storm"], "event_types": ["storm"],
             "max_active": 1, "cooldown_days": 2, "mutual_exclusion_group": "weather"},
            {"rule_id": "fire", "issue_type_ids": ["fire"], "event_types": ["fire"],
             "max_active": 2, "mutual_exclusion_group": "weather"},
        ],
    })
    manager, runtime = manager_with(contract, "storm", "fire")
    assert runtime.allow_event("storm", sim_time=0, issue_manager=manager)["allowed"] is True
    blocked_event = runtime.allow_event("storm", sim_time=DAY, issue_manager=manager)
    assert blocked_event == {"allowed": False, "managed": True, "rule_id": "storm", "reason": "cooldown"}
    first = open_issue(manager, "storm", "storm-1")
    second = open_issue(manager, "storm", "storm-2")
    other = open_issue(manager, "fire", "fire-1")
    assert first["issue"]["status"] == "open"
    assert second["issue"]["rhythm"]["queue_reason"] == "rule_active_limit"
    assert other["issue"]["rhythm"]["queue_reason"] == "mutual_exclusion"


def test_critical_bypasses_quiet_and_queue_limit_rejects():
    contract = RhythmContract.from_mapping({
        "enabled": True, "max_active_issues": 4, "attention_budget": 20,
        "queue_limit": 1, "critical_bypasses_quiet": True,
        "rules": [{"rule_id": "risk", "issue_type_ids": ["risk"], "max_active": 4}],
    })
    manager, runtime = manager_with(contract, "risk")
    runtime.quiet_until = 2 * DAY
    queued = open_issue(manager, "risk", "normal")
    assert queued["issue"]["status"] == "queued"
    critical = open_issue(manager, "risk", "critical", urgency="critical")
    assert critical["issue"]["status"] == "open"
    with pytest.raises(IssueContractError) as exc:
        open_issue(manager, "risk", "overflow")
    assert exc.value.code == "issue.rhythm_queue_full"


def test_escalation_deescalation_climax_and_snapshot_restore():
    contract = RhythmContract.from_mapping({
        "enabled": True, "max_active_issues": 4, "attention_budget": 20,
        "rules": [{
            "rule_id": "crisis", "issue_type_ids": ["crisis"], "max_active": 2,
            "escalation": {
                "enabled": True, "after_days": 1, "interval_days": 1,
                "max_level": 2, "urgency_by_level": ["normal", "high", "critical"],
            },
            "deescalation": {
                "enabled": True, "interval_days": 0.5, "target_level": 0,
                "conditions": [{"kind": "time", "name": "days", "operator": "eq", "value": 1.5}],
            },
            "climax": {
                "enabled": True, "level": 2, "after_days": 0.5,
                "quiet_period_days": 3, "issue_status": "resolved", "case_status": "resolved",
            },
        }],
    })
    manager, runtime = manager_with(contract, "crisis")
    opened = open_issue(manager, "crisis", "crisis-1")
    issue_id = opened["issue"]["issue_id"]
    first = runtime.poll(DAY, issue_manager=manager, condition_context=CausalConditionContext(sim_time=DAY))
    assert first.transitions[0]["kind"] == "escalation"
    assert manager.list_issues(sim_time=DAY)[0]["urgency"] == "high"
    down = runtime.poll(1.5 * DAY, issue_manager=manager, condition_context=CausalConditionContext(sim_time=1.5 * DAY))
    assert down.transitions[0]["kind"] == "deescalation"
    assert manager.list_issues(sim_time=1.5 * DAY)[0]["rhythm"]["level"] == 0
    runtime.poll(2.5 * DAY, issue_manager=manager, condition_context=CausalConditionContext(sim_time=2.5 * DAY))
    up2 = runtime.poll(3.5 * DAY, issue_manager=manager, condition_context=CausalConditionContext(sim_time=3.5 * DAY))
    assert any(item["kind"] == "escalation" for item in up2.transitions)
    climax = runtime.poll(4 * DAY, issue_manager=manager, condition_context=CausalConditionContext(sim_time=4 * DAY))
    assert any(item["kind"] == "climax" for item in climax.transitions)
    assert manager.list_issues(sim_time=4 * DAY)[0]["status"] == "resolved"
    assert manager.list_cases(sim_time=4 * DAY)[0]["status"] == "resolved"
    assert manager.list_decision_requests(sim_time=4 * DAY)[0]["status"] == "cancelled"
    assert runtime.quiet_until == 7 * DAY

    snapshot = runtime.snapshot()
    restored = RhythmRuntime(contract, generation=1)
    restored.restore(snapshot, sim_time=4 * DAY)
    assert restored.public_state(issue_manager=manager, sim_time=4 * DAY)["quiet_active"] is True
