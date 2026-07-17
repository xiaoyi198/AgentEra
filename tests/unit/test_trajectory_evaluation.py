"""Tests for deterministic and live trajectory evaluation."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.trajectory_evaluation import (
    TrajectoryEvaluationError,
    TrajectoryEvaluator,
    load_trajectory_suite,
    write_report_atomic,
)


SUITE_PATH = "tests/evals/daming_trajectory_scenarios.json"


def evaluate(suite, **kwargs):
    return asyncio.run(TrajectoryEvaluator(**kwargs).evaluate_suite(suite))


def test_replay_suite_scores_all_core_metrics():
    suite = load_trajectory_suite(SUITE_PATH)
    report = evaluate(suite)

    assert report.passed
    assert report.score == 100
    assert report.metrics["scenario_count"] == 4
    assert report.metrics["tool_legality_rate"] == 1
    assert report.metrics["termination_rate"] == 1
    assert report.metrics["routing_accuracy"] == 1
    assert report.metrics["memory_recall_rate"] == 1
    assert report.metrics["commitment_outcome_rate"] == 1
    assert report.metrics["resource_conservation_rate"] == 1
    assert report.metrics["round_count_total"] == 7
    assert report.metrics["prompt_tokens"] == 855
    assert report.metrics["completion_tokens"] == 169
    assert report.metrics["model_latency_ms_total"] == 78


def test_illegal_tool_and_wrong_terminal_fail_without_hiding_trace():
    suite = load_trajectory_suite(SUITE_PATH)
    suite["scenarios"] = [deepcopy(suite["scenarios"][1])]
    call = suite["scenarios"][0]["replay"]["responses"][0]["tool_calls"][0]
    call["function"]["name"] = "delete_world"

    report = evaluate(suite)
    result = report.scenarios[0]

    assert not report.passed
    assert not result.passed
    checks = {item.check_id: item for item in result.checks}
    assert not checks["tool_legality"].passed
    assert not checks["terminal_tool"].passed
    assert result.stop_reason == "llm_error"
    assert result.tool_calls == 1


def test_resource_conservation_uses_common_base_value_anchor():
    suite = load_trajectory_suite(SUITE_PATH)
    suite["scenarios"] = [deepcopy(suite["scenarios"][2])]
    suite["scenarios"][0]["replay"]["evidence"]["resource"]["after_base_value"] = 199

    report = evaluate(suite)
    check = next(
        item for item in report.scenarios[0].checks
        if item.check_id == "resource_conservation"
    )

    assert not report.passed
    assert not check.passed
    assert "delta=1" in check.message


def test_duplicate_scenario_ids_are_rejected():
    suite = load_trajectory_suite(SUITE_PATH)
    suite["scenarios"] = [suite["scenarios"][0], deepcopy(suite["scenarios"][0])]
    with pytest.raises(TrajectoryEvaluationError, match="Duplicate scenario id"):
        evaluate(suite)


class FakeLiveLLM:
    async def chat(self, **_kwargs):
        return LLMResponse(
            tool_calls=[{
                "id": "live-1",
                "function": {
                    "name": "route_intent",
                    "arguments": json.dumps({"target": "hubu"}),
                },
            }],
            input_tokens=12,
            output_tokens=4,
            latency_ms=7,
        )


class FakeGateway:
    def __init__(self, api_key="configured-at-runtime"):
        self._clients = {"eval": SimpleNamespace(config=SimpleNamespace(api_key=api_key))}

    def candidates(self, _task, _override=""):
        return ["eval"]

    def credentials_available(self, _task, _override=""):
        return bool(self._clients["eval"].config.api_key)

    def for_task(self, _task, **_kwargs):
        return FakeLiveLLM()


def test_live_mode_uses_gateway_and_keeps_report_body_free():
    suite = load_trajectory_suite(SUITE_PATH)
    suite["scenarios"] = [deepcopy(suite["scenarios"][1])]

    report = asyncio.run(
        TrajectoryEvaluator(gateway=FakeGateway()).evaluate_suite(suite, mode="live")
    )
    payload = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.passed
    assert report.scenarios[0].route_target == "hubu"
    assert "configured-at-runtime" not in payload
    assert "Treasury and grain" not in payload
    assert "messages" not in payload


def test_live_mode_without_credentials_fails_explicitly():
    suite = load_trajectory_suite(SUITE_PATH)
    suite["scenarios"] = [deepcopy(suite["scenarios"][1])]

    with pytest.raises(TrajectoryEvaluationError, match="credentials are not configured"):
        asyncio.run(
            TrajectoryEvaluator(gateway=FakeGateway(api_key="")).evaluate_suite(
                suite, mode="live"
            )
        )


def test_atomic_report_contains_metrics_but_no_inputs(tmp_path):
    report = evaluate(load_trajectory_suite(SUITE_PATH))
    target = tmp_path / "runtime" / "evals" / "report.json"
    write_report_atomic(target, report.to_dict())

    payload = json.loads(target.read_text(encoding="utf-8"))
    raw = target.read_text(encoding="utf-8")
    assert payload["metrics"]["scenario_count"] == 4
    assert "Review the prior tax promise" not in raw
    assert "api_key" not in raw.lower()



class FakeWorldLLM:
    async def chat(self, **_kwargs):
        return LLMResponse(
            tool_calls=[{
                "id": "world-live-1",
                "function": {
                    "name": "apply_world_command",
                    "arguments": json.dumps({"command_type": "transfer_resource"}),
                },
            }],
            input_tokens=20,
            output_tokens=5,
            latency_ms=6,
        )


class FakeWorldGateway(FakeGateway):
    def for_task(self, _task, **_kwargs):
        return FakeWorldLLM()


def test_live_tool_executor_can_emit_dynamic_conservation_evidence():
    suite = load_trajectory_suite(SUITE_PATH)
    scenario = deepcopy(suite["scenarios"][2])
    scenario["live"]["expect"] = {
        "allowed_tools": ["inspect_inventory", "apply_world_command"],
        "required_terminal_tool": "apply_world_command",
        "resource_conservation": True,
        "max_rounds": 2,
    }
    scenario["live"]["tool_results"] = []
    scenario["live"]["evidence"] = {}
    suite["scenarios"] = [scenario]

    async def execute(_scenario, name, _arguments, _tool_call):
        assert name == "apply_world_command"
        return {
            "ok": True,
            "accepted": True,
            "_evaluation_evidence": {
                "resource": {
                    "before_base_value": 125,
                    "after_base_value": 125,
                    "external_delta": 0,
                }
            },
        }

    report = asyncio.run(
        TrajectoryEvaluator(
            gateway=FakeWorldGateway(),
            live_tool_executor=execute,
        ).evaluate_suite(suite, mode="live")
    )

    assert report.passed
    assert report.metrics["resource_conservation_rate"] == 1
    assert "_evaluation_evidence" not in json.dumps(report.to_dict())


def _scenario_with_contracts(index, contracts):
    suite = load_trajectory_suite(SUITE_PATH)
    scenario = deepcopy(suite["scenarios"][index])
    scenario["expect"]["tool_argument_contracts"] = contracts
    suite["scenarios"] = [scenario]
    return suite, scenario


def test_tool_argument_contract_literal_equals_passes_and_aggregates():
    suite, _scenario = _scenario_with_contracts(0, [
        {
            "tool": "recall_memory",
            "occurrence": 1,
            "path": "query",
            "equals": "tax promise",
        },
        {
            "tool": "submit_report",
            "occurrence": 1,
            "path": "status",
            "equals": "resolved",
        },
    ])

    report = evaluate(suite)
    contract_checks = [
        item for item in report.scenarios[0].checks
        if item.check_id == "tool_argument_contract"
    ]

    assert report.passed
    assert report.metrics["tool_argument_contract_rate"] == 1
    assert [item.message for item in contract_checks] == ["matched", "matched"]


def test_tool_argument_contract_literal_mismatch_is_generic():
    actual_value = "private-actual-value"
    expected_value = "private-expected-value"
    suite, scenario = _scenario_with_contracts(0, [{
        "tool": "submit_report",
        "occurrence": 1,
        "path": "status",
        "equals": expected_value,
    }])
    scenario["replay"]["responses"][1]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "status": actual_value,
    })

    report = evaluate(suite)
    payload = json.dumps(report.to_dict(), ensure_ascii=False)
    check = next(
        item for item in report.scenarios[0].checks
        if item.check_id == "tool_argument_contract"
    )

    assert not report.passed
    assert not check.passed
    assert check.message == "value mismatch"
    assert actual_value not in payload
    assert expected_value not in payload
    assert report.metrics["tool_argument_contract_rate"] == 0


def test_tool_argument_contract_can_reference_prior_tool_result_nested_list():
    suite, scenario = _scenario_with_contracts(0, [{
        "tool": "submit_report",
        "occurrence": 1,
        "path": "commitment.id",
        "equals_tool_result": {
            "tool": "recall_memory",
            "occurrence": 1,
            "path": "memory_ids[0]",
        },
    }])
    scenario["replay"]["responses"][1]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "status": "resolved",
        "commitment": {"id": "mem_tax_promise"},
    })

    report = evaluate(suite)
    check = next(
        item for item in report.scenarios[0].checks
        if item.check_id == "tool_argument_contract"
    )

    assert report.passed
    assert check.passed
    assert check.message == "matched"


def test_tool_argument_contract_missing_tool_or_path_fails_safely():
    suite, _scenario = _scenario_with_contracts(0, [
        {
            "tool": "submit_report",
            "occurrence": 2,
            "path": "status",
            "equals": "resolved",
        },
        {
            "tool": "submit_report",
            "occurrence": 1,
            "path": "missing.path",
            "equals": "resolved",
        },
        {
            "tool": "submit_report",
            "occurrence": 1,
            "path": "status",
            "equals_tool_result": {
                "tool": "recall_memory",
                "occurrence": 1,
                "path": "missing[0]",
            },
        },
    ])

    report = evaluate(suite)
    checks = [
        item for item in report.scenarios[0].checks
        if item.check_id == "tool_argument_contract"
    ]

    assert not report.passed
    assert [item.message for item in checks] == [
        "tool/path missing",
        "tool/path missing",
        "tool/path missing",
    ]


@pytest.mark.parametrize("occurrence", [0, -1, True, "1", 1.0, 1.5])
def test_tool_argument_contract_rejects_invalid_occurrence(occurrence):
    suite, _scenario = _scenario_with_contracts(0, [{
        "tool": "submit_report",
        "occurrence": occurrence,
        "path": "status",
        "equals": "resolved",
    }])

    with pytest.raises(TrajectoryEvaluationError, match="positive integer"):
        evaluate(suite)


@pytest.mark.parametrize("contract", [
    {
        "tool": "submit_report",
        "occurrence": 1,
        "path": "status",
    },
    {
        "tool": "submit_report",
        "occurrence": 1,
        "path": "status",
        "equals": "resolved",
        "equals_tool_result": {
            "tool": "recall_memory",
            "occurrence": 1,
            "path": "memory_ids[0]",
        },
    },
    {
        "tool": "submit_report",
        "occurrence": 1,
        "path": "status",
        "equals_tool_result": "not-an-object",
    },
    {
        "tool": "submit_report",
        "occurrence": 1,
        "path": "items[-1]",
        "equals": "resolved",
    },
    {
        "tool": "submit_report",
        "occurrence": 1,
        "path": "items[0]value",
        "equals": "resolved",
    },
])
def test_malformed_tool_argument_contract_is_rejected(contract):
    suite, _scenario = _scenario_with_contracts(0, [contract])

    with pytest.raises(TrajectoryEvaluationError):
        evaluate(suite)


def test_tool_argument_contract_report_excludes_prompts_urls_and_keys():
    prompt_secret = "prompt-private-marker"
    actual_secret = "sk-actualprivate123456"
    expected_secret = "sk-expectedprivate123456"
    provider_url = "https://private-provider.example/v1"
    suite, scenario = _scenario_with_contracts(0, [{
        "tool": "submit_report",
        "occurrence": 1,
        "path": "status",
        "equals": expected_secret,
    }])
    scenario["replay"]["messages"] = [{
        "role": "user",
        "content": f"{prompt_secret} {provider_url}",
    }]
    scenario["replay"]["responses"][1]["tool_calls"][0]["function"]["arguments"] = json.dumps({
        "status": actual_secret,
    })

    report = evaluate(suite)
    payload = json.dumps(report.to_dict(), ensure_ascii=False)

    assert not report.passed
    assert "value mismatch" in payload
    assert prompt_secret not in payload
    assert actual_secret not in payload
    assert expected_secret not in payload
    assert provider_url not in payload
