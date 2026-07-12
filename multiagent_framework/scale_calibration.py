"""Calibrate environment-specific scale thresholds from repeatable reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import time
from dataclasses import asdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .scale_evaluation import ScaleConfig, ScaleEvaluationError
from .trajectory_evaluation import write_report_atomic


class ScaleCalibrationError(ValueError):
    """Raised when reports cannot form one trustworthy environment baseline."""


_ENVIRONMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ScaleCalibrationError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ScaleCalibrationError(f"{label} must be finite and nonnegative")
    return parsed


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _workload_config(report: Mapping[str, Any]) -> dict[str, Any]:
    raw = report.get("config")
    if not isinstance(raw, Mapping):
        raise ScaleCalibrationError("report.config must be an object")
    try:
        normalized = asdict(ScaleConfig.from_mapping(raw))
    except ScaleEvaluationError as exc:
        raise ScaleCalibrationError("report workload config is invalid") from exc
    normalized.pop("thresholds_ms", None)
    return normalized


def _fingerprint(config: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(config).encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ScaleCalibrationError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _sample_stats(values: Sequence[float], *, headroom: float) -> dict[str, Any]:
    if not values:
        raise ScaleCalibrationError("at least one sample is required")
    ordered = sorted(float(value) for value in values)
    median = statistics.median(ordered)
    deviations = [abs(value - median) for value in ordered]
    mad = statistics.median(deviations)
    outlier_limit = median + max(6.0 * mad, median * 0.10, 1.0)
    accepted = [value for value in ordered if value <= outlier_limit]
    if len(accepted) < min(3, len(ordered)):
        accepted = ordered
    outliers = len(ordered) - len(accepted)
    p95 = _percentile(accepted, 0.95)
    recommended = max(p95 * headroom, max(accepted) * 1.05)
    return {
        "sample_count": len(ordered),
        "accepted_sample_count": len(accepted),
        "outlier_count": outliers,
        "minimum": round(min(ordered), 3),
        "median": round(median, 3),
        "p90": round(_percentile(ordered, 0.90), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "maximum": round(max(ordered), 3),
        "mad": round(mad, 3),
        "outlier_upper_bound": round(outlier_limit, 3),
        "recommended_limit": round(recommended, 3),
    }


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaleCalibrationError(f"cannot load {label}") from exc
    if not isinstance(value, dict):
        raise ScaleCalibrationError(f"{label} root must be an object")
    return value


def _phase_invariants_passed(phase: Mapping[str, Any]) -> bool:
    """Read invariant status while conservatively supporting legacy reports."""
    if "invariants_passed" in phase:
        return phase.get("invariants_passed") is True
    return phase.get("passed") is True


def _phase_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = report.get("phases")
    if not isinstance(raw, list) or not raw:
        raise ScaleCalibrationError("report.phases must be a non-empty array")
    phases: dict[str, Mapping[str, Any]] = {}
    for index, phase in enumerate(raw):
        if not isinstance(phase, Mapping):
            raise ScaleCalibrationError(f"report.phases[{index}] must be an object")
        name = str(phase.get("phase", ""))
        if not name or name in phases:
            raise ScaleCalibrationError("phase names must be non-empty and unique")
        phases[name] = phase
    return phases


def calibrate_scale_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    environment_id: str,
    minimum_samples: int = 3,
    duration_headroom: float = 1.25,
    memory_headroom: float = 1.20,
) -> dict[str, Any]:
    """Create one secret-free baseline from compatible passing scale reports."""
    if not _ENVIRONMENT_ID.fullmatch(environment_id):
        raise ScaleCalibrationError("environment_id must match [A-Za-z0-9._-] and be <= 64 chars")
    if minimum_samples < 3:
        raise ScaleCalibrationError("minimum_samples must be at least 3")
    if len(reports) < minimum_samples:
        raise ScaleCalibrationError(
            f"at least {minimum_samples} reports are required; got {len(reports)}"
        )
    duration_headroom = _finite_nonnegative(duration_headroom, "duration_headroom")
    memory_headroom = _finite_nonnegative(memory_headroom, "memory_headroom")
    if duration_headroom < 1.0 or memory_headroom < 1.0:
        raise ScaleCalibrationError("headroom multipliers must be at least 1.0")

    first = reports[0]
    suite_id = str(first.get("suite_id", ""))
    if not suite_id:
        raise ScaleCalibrationError("report.suite_id is required")
    config = _workload_config(first)
    workload_fingerprint = _fingerprint(config)
    phase_names = tuple(_phase_map(first))
    samples: dict[str, dict[str, list[float]]] = {
        name: {"elapsed_ms": [], "peak_memory_mb": []} for name in phase_names
    }
    totals: list[float] = []
    suite_peaks: list[float] = []

    for index, report in enumerate(reports):
        if str(report.get("suite_id", "")) != suite_id:
            raise ScaleCalibrationError(f"report {index} uses a different suite_id")
        current_config = _workload_config(report)
        if _fingerprint(current_config) != workload_fingerprint:
            raise ScaleCalibrationError(f"report {index} uses a different workload config")
        phases = _phase_map(report)
        if tuple(phases) != phase_names:
            raise ScaleCalibrationError(f"report {index} uses a different phase order or set")
        totals.append(_finite_nonnegative(report.get("total_elapsed_ms"), "total_elapsed_ms"))
        suite_peaks.append(_finite_nonnegative(report.get("peak_memory_mb"), "peak_memory_mb"))
        for name in phase_names:
            phase = phases[name]
            if not _phase_invariants_passed(phase):
                raise ScaleCalibrationError(
                    f"report {index} phase {name} did not pass its invariants"
                )
            samples[name]["elapsed_ms"].append(
                _finite_nonnegative(phase.get("elapsed_ms"), f"{name}.elapsed_ms")
            )
            samples[name]["peak_memory_mb"].append(
                _finite_nonnegative(phase.get("peak_memory_mb"), f"{name}.peak_memory_mb")
            )

    phases_out: dict[str, Any] = {}
    recommended_thresholds: dict[str, float] = {}
    for name in phase_names:
        elapsed = _sample_stats(samples[name]["elapsed_ms"], headroom=duration_headroom)
        memory = _sample_stats(samples[name]["peak_memory_mb"], headroom=memory_headroom)
        phases_out[name] = {"elapsed_ms": elapsed, "peak_memory_mb": memory}
        recommended_thresholds[name] = elapsed["recommended_limit"]

    baseline_core = {
        "schema_version": "1.0",
        "suite_id": suite_id,
        "environment_id": environment_id,
        "workload_fingerprint": workload_fingerprint,
        "sample_count": len(reports),
        "phase_order": list(phase_names),
        "strategy": {
            "duration_percentile": 0.95,
            "duration_headroom": duration_headroom,
            "memory_percentile": 0.95,
            "memory_headroom": memory_headroom,
            "outlier_rule": "value > median + max(6*MAD, 10% median, 1.0)",
            "minimum_samples": minimum_samples,
        },
        "workload": config,
        "suite_elapsed_ms": _sample_stats(totals, headroom=duration_headroom),
        "suite_peak_memory_mb": _sample_stats(suite_peaks, headroom=memory_headroom),
        "phases": phases_out,
        "recommended_scale_thresholds_ms": recommended_thresholds,
    }
    baseline_id = hashlib.sha256(_canonical(baseline_core).encode("utf-8")).hexdigest()
    return {
        **baseline_core,
        "baseline_id": baseline_id,
        "generated_at": time.time(),
    }


def compare_scale_report(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare one candidate report with a previously calibrated baseline."""
    if baseline.get("schema_version") != "1.0":
        raise ScaleCalibrationError("unsupported baseline schema_version")
    baseline_suite = str(baseline.get("suite_id", ""))
    if str(candidate.get("suite_id", "")) != baseline_suite:
        raise ScaleCalibrationError("candidate uses a different suite_id")
    candidate_config = _workload_config(candidate)
    fingerprint = _fingerprint(candidate_config)
    if fingerprint != str(baseline.get("workload_fingerprint", "")):
        raise ScaleCalibrationError("candidate uses a different workload config")
    phase_baselines = baseline.get("phases")
    if not isinstance(phase_baselines, Mapping) or not phase_baselines:
        raise ScaleCalibrationError("baseline phases are missing")
    candidate_phases = _phase_map(candidate)
    baseline_order_raw = baseline.get("phase_order")
    if baseline_order_raw is None:
        baseline_order = tuple(phase_baselines)
    elif isinstance(baseline_order_raw, list) and all(
        isinstance(name, str) and name for name in baseline_order_raw
    ):
        baseline_order = tuple(baseline_order_raw)
    else:
        raise ScaleCalibrationError("baseline phase_order is invalid")
    if tuple(candidate_phases) != baseline_order or set(baseline_order) != set(phase_baselines):
        raise ScaleCalibrationError("candidate uses a different phase order or set")

    phase_results = []
    for name in baseline_order:
        phase_baseline = phase_baselines[name]
        if not isinstance(phase_baseline, Mapping):
            raise ScaleCalibrationError(f"baseline phase {name} is invalid")
        elapsed_baseline = phase_baseline.get("elapsed_ms", {})
        memory_baseline = phase_baseline.get("peak_memory_mb", {})
        if not isinstance(elapsed_baseline, Mapping) or not isinstance(memory_baseline, Mapping):
            raise ScaleCalibrationError(f"baseline phase {name} limits are invalid")
        elapsed_limit = _finite_nonnegative(
            elapsed_baseline.get("recommended_limit"), f"baseline.{name}.elapsed_limit"
        )
        memory_limit = _finite_nonnegative(
            memory_baseline.get("recommended_limit"), f"baseline.{name}.memory_limit"
        )
        phase = candidate_phases[name]
        elapsed = _finite_nonnegative(phase.get("elapsed_ms"), f"candidate.{name}.elapsed_ms")
        memory = _finite_nonnegative(
            phase.get("peak_memory_mb"), f"candidate.{name}.peak_memory_mb"
        )
        invariants_passed = _phase_invariants_passed(phase)
        source_performance_passed = phase.get(
            "performance_passed", phase.get("passed") is True
        ) is True
        elapsed_passed = elapsed <= elapsed_limit
        memory_passed = memory <= memory_limit
        phase_results.append({
            "phase": name,
            "passed": invariants_passed and elapsed_passed and memory_passed,
            "source_invariants_passed": invariants_passed,
            "source_performance_passed": source_performance_passed,
            "elapsed_ms": round(elapsed, 3),
            "elapsed_limit_ms": round(elapsed_limit, 3),
            "elapsed_passed": elapsed_passed,
            "peak_memory_mb": round(memory, 3),
            "peak_memory_limit_mb": round(memory_limit, 3),
            "peak_memory_passed": memory_passed,
        })
    invariants_passed = all(item["source_invariants_passed"] for item in phase_results)
    passed = all(item["passed"] for item in phase_results)
    return {
        "schema_version": "1.0",
        "baseline_id": str(baseline.get("baseline_id", "")),
        "suite_id": baseline_suite,
        "environment_id": str(baseline.get("environment_id", "")),
        "workload_fingerprint": fingerprint,
        "passed": passed,
        "source_report_passed": candidate.get("passed") is True,
        "source_invariants_passed": invariants_passed,
        "phases": phase_results,
        "compared_at": time.time(),
    }


def _run_calibrate(args: argparse.Namespace) -> int:
    reports = [_load_json(path, "scale report") for path in args.reports]
    baseline = calibrate_scale_reports(
        reports,
        environment_id=args.environment_id,
        minimum_samples=args.minimum_samples,
        duration_headroom=args.duration_headroom,
        memory_headroom=args.memory_headroom,
    )
    if args.output:
        write_report_atomic(args.output, baseline)
    print(json.dumps(baseline, ensure_ascii=False, indent=2))
    return 0


def _run_compare(args: argparse.Namespace) -> int:
    baseline = _load_json(args.baseline, "scale baseline")
    candidate = _load_json(args.candidate, "candidate report")
    result = compare_scale_report(baseline, candidate)
    if args.output:
        write_report_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate and enforce environment-specific simulator scale baselines"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("reports", nargs="+")
    calibrate.add_argument("--environment-id", required=True)
    calibrate.add_argument("--minimum-samples", type=int, default=3)
    calibrate.add_argument("--duration-headroom", type=float, default=1.25)
    calibrate.add_argument("--memory-headroom", type=float, default=1.20)
    calibrate.add_argument("--output", default="")
    calibrate.set_defaults(handler=_run_calibrate)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--output", default="")
    compare.set_defaults(handler=_run_compare)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ScaleCalibrationError as exc:
        print(f"scale calibration error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
