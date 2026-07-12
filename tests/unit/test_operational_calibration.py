from __future__ import annotations

import copy
import json

import pytest

from multiagent_framework.operational_calibration import (
    OperationalCalibrationError,
    calibrate_operational_snapshots,
    compare_operational_snapshot,
    main,
)
from multiagent_framework.operational_metrics import default_alert_thresholds


def make_snapshot(index: int = 0, **overrides):
    rates = {
        "llm_error_rate": 0.01 + index * 0.001,
        "llm_fallback_rate": 0.02 + index * 0.001,
        "dlc_reload_failure_rate": 0.01,
        "world_command_rejection_rate": 0.03 + index * 0.001,
        "agent_turn_error_rate": 0.01,
        "busy_agent_ratio": 0.20 + index * 0.01,
    }
    rates.update(overrides.pop("rates", {}))
    snapshot = {
        "schema_version": "1.0",
        "status": "healthy",
        "collected_at": 1000.0 + index,
        "metrics": {
            "rates": rates,
            "llm": {"totals": {"calls": 100 + index}},
            "dlc": {
                "metrics": {"attempts": 20 + index, "max_duration_ms": 100 + index},
                "archive": {"issue_count": 0},
            },
            "world_commands": {"persisted_total": 200 + index},
            "agent_turns": {"agent_turns": 80 + index},
            "agents": {"total": 10, "busy": 2},
        },
        "thresholds": default_alert_thresholds(),
        "alerts": [],
    }
    for key, value in overrides.items():
        snapshot[key] = value
    return snapshot


def make_baseline():
    return calibrate_operational_snapshots(
        [make_snapshot(index) for index in range(5)],
        environment_id="local-fixture",
    )


def test_calibrates_all_signals_with_safety_caps_and_stable_id():
    snapshots = [make_snapshot(index) for index in range(5)]
    first = calibrate_operational_snapshots(snapshots, environment_id="local-fixture")
    second = calibrate_operational_snapshots(snapshots, environment_id="local-fixture")

    assert first["ready"]
    assert first["sample_count"] == 5
    assert first["baseline_id"] == second["baseline_id"]
    assert list(first["signals"]) == list(default_alert_thresholds())
    assert first["recommended_alert_thresholds"]["archive_issues"] == 0
    for name, threshold in first["recommended_alert_thresholds"].items():
        assert threshold <= default_alert_thresholds()[name]


def test_zero_baseline_gets_small_ratio_margin_not_unbounded_default():
    snapshots = [make_snapshot(index, rates={name: 0 for name in (
        "llm_error_rate", "llm_fallback_rate", "dlc_reload_failure_rate",
        "world_command_rejection_rate", "agent_turn_error_rate", "busy_agent_ratio"
    )}) for index in range(3)]
    baseline = calibrate_operational_snapshots(snapshots, environment_id="zero")

    assert baseline["recommended_alert_thresholds"]["llm_error_rate"] == 0.01
    assert baseline["recommended_alert_thresholds"]["busy_agent_ratio"] == 0.01


def test_policy_exceedance_requires_review_even_when_statistical_outlier():
    snapshots = [make_snapshot(index) for index in range(5)]
    snapshots[-1]["metrics"]["rates"]["llm_error_rate"] = 0.9
    baseline = calibrate_operational_snapshots(snapshots, environment_id="incident")

    signal = baseline["signals"]["llm_error_rate"]
    assert not baseline["ready"]
    assert not signal["ready"]
    assert signal["observed_policy_exceedances"] == 1
    assert signal["statistics"]["outlier_count"] == 1
    assert signal["recommended_threshold"] <= 0.1


def test_archive_issue_always_requires_review_and_never_becomes_normal():
    snapshots = [make_snapshot(index) for index in range(3)]
    snapshots[1]["metrics"]["dlc"]["archive"]["issue_count"] = 1
    baseline = calibrate_operational_snapshots(snapshots, environment_id="archive")

    assert not baseline["ready"]
    assert baseline["recommended_alert_thresholds"]["archive_issues"] == 0
    assert baseline["signals"]["archive_issues"]["observed_policy_exceedances"] == 1


@pytest.mark.parametrize("count", [0, 1, 2])
def test_requires_three_snapshots(count):
    with pytest.raises(OperationalCalibrationError, match="at least 3 snapshots"):
        calibrate_operational_snapshots(
            [make_snapshot(index) for index in range(count)], environment_id="local"
        )


@pytest.mark.parametrize("environment_id", ["", "with space", "../path", "line\nbreak", "x" * 65])
def test_environment_id_is_safe(environment_id):
    with pytest.raises(OperationalCalibrationError, match="environment_id"):
        calibrate_operational_snapshots(
            [make_snapshot(index) for index in range(3)], environment_id=environment_id
        )


def test_duplicate_snapshot_is_rejected_instead_of_inflating_sample_count():
    snapshots = [make_snapshot(0), make_snapshot(1), make_snapshot(2)]
    snapshots[2]["collected_at"] = snapshots[1]["collected_at"]

    with pytest.raises(OperationalCalibrationError, match="must be unique"):
        calibrate_operational_snapshots(snapshots, environment_id="local")


def test_each_rate_requires_real_exposure():
    snapshots = [make_snapshot(index) for index in range(3)]
    for snapshot in snapshots:
        snapshot["metrics"]["llm"]["totals"]["calls"] = 0

    with pytest.raises(OperationalCalibrationError, match="llm_error_rate"):
        calibrate_operational_snapshots(snapshots, environment_id="local")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 1.1])
def test_invalid_ratio_is_rejected(bad):
    snapshots = [make_snapshot(index) for index in range(3)]
    snapshots[1]["metrics"]["rates"]["llm_error_rate"] = bad

    with pytest.raises(OperationalCalibrationError):
        calibrate_operational_snapshots(snapshots, environment_id="local")


def test_compare_passes_and_classifies_one_regression():
    baseline = make_baseline()
    candidate = make_snapshot(2)
    candidate["metrics"]["rates"]["llm_error_rate"] = 0.09
    result = compare_operational_snapshot(baseline, candidate)

    assert not result["passed"]
    failed = [item for item in result["signals"] if not item["passed"]]
    assert [item["signal"] for item in failed] == ["llm_error_rate"]
    assert "metrics" not in result
    assert "alerts" not in result


def test_compare_ignores_unexposed_rate_in_a_fresh_process():
    baseline = make_baseline()
    candidate = make_snapshot(20)
    candidate["metrics"]["llm"]["totals"]["calls"] = 0
    candidate["metrics"]["rates"]["llm_error_rate"] = 1
    candidate["metrics"]["rates"]["llm_fallback_rate"] = 1
    result = compare_operational_snapshot(baseline, candidate)

    llm = {item["signal"]: item for item in result["signals"]}
    assert llm["llm_error_rate"]["passed"]
    assert not llm["llm_error_rate"]["exposed"]
    assert llm["llm_fallback_rate"]["passed"]


def test_compare_refuses_unreviewed_or_tampered_baseline():
    baseline = make_baseline()
    with pytest.raises(OperationalCalibrationError, match="requires review"):
        compare_operational_snapshot(dict(baseline, ready=False), make_snapshot())
    with pytest.raises(OperationalCalibrationError, match="environment_id"):
        compare_operational_snapshot(dict(baseline, environment_id="../secret"), make_snapshot())
    with pytest.raises(OperationalCalibrationError, match="baseline_id"):
        compare_operational_snapshot(dict(baseline, baseline_id="secret"), make_snapshot())


def test_reports_do_not_copy_snapshot_secrets_or_paths():
    snapshots = [make_snapshot(index) for index in range(3)]
    for snapshot in snapshots:
        snapshot.update({
            "prompt": "private prompt",
            "message": "private message",
            "error": "private error",
            "api_key": "sk-secret",
            "provider_url": "https://secret.invalid",
            "archive_path": "C:/private/archive",
        })
    baseline = calibrate_operational_snapshots(snapshots, environment_id="safe")
    comparison = compare_operational_snapshot(baseline, make_snapshot(10))
    serialized = json.dumps({"baseline": baseline, "comparison": comparison}).lower()

    for secret in (
        "private prompt", "private message", "private error", "sk-secret",
        "secret.invalid", "private/archive", "provider_url", "api_key"
    ):
        assert secret not in serialized


def test_cli_success_regression_review_and_invalid_exit_codes(tmp_path, capsys):
    paths = []
    for index in range(3):
        path = tmp_path / f"snapshot-{index}.json"
        path.write_text(json.dumps(make_snapshot(index)), encoding="utf-8")
        paths.append(str(path))
    baseline_path = tmp_path / "baseline.json"
    assert main([
        "calibrate", *paths, "--environment-id", "local",
        "--output", str(baseline_path)
    ]) == 0

    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(make_snapshot(10)), encoding="utf-8")
    comparison_path = tmp_path / "comparison.json"
    assert main([
        "compare", "--baseline", str(baseline_path), "--snapshot", str(candidate_path),
        "--output", str(comparison_path)
    ]) == 0
    assert comparison_path.exists()

    candidate = make_snapshot(10)
    candidate["metrics"]["rates"]["llm_error_rate"] = 0.5
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    assert main([
        "compare", "--baseline", str(baseline_path), "--snapshot", str(candidate_path)
    ]) == 1

    incident_paths = []
    for index in range(3):
        snapshot = make_snapshot(index)
        snapshot["metrics"]["dlc"]["archive"]["issue_count"] = 1
        path = tmp_path / f"incident-{index}.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        incident_paths.append(str(path))
    assert main([
        "calibrate", *incident_paths, "--environment-id", "incident"
    ]) == 1

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    assert main([
        "compare", "--baseline", str(baseline_path), "--snapshot", str(invalid)
    ]) == 2
    assert "cannot load operational snapshot" in capsys.readouterr().out
