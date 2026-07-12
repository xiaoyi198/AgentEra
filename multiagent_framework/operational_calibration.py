"""Calibrate deployment alert thresholds from independent operational snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .operational_metrics import default_alert_thresholds
from .trajectory_evaluation import write_report_atomic


class OperationalCalibrationError(ValueError):
    """Raised when operational snapshots cannot form a trustworthy baseline."""


_ENVIRONMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RATIO_SIGNALS = {
    "llm_error_rate",
    "llm_fallback_rate",
    "dlc_reload_failure_rate",
    "world_command_rejection_rate",
    "agent_turn_error_rate",
    "busy_agent_ratio",
}
_SIGNAL_ORDER = (
    "llm_error_rate",
    "llm_fallback_rate",
    "dlc_reload_failure_rate",
    "dlc_reload_max_ms",
    "world_command_rejection_rate",
    "agent_turn_error_rate",
    "archive_issues",
    "busy_agent_ratio",
)


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise OperationalCalibrationError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise OperationalCalibrationError(f"{label} must be finite and nonnegative")
    return parsed


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise OperationalCalibrationError("cannot calculate a percentile without samples")
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


def _sample_stats(values: Sequence[float]) -> tuple[dict[str, Any], list[float]]:
    ordered = sorted(values)
    if not ordered:
        raise OperationalCalibrationError("at least one eligible sample is required")
    median = statistics.median(ordered)
    mad = statistics.median(abs(value - median) for value in ordered)
    floor = 0.01 if max(ordered) <= 1.0 else 1.0
    outlier_limit = median + max(6.0 * mad, median * 0.10, floor)
    accepted = [value for value in ordered if value <= outlier_limit]
    if len(accepted) < min(3, len(ordered)):
        accepted = ordered
    stats = {
        "sample_count": len(ordered),
        "accepted_sample_count": len(accepted),
        "outlier_count": len(ordered) - len(accepted),
        "minimum": round(min(ordered), 6),
        "median": round(median, 6),
        "p90": round(_percentile(ordered, 0.90), 6),
        "p95": round(_percentile(ordered, 0.95), 6),
        "maximum": round(max(ordered), 6),
        "mad": round(mad, 6),
        "outlier_upper_bound": round(outlier_limit, 6),
    }
    return stats, accepted


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OperationalCalibrationError(f"{label} must be an object")
    return value


def _signal_values(snapshot: Mapping[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    if snapshot.get("schema_version") != "1.0":
        raise OperationalCalibrationError("unsupported operational snapshot schema_version")
    metrics = _mapping(snapshot.get("metrics"), "snapshot.metrics")
    rates = _mapping(metrics.get("rates"), "snapshot.metrics.rates")
    llm = _mapping(metrics.get("llm"), "snapshot.metrics.llm")
    llm_totals = _mapping(llm.get("totals"), "snapshot.metrics.llm.totals")
    dlc = _mapping(metrics.get("dlc"), "snapshot.metrics.dlc")
    dlc_metrics = _mapping(dlc.get("metrics"), "snapshot.metrics.dlc.metrics")
    archive = _mapping(dlc.get("archive"), "snapshot.metrics.dlc.archive")
    world = _mapping(metrics.get("world_commands"), "snapshot.metrics.world_commands")
    turns = _mapping(metrics.get("agent_turns"), "snapshot.metrics.agent_turns")
    agents = _mapping(metrics.get("agents"), "snapshot.metrics.agents")

    values = {
        name: _finite_nonnegative(rates.get(name), f"snapshot.rates.{name}")
        for name in _RATIO_SIGNALS
    }
    for name in _RATIO_SIGNALS:
        if values[name] > 1.0:
            raise OperationalCalibrationError(f"snapshot.rates.{name} must not exceed 1.0")
    values["dlc_reload_max_ms"] = _finite_nonnegative(
        dlc_metrics.get("max_duration_ms", 0), "snapshot.dlc.max_duration_ms"
    )
    values["archive_issues"] = _finite_nonnegative(
        archive.get("issue_count", 0), "snapshot.dlc.archive.issue_count"
    )
    exposures = {
        "llm_error_rate": _finite_nonnegative(llm_totals.get("calls", 0), "snapshot.llm.calls"),
        "llm_fallback_rate": _finite_nonnegative(llm_totals.get("calls", 0), "snapshot.llm.calls"),
        "dlc_reload_failure_rate": _finite_nonnegative(dlc_metrics.get("attempts", 0), "snapshot.dlc.attempts"),
        "dlc_reload_max_ms": _finite_nonnegative(dlc_metrics.get("attempts", 0), "snapshot.dlc.attempts"),
        "world_command_rejection_rate": _finite_nonnegative(world.get("persisted_total", 0), "snapshot.world.persisted_total"),
        "agent_turn_error_rate": _finite_nonnegative(turns.get("agent_turns", 0), "snapshot.agent_turns"),
        "archive_issues": 1.0,
        "busy_agent_ratio": _finite_nonnegative(agents.get("total", 0), "snapshot.agents.total"),
    }
    return values, exposures


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationalCalibrationError(f"cannot load {label}") from exc
    if not isinstance(value, dict):
        raise OperationalCalibrationError(f"{label} root must be an object")
    return value


def calibrate_operational_snapshots(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    environment_id: str,
    minimum_samples: int = 3,
    ratio_headroom: float = 1.50,
    duration_headroom: float = 1.25,
    ratio_margin: float = 0.01,
    duration_margin_ms: float = 1.0,
) -> dict[str, Any]:
    """Create a safety-capped, secret-free alert baseline."""
    if not _ENVIRONMENT_ID.fullmatch(environment_id):
        raise OperationalCalibrationError(
            "environment_id must match [A-Za-z0-9._-] and be <= 64 chars"
        )
    if minimum_samples < 3:
        raise OperationalCalibrationError("minimum_samples must be at least 3")
    if len(snapshots) < minimum_samples:
        raise OperationalCalibrationError(
            f"at least {minimum_samples} snapshots are required; got {len(snapshots)}"
        )
    ratio_headroom = _finite_nonnegative(ratio_headroom, "ratio_headroom")
    duration_headroom = _finite_nonnegative(duration_headroom, "duration_headroom")
    ratio_margin = _finite_nonnegative(ratio_margin, "ratio_margin")
    duration_margin_ms = _finite_nonnegative(duration_margin_ms, "duration_margin_ms")
    if ratio_headroom < 1.0 or duration_headroom < 1.0:
        raise OperationalCalibrationError("headroom multipliers must be at least 1.0")

    timestamps: set[float] = set()
    samples = {name: [] for name in _SIGNAL_ORDER}
    eligible = {name: 0 for name in _SIGNAL_ORDER}
    ceilings = default_alert_thresholds()
    for index, snapshot in enumerate(snapshots):
        collected_at = _finite_nonnegative(snapshot.get("collected_at"), "snapshot.collected_at")
        if collected_at in timestamps:
            raise OperationalCalibrationError("snapshot collected_at values must be unique")
        timestamps.add(collected_at)
        values, exposures = _signal_values(snapshot)
        for name in _SIGNAL_ORDER:
            if exposures[name] <= 0:
                continue
            samples[name].append(values[name])
            eligible[name] += 1

    insufficient = [name for name in _SIGNAL_ORDER if eligible[name] < minimum_samples]
    if insufficient:
        raise OperationalCalibrationError(
            "insufficient exposed samples for: " + ", ".join(insufficient)
        )

    signals: dict[str, Any] = {}
    recommended: dict[str, float] = {}
    ready = True
    for name in _SIGNAL_ORDER:
        stats, accepted = _sample_stats(samples[name])
        ceiling = float(ceilings[name])
        exceedances = sum(value > ceiling for value in samples[name])
        if name == "archive_issues":
            raw_recommendation = 0.0
        elif name in _RATIO_SIGNALS:
            raw_recommendation = max(
                _percentile(accepted, 0.95) * ratio_headroom,
                max(accepted) + ratio_margin,
            )
        else:
            raw_recommendation = max(
                _percentile(accepted, 0.95) * duration_headroom,
                max(accepted) + duration_margin_ms,
            )
        threshold = min(ceiling, raw_recommendation)
        signal_ready = exceedances == 0
        ready = ready and signal_ready
        recommended[name] = round(threshold, 6)
        signals[name] = {
            "kind": "ratio" if name in _RATIO_SIGNALS else (
                "duration_ms" if name == "dlc_reload_max_ms" else "count"
            ),
            "eligible_sample_count": eligible[name],
            "statistics": stats,
            "policy_ceiling": ceiling,
            "observed_policy_exceedances": exceedances,
            "recommended_threshold": round(threshold, 6),
            "ready": signal_ready,
        }

    core = {
        "schema_version": "1.0",
        "baseline_type": "operational-alerts",
        "environment_id": environment_id,
        "sample_count": len(snapshots),
        "ready": ready,
        "strategy": {
            "minimum_samples": minimum_samples,
            "ratio_percentile": 0.95,
            "ratio_headroom": ratio_headroom,
            "ratio_margin": ratio_margin,
            "duration_percentile": 0.95,
            "duration_headroom": duration_headroom,
            "duration_margin_ms": duration_margin_ms,
            "policy": "recommendations are capped by built-in safety ceilings",
            "outlier_rule": "value > median + max(6*MAD, 10% median, kind floor)",
        },
        "signals": signals,
        "recommended_alert_thresholds": recommended,
    }
    return {
        **core,
        "baseline_id": hashlib.sha256(_canonical(core).encode("utf-8")).hexdigest(),
        "generated_at": time.time(),
    }


def compare_operational_snapshot(
    baseline: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare one operational snapshot against a reviewed ready baseline."""
    if baseline.get("schema_version") != "1.0" or baseline.get("baseline_type") != "operational-alerts":
        raise OperationalCalibrationError("unsupported operational baseline schema")
    if baseline.get("ready") is not True:
        raise OperationalCalibrationError("operational baseline requires review before enforcement")
    environment_id = str(baseline.get("environment_id", ""))
    if not _ENVIRONMENT_ID.fullmatch(environment_id):
        raise OperationalCalibrationError("baseline environment_id is invalid")
    baseline_id = str(baseline.get("baseline_id", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", baseline_id):
        raise OperationalCalibrationError("baseline_id is invalid")
    thresholds = _mapping(
        baseline.get("recommended_alert_thresholds"),
        "baseline.recommended_alert_thresholds",
    )
    if tuple(thresholds) != _SIGNAL_ORDER:
        if set(thresholds) != set(_SIGNAL_ORDER):
            raise OperationalCalibrationError("baseline alert signal set is invalid")
    values, exposures = _signal_values(snapshot)
    results = []
    for name in _SIGNAL_ORDER:
        threshold = _finite_nonnegative(thresholds.get(name), f"baseline.thresholds.{name}")
        value = values[name]
        exposed = exposures[name] > 0
        passed = not exposed or value <= threshold
        results.append({
            "signal": name,
            "passed": passed,
            "exposed": exposed,
            "value": round(value, 6),
            "threshold": round(threshold, 6),
            "comparison": ">",
        })
    return {
        "schema_version": "1.0",
        "baseline_id": baseline_id,
        "environment_id": environment_id,
        "passed": all(item["passed"] for item in results),
        "signals": results,
        "compared_at": time.time(),
    }


def _run_calibrate(args: argparse.Namespace) -> int:
    snapshots = [_load_json(path, "operational snapshot") for path in args.snapshots]
    baseline = calibrate_operational_snapshots(
        snapshots,
        environment_id=args.environment_id,
        minimum_samples=args.minimum_samples,
        ratio_headroom=args.ratio_headroom,
        duration_headroom=args.duration_headroom,
        ratio_margin=args.ratio_margin,
        duration_margin_ms=args.duration_margin_ms,
    )
    if args.output:
        write_report_atomic(args.output, baseline)
    print(json.dumps(baseline, ensure_ascii=False, indent=2))
    return 0 if baseline["ready"] else 1


def _run_compare(args: argparse.Namespace) -> int:
    baseline = _load_json(args.baseline, "operational baseline")
    snapshot = _load_json(args.snapshot, "operational snapshot")
    result = compare_operational_snapshot(baseline, snapshot)
    if args.output:
        write_report_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate and enforce deployment operational alert baselines"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("snapshots", nargs="+")
    calibrate.add_argument("--environment-id", required=True)
    calibrate.add_argument("--minimum-samples", type=int, default=3)
    calibrate.add_argument("--ratio-headroom", type=float, default=1.50)
    calibrate.add_argument("--duration-headroom", type=float, default=1.25)
    calibrate.add_argument("--ratio-margin", type=float, default=0.01)
    calibrate.add_argument("--duration-margin-ms", type=float, default=1.0)
    calibrate.add_argument("--output", default="")
    calibrate.set_defaults(handler=_run_calibrate)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--snapshot", required=True)
    compare.add_argument("--output", default="")
    compare.set_defaults(handler=_run_compare)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except OperationalCalibrationError as exc:
        print(f"operational calibration error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
