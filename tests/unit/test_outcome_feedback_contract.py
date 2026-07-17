from __future__ import annotations

import pytest

from multiagent_framework.outcome_feedback_contract import (
    OutcomeFeedbackContract,
    OutcomeFeedbackContractError,
)


def document() -> dict:
    return {
        "enabled": True,
        "recent_limit": 4,
        "pending_limit": 5,
        "receipt_limit": 16,
        "metric_delta_epsilon": 0.1,
        "immediate": {"enabled": True, "max_changes": 3, "max_metric_changes": 2},
        "case_summaries": {"enabled": True, "include_decisions": True},
        "rules": [
            {
                "rule_id": "supply_followup",
                "display_name": "Supply follow-up",
                "priority": 20,
                "source_kinds": ["action", "causal_event"],
                "delivery": "delayed",
                "action_ids": ["allocate_supply"],
                "metric_ids": ["supply_stability"],
                "delay_days": 2,
                "conditions": [{"kind": "time", "field": "sim_day", "operator": "gte", "value": 1}],
                "title": "Supply report",
                "summary": "The delayed logistics report is ready.",
            },
            {
                "rule_id": "hidden_cause",
                "display_name": "Hidden cause",
                "source_kinds": ["causal_event"],
                "delivery": "causal_reveal",
                "event_types": ["supply_shock"],
                "reveal_text": "A prior shortage caused the later disruption.",
            },
        ],
    }


def test_contract_round_trip_fingerprint_and_safe_public_state():
    contract = OutcomeFeedbackContract.from_mapping(document())
    cloned = OutcomeFeedbackContract.from_mapping(contract.to_dict())
    assert contract.enabled is True
    assert contract.rules[0].rule_id == "supply_followup"
    assert contract.fingerprint() == cloned.fingerprint()
    public = contract.public_state(generation=7)
    assert public["generation"] == 7
    assert public["rules"][0]["delivery"] == "delayed"
    assert "conditions" not in public["rules"][0]
    assert "reveal_text" not in public["rules"][1]


def test_contract_rejects_duplicate_ids_and_missing_reveal_text():
    duplicate = document()
    duplicate["rules"].append(dict(duplicate["rules"][0]))
    with pytest.raises(OutcomeFeedbackContractError) as exc:
        OutcomeFeedbackContract.from_mapping(duplicate)
    assert exc.value.code == "outcome_feedback.rule.duplicate_id"

    invalid = document()
    invalid["rules"][1]["reveal_text"] = ""
    with pytest.raises(OutcomeFeedbackContractError) as exc:
        OutcomeFeedbackContract.from_mapping(invalid)
    assert exc.value.code == "outcome_feedback.rule.reveal_required"


def test_disabled_legacy_contract_is_stable():
    contract = OutcomeFeedbackContract.from_mapping({})
    assert contract.enabled is False
    assert contract.rules == ()
    assert contract.public_state()["configured"] is False
