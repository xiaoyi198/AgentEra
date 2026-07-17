"""Tests for strict, redacted task/profile trajectory matrices."""

import asyncio
import json
from copy import deepcopy

import pytest

from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.trajectory_evaluation import load_trajectory_suite, write_report_atomic
from multiagent_framework.trajectory_matrix import load_matrix_config
from multiagent_framework.trajectory_runtime import IsolatedTrajectoryRuntimeFactory
from multiagent_framework.trajectory_matrix import (
    REVIEW_DIMENSIONS,
    TrajectoryMatrixError,
    TrajectoryMatrixRunner,
    build_review_template,
    main,
    make_cell_id,
    production_policy_patch,
)


class MatrixLLM:
    def __init__(self, profile):
        self.profile = profile

    async def chat(self, **kwargs):
        tool = kwargs["tools"][-1]["function"]["name"]
        base = 10 if self.profile == "alpha" else 20
        return LLMResponse(
            tool_calls=[{
                "id": "redacted-call-id",
                "function": {"name": tool, "arguments": "{}"},
            }],
            input_tokens=base,
            output_tokens=5,
            latency_ms=base,
        )


class MatrixGateway:
    def __init__(self):
        self.profiles = {
            "alpha": {
                "profile": "alpha",
                "provider": "provider-a",
                "model": "model-a",
                "supports_tools": True,
            },
            "beta": {
                "profile": "beta",
                "provider": "provider-b",
                "model": "model-b",
                "supports_tools": True,
            },
        }
        self.missing_credentials = set()
        self.bindings = []

    def has_profile(self, profile):
        return profile in self.profiles

    def profile_metadata(self, profile):
        return dict(self.profiles[profile])

    def candidates(self, task, profile_override="", *, strict_profile=False):
        assert strict_profile
        return [profile_override] if profile_override in self.profiles else []

    def credentials_available(self, task, profile_override="", *, strict_profile=False):
        assert strict_profile
        return profile_override not in self.missing_credentials

    def for_task(self, task, **kwargs):
        assert kwargs.get("strict_profile") is True
        profile = kwargs["profile_override"]
        self.bindings.append((task, profile))
        return MatrixLLM(profile)


def scenario(task="agent.turn", scenario_id="scenario-one"):
    terminal = "submit_result"
    return {
        "id": scenario_id,
        "task": task,
        "live": {
            "messages": [{"role": "user", "content": "SECRET PROMPT BODY"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": terminal,
                    "description": "terminal",
                    "parameters": {"type": "object"},
                },
            }],
            "terminal_tools": [terminal],
            "budget": {"max_rounds": 1, "max_tool_calls": 1, "max_tokens": 100},
            "expect": {
                "allowed_tools": [terminal],
                "required_terminal_tool": terminal,
                "max_rounds": 1,
                "max_tool_calls": 1,
                "max_tokens": 100,
            },
        },
    }


def suite(*scenarios):
    return {
        "schema_version": "1.0",
        "suite_id": "safe-suite",
        "scenarios": list(scenarios or (scenario(),)),
    }


def matrix(**overrides):
    value = {
        "schema_version": "1.0",
        "matrix_id": "safe-matrix",
        "tasks": {"agent.turn": ["alpha", "beta"]},
        "repetitions": 2,
        "minimum_review_coverage": 1.0,
    }
    value.update(overrides)
    return value


def run(
    matrix_value=None,
    suite_value=None,
    gateway=None,
    reviews=None,
    runtime_factory=None,
):
    gateway = gateway or MatrixGateway()
    result = asyncio.run(
        TrajectoryMatrixRunner(
            gateway=gateway,
            live_tool_session_factory=runtime_factory,
        ).evaluate(matrix_value or matrix(), suite_value or suite(), reviews=reviews)
    )
    return result, gateway


def complete_reviews(matrix_value=None, suite_value=None, score=90):
    template = build_review_template(matrix_value or matrix(), suite_value or suite())
    for index, review in enumerate(template["reviews"]):
        review["reviewer_id"] = f"reviewer-{index + 1}"
        review["dimensions"] = {name: score for name in REVIEW_DIMENSIONS}
    return template


def test_matrix_expands_task_profile_repetitions_and_forces_strict_override():
    result, gateway = run()

    assert len(result.cells) == 2
    assert [cell.runs for cell in result.cells] == [2, 2]
    assert gateway.bindings == [
        ("agent.turn", "alpha"),
        ("agent.turn", "alpha"),
        ("agent.turn", "beta"),
        ("agent.turn", "beta"),
    ]
    assert result.passed
    assert not result.approved
    assert all(cell.recommendation_status == "provisional" for cell in result.cells)


def test_matrix_aggregates_safe_latency_tokens_rates_and_metadata():
    result, _gateway = run()
    alpha, beta = result.cells

    assert alpha.provider == "provider-a"
    assert alpha.model == "model-a"
    assert alpha.passed_runs == 2
    assert alpha.pass_rate == 1
    assert alpha.automated_score_mean == 100
    assert alpha.latency_ms_p50 >= 0
    assert alpha.latency_ms_p95 >= alpha.latency_ms_p50
    assert alpha.tokens_mean == 15
    assert alpha.tokens_total == 30
    assert alpha.tool_legality_rate == 1
    assert beta.tokens_total == 50

    raw = json.dumps(result.to_dict(), ensure_ascii=False)
    for forbidden in (
        "SECRET PROMPT BODY",
        "redacted-call-id",
        "messages",
        "response",
        "api_key",
        "https://",
        "trace_id",
        "agent_id",
    ):
        assert forbidden not in raw


@pytest.mark.parametrize("failure", ["missing", "credentials", "tools"])
def test_matrix_rejects_unusable_profile(failure):
    gateway = MatrixGateway()
    matrix_value = matrix(tasks={"agent.turn": ["alpha"]})
    if failure == "missing":
        matrix_value["tasks"]["agent.turn"] = ["unknown"]
    elif failure == "credentials":
        gateway.missing_credentials.add("alpha")
    else:
        gateway.profiles["alpha"]["supports_tools"] = False

    with pytest.raises(TrajectoryMatrixError):
        run(matrix_value=matrix_value, gateway=gateway)


def test_human_reviews_promote_complete_cells_and_policy_patch():
    reviews = complete_reviews()
    result, _gateway = run(reviews=reviews)

    assert result.approved
    assert all(cell.review_coverage == 1 for cell in result.cells)
    assert all(cell.human_score_mean == 90 for cell in result.cells)
    assert all(cell.recommendation_status == "reviewed" for cell in result.cells)
    assert all(item.status == "reviewed" for item in result.rankings)
    patch = production_policy_patch(result)
    assert patch["task_policies"] == {"agent.turn": ["alpha"]}
    assert "provider" not in json.dumps(patch)


def test_partial_reviews_remain_provisional_and_never_enter_policy_patch():
    suite_value = suite(
        scenario(scenario_id="scenario-one"),
        scenario(scenario_id="scenario-two"),
    )
    matrix_value = matrix(tasks={"agent.turn": ["alpha"]})
    reviews = complete_reviews(matrix_value, suite_value)
    reviews["reviews"] = reviews["reviews"][:1]

    result, _gateway = run(matrix_value, suite_value, reviews=reviews)

    assert result.cells[0].review_coverage == 0.5
    assert result.cells[0].recommendation_status == "provisional"
    assert not result.approved
    assert production_policy_patch(result)["task_policies"] == {}


@pytest.mark.parametrize("mutation", ["duplicate", "range", "nan", "notes", "dimensions"])
def test_human_review_schema_is_strict(mutation):
    reviews = complete_reviews()
    if mutation == "duplicate":
        reviews["reviews"].append(deepcopy(reviews["reviews"][0]))
    elif mutation == "range":
        reviews["reviews"][0]["dimensions"]["correctness"] = 101
    elif mutation == "nan":
        reviews["reviews"][0]["dimensions"]["correctness"] = float("nan")
    elif mutation == "notes":
        reviews["reviews"][0]["notes"] = "model output goes here"
    else:
        reviews["reviews"][0]["dimensions"].pop("cost_value")

    with pytest.raises(TrajectoryMatrixError):
        run(reviews=reviews)


def test_review_template_contains_only_safe_identifiers_and_empty_dimensions():
    value = build_review_template(matrix(), suite())
    raw = json.dumps(value, ensure_ascii=False)

    assert len(value["reviews"]) == 2
    assert value["reviews"][0]["cell_id"] == make_cell_id("agent.turn", "alpha")
    assert all(score is None for score in value["reviews"][0]["dimensions"].values())
    assert "SECRET PROMPT BODY" not in raw
    assert "messages" not in raw
    assert "response" not in raw


def test_atomic_matrix_report_has_no_inputs(tmp_path):
    result, _gateway = run()
    target = tmp_path / "report.json"
    write_report_atomic(target, result.to_dict())

    raw = target.read_text(encoding="utf-8")
    assert json.loads(raw)["matrix_id"] == "safe-matrix"
    assert "SECRET PROMPT BODY" not in raw
    assert "api_key" not in raw.lower()


def test_cli_invalid_input_returns_two(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main([str(bad)]) == 2



class SequenceLLM:
    def __init__(self):
        self.index = 0

    async def chat(self, **kwargs):
        tools = kwargs["tools"]
        selected = tools[min(self.index, len(tools) - 1)]["function"]["name"]
        arguments_by_tool = {
            "recall_memory": {"query": "tax commitment"},
            "submit_report": {
                "commitment_id": "commitment-tax-v1",
                "status": "resolved",
                "summary": "resolved from retrieved evidence",
            },
            "route_intent": {"target": "hubu", "reason_code": "fiscal_reserve"},
            "inspect_inventory": {"scope": "anchored_transfer"},
            "apply_world_command": {
                "command_type": "transfer_resource",
                "source_entity": "treasury",
                "target_entity": "relief_granary",
                "resource_id": "grain_token",
                "quantity": 10,
                "unit_base_value": 2,
                "total_base_value": 20,
                "evidence_generation": 4,
            },
            "inspect_memory": {"scope": "unresolved_commitments"},
            "submit_memory_summary": {
                "preserved_ids": ["commitment-grain-v1"],
                "commitment_id": "commitment-grain-v1",
                "status": "retained",
                "summary": "commitment retained",
            },
            "inspect_world": {"topic": "grain"},
            "submit_advice": {
                "summary": "bounded grain advice",
                "evidence_generation": 7,
                "reserve_days": 60,
                "actions": ["reduce outflow", "protect relief floor"],
            },
        }
        self.index += 1
        return LLMResponse(
            tool_calls=[{
                "id": f"call-{self.index}",
                "function": {
                    "name": selected,
                    "arguments": json.dumps(arguments_by_tool[selected]),
                },
            }],
            input_tokens=10,
            output_tokens=5,
            latency_ms=2,
        )


class SequenceGateway(MatrixGateway):
    def for_task(self, task, **kwargs):
        assert kwargs.get("strict_profile") is True
        self.bindings.append((task, kwargs["profile_override"]))
        return SequenceLLM()


def test_repository_live_suite_covers_all_model_tasks_with_fake_live_gateway():
    matrix_value = load_matrix_config("tests/evals/daming_live_model_matrix.json")
    suite_value = load_trajectory_suite("tests/evals/daming_live_trajectory_scenarios.json")
    matrix_value["tasks"] = {
        task: ["alpha"] for task in matrix_value["tasks"]
    }
    matrix_value["repetitions"] = 1

    result, gateway = run(
        matrix_value=matrix_value,
        suite_value=suite_value,
        gateway=SequenceGateway(),
        runtime_factory=IsolatedTrajectoryRuntimeFactory(),
    )

    assert result.passed
    assert len(result.cells) == 5
    assert {cell.task_type for cell in result.cells} == {
        "agent.turn",
        "router.ambiguous",
        "action.simulation",
        "memory.compress",
        "tianji.chat",
    }
    assert next(
        cell for cell in result.cells if cell.task_type == "action.simulation"
    ).resource_conservation_rate == 1
    assert all(cell.tool_argument_contract_rate == 1 for cell in result.cells)
    assert len(gateway.bindings) == 5


def test_reviews_cannot_reference_unknown_cell_or_scenario():
    reviews = complete_reviews()
    reviews["reviews"][0]["scenario_id"] = "outside-suite"
    with pytest.raises(TrajectoryMatrixError, match="outside this matrix"):
        run(reviews=reviews)


def test_unsafe_provider_or_model_metadata_is_rejected():
    gateway = MatrixGateway()
    gateway.profiles["alpha"]["provider"] = "https://private-provider.invalid/v1"
    with pytest.raises(TrajectoryMatrixError, match="Unsafe provider metadata"):
        run(matrix_value=matrix(tasks={"agent.turn": ["alpha"]}), gateway=gateway)


@pytest.mark.parametrize("status", [0, 1, 2])
def test_cli_preserves_documented_exit_codes(monkeypatch, status):
    import multiagent_framework.trajectory_matrix as module

    async def fake_run(_args):
        return status

    monkeypatch.setattr(module, "_run_cli", fake_run)
    assert module.main(["unused.json"]) == status


def test_cli_can_generate_review_template_without_credentials(tmp_path):
    target = tmp_path / "review-template.json"
    status = main([
        "tests/evals/daming_live_model_matrix.json",
        "--review-template",
        str(target),
        "--template-only",
    ])

    assert status == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert len(payload["reviews"]) == 18
    assert len({
        (item["cell_id"], item["scenario_id"])
        for item in payload["reviews"]
    }) == 9
    raw = target.read_text(encoding="utf-8")
    assert "messages" not in raw
    assert "api_key" not in raw.lower()


def test_reviewer_quorum_requires_independent_reviews_per_target():
    matrix_value = matrix(
        tasks={"agent.turn": ["alpha"]},
        minimum_reviewers_per_target=2,
    )
    reviews = complete_reviews(matrix_value, suite())
    assert len(reviews["reviews"]) == 2
    reviews["reviews"] = reviews["reviews"][:1]

    result, _gateway = run(matrix_value=matrix_value, reviews=reviews)

    assert result.cells[0].reviewer_count == 1
    assert result.cells[0].review_coverage == 0
    assert result.cells[0].recommendation_status == "provisional"
    assert not result.approved


def test_review_disagreement_blocks_approval_even_with_full_quorum():
    matrix_value = matrix(
        tasks={"agent.turn": ["alpha"]},
        minimum_reviewers_per_target=2,
        maximum_review_disagreement=10,
    )
    reviews = complete_reviews(matrix_value, suite())
    reviews["reviews"][0]["dimensions"] = {
        name: 0 for name in REVIEW_DIMENSIONS
    }
    reviews["reviews"][1]["dimensions"] = {
        name: 100 for name in REVIEW_DIMENSIONS
    }

    result, _gateway = run(matrix_value=matrix_value, reviews=reviews)

    assert result.cells[0].review_coverage == 1
    assert result.cells[0].review_disagreement_max == 50
    assert result.cells[0].recommendation_status == "provisional"
    assert not result.approved


def test_all_passing_candidates_require_review_before_task_is_approved():
    reviews = complete_reviews()
    reviews["reviews"] = [
        item for item in reviews["reviews"]
        if item["cell_id"] == make_cell_id("agent.turn", "alpha")
    ]

    result, _gateway = run(reviews=reviews)

    alpha = next(cell for cell in result.cells if cell.profile == "alpha")
    beta = next(cell for cell in result.cells if cell.profile == "beta")
    assert alpha.recommendation_status == "reviewed"
    assert beta.recommendation_status == "provisional"
    assert result.rankings[0].status == "provisional"
    assert not result.approved
    assert production_policy_patch(result)["task_policies"] == {}


def test_cohort_partition_filters_scenarios_and_emits_separate_policy_advice():
    suite_value = suite(
        {**scenario(scenario_id="core-one"), "cohorts": ["daming-core"]},
        {**scenario(scenario_id="frontier-one"), "cohorts": ["frontier"]},
    )
    matrix_value = matrix(
        tasks={"agent.turn": ["alpha"]},
        cohorts=["daming-core"],
        repetitions=1,
    )
    reviews = complete_reviews(matrix_value, suite_value)

    result, gateway = run(matrix_value, suite_value, reviews=reviews)

    assert len(result.cells) == 1
    assert result.cells[0].cohort == "daming-core"
    assert result.cells[0].cell_id == "daming-core::agent.turn::alpha"
    assert result.rankings[0].cohort == "daming-core"
    assert result.approved
    patch = production_policy_patch(result)
    assert patch["task_policies"] == {}
    assert patch["cohort_task_policies"] == {
        "daming-core": {"agent.turn": ["alpha"]}
    }
    assert gateway.bindings == [("agent.turn", "alpha")]


def test_explicit_cohort_requires_matching_scenarios():
    matrix_value = matrix(
        tasks={"agent.turn": ["alpha"]},
        cohorts=["missing-cohort"],
    )
    with pytest.raises(TrajectoryMatrixError, match="No trajectory scenarios"):
        run(matrix_value=matrix_value)


@pytest.mark.parametrize("value", [0, 21, 1.5, "2", True])
def test_reviewer_quorum_rejects_invalid_values(value):
    with pytest.raises(TrajectoryMatrixError, match="minimum_reviewers_per_target"):
        run(matrix_value=matrix(minimum_reviewers_per_target=value))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 101])
def test_review_disagreement_rejects_non_finite_or_out_of_range_values(value):
    with pytest.raises(TrajectoryMatrixError, match="maximum_review_disagreement"):
        run(matrix_value=matrix(maximum_review_disagreement=value))


@pytest.mark.parametrize(
    ("cohorts", "message"),
    [
        (["all", "daming-core"], "cannot be combined"),
        (["daming-core", "daming-core"], "duplicates"),
    ],
)
def test_matrix_rejects_ambiguous_or_duplicate_cohorts(cohorts, message):
    with pytest.raises(TrajectoryMatrixError, match=message):
        run(matrix_value=matrix(cohorts=cohorts))


@pytest.mark.parametrize(
    ("scenario_cohorts", "message"),
    [
        ("daming-core", "cohorts must be a list"),
        (["daming-core", "daming-core"], "duplicate cohorts"),
    ],
)
def test_suite_rejects_invalid_scenario_cohorts(scenario_cohorts, message):
    suite_value = suite({**scenario(), "cohorts": scenario_cohorts})
    with pytest.raises(TrajectoryMatrixError, match=message):
        run(suite_value=suite_value)
