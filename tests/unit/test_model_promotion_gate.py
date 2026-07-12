"""Tests for secret-free reviewed model-policy promotion gates."""

import json
from pathlib import Path

import pytest

from multiagent_framework.model_promotion_gate import (
    ModelPromotionGateError,
    evaluate_model_promotion,
    main,
)


BASELINE_ID = "a" * 64


def config(**overrides):
    value = {
        "schema_version": "1.0",
        "gate_id": "daming-model-promotion-v1",
        "environment_id": "staging",
        "required_matrix_id": "daming-live-model-matrix-v1",
        "required_suite_id": "daming-live-trajectories-v1",
        "minimum_repetitions": 3,
        "minimum_reviewers_per_target": 2,
        "minimum_review_coverage": 1.0,
        "maximum_review_disagreement": 15,
        "required_targets": [
            {"cohort": "daming-core", "task": "agent.turn"},
        ],
        "require_scale_gate": True,
        "require_operational_gate": True,
    }
    value.update(overrides)
    return value


def cell(profile="alpha", disagreement=4, status="reviewed", passed=True):
    return {
        "cell_id": f"daming-core::agent.turn::{profile}",
        "cohort": "daming-core",
        "task_type": "agent.turn",
        "profile": profile,
        "provider": "provider-a",
        "model": f"model-{profile}",
        "runs": 3,
        "passed_runs": 3 if passed else 2,
        "review_coverage": 1.0,
        "review_disagreement_max": disagreement,
        "recommendation_status": status,
    }


def report(**overrides):
    value = {
        "schema_version": "1.0",
        "matrix_id": "daming-live-model-matrix-v1",
        "suite_id": "daming-live-trajectories-v1",
        "mode": "live",
        "repetitions": 3,
        "passed": True,
        "approved": True,
        "cells": [cell("alpha"), cell("beta")],
        "rankings": [{
            "cohort": "daming-core",
            "task_type": "agent.turn",
            "ordered_profiles": ["alpha", "beta"],
            "recommended_profile": "alpha",
            "status": "reviewed",
        }],
        "review_summary": {
            "minimum_coverage": 1.0,
            "minimum_reviewers_per_target": 2,
            "maximum_review_disagreement": 15,
            "reviewed_targets": 4,
            "total_targets": 4,
            "coverage": 1.0,
            "review_count": 8,
            "dimensions": [
                "correctness",
                "tool_discipline",
                "memory_quality",
                "world_consistency",
                "latency_value",
                "cost_value",
            ],
        },
    }
    value.update(overrides)
    return value


def patch(**overrides):
    value = {
        "schema_version": "1.0",
        "matrix_id": "daming-live-model-matrix-v1",
        "task_policies": {},
        "cohort_task_policies": {
            "daming-core": {"agent.turn": ["alpha"]},
        },
    }
    value.update(overrides)
    return value


def gate(passed=True, environment_id="staging"):
    return {
        "schema_version": "1.0",
        "baseline_id": BASELINE_ID,
        "environment_id": environment_id,
        "passed": passed,
    }


def evaluate(**kwargs):
    return evaluate_model_promotion(
        kwargs.pop("config_value", config()),
        kwargs.pop("report_value", report()),
        kwargs.pop("patch_value", patch()),
        scale_gate=kwargs.pop("scale_value", gate()),
        operational_gate=kwargs.pop("operational_value", gate()),
        **kwargs,
    )


def test_approved_gate_combines_reviewed_matrix_policy_and_environment_evidence():
    result = evaluate()

    assert result["approved"]
    assert result["decision"] == "approved"
    assert result["reason_codes"] == []
    assert result["reviewer_quorum"] == 2
    assert result["review_coverage"] == 1
    assert result["maximum_review_disagreement_observed"] == 4
    assert result["policy_patch"]["task_policies"] == {}
    assert result["policy_patch"]["cohort_task_policies"] == {
        "daming-core": {"agent.turn": ["alpha"]}
    }
    assert len(result["matrix_fingerprint"]) == 64
    assert len(result["policy_fingerprint"]) == 64


def test_unapproved_matrix_is_a_valid_blocked_decision_not_an_input_error():
    report_value = report(
        passed=False,
        approved=False,
        cells=[cell("alpha", status="provisional", passed=False)],
        rankings=[{
            "cohort": "daming-core",
            "task_type": "agent.turn",
            "ordered_profiles": ["alpha"],
            "recommended_profile": "alpha",
            "status": "provisional",
        }],
        review_summary={
            "minimum_coverage": 1.0,
            "minimum_reviewers_per_target": 2,
            "maximum_review_disagreement": 15,
            "reviewed_targets": 0,
            "total_targets": 1,
            "coverage": 0.0,
            "review_count": 1,
        },
    )
    patch_value = patch(cohort_task_policies={})

    result = evaluate(report_value=report_value, patch_value=patch_value)

    assert not result["approved"]
    assert result["decision"] == "blocked"
    assert "matrix_automated_checks_failed" in result["reason_codes"]
    assert "matrix_not_review_approved" in result["reason_codes"]
    assert "ranking_not_reviewed" in result["reason_codes"]
    assert "review_targets_incomplete" in result["reason_codes"]


def test_policy_patch_must_exactly_match_reviewed_rankings():
    patch_value = patch(
        cohort_task_policies={"daming-core": {"agent.turn": ["beta"]}}
    )
    with pytest.raises(
        ModelPromotionGateError, match="policy_patch_does_not_match_rankings"
    ):
        evaluate(patch_value=patch_value)


def test_required_environment_gate_missing_or_failed_blocks_promotion():
    missing = evaluate(scale_value=None)
    failed = evaluate(operational_value=gate(False))

    assert missing["reason_codes"] == ["scale_gate_missing"]
    assert failed["reason_codes"] == ["operational_gate_failed"]


def test_environment_gate_must_target_same_environment():
    with pytest.raises(ModelPromotionGateError, match="environment_mismatch"):
        evaluate(scale_value=gate(environment_id="production"))


def test_required_target_coverage_and_additional_target_policy_are_enforced():
    missing = evaluate(
        config_value=config(required_targets=[
            {"cohort": "daming-core", "task": "memory.compress"},
        ])
    )
    additional = evaluate(
        config_value=config(required_targets=[
            {"cohort": "daming-core", "task": "agent.turn"},
            {"cohort": "daming-core", "task": "memory.compress"},
        ])
    )

    assert "required_matrix_targets_missing" in missing["reason_codes"]
    assert "unexpected_matrix_targets_present" in missing["reason_codes"]
    assert "required_matrix_targets_missing" in additional["reason_codes"]


def test_permissive_or_disputed_review_policy_blocks_promotion():
    report_value = report()
    report_value["review_summary"]["maximum_review_disagreement"] = 25
    report_value["cells"][0]["review_disagreement_max"] = 20

    result = evaluate(report_value=report_value)

    assert "matrix_disagreement_policy_too_permissive" in result["reason_codes"]
    assert "cell_review_disagreement_exceeded" in result["reason_codes"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"minimum_reviewers_per_target": 1.5}),
        lambda value: value.update({"maximum_review_disagreement": float("nan")}),
        lambda value: value.update({"required_targets": []}),
        lambda value: value.update({
            "required_targets": [
                {"cohort": "daming-core", "task": "agent.turn"},
                {"cohort": "daming-core", "task": "agent.turn"},
            ]
        }),
    ],
)
def test_gate_config_schema_is_strict(mutation):
    config_value = config()
    mutation(config_value)
    with pytest.raises(ModelPromotionGateError):
        evaluate(config_value=config_value)


def test_safe_model_metadata_supports_namespaced_model_identifiers():
    report_value = report()
    report_value["cells"][0]["model"] = "vendor/model-alpha:v2"

    assert evaluate(report_value=report_value)["approved"]


def test_sensitive_content_is_rejected_without_copying_it_to_output():
    report_value = report()
    report_value["messages"] = [{"role": "user", "content": "private"}]
    with pytest.raises(
        ModelPromotionGateError, match="matrix_report_contains_forbidden_content"
    ):
        evaluate(report_value=report_value)


def test_optional_environment_gates_can_be_omitted():
    result = evaluate(
        config_value=config(
            require_scale_gate=False,
            require_operational_gate=False,
        ),
        scale_value=None,
        operational_value=None,
    )

    assert result["approved"]
    assert not result["prerequisites"]["scale"]["present"]
    assert not result["prerequisites"]["operational"]["present"]


def test_cli_writes_atomic_secret_free_result_and_uses_documented_exit_codes(
    tmp_path, capsys
):
    paths = {}
    for name, value in (
        ("config", config()),
        ("matrix", report()),
        ("patch", patch()),
        ("scale", gate()),
        ("operational", gate()),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    output = tmp_path / "promotion.json"

    status = main([
        "--config", str(paths["config"]),
        "--matrix-report", str(paths["matrix"]),
        "--policy-patch", str(paths["patch"]),
        "--scale-gate", str(paths["scale"]),
        "--operational-gate", str(paths["operational"]),
        "--output", str(output),
    ])

    assert status == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["approved"]
    raw = capsys.readouterr().out.lower()
    for forbidden in ("messages", "api_key", "authorization", "provider_url"):
        assert forbidden not in raw

    blocked_report = report(approved=False)
    paths["matrix"].write_text(json.dumps(blocked_report), encoding="utf-8")
    assert main([
        "--config", str(paths["config"]),
        "--matrix-report", str(paths["matrix"]),
        "--policy-patch", str(paths["patch"]),
        "--scale-gate", str(paths["scale"]),
        "--operational-gate", str(paths["operational"]),
    ]) == 1

    paths["patch"].write_text("{}", encoding="utf-8")
    assert main([
        "--config", str(paths["config"]),
        "--matrix-report", str(paths["matrix"]),
        "--policy-patch", str(paths["patch"]),
    ]) == 2


def test_repository_gate_config_requires_complete_daming_core_task_set():
    payload = json.loads(
        Path("tests/evals/daming_model_promotion_gate.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["minimum_repetitions"] == 3
    assert payload["minimum_reviewers_per_target"] == 2
    assert payload["minimum_review_coverage"] == 1
    assert payload["maximum_review_disagreement"] == 15
    assert payload["require_scale_gate"] is True
    assert payload["require_operational_gate"] is True
    assert {
        (item["cohort"], item["task"])
        for item in payload["required_targets"]
    } == {
        ("daming-core", "agent.turn"),
        ("daming-core", "router.ambiguous"),
        ("daming-core", "action.simulation"),
        ("daming-core", "memory.compress"),
        ("daming-core", "tianji.chat"),
    }
