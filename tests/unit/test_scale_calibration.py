from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from multiagent_framework.scale_calibration import (
    ScaleCalibrationError,
    _sample_stats,
    calibrate_scale_reports,
    compare_scale_report,
    main,
)
from multiagent_framework.scale_evaluation import ScaleConfig


def make_report(
    elapsed: float = 100.0,
    memory: float = 10.0,
    *,
    suite_id: str = "daming-scale-soak",
    invariant_ok: bool = True,
    performance_ok: bool = True,
    legacy: bool = False,
):
    phases = []
    for index, name in enumerate(("dlc", "router", "world", "memory", "soak"), 1):
        phase = {
            "phase": name,
            "operations": index * 10,
            "elapsed_ms": elapsed + index,
            "operations_per_second": 1000.0,
            "peak_memory_mb": memory + index / 10,
            "passed": invariant_ok and performance_ok,
            "invariants": {"safe": invariant_ok},
            "threshold_ms": 1.0 if not performance_ok else None,
        }
        if not legacy:
            phase["invariants_passed"] = invariant_ok
            phase["performance_passed"] = performance_ok
        phases.append(phase)
    return {
        "suite_id": suite_id,
        "passed": invariant_ok and performance_ok,
        "config": asdict(ScaleConfig(soak_cycles=2)),
        "total_elapsed_ms": elapsed * 5,
        "peak_memory_mb": memory + 1,
        "phases": phases,
    }


def make_baseline():
    return calibrate_scale_reports(
        [make_report(100 + offset, 10 + offset / 10) for offset in (0, 2, 4)],
        environment_id="local-windows",
    )


def test_calibrates_compatible_reports_with_stable_fingerprint_and_thresholds():
    reports = [make_report(100 + offset, 10 + offset / 10) for offset in (0, 2, 4)]
    first = calibrate_scale_reports(reports, environment_id="local-windows")
    second = calibrate_scale_reports(reports, environment_id="local-windows")

    assert first["sample_count"] == 3
    assert first["baseline_id"] == second["baseline_id"]
    assert first["workload_fingerprint"] == second["workload_fingerprint"]
    assert set(first["recommended_scale_thresholds_ms"]) == {
        "dlc", "router", "world", "memory", "soak"
    }
    assert first["phases"]["router"]["elapsed_ms"]["p95"] == pytest.approx(105.8)


def test_robust_statistics_do_not_let_one_extreme_outlier_set_the_limit():
    stats = _sample_stats([10, 11, 12, 13, 1000], headroom=1.25)

    assert stats["accepted_sample_count"] == 4
    assert stats["outlier_count"] == 1
    assert stats["maximum"] == 1000
    assert stats["recommended_limit"] < 20


def test_calibration_accepts_new_report_that_only_failed_its_old_performance_limit():
    reports = [make_report(100 + offset, performance_ok=False) for offset in (0, 1, 2)]

    baseline = calibrate_scale_reports(reports, environment_id="ci-win-x64")

    assert baseline["sample_count"] == 3


def test_calibration_conservatively_rejects_failed_legacy_report():
    reports = [make_report(100 + offset, performance_ok=False, legacy=True) for offset in (0, 1, 2)]

    with pytest.raises(ScaleCalibrationError, match="did not pass its invariants"):
        calibrate_scale_reports(reports, environment_id="legacy")


@pytest.mark.parametrize("count", [0, 1, 2])
def test_calibration_requires_at_least_three_reports(count):
    with pytest.raises(ScaleCalibrationError, match="at least 3 reports"):
        calibrate_scale_reports([make_report()] * count, environment_id="local")


@pytest.mark.parametrize("environment_id", ["with space", "../path", "line\nbreak", "", "x" * 65])
def test_environment_id_rejects_path_and_control_characters(environment_id):
    with pytest.raises(ScaleCalibrationError, match="environment_id"):
        calibrate_scale_reports([make_report()] * 3, environment_id=environment_id)


def test_calibration_rejects_different_suite_workload_and_phase_set():
    reports = [make_report(), make_report(101), make_report(102)]
    reports[1]["suite_id"] = "other"
    with pytest.raises(ScaleCalibrationError, match="suite_id"):
        calibrate_scale_reports(reports, environment_id="local")

    reports = [make_report(), make_report(101), make_report(102)]
    reports[1]["config"]["dlc_count"] += 1
    with pytest.raises(ScaleCalibrationError, match="workload config"):
        calibrate_scale_reports(reports, environment_id="local")

    reports = [make_report(), make_report(101), make_report(102)]
    reports[1]["phases"].pop()
    with pytest.raises(ScaleCalibrationError, match="phase order or set"):
        calibrate_scale_reports(reports, environment_id="local")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_calibration_rejects_nonfinite_or_negative_measurements(bad):
    reports = [make_report(), make_report(101), make_report(102)]
    reports[1]["phases"][0]["elapsed_ms"] = bad

    with pytest.raises(ScaleCalibrationError, match="finite and nonnegative"):
        calibrate_scale_reports(reports, environment_id="local")


def test_calibration_rejects_unknown_config_fields_instead_of_serializing_them():
    reports = [make_report(), make_report(101), make_report(102)]
    reports[0]["config"]["api_key"] = "top-secret"

    with pytest.raises(ScaleCalibrationError, match="workload config is invalid"):
        calibrate_scale_reports(reports, environment_id="local")


def test_candidate_passes_using_new_baseline_even_if_old_performance_threshold_failed():
    baseline = make_baseline()
    candidate = make_report(102, 10.2, performance_ok=False)

    result = compare_scale_report(baseline, candidate)

    assert result["passed"]
    assert [phase["phase"] for phase in result["phases"]] == [
        "dlc", "router", "world", "memory", "soak"
    ]
    assert result["source_invariants_passed"]
    assert not result["source_report_passed"]
    assert all(not phase["source_performance_passed"] for phase in result["phases"])


def test_candidate_elapsed_and_memory_regressions_are_reported_separately():
    baseline = make_baseline()
    elapsed_candidate = make_report(100, 10)
    elapsed_candidate["phases"][1]["elapsed_ms"] = 1_000_000
    elapsed_result = compare_scale_report(baseline, elapsed_candidate)
    assert not elapsed_result["passed"]
    assert not elapsed_result["phases"][1]["elapsed_passed"]
    assert elapsed_result["phases"][1]["peak_memory_passed"]

    memory_candidate = make_report(100, 10)
    memory_candidate["phases"][2]["peak_memory_mb"] = 1_000_000
    memory_result = compare_scale_report(baseline, memory_candidate)
    assert not memory_result["passed"]
    assert memory_result["phases"][2]["elapsed_passed"]
    assert not memory_result["phases"][2]["peak_memory_passed"]


def test_candidate_invariant_failure_cannot_be_hidden_by_fast_performance():
    result = compare_scale_report(make_baseline(), make_report(1, 1, invariant_ok=False))

    assert not result["passed"]
    assert not result["source_invariants_passed"]
    assert all(not phase["source_invariants_passed"] for phase in result["phases"])


def test_compare_rejects_schema_suite_workload_and_phase_mismatch():
    baseline = make_baseline()
    invalid_schema = dict(baseline, schema_version="2.0")
    with pytest.raises(ScaleCalibrationError, match="schema_version"):
        compare_scale_report(invalid_schema, make_report())
    with pytest.raises(ScaleCalibrationError, match="suite_id"):
        compare_scale_report(baseline, make_report(suite_id="other"))

    candidate = make_report()
    candidate["config"]["world_entity_count"] += 1
    with pytest.raises(ScaleCalibrationError, match="workload config"):
        compare_scale_report(baseline, candidate)

    candidate = make_report()
    candidate["phases"].reverse()
    with pytest.raises(ScaleCalibrationError, match="phase order or set"):
        compare_scale_report(baseline, candidate)


def test_baseline_and_comparison_are_secret_free_aggregates():
    reports = [make_report(100 + index) for index in range(3)]
    for report in reports:
        report["prompt"] = "private prompt"
        report["messages"] = ["private message"]
        report["error"] = "private error"
        report["provider_url"] = "https://secret.invalid"
        report["api_key"] = "sk-secret"
        report["archive_path"] = "C:/private/archive"
    baseline = calibrate_scale_reports(reports, environment_id="safe-local")
    comparison = compare_scale_report(baseline, make_report())
    serialized = json.dumps({"baseline": baseline, "comparison": comparison})

    for secret in (
        "private prompt", "private message", "private error", "secret.invalid",
        "sk-secret", "private/archive"
    ):
        assert secret not in serialized


def test_cli_exit_codes_and_atomic_outputs(tmp_path, capsys):
    report_paths = []
    for index in range(3):
        path = tmp_path / f"report-{index}.json"
        path.write_text(json.dumps(make_report(100 + index)), encoding="utf-8")
        report_paths.append(str(path))
    baseline_path = tmp_path / "baseline.json"
    assert main([
        "calibrate", *report_paths, "--environment-id", "local",
        "--output", str(baseline_path)
    ]) == 0
    assert baseline_path.exists()

    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(make_report()), encoding="utf-8")
    comparison_path = tmp_path / "comparison.json"
    assert main([
        "compare", "--baseline", str(baseline_path), "--candidate", str(candidate_path),
        "--output", str(comparison_path)
    ]) == 0
    assert comparison_path.exists()

    regressed = make_report()
    regressed["phases"][0]["elapsed_ms"] = 1_000_000
    candidate_path.write_text(json.dumps(regressed), encoding="utf-8")
    assert main([
        "compare", "--baseline", str(baseline_path), "--candidate", str(candidate_path)
    ]) == 1

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not json", encoding="utf-8")
    assert main([
        "compare", "--baseline", str(baseline_path), "--candidate", str(invalid_path)
    ]) == 2
    assert "cannot load candidate report" in capsys.readouterr().out
