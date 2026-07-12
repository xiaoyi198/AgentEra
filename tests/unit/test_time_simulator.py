import math
import pytest

from multiagent_framework.time_simulator import (
    ActionTimeRule, TimeRuleError, TimeSimulator,
)


def test_rule_validation_rejects_unsafe_and_ambiguous_values():
    bad = [
        {"rule_id": ""},
        {"rule_id": "bad/id"},
        {"rule_id": "ok", "action_types": "policy"},
        {"rule_id": "ok", "action_types": ["policy", "policy"]},
        {"rule_id": "ok", "enabled": 1},
        {"rule_id": "ok", "priority": True},
        {"rule_id": "ok", "base_days": True},
        {"rule_id": "ok", "min_days": 2, "max_days": 1},
        {"rule_id": "ok", "max_days": math.inf},
    ]
    for raw in bad:
        with pytest.raises(TimeRuleError):
            ActionTimeRule.from_dict(raw)


def test_configuration_is_atomic_and_fingerprinted():
    sim = TimeSimulator([{"rule_id": "a", "action_types": ["*"]}])
    before = sim.fingerprint
    with pytest.raises(TimeRuleError):
        sim.configure([{"rule_id": "a"}, {"rule_id": "a"}])
    assert sim.fingerprint == before
    assert [rule.rule_id for rule in sim.rules] == ["a"]
    sim.configure([{"rule_id": "a", "action_types": ["*"], "base_days": 1}])
    assert sim.fingerprint != before


def test_specific_rule_beats_wildcard_then_priority_and_id_are_deterministic():
    sim = TimeSimulator([
        {"rule_id": "wild", "action_types": ["*"], "priority": 99, "base_days": 20, "workload_factor": 0},
        {"rule_id": "z", "action_types": ["policy"], "priority": 1, "base_days": 3, "workload_factor": 0},
        {"rule_id": "a", "action_types": ["policy"], "priority": 1, "base_days": 2, "workload_factor": 0},
    ])
    estimate = sim.estimate(action_type="policy", proposed_days=100)
    assert estimate.rule_id == "a"
    assert estimate.duration_days == 2


def test_formula_uses_severity_distance_resources_and_failure_factor():
    sim = TimeSimulator([{
        "rule_id": "policy", "action_types": ["policy"],
        "base_days": 2, "workload_factor": 0.5, "severity_factor": 9,
        "distance_factor": 2, "resource_factor": 1, "success_factor": 0.5,
        "min_days": 0, "max_days": 100,
    }])
    estimate = sim.estimate(
        action_type="policy", proposed_days=4, severity=10,
        distance_days=3, resource_value=math.e - 1, success=False,
    )
    # (2 + 2 + 9 + 6 - 1) * 0.5
    assert estimate.duration_days == 9
    assert estimate.components["resource_discount_days"] == 1
    assert estimate.components["success_adjusted"] is True


def test_extreme_or_non_finite_llm_hints_are_replaced_and_clamped():
    sim = TimeSimulator([{
        "rule_id": "safe", "action_types": ["*"],
        "min_days": 0.25, "max_days": 10, "workload_factor": 1,
    }])
    for bad in (-1, math.nan, math.inf, True, "100"):
        estimate = sim.estimate(action_type="anything", proposed_days=bad)
        assert estimate.duration_days == 1
        assert estimate.components["invalid_hint_replaced"] is True
    assert sim.estimate(action_type="anything", proposed_days=999).duration_days == 10


def test_public_metadata_contains_no_action_or_entity_text():
    estimate = TimeSimulator().estimate(
        action_type="secret-action", proposed_days=2, resource_value=5, distance_days=1,
    )
    metadata = estimate.public_metadata()
    serialized = repr(metadata)
    assert "secret-action" not in serialized
    assert set(metadata) == {"duration_days", "rule_id", "components", "clamped", "source"}
