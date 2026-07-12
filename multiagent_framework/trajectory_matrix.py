"""Credential-gated live trajectory matrix evaluation with redacted reports.

The matrix compares explicit model profiles for each task. It never serializes
prompts, messages, responses, provider URLs, credentials, trace IDs, agent IDs,
or input paths.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .model_gateway import ModelGateway, ModelGatewayError
from .trajectory_evaluation import (
    LiveToolExecutor,
    TrajectoryEvaluationError,
    TrajectoryEvaluator,
    load_trajectory_suite,
    write_report_atomic,
)


REVIEW_DIMENSIONS = (
    "correctness",
    "tool_discipline",
    "memory_quality",
    "world_consistency",
    "latency_value",
    "cost_value",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_RATE_KEYS = (
    "tool_legality_rate",
    "termination_rate",
    "routing_accuracy",
    "memory_recall_rate",
    "commitment_outcome_rate",
    "resource_conservation_rate",
)


class TrajectoryMatrixError(ValueError):
    """Raised when a matrix or human-review input is unsafe or malformed."""


@dataclass(frozen=True)
class MatrixCellResult:
    cell_id: str
    cohort: str
    task_type: str
    profile: str
    provider: str
    model: str
    runs: int
    passed_runs: int
    pass_rate: float
    automated_score_mean: float
    automated_score_min: float
    automated_score_stddev: float
    latency_ms_p50: float
    latency_ms_p95: float
    tokens_mean: float
    tokens_total: int
    tool_legality_rate: float | None = None
    termination_rate: float | None = None
    routing_accuracy: float | None = None
    memory_recall_rate: float | None = None
    commitment_outcome_rate: float | None = None
    resource_conservation_rate: float | None = None
    human_score_mean: float | None = None
    reviewer_count: int = 0
    review_coverage: float = 0.0
    review_disagreement_max: float = 0.0
    recommendation_status: str = "provisional"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatrixRanking:
    cohort: str
    task_type: str
    ordered_profiles: tuple[str, ...]
    recommended_profile: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ordered_profiles"] = list(self.ordered_profiles)
        return data


@dataclass(frozen=True)
class TrajectoryMatrixResult:
    matrix_id: str
    suite_id: str
    mode: str
    repetitions: int
    passed: bool
    approved: bool
    cells: tuple[MatrixCellResult, ...] = ()
    rankings: tuple[MatrixRanking, ...] = ()
    review_summary: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "matrix_id": self.matrix_id,
            "suite_id": self.suite_id,
            "mode": self.mode,
            "repetitions": self.repetitions,
            "passed": self.passed,
            "approved": self.approved,
            "cells": [item.to_dict() for item in self.cells],
            "rankings": [item.to_dict() for item in self.rankings],
            "review_summary": dict(self.review_summary),
        }


@dataclass(frozen=True)
class _Review:
    cell_id: str
    scenario_id: str
    reviewer_id: str
    dimensions: Mapping[str, float]

    @property
    def score(self) -> float:
        return sum(self.dimensions.values()) / len(self.dimensions)


EvaluatorFactory = Callable[[ModelGateway, LiveToolExecutor | None], TrajectoryEvaluator]


class TrajectoryMatrixRunner:
    """Run a strict task/profile/repetition live matrix and aggregate safe evidence."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        live_tool_executor: LiveToolExecutor | None = None,
        evaluator_factory: EvaluatorFactory | None = None,
    ) -> None:
        self.gateway = gateway
        self.live_tool_executor = live_tool_executor
        self.evaluator_factory = evaluator_factory or self._default_evaluator

    @staticmethod
    def _default_evaluator(
        gateway: ModelGateway, live_tool_executor: LiveToolExecutor | None
    ) -> TrajectoryEvaluator:
        return TrajectoryEvaluator(
            gateway=gateway,
            live_tool_executor=live_tool_executor,
            strict_profile=True,
        )

    async def evaluate(
        self,
        matrix: Mapping[str, Any],
        suite: Mapping[str, Any],
        *,
        reviews: Mapping[str, Any] | None = None,
    ) -> TrajectoryMatrixResult:
        spec = _validate_matrix(matrix)
        suite_id, scenarios_by_task = _index_suite(suite)
        review_items = _validate_reviews(reviews, spec["matrix_id"])
        allowed_review_targets: set[tuple[str, str]] = set()
        for cohort in spec["cohorts"]:
            for task_type, profiles in spec["tasks"].items():
                scenarios = _scenarios_for(
                    scenarios_by_task.get(task_type, ()), cohort
                )
                for profile in profiles:
                    cell_id = make_cell_id(task_type, profile, cohort=cohort)
                    allowed_review_targets.update(
                        (cell_id, str(scenario["id"])) for scenario in scenarios
                    )
        unknown_review_targets = sorted({
            (review.cell_id, review.scenario_id)
            for review in review_items
            if (review.cell_id, review.scenario_id) not in allowed_review_targets
        })
        if unknown_review_targets:
            raise TrajectoryMatrixError(
                "Human reviews reference cells or scenarios outside this matrix"
            )
        review_index = _review_index(review_items)
        cells: list[MatrixCellResult] = []
        target_count = 0
        reviewed_count = 0

        for cohort in spec["cohorts"]:
            for task_type, profiles in spec["tasks"].items():
                scenarios = _scenarios_for(
                    scenarios_by_task.get(task_type, ()), cohort
                )
                if not scenarios:
                    raise TrajectoryMatrixError(
                        f"No trajectory scenarios are defined for cohort {cohort!r} "
                        f"and task {task_type!r}"
                    )
                scenario_ids = tuple(str(item["id"]) for item in scenarios)
                for profile in profiles:
                    metadata = self._validate_profile(task_type, profile)
                    cell_id = make_cell_id(task_type, profile, cohort=cohort)
                    reviews_by_scenario = {
                        scenario_id: review_index.get((cell_id, scenario_id), ())
                        for scenario_id in scenario_ids
                    }
                    qualified_scenarios = {
                        scenario_id
                        for scenario_id, target_reviews in reviews_by_scenario.items()
                        if len({item.reviewer_id for item in target_reviews})
                        >= spec["minimum_reviewers_per_target"]
                    }
                    cell_reviews = [
                        review
                        for target_reviews in reviews_by_scenario.values()
                        for review in target_reviews
                    ]
                    coverage = len(qualified_scenarios) / len(scenario_ids)
                    target_count += len(scenario_ids)
                    reviewed_count += len(qualified_scenarios)
                    reports = []
                    evaluator = self.evaluator_factory(
                        self.gateway, self.live_tool_executor
                    )
                    for repetition in range(spec["repetitions"]):
                        cell_suite = _cell_suite(
                            suite_id=suite_id,
                            cohort=cohort,
                            task_type=task_type,
                            profile=profile,
                            repetition=repetition,
                            scenarios=scenarios,
                        )
                        try:
                            reports.append(
                                await evaluator.evaluate_suite(cell_suite, mode="live")
                            )
                        except (TrajectoryEvaluationError, ModelGatewayError) as exc:
                            raise TrajectoryMatrixError(
                                f"Matrix cell {cell_id!r} could not be evaluated safely"
                            ) from exc
                    cells.append(
                        _aggregate_cell(
                            cell_id=cell_id,
                            cohort=cohort,
                            task_type=task_type,
                            profile=profile,
                            metadata=metadata,
                            reports=reports,
                            reviews=cell_reviews,
                            reviews_by_scenario=reviews_by_scenario,
                            coverage=coverage,
                            minimum_review_coverage=spec["minimum_review_coverage"],
                            maximum_review_disagreement=spec[
                                "maximum_review_disagreement"
                            ],
                        )
                    )

        rankings = _rank_cells(cells)
        passed = bool(cells) and all(cell.passed_runs == cell.runs for cell in cells)
        approved = passed and bool(rankings) and all(
            item.status == "reviewed" for item in rankings
        )
        return TrajectoryMatrixResult(
            matrix_id=spec["matrix_id"],
            suite_id=suite_id,
            mode="live",
            repetitions=spec["repetitions"],
            passed=passed,
            approved=approved,
            cells=tuple(cells),
            rankings=tuple(rankings),
            review_summary={
                "minimum_coverage": spec["minimum_review_coverage"],
                "minimum_reviewers_per_target": spec[
                    "minimum_reviewers_per_target"
                ],
                "maximum_review_disagreement": spec[
                    "maximum_review_disagreement"
                ],
                "reviewed_targets": reviewed_count,
                "total_targets": target_count,
                "coverage": round(reviewed_count / target_count, 6)
                if target_count
                else 0.0,
                "review_count": len(review_items),
                "dimensions": list(REVIEW_DIMENSIONS),
            },
        )

    def _validate_profile(self, task_type: str, profile: str) -> Mapping[str, Any]:
        if not self.gateway.has_profile(profile):
            raise TrajectoryMatrixError(f"Unknown model profile: {profile!r}")
        metadata = self.gateway.profile_metadata(profile)
        if not bool(metadata.get("supports_tools", False)):
            raise TrajectoryMatrixError(
                f"Model profile {profile!r} does not support tool calling"
            )
        candidates = self.gateway.candidates(
            task_type, profile, strict_profile=True
        )
        if candidates != [profile]:
            raise TrajectoryMatrixError(
                f"Model profile {profile!r} cannot be selected strictly"
            )
        if not self.gateway.credentials_available(
            task_type, profile, strict_profile=True
        ):
            raise TrajectoryMatrixError(
                f"Credentials are not configured for model profile {profile!r}"
            )
        return metadata


def make_cell_id(
    task_type: str, profile: str, *, cohort: str = "all"
) -> str:
    task = _safe_id(task_type, "task type")
    selected_profile = _safe_id(profile, "profile")
    selected_cohort = _safe_id(cohort, "cohort")
    if selected_cohort == "all":
        return f"{task}::{selected_profile}"
    return f"{selected_cohort}::{task}::{selected_profile}"


def build_review_template(
    matrix: Mapping[str, Any], suite: Mapping[str, Any]
) -> dict[str, Any]:
    spec = _validate_matrix(matrix)
    _suite_id, scenarios_by_task = _index_suite(suite)
    reviews: list[dict[str, Any]] = []
    for cohort in spec["cohorts"]:
        for task_type, profiles in spec["tasks"].items():
            scenarios = _scenarios_for(
                scenarios_by_task.get(task_type, ()), cohort
            )
            if not scenarios:
                raise TrajectoryMatrixError(
                    f"No trajectory scenarios are defined for cohort {cohort!r} "
                    f"and task {task_type!r}"
                )
            for profile in profiles:
                cell_id = make_cell_id(task_type, profile, cohort=cohort)
                for scenario in scenarios:
                    for _slot in range(spec["minimum_reviewers_per_target"]):
                        reviews.append(
                            {
                                "cell_id": cell_id,
                                "scenario_id": str(scenario["id"]),
                                "reviewer_id": "",
                                "dimensions": {
                                    name: None for name in REVIEW_DIMENSIONS
                                },
                            }
                        )
    return {
        "schema_version": "1.0",
        "matrix_id": spec["matrix_id"],
        "reviews": reviews,
    }


def production_policy_patch(result: TrajectoryMatrixResult) -> dict[str, Any]:
    task_policies: dict[str, list[str]] = {}
    cohort_policies: dict[str, dict[str, list[str]]] = {}
    for ranking in result.rankings:
        if ranking.status != "reviewed" or not ranking.recommended_profile:
            continue
        if ranking.cohort == "all":
            task_policies[ranking.task_type] = [ranking.recommended_profile]
        else:
            cohort_policies.setdefault(ranking.cohort, {})[ranking.task_type] = [
                ranking.recommended_profile
            ]
    return {
        "schema_version": "1.0",
        "matrix_id": result.matrix_id,
        "task_policies": task_policies,
        "cohort_task_policies": cohort_policies,
    }


def load_matrix_config(path: str | Path) -> dict[str, Any]:
    return _load_json_object(path, "trajectory matrix")


def load_human_reviews(path: str | Path) -> dict[str, Any]:
    return _load_json_object(path, "human reviews")


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrajectoryMatrixError(f"Cannot load {label} input") from exc
    if not isinstance(value, dict):
        raise TrajectoryMatrixError(f"{label} root must be an object")
    return value


def _validate_matrix(matrix: Mapping[str, Any]) -> dict[str, Any]:
    if str(matrix.get("schema_version", "")) != "1.0":
        raise TrajectoryMatrixError("Unsupported matrix schema_version")
    matrix_id = _safe_id(matrix.get("matrix_id"), "matrix_id")
    raw_tasks = matrix.get("tasks")
    if not isinstance(raw_tasks, Mapping) or not raw_tasks:
        raise TrajectoryMatrixError("Matrix tasks must be a non-empty object")
    tasks: dict[str, tuple[str, ...]] = {}
    for raw_task, raw_profiles in raw_tasks.items():
        task = _safe_id(raw_task, "task type")
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raise TrajectoryMatrixError(
                f"Matrix task {task!r} requires at least one profile"
            )
        profiles = tuple(_safe_id(item, "profile") for item in raw_profiles)
        if len(set(profiles)) != len(profiles):
            raise TrajectoryMatrixError(
                f"Matrix task {task!r} contains duplicate profiles"
            )
        tasks[task] = profiles
    raw_cohorts = matrix.get("cohorts", ["all"])
    if not isinstance(raw_cohorts, list) or not raw_cohorts:
        raise TrajectoryMatrixError("Matrix cohorts must be a non-empty list")
    cohorts = tuple(_safe_id(item, "cohort") for item in raw_cohorts)
    if len(set(cohorts)) != len(cohorts):
        raise TrajectoryMatrixError("Matrix cohorts contain duplicates")
    if "all" in cohorts and len(cohorts) > 1:
        raise TrajectoryMatrixError(
            "The all cohort cannot be combined with explicit cohorts"
        )
    try:
        repetitions = int(matrix.get("repetitions", 1))
    except (TypeError, ValueError) as exc:
        raise TrajectoryMatrixError("Matrix repetitions must be an integer") from exc
    if not 1 <= repetitions <= 100:
        raise TrajectoryMatrixError("Matrix repetitions must be between 1 and 100")
    raw_reviewer_quorum = matrix.get("minimum_reviewers_per_target", 1)
    if isinstance(raw_reviewer_quorum, bool) or not isinstance(
        raw_reviewer_quorum, int
    ):
        raise TrajectoryMatrixError(
            "minimum_reviewers_per_target must be an integer"
        )
    reviewer_quorum = raw_reviewer_quorum
    if not 1 <= reviewer_quorum <= 20:
        raise TrajectoryMatrixError(
            "minimum_reviewers_per_target must be between 1 and 20"
        )
    coverage = _finite_number(
        matrix.get("minimum_review_coverage", 1.0),
        "minimum_review_coverage",
    )
    if not 0 <= coverage <= 1:
        raise TrajectoryMatrixError(
            "minimum_review_coverage must be between 0 and 1"
        )
    disagreement = _finite_number(
        matrix.get("maximum_review_disagreement", 100.0),
        "maximum_review_disagreement",
    )
    if not 0 <= disagreement <= 100:
        raise TrajectoryMatrixError(
            "maximum_review_disagreement must be between 0 and 100"
        )
    return {
        "matrix_id": matrix_id,
        "tasks": tasks,
        "cohorts": cohorts,
        "repetitions": repetitions,
        "minimum_reviewers_per_target": reviewer_quorum,
        "minimum_review_coverage": coverage,
        "maximum_review_disagreement": disagreement,
    }


def _index_suite(
    suite: Mapping[str, Any],
) -> tuple[str, dict[str, tuple[Mapping[str, Any], ...]]]:
    if str(suite.get("schema_version", "")) != "1.0":
        raise TrajectoryMatrixError("Unsupported trajectory schema_version")
    suite_id = _safe_id(suite.get("suite_id"), "suite_id")
    raw_scenarios = suite.get("scenarios")
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise TrajectoryMatrixError("Trajectory suite requires scenarios")
    result: dict[str, list[Mapping[str, Any]]] = {}
    seen: set[str] = set()
    for raw in raw_scenarios:
        if not isinstance(raw, Mapping):
            raise TrajectoryMatrixError("Trajectory scenarios must be objects")
        scenario_id = _safe_id(raw.get("id"), "scenario_id")
        task = _safe_id(raw.get("task"), "task type")
        raw_cohorts = raw.get("cohorts", [])
        if not isinstance(raw_cohorts, list):
            raise TrajectoryMatrixError(
                f"Trajectory scenario {scenario_id!r} cohorts must be a list"
            )
        cohorts = tuple(_safe_id(item, "cohort") for item in raw_cohorts)
        if len(set(cohorts)) != len(cohorts):
            raise TrajectoryMatrixError(
                f"Trajectory scenario {scenario_id!r} has duplicate cohorts"
            )
        if scenario_id in seen:
            raise TrajectoryMatrixError(
                f"Duplicate trajectory scenario id: {scenario_id!r}"
            )
        seen.add(scenario_id)
        if not isinstance(raw.get("live"), Mapping):
            raise TrajectoryMatrixError(
                f"Trajectory scenario {scenario_id!r} has no live definition"
            )
        result.setdefault(task, []).append(raw)
    return suite_id, {key: tuple(value) for key, value in result.items()}


def _scenarios_for(
    scenarios: Sequence[Mapping[str, Any]], cohort: str
) -> tuple[Mapping[str, Any], ...]:
    if cohort == "all":
        return tuple(scenarios)
    return tuple(
        scenario
        for scenario in scenarios
        if cohort in {str(item) for item in scenario.get("cohorts", [])}
    )


def _cell_suite(
    *,
    suite_id: str,
    cohort: str,
    task_type: str,
    profile: str,
    repetition: int,
    scenarios: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    copied = []
    for scenario in scenarios:
        item = copy.deepcopy(dict(scenario))
        item["profile_override"] = profile
        copied.append(item)
    return {
        "schema_version": "1.0",
        "suite_id": f"{suite_id}:{cohort}:{task_type}:{profile}:{repetition + 1}",
        "mode": "live",
        "minimum_score": 100,
        "scenarios": copied,
    }


def _validate_reviews(
    reviews: Mapping[str, Any] | None, matrix_id: str
) -> tuple[_Review, ...]:
    if reviews is None:
        return ()
    if str(reviews.get("schema_version", "")) != "1.0":
        raise TrajectoryMatrixError("Unsupported human review schema_version")
    if str(reviews.get("matrix_id", "")) != matrix_id:
        raise TrajectoryMatrixError("Human reviews target a different matrix_id")
    raw_reviews = reviews.get("reviews")
    if not isinstance(raw_reviews, list):
        raise TrajectoryMatrixError("Human reviews must be a list")
    allowed = {"cell_id", "scenario_id", "reviewer_id", "dimensions"}
    seen: set[tuple[str, str, str]] = set()
    result: list[_Review] = []
    for index, raw in enumerate(raw_reviews):
        if not isinstance(raw, Mapping):
            raise TrajectoryMatrixError(f"reviews[{index}] must be an object")
        unexpected = set(raw) - allowed
        if unexpected:
            raise TrajectoryMatrixError(
                "Human reviews may not contain notes or unrecognized fields"
            )
        cell_id = _safe_id(raw.get("cell_id"), "cell_id")
        scenario_id = _safe_id(raw.get("scenario_id"), "scenario_id")
        reviewer_id = _safe_id(raw.get("reviewer_id"), "reviewer_id")
        raw_dimensions = raw.get("dimensions")
        if not isinstance(raw_dimensions, Mapping):
            raise TrajectoryMatrixError("Review dimensions must be an object")
        if set(raw_dimensions) != set(REVIEW_DIMENSIONS):
            raise TrajectoryMatrixError(
                "Review dimensions must contain the complete approved dimension set"
            )
        dimensions = {
            name: _review_score(raw_dimensions[name], name)
            for name in REVIEW_DIMENSIONS
        }
        key = (cell_id, scenario_id, reviewer_id)
        if key in seen:
            raise TrajectoryMatrixError(
                "Duplicate reviewer/cell/scenario review"
            )
        seen.add(key)
        result.append(
            _Review(
                cell_id=cell_id,
                scenario_id=scenario_id,
                reviewer_id=reviewer_id,
                dimensions=dimensions,
            )
        )
    return tuple(result)


def _review_index(
    reviews: Sequence[_Review],
) -> dict[tuple[str, str], tuple[_Review, ...]]:
    grouped: dict[tuple[str, str], list[_Review]] = {}
    for review in reviews:
        grouped.setdefault((review.cell_id, review.scenario_id), []).append(review)
    return {key: tuple(value) for key, value in grouped.items()}


def _aggregate_cell(
    *,
    cell_id: str,
    cohort: str,
    task_type: str,
    profile: str,
    metadata: Mapping[str, Any],
    reports: Sequence[Any],
    reviews: Sequence[_Review],
    reviews_by_scenario: Mapping[str, Sequence[_Review]],
    coverage: float,
    minimum_review_coverage: float,
    maximum_review_disagreement: float,
) -> MatrixCellResult:
    scores = [float(report.score) for report in reports]
    latencies = [
        float(scenario.elapsed_ms)
        for report in reports
        for scenario in report.scenarios
    ]
    tokens = [
        int(scenario.total_tokens)
        for report in reports
        for scenario in report.scenarios
    ]
    rates: dict[str, float | None] = {}
    for key in _RATE_KEYS:
        values = [
            float(report.metrics[key])
            for report in reports
            if report.metrics.get(key) is not None
        ]
        rates[key] = round(statistics.fmean(values), 6) if values else None
    human_score = (
        round(statistics.fmean(review.score for review in reviews), 3)
        if reviews
        else None
    )
    disagreements = [
        statistics.pstdev(item.score for item in target_reviews)
        if len(target_reviews) > 1
        else 0.0
        for target_reviews in reviews_by_scenario.values()
        if target_reviews
    ]
    disagreement_max = max(disagreements, default=0.0)
    status = (
        "reviewed"
        if human_score is not None
        and coverage >= minimum_review_coverage
        and disagreement_max <= maximum_review_disagreement
        else "provisional"
    )
    return MatrixCellResult(
        cell_id=cell_id,
        cohort=cohort,
        task_type=task_type,
        profile=profile,
        provider=_safe_metadata(metadata.get("provider"), "provider"),
        model=_safe_metadata(metadata.get("model"), "model"),
        runs=len(reports),
        passed_runs=sum(1 for report in reports if report.passed),
        pass_rate=round(
            sum(1 for report in reports if report.passed) / len(reports), 6
        ),
        automated_score_mean=round(statistics.fmean(scores), 3),
        automated_score_min=round(min(scores), 3),
        automated_score_stddev=round(
            statistics.pstdev(scores) if len(scores) > 1 else 0.0, 3
        ),
        latency_ms_p50=round(_percentile(latencies, 0.50), 3),
        latency_ms_p95=round(_percentile(latencies, 0.95), 3),
        tokens_mean=round(statistics.fmean(tokens), 3) if tokens else 0.0,
        tokens_total=sum(tokens),
        human_score_mean=human_score,
        reviewer_count=len({review.reviewer_id for review in reviews}),
        review_coverage=round(coverage, 6),
        review_disagreement_max=round(disagreement_max, 3),
        recommendation_status=status,
        **rates,
    )


def _rank_cells(cells: Sequence[MatrixCellResult]) -> list[MatrixRanking]:
    grouped: dict[tuple[str, str], list[MatrixCellResult]] = {}
    for cell in cells:
        grouped.setdefault((cell.cohort, cell.task_type), []).append(cell)
    rankings: list[MatrixRanking] = []
    for (cohort, task_type), task_cells in grouped.items():
        ordered = sorted(
            task_cells,
            key=lambda cell: (
                cell.passed_runs == cell.runs,
                cell.human_score_mean if cell.human_score_mean is not None else -1.0,
                cell.automated_score_mean,
                cell.pass_rate,
                -cell.review_disagreement_max,
                -cell.latency_ms_p95,
                -cell.tokens_mean,
                cell.profile,
            ),
            reverse=True,
        )
        winner = ordered[0]
        all_candidates_reviewed = all(
            cell.recommendation_status == "reviewed"
            for cell in task_cells
            if cell.passed_runs == cell.runs
        )
        if winner.passed_runs != winner.runs:
            status = "not_approved"
            recommended_profile = ""
        elif all_candidates_reviewed:
            status = "reviewed"
            recommended_profile = winner.profile
        else:
            status = "provisional"
            recommended_profile = winner.profile
        rankings.append(
            MatrixRanking(
                cohort=cohort,
                task_type=task_type,
                ordered_profiles=tuple(cell.profile for cell in ordered),
                recommended_profile=recommended_profile,
                status=status,
            )
        )
    return rankings


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise TrajectoryMatrixError(
            f"{label} must be a short identifier using letters, numbers, . _ : or -"
        )
    return text


def _safe_metadata(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160 or any(ch in text for ch in "\r\n\t"):
        raise TrajectoryMatrixError(f"Unsafe {label} metadata")
    lowered = text.lower()
    if "://" in lowered or "api_key" in lowered or "authorization" in lowered:
        raise TrajectoryMatrixError(f"Unsafe {label} metadata")
    return text


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryMatrixError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise TrajectoryMatrixError(f"{label} must be finite")
    return number


def _review_score(value: Any, label: str) -> float:
    number = _finite_number(value, label)
    if not 0 <= number <= 100:
        raise TrajectoryMatrixError(f"{label} must be between 0 and 100")
    return number


async def _run_cli(args: argparse.Namespace) -> int:
    matrix = load_matrix_config(args.matrix)
    suite_path = args.suite or str(matrix.get("suite", ""))
    if not suite_path:
        raise TrajectoryMatrixError("Matrix config or --suite must specify a suite")
    suite = load_trajectory_suite(suite_path)
    reviews = load_human_reviews(args.reviews) if args.reviews else None
    if args.review_template:
        write_report_atomic(args.review_template, build_review_template(matrix, suite))
    if args.template_only:
        if not args.review_template:
            raise TrajectoryMatrixError(
                "--template-only requires --review-template"
            )
        return 0
    gateway = ModelGateway.from_files(args.models, args.legacy)
    try:
        result = await TrajectoryMatrixRunner(gateway=gateway).evaluate(
            matrix, suite, reviews=reviews
        )
        payload = result.to_dict()
        if args.output:
            write_report_atomic(args.output, payload)
        if args.policy_output:
            write_report_atomic(args.policy_output, production_policy_patch(result))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result.approved else 1
    finally:
        await gateway.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a strict live trajectory model matrix"
    )
    parser.add_argument("matrix", help="Matrix JSON path")
    parser.add_argument("--suite", default="")
    parser.add_argument("--reviews", default="")
    parser.add_argument("--models", default="config/models.json")
    parser.add_argument("--legacy", default="config/llm.json")
    parser.add_argument("--output", default="")
    parser.add_argument("--review-template", default="")
    parser.add_argument("--template-only", action="store_true")
    parser.add_argument("--policy-output", default="")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_cli(args))
    except (TrajectoryMatrixError, TrajectoryEvaluationError, ValueError) as exc:
        print(f"trajectory matrix error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
