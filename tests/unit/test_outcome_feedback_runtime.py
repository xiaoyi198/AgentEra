from __future__ import annotations

from multiagent_framework.causal_conditions import CausalConditionContext
from multiagent_framework.outcome_feedback_contract import DAY, OutcomeFeedbackContract
from multiagent_framework.outcome_feedback_runtime import OutcomeFeedbackRuntime


def contract(*, recent_limit: int = 8, pending_limit: int = 8) -> OutcomeFeedbackContract:
    return OutcomeFeedbackContract.from_mapping({
        "enabled": True,
        "recent_limit": recent_limit,
        "pending_limit": pending_limit,
        "receipt_limit": 32,
        "metric_delta_epsilon": 0.01,
        "rules": [
            {
                "rule_id": "action_report",
                "display_name": "Action report",
                "source_kinds": ["action"],
                "delivery": "delayed",
                "action_ids": ["allocate_supply"],
                "delay_days": 1,
                "title": "Follow-up report",
                "summary": "The follow-up report is ready.",
            },
            {
                "rule_id": "cause_reveal",
                "display_name": "Cause reveal",
                "source_kinds": ["causal_event"],
                "delivery": "causal_reveal",
                "event_types": ["supply_shock"],
                "conditions": [{"kind": "time", "field": "sim_day", "operator": "gte", "value": 2}],
                "reveal_text": "The shortage was caused by an earlier disruption.",
            },
        ],
    })


def test_action_immediate_feedback_is_safe_and_idempotent():
    runtime = OutcomeFeedbackRuntime()
    runtime.configure(contract(), generation=3, preserve_state=False)
    payload = dict(
        source_kind="action", source_id="inv-1", receipt_id="action:inv-1", sim_time=0,
        display_name="Allocate supply", action_id="allocate_supply", summary="Supply allocation completed.",
        changes=[{"entity_id": "depot", "quantity_delta": -2, "command_payload": {"secret": True}}],
        metric_changes=[{"metric_id": "supply_stability", "display_name": "Stability", "before": 20, "after": 30, "delta": 10, "formula": "secret"}],
        details={"action_id": "allocate_supply", "prompt": "secret"},
    )
    first = runtime.record(**payload)
    second = runtime.record(**payload)
    assert [item["kind"] for item in first["delivered"]] == ["immediate"]
    assert len(first["queued"]) == 1
    assert second["delivered"] == []
    assert second["queued"] == []
    public = runtime.public_state()
    immediate = public["recent"][0]
    assert immediate["changes"][0] == {"entity_id": "depot", "quantity_delta": -2}
    assert "formula" not in immediate["metric_changes"][0]
    assert immediate["details"] == {"action_id": "allocate_supply"}
    assert all("conditions" not in item for item in public["pending"])


def test_delayed_report_delivers_only_when_due_and_once_after_restore():
    runtime = OutcomeFeedbackRuntime()
    runtime.configure(contract(), generation=1, preserve_state=False)
    runtime.record(
        source_kind="action", source_id="inv-1", receipt_id="action:inv-1", sim_time=0,
        display_name="Allocate supply", action_id="allocate_supply",
    )
    assert runtime.poll(sim_time=DAY - 1)["delivered"] == []
    saved = runtime.snapshot()

    restored = OutcomeFeedbackRuntime()
    restored.configure(contract(), generation=1, preserve_state=False)
    restored.restore(saved)
    delivered = restored.poll(sim_time=DAY)["delivered"]
    assert len(delivered) == 1
    assert delivered[0]["kind"] == "delayed_report"
    assert restored.poll(sim_time=DAY * 2)["delivered"] == []


def test_causal_reveal_waits_for_due_condition_without_exposing_it():
    runtime = OutcomeFeedbackRuntime()
    runtime.configure(contract(), generation=2, preserve_state=False)
    runtime.record(
        source_kind="causal_event", source_id="event-1", receipt_id="event:event-1",
        sim_time=0, event_type="supply_shock", display_name="Supply shock",
    )
    assert runtime.poll(sim_time=DAY, condition_context=CausalConditionContext(sim_time=DAY))["delivered"] == []
    delivered = runtime.poll(sim_time=DAY * 2, condition_context=CausalConditionContext(sim_time=DAY * 2))["delivered"]
    assert delivered[0]["kind"] == "causal_reveal"
    assert "earlier disruption" in delivered[0]["reveal_text"]
    assert "conditions" not in runtime.public_state()["recent"][0]


def test_case_summary_and_strict_bounds():
    runtime = OutcomeFeedbackRuntime()
    runtime.configure(contract(recent_limit=2, pending_limit=1), generation=1, preserve_state=False)
    for index in range(3):
        runtime.record(
            source_kind="case_closed", source_id=f"case-{index}", receipt_id=f"case:{index}",
            sim_time=float(index), display_name=f"Case {index}", summary="Closed.",
            decisions=[{"decision_id": "x"}], changes=[{"new_status": "closed"}],
        )
    state = runtime.public_state()
    assert state["recent_count"] == 2
    assert [item["source"]["id"] for item in state["recent"]] == ["case-1", "case-2"]


def test_hot_reload_keeps_only_pending_rules_with_same_fingerprint():
    runtime = OutcomeFeedbackRuntime()
    original = contract()
    runtime.configure(original, generation=1, preserve_state=False)
    runtime.record(
        source_kind="action", source_id="inv-1", receipt_id="action:inv-1", sim_time=0,
        display_name="Allocate supply", action_id="allocate_supply",
    )
    assert runtime.public_state()["pending_count"] == 1

    changed_doc = original.to_dict()
    changed_doc["rules"][0]["delay_days"] = 3
    runtime.configure(OutcomeFeedbackContract.from_mapping(changed_doc), generation=2, preserve_state=True)
    assert runtime.public_state()["pending_count"] == 0
