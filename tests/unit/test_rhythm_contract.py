from __future__ import annotations

import pytest

from multiagent_framework.rhythm_contract import (
    RhythmContract, RhythmContractError, normalize_rhythm_document,
)


def test_contract_normalizes_sorts_and_serializes_rules():
    contract = RhythmContract.from_mapping({
        "enabled": True,
        "max_active_issues": 3,
        "attention_budget": 9,
        "urgency_costs": {"critical": 6},
        "rules": [
            {"rule_id": "late", "event_types": ["storm"], "priority": 1},
            {"rule_id": "first", "issue_type_ids": ["security"], "priority": 10,
             "escalation": {"enabled": True, "max_level": 1, "urgency_by_level": ["normal", "high"]}},
        ],
    })
    assert [rule.rule_id for rule in contract.rules] == ["first", "late"]
    assert contract.rule_for_issue("security").rule_id == "first"
    assert contract.rule_for_event("storm").rule_id == "late"
    assert contract.urgency_costs["normal"] == 2
    assert contract.to_dict()["max_active_issues"] == 3
    assert len(contract.fingerprint()) == 64


def test_contract_rejects_duplicate_invalid_range_and_missing_match():
    with pytest.raises(RhythmContractError) as exc:
        RhythmContract.from_mapping({"rules": [
            {"rule_id": "same", "event_types": ["a"]},
            {"rule_id": "same", "event_types": ["b"]},
        ]})
    assert exc.value.code == "rhythm.rule.duplicate"
    with pytest.raises(RhythmContractError) as exc:
        RhythmContract.from_mapping({"max_active_issues": 0})
    assert exc.value.code == "rhythm.max_active_invalid"
    with pytest.raises(RhythmContractError) as exc:
        RhythmContract.from_mapping({"rules": [{"rule_id": "empty"}]})
    assert exc.value.code == "rhythm.rule.match_required"


def test_disabled_nested_transitions_do_not_constrain_escalation_level():
    contract = RhythmContract.from_mapping({
        "rules": [{
            "rule_id": "small", "issue_type_ids": ["security"],
            "escalation": {"enabled": True, "max_level": 1, "urgency_by_level": ["normal", "high"]},
        }],
    })
    assert contract.rules[0].climax.enabled is False
    assert normalize_rhythm_document(None) == {}
