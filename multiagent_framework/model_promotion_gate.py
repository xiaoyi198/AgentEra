"""Secret-free deployment gate for reviewed model-policy promotions.

The gate combines one live trajectory matrix report, its exact policy patch,
and optional environment-specific scale/operational gate results. It emits only
safe identifiers, aggregate counts, reason codes, and canonical fingerprints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .trajectory_evaluation import write_report_atomic


class ModelPromotionGateError(ValueError):
    """Raised when promotion evidence is malformed, inconsistent, or unsafe."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "provider_url",
    "base_url",
    "secret",
    "token",
    "credential",
    "credentials",
    "prompt",
    "prompts",
    "message",
    "messages",
    "response",
    "responses",
    "memory",
    "memories",
    "error",
    "errors",
    "raw_error",
    "source_path",
    "input_path",
    "config_path",
    "command_id",
    "chain_id",
    "trace_id",
    "agent_id",
    "tool_call_id",
}
_SENSITIVE_REFERENCE = re.compile(
    r"(?:\$\{[A-Za-z_][A-Za-z0-9_]*(?:KEY|SECRET|TOKEN|CREDENTIAL)[A-Za-z0-9_]*\}"
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:API_KEY|SECRET|TOKEN|CREDENTIALS?)\b)",
    re.IGNORECASE,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise ModelPromotionGateError(f"{label}_invalid")
    return text


def _safe_metadata(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if (
        not _SAFE_METADATA.fullmatch(text)
        or "://" in text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:[\\/]", text)
        or _SENSITIVE_REFERENCE.search(text)
    ):
        raise ModelPromotionGateError(f"{label}_invalid")
    return text


def _strict_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelPromotionGateError(f"{label}_must_be_integer")
    if not minimum <= value <= maximum:
        raise ModelPromotionGateError(f"{label}_out_of_range")
    return value


def _finite(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ModelPromotionGateError(f"{label}_must_be_numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ModelPromotionGateError(f"{label}_must_be_numeric") from exc
    if not math.isfinite(number):
        raise ModelPromotionGateError(f"{label}_must_be_finite")
    if not minimum <= number <= maximum:
        raise ModelPromotionGateError(f"{label}_out_of_range")
    return number


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelPromotionGateError(f"{label}_must_be_object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ModelPromotionGateError(f"{label}_must_be_list")
    return value


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if (
                key in _FORBIDDEN_KEYS
                or key.endswith("_api_key")
                or key.endswith("_secret")
                or key.endswith("_credential")
                or key.endswith("_token")
                or key.endswith("_path")
                or key.endswith("_env")
            ):
                return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    elif isinstance(value, str):
        text = value.strip()
        if (
            "://" in text
            or re.match(r"^[A-Za-z]:[\\/]", text)
            or text.startswith("/")
            or _SENSITIVE_REFERENCE.search(text)
        ):
            return True
    return False


def _validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if config.get("schema_version") != "1.0":
        raise ModelPromotionGateError("unsupported_gate_schema")
    gate_id = _safe_id(config.get("gate_id"), "gate_id")
    environment_id = _safe_id(config.get("environment_id"), "environment_id")
    matrix_id = _safe_id(config.get("required_matrix_id"), "required_matrix_id")
    suite_id = _safe_id(config.get("required_suite_id"), "required_suite_id")
    repetitions = _strict_int(
        config.get("minimum_repetitions", 1), "minimum_repetitions", 1, 100
    )
    reviewer_quorum = _strict_int(
        config.get("minimum_reviewers_per_target", 1),
        "minimum_reviewers_per_target",
        1,
        20,
    )
    coverage = _finite(
        config.get("minimum_review_coverage", 1.0),
        "minimum_review_coverage",
        0,
        1,
    )
    disagreement = _finite(
        config.get("maximum_review_disagreement", 100.0),
        "maximum_review_disagreement",
        0,
        100,
    )
    raw_targets = _list(config.get("required_targets"), "required_targets")
    if not raw_targets:
        raise ModelPromotionGateError("required_targets_empty")
    targets: list[tuple[str, str]] = []
    for index, raw in enumerate(raw_targets):
        item = _mapping(raw, f"required_targets_{index}")
        if set(item) != {"cohort", "task"}:
            raise ModelPromotionGateError("required_target_shape_invalid")
        targets.append(
            (
                _safe_id(item.get("cohort"), "required_target_cohort"),
                _safe_id(item.get("task"), "required_target_task"),
            )
        )
    if len(set(targets)) != len(targets):
        raise ModelPromotionGateError("required_targets_duplicate")
    allow_additional = config.get("allow_additional_targets", False)
    require_scale = config.get("require_scale_gate", False)
    require_operational = config.get("require_operational_gate", False)
    for label, value in (
        ("allow_additional_targets", allow_additional),
        ("require_scale_gate", require_scale),
        ("require_operational_gate", require_operational),
    ):
        if not isinstance(value, bool):
            raise ModelPromotionGateError(f"{label}_must_be_boolean")
    return {
        "gate_id": gate_id,
        "environment_id": environment_id,
        "matrix_id": matrix_id,
        "suite_id": suite_id,
        "minimum_repetitions": repetitions,
        "minimum_reviewers_per_target": reviewer_quorum,
        "minimum_review_coverage": coverage,
        "maximum_review_disagreement": disagreement,
        "required_targets": tuple(targets),
        "allow_additional_targets": allow_additional,
        "require_scale_gate": require_scale,
        "require_operational_gate": require_operational,
    }


def _validate_gate_evidence(
    value: Mapping[str, Any] | None,
    *,
    label: str,
    environment_id: str,
    required: bool,
) -> tuple[dict[str, Any], list[str]]:
    if value is None:
        return (
            {"required": required, "present": False, "passed": False, "baseline_id": ""},
            [f"{label}_gate_missing"] if required else [],
        )
    if _contains_sensitive(value):
        raise ModelPromotionGateError(f"{label}_gate_contains_forbidden_content")
    if value.get("schema_version") != "1.0":
        raise ModelPromotionGateError(f"{label}_gate_schema_invalid")
    evidence_environment = _safe_id(
        value.get("environment_id"), f"{label}_gate_environment_id"
    )
    if evidence_environment != environment_id:
        raise ModelPromotionGateError(f"{label}_gate_environment_mismatch")
    baseline_id = str(value.get("baseline_id", ""))
    if not _SHA256.fullmatch(baseline_id):
        raise ModelPromotionGateError(f"{label}_gate_baseline_id_invalid")
    passed = value.get("passed") is True
    reasons = [f"{label}_gate_failed"] if required and not passed else []
    return (
        {
            "required": required,
            "present": True,
            "passed": passed,
            "baseline_id": baseline_id,
            "evidence_fingerprint": _fingerprint(value),
        },
        reasons,
    )


def _expected_policy(rankings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    task_policies: dict[str, list[str]] = {}
    cohort_policies: dict[str, dict[str, list[str]]] = {}
    for ranking in rankings:
        cohort = _safe_id(ranking.get("cohort"), "ranking_cohort")
        task = _safe_id(ranking.get("task_type"), "ranking_task_type")
        if ranking.get("status") != "reviewed":
            continue
        profile = _safe_id(
            ranking.get("recommended_profile"), "ranking_recommended_profile"
        )
        if cohort == "all":
            task_policies[task] = [profile]
        else:
            cohort_policies.setdefault(cohort, {})[task] = [profile]
    return {
        "schema_version": "1.0",
        "task_policies": task_policies,
        "cohort_task_policies": cohort_policies,
    }


def evaluate_model_promotion(
    config: Mapping[str, Any],
    matrix_report: Mapping[str, Any],
    policy_patch: Mapping[str, Any],
    *,
    scale_gate: Mapping[str, Any] | None = None,
    operational_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate safe artifacts and return one deterministic promotion decision."""

    for label, value in (
        ("config", config),
        ("matrix_report", matrix_report),
        ("policy_patch", policy_patch),
    ):
        if _contains_sensitive(value):
            raise ModelPromotionGateError(f"{label}_contains_forbidden_content")
    spec = _validate_config(config)
    reasons: list[str] = []

    if matrix_report.get("schema_version") != "1.0":
        raise ModelPromotionGateError("matrix_report_schema_invalid")
    matrix_id = _safe_id(matrix_report.get("matrix_id"), "matrix_id")
    suite_id = _safe_id(matrix_report.get("suite_id"), "suite_id")
    if matrix_id != spec["matrix_id"]:
        raise ModelPromotionGateError("matrix_id_mismatch")
    if suite_id != spec["suite_id"]:
        raise ModelPromotionGateError("suite_id_mismatch")
    if matrix_report.get("mode") != "live":
        raise ModelPromotionGateError("matrix_mode_must_be_live")
    repetitions = _strict_int(
        matrix_report.get("repetitions"), "matrix_repetitions", 1, 100
    )
    if repetitions < spec["minimum_repetitions"]:
        reasons.append("matrix_repetitions_below_minimum")
    if matrix_report.get("passed") is not True:
        reasons.append("matrix_automated_checks_failed")
    if matrix_report.get("approved") is not True:
        reasons.append("matrix_not_review_approved")

    summary = _mapping(matrix_report.get("review_summary"), "review_summary")
    quorum = _strict_int(
        summary.get("minimum_reviewers_per_target"),
        "report_minimum_reviewers_per_target",
        1,
        20,
    )
    coverage = _finite(summary.get("coverage"), "report_review_coverage", 0, 1)
    configured_disagreement = _finite(
        summary.get("maximum_review_disagreement"),
        "report_maximum_review_disagreement",
        0,
        100,
    )
    reviewed_targets = _strict_int(
        summary.get("reviewed_targets"), "reviewed_targets", 0, 1_000_000
    )
    total_review_targets = _strict_int(
        summary.get("total_targets"), "total_targets", 1, 1_000_000
    )
    review_count = _strict_int(
        summary.get("review_count"), "review_count", 0, 20_000_000
    )
    if quorum < spec["minimum_reviewers_per_target"]:
        reasons.append("reviewer_quorum_below_minimum")
    if coverage < spec["minimum_review_coverage"]:
        reasons.append("review_coverage_below_minimum")
    if reviewed_targets != total_review_targets:
        reasons.append("review_targets_incomplete")
    if review_count < total_review_targets * spec["minimum_reviewers_per_target"]:
        reasons.append("review_count_below_required_quorum")
    if configured_disagreement > spec["maximum_review_disagreement"]:
        reasons.append("matrix_disagreement_policy_too_permissive")

    cells = _list(matrix_report.get("cells"), "matrix_cells")
    if not cells:
        raise ModelPromotionGateError("matrix_cells_empty")
    cell_profiles: dict[tuple[str, str], set[str]] = {}
    maximum_observed_disagreement = 0.0
    for index, raw in enumerate(cells):
        cell = _mapping(raw, f"matrix_cell_{index}")
        _safe_id(cell.get("cell_id"), "cell_id")
        cohort = _safe_id(cell.get("cohort"), "cell_cohort")
        task = _safe_id(cell.get("task_type"), "cell_task_type")
        profile = _safe_id(cell.get("profile"), "cell_profile")
        _safe_metadata(cell.get("provider"), "cell_provider")
        _safe_metadata(cell.get("model"), "cell_model")
        runs = _strict_int(cell.get("runs"), "cell_runs", 1, 100)
        passed_runs = _strict_int(
            cell.get("passed_runs"), "cell_passed_runs", 0, 100
        )
        cell_coverage = _finite(
            cell.get("review_coverage"), "cell_review_coverage", 0, 1
        )
        cell_disagreement = _finite(
            cell.get("review_disagreement_max"),
            "cell_review_disagreement_max",
            0,
            100,
        )
        maximum_observed_disagreement = max(
            maximum_observed_disagreement, cell_disagreement
        )
        cell_profiles.setdefault((cohort, task), set()).add(profile)
        if runs < spec["minimum_repetitions"] or passed_runs != runs:
            reasons.append("cell_automated_checks_failed")
        if cell.get("recommendation_status") != "reviewed":
            reasons.append("cell_not_reviewed")
        if cell_coverage < spec["minimum_review_coverage"]:
            reasons.append("cell_review_coverage_below_minimum")
        if cell_disagreement > spec["maximum_review_disagreement"]:
            reasons.append("cell_review_disagreement_exceeded")

    rankings_raw = _list(matrix_report.get("rankings"), "matrix_rankings")
    if not rankings_raw:
        raise ModelPromotionGateError("matrix_rankings_empty")
    rankings: list[Mapping[str, Any]] = []
    actual_targets: list[tuple[str, str]] = []
    for index, raw in enumerate(rankings_raw):
        ranking = _mapping(raw, f"matrix_ranking_{index}")
        cohort = _safe_id(ranking.get("cohort"), "ranking_cohort")
        task = _safe_id(ranking.get("task_type"), "ranking_task_type")
        ordered = _list(ranking.get("ordered_profiles"), "ordered_profiles")
        ordered_profiles = tuple(
            _safe_id(item, "ordered_profile") for item in ordered
        )
        if not ordered_profiles or len(set(ordered_profiles)) != len(ordered_profiles):
            raise ModelPromotionGateError("ordered_profiles_invalid")
        status = str(ranking.get("status", ""))
        raw_recommended = str(ranking.get("recommended_profile", "")).strip()
        if status == "reviewed":
            recommended = _safe_id(
                raw_recommended, "ranking_recommended_profile"
            )
            if recommended not in ordered_profiles:
                raise ModelPromotionGateError("recommended_profile_not_ranked")
        elif raw_recommended:
            recommended = _safe_id(
                raw_recommended, "ranking_recommended_profile"
            )
            if recommended not in ordered_profiles:
                raise ModelPromotionGateError("recommended_profile_not_ranked")
        if set(ordered_profiles) != cell_profiles.get((cohort, task), set()):
            raise ModelPromotionGateError("ranking_cell_profiles_mismatch")
        if status != "reviewed":
            reasons.append("ranking_not_reviewed")
        actual_targets.append((cohort, task))
        rankings.append(ranking)
    if len(set(actual_targets)) != len(actual_targets):
        raise ModelPromotionGateError("ranking_targets_duplicate")
    required_targets = set(spec["required_targets"])
    actual_target_set = set(actual_targets)
    if not required_targets.issubset(actual_target_set):
        reasons.append("required_matrix_targets_missing")
    if not spec["allow_additional_targets"] and actual_target_set != required_targets:
        reasons.append("unexpected_matrix_targets_present")

    if policy_patch.get("schema_version") != "1.0":
        raise ModelPromotionGateError("policy_patch_schema_invalid")
    if _safe_id(policy_patch.get("matrix_id"), "policy_matrix_id") != matrix_id:
        raise ModelPromotionGateError("policy_matrix_id_mismatch")
    expected_policy = _expected_policy(rankings)
    supplied_policy = {
        "schema_version": policy_patch.get("schema_version"),
        "task_policies": policy_patch.get("task_policies"),
        "cohort_task_policies": policy_patch.get("cohort_task_policies"),
    }
    if supplied_policy != expected_policy:
        raise ModelPromotionGateError("policy_patch_does_not_match_rankings")

    scale_result, scale_reasons = _validate_gate_evidence(
        scale_gate,
        label="scale",
        environment_id=spec["environment_id"],
        required=spec["require_scale_gate"],
    )
    operational_result, operational_reasons = _validate_gate_evidence(
        operational_gate,
        label="operational",
        environment_id=spec["environment_id"],
        required=spec["require_operational_gate"],
    )
    reasons.extend(scale_reasons)
    reasons.extend(operational_reasons)
    reason_codes = sorted(set(reasons))
    approved = not reason_codes

    return {
        "schema_version": "1.0",
        "gate_id": spec["gate_id"],
        "environment_id": spec["environment_id"],
        "decision": "approved" if approved else "blocked",
        "approved": approved,
        "reason_codes": reason_codes,
        "matrix_id": matrix_id,
        "suite_id": suite_id,
        "matrix_fingerprint": _fingerprint(matrix_report),
        "policy_fingerprint": _fingerprint(policy_patch),
        "repetitions": repetitions,
        "cell_count": len(cells),
        "target_count": len(actual_targets),
        "review_target_count": total_review_targets,
        "reviewer_quorum": quorum,
        "review_coverage": coverage,
        "maximum_review_disagreement_observed": round(
            maximum_observed_disagreement, 6
        ),
        "prerequisites": {
            "scale": scale_result,
            "operational": operational_result,
        },
        "policy_patch": {
            "task_policies": expected_policy["task_policies"],
            "cohort_task_policies": expected_policy["cohort_task_policies"],
        },
    }


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelPromotionGateError(f"cannot_load_{label}") from exc
    if not isinstance(value, dict):
        raise ModelPromotionGateError(f"{label}_root_invalid")
    return value


def _run(args: argparse.Namespace) -> int:
    result = evaluate_model_promotion(
        _load_json(args.config, "config"),
        _load_json(args.matrix_report, "matrix_report"),
        _load_json(args.policy_patch, "policy_patch"),
        scale_gate=_load_json(args.scale_gate, "scale_gate") if args.scale_gate else None,
        operational_gate=(
            _load_json(args.operational_gate, "operational_gate")
            if args.operational_gate
            else None
        ),
    )
    if args.output:
        write_report_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["approved"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate reviewed model-policy promotion artifacts"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--matrix-report", required=True)
    parser.add_argument("--policy-patch", required=True)
    parser.add_argument("--scale-gate", default="")
    parser.add_argument("--operational-gate", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except ModelPromotionGateError as exc:
        print(f"model promotion gate error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
