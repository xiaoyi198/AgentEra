"""Replayable and live trajectory evaluation for multi-round simulator tasks.

Reports intentionally contain only aggregate execution evidence and assertions. They
never serialize prompts, model response bodies, provider URLs, or credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import tempfile
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .llm_client import LLMResponse
from .model_gateway import ModelGateway
from .turn_engine import ToolExecutor, TurnBudget, TurnEngine, TurnOutcome


class TrajectoryEvaluationError(ValueError):
    """Raised when a trajectory suite is malformed or cannot be executed safely."""


@dataclass(frozen=True)
class TrajectoryCheck:
    check_id: str
    category: str
    passed: bool
    message: str
    required: bool = True
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrajectoryScenarioResult:
    scenario_id: str
    task_type: str
    mode: str
    passed: bool
    score: float
    minimum_score: float
    stop_reason: str
    terminal_tool: str
    rounds: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    elapsed_ms: float
    model_latency_ms: float
    route_target: str = ""
    commitment_status: str = ""
    checks: tuple[TrajectoryCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [item.to_dict() for item in self.checks]
        return data


@dataclass(frozen=True)
class TrajectorySuiteResult:
    suite_id: str
    mode: str
    passed: bool
    score: float
    minimum_score: float
    metrics: Mapping[str, Any] = field(default_factory=dict)
    scenarios: tuple[TrajectoryScenarioResult, ...] = ()
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["metrics"] = dict(self.metrics)
        data["scenarios"] = [item.to_dict() for item in self.scenarios]
        return data


LiveToolExecutor = Callable[
    [Mapping[str, Any], str, dict[str, Any], dict[str, Any]],
    Awaitable[dict[str, Any]],
]
LiveToolSessionFactory = Callable[
    [Mapping[str, Any], Mapping[str, Any]],
    AbstractAsyncContextManager[LiveToolExecutor],
]


class _ReplayLLM:
    def __init__(self, responses: Sequence[Mapping[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        if self.calls >= len(self._responses):
            raise TrajectoryEvaluationError("Replay response script exhausted")
        raw = self._responses[self.calls]
        self.calls += 1
        tool_calls = raw.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            raise TrajectoryEvaluationError("Replay response tool_calls must be a list")
        return LLMResponse(
            content=str(raw.get("content", "")),
            tool_calls=[dict(item) for item in tool_calls if isinstance(item, Mapping)],
            input_tokens=_nonnegative_int(raw.get("input_tokens", 0), "input_tokens"),
            output_tokens=_nonnegative_int(raw.get("output_tokens", 0), "output_tokens"),
            latency_ms=_nonnegative_float(raw.get("latency_ms", 0), "latency_ms"),
        )


class TrajectoryEvaluator:
    """Execute and score declarative trajectories in replay or live mode."""

    def __init__(
        self,
        *,
        gateway: ModelGateway | None = None,
        turn_engine: TurnEngine | None = None,
        live_tool_executor: LiveToolExecutor | None = None,
        live_tool_session_factory: LiveToolSessionFactory | None = None,
        default_minimum_score: float = 100.0,
        strict_profile: bool = False,
    ) -> None:
        self.gateway = gateway
        self.turn_engine = turn_engine or TurnEngine()
        if live_tool_executor is not None and live_tool_session_factory is not None:
            raise TrajectoryEvaluationError(
                "Configure either live_tool_executor or live_tool_session_factory, not both"
            )
        self.live_tool_executor = live_tool_executor
        self.live_tool_session_factory = live_tool_session_factory
        self.default_minimum_score = _score(default_minimum_score, "default_minimum_score")
        self.strict_profile = bool(strict_profile)

    async def evaluate_suite(
        self,
        suite: Mapping[str, Any],
        *,
        mode: str | None = None,
        source: str = "",
    ) -> TrajectorySuiteResult:
        if str(suite.get("schema_version", "")) != "1.0":
            raise TrajectoryEvaluationError("Unsupported trajectory schema_version")
        suite_id = _required_text(suite, "suite_id")
        selected_mode = str(mode or suite.get("mode", "replay")).strip().lower()
        if selected_mode not in {"replay", "live"}:
            raise TrajectoryEvaluationError("Trajectory mode must be replay or live")
        scenarios_raw = suite.get("scenarios")
        if not isinstance(scenarios_raw, list) or not scenarios_raw:
            raise TrajectoryEvaluationError("Trajectory suite requires scenarios")
        suite_minimum = _score(
            suite.get("minimum_score", self.default_minimum_score),
            "suite.minimum_score",
        )
        if selected_mode == "live":
            self._ensure_live_ready(scenarios_raw)

        results: list[TrajectoryScenarioResult] = []
        seen: set[str] = set()
        for index, raw in enumerate(scenarios_raw):
            if not isinstance(raw, Mapping):
                raise TrajectoryEvaluationError(f"scenarios[{index}] must be an object")
            scenario_id = _required_text(raw, "id")
            if scenario_id in seen:
                raise TrajectoryEvaluationError(f"Duplicate scenario id: {scenario_id}")
            seen.add(scenario_id)
            results.append(await self.evaluate_scenario(raw, mode=selected_mode))

        score = round(sum(item.score for item in results) / len(results), 2)
        metrics = _aggregate_metrics(results)
        return TrajectorySuiteResult(
            suite_id=suite_id,
            mode=selected_mode,
            passed=score >= suite_minimum and all(item.passed for item in results),
            score=score,
            minimum_score=suite_minimum,
            metrics=metrics,
            scenarios=tuple(results),
            source=source,
        )

    async def evaluate_scenario(
        self,
        scenario: Mapping[str, Any],
        *,
        mode: str,
    ) -> TrajectoryScenarioResult:
        scenario_id = _required_text(scenario, "id")
        task_type = _required_text(scenario, "task")
        base_expectation = scenario.get("expect", {})
        if not isinstance(base_expectation, Mapping):
            raise TrajectoryEvaluationError(f"Scenario {scenario_id} expect must be an object")
        minimum_score = _score(
            scenario.get("minimum_score", self.default_minimum_score),
            f"{scenario_id}.minimum_score",
        )
        execution = scenario.get(mode)
        if not isinstance(execution, Mapping):
            raise TrajectoryEvaluationError(
                f"Scenario {scenario_id} has no {mode} execution definition"
            )
        expectation = execution.get("expect", base_expectation)
        if not isinstance(expectation, Mapping):
            raise TrajectoryEvaluationError(
                f"Scenario {scenario_id} {mode}.expect must be an object"
            )

        outcome, runtime_evidence = await self._execute(scenario, execution, mode=mode)
        static_evidence = execution.get("evidence", {})
        if not isinstance(static_evidence, Mapping):
            raise TrajectoryEvaluationError(f"Scenario {scenario_id} evidence must be an object")
        evidence = {**dict(static_evidence), **runtime_evidence}
        checks, route_target, commitment_status = _score_outcome(
            scenario_id, outcome, expectation, evidence
        )
        required = [item for item in checks if item.required]
        total_weight = sum(item.weight for item in required)
        earned = sum(item.weight for item in required if item.passed)
        score = round(100.0 * earned / total_weight, 2) if total_weight else 100.0
        passed = score >= minimum_score and all(item.passed for item in required)
        return TrajectoryScenarioResult(
            scenario_id=scenario_id,
            task_type=task_type,
            mode=mode,
            passed=passed,
            score=score,
            minimum_score=minimum_score,
            stop_reason=outcome.stop_reason,
            terminal_tool=outcome.terminal_tool,
            rounds=outcome.rounds,
            tool_calls=outcome.tool_calls,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            total_tokens=outcome.total_tokens,
            elapsed_ms=round(outcome.elapsed_ms, 3),
            model_latency_ms=round(outcome.model_latency_ms, 3),
            route_target=route_target,
            commitment_status=commitment_status,
            checks=tuple(checks),
        )

    async def _execute(
        self,
        scenario: Mapping[str, Any],
        execution: Mapping[str, Any],
        *,
        mode: str,
    ) -> tuple[TurnOutcome, dict[str, Any]]:
        messages = execution.get("messages", [{"role": "user", "content": "evaluation"}])
        tools = execution.get("tools", [])
        if not isinstance(messages, list) or not all(isinstance(item, Mapping) for item in messages):
            raise TrajectoryEvaluationError("execution.messages must be a list of objects")
        if not isinstance(tools, list) or not all(isinstance(item, Mapping) for item in tools):
            raise TrajectoryEvaluationError("execution.tools must be a list of objects")
        budget = _budget(execution.get("budget", {}))
        terminal_tools = execution.get("terminal_tools", [])
        if not isinstance(terminal_tools, list):
            raise TrajectoryEvaluationError("execution.terminal_tools must be a list")
        terminal_names = {str(item) for item in terminal_tools if str(item)}
        scripted_results = execution.get("tool_results", [])
        if not isinstance(scripted_results, list):
            raise TrajectoryEvaluationError("execution.tool_results must be a list")
        result_index = 0
        runtime_evidence: dict[str, Any] = {}
        active_live_executor = self.live_tool_executor

        async def execute_tool(
            name: str, arguments: dict[str, Any], tool_call: dict[str, Any]
        ) -> dict[str, Any]:
            nonlocal result_index
            if mode == "live" and active_live_executor is not None:
                result = await active_live_executor(scenario, name, arguments, tool_call)
            elif result_index < len(scripted_results):
                raw_result = scripted_results[result_index]
                result_index += 1
                if not isinstance(raw_result, Mapping):
                    raise TrajectoryEvaluationError("tool_results items must be objects")
                expected_tool = str(raw_result.get("tool", ""))
                if expected_tool and expected_tool != name:
                    return {
                        "ok": False,
                        "accepted": False,
                        "error": "scripted tool order mismatch",
                    }
                payload = raw_result.get("result", raw_result)
                if not isinstance(payload, Mapping):
                    raise TrajectoryEvaluationError("scripted tool result must be an object")
                result = dict(payload)
            else:
                result = {"ok": True, "accepted": True, "tool": name, "arguments": arguments}
            if not isinstance(result, Mapping):
                raise TrajectoryEvaluationError("tool executor result must be an object")
            safe_result = dict(result)
            emitted_evidence = safe_result.pop("_evaluation_evidence", None)
            if emitted_evidence is not None:
                if not isinstance(emitted_evidence, Mapping):
                    raise TrajectoryEvaluationError("_evaluation_evidence must be an object")
                runtime_evidence.update(dict(emitted_evidence))
            return safe_result

        if mode == "replay":
            responses = execution.get("responses")
            if not isinstance(responses, list) or not responses:
                raise TrajectoryEvaluationError("Replay execution requires responses")
            llm: Any = _ReplayLLM(responses)
        else:
            if self.gateway is None:
                raise TrajectoryEvaluationError("Live evaluation requires a ModelGateway")
            client_kwargs: dict[str, Any] = {
                "agent_id": str(scenario.get("agent_id", "")),
                "profile_override": str(scenario.get("profile_override", "")),
            }
            if self.strict_profile:
                client_kwargs["strict_profile"] = True
            llm = self.gateway.for_task(
                _required_text(scenario, "task"),
                **client_kwargs,
            )

        trace_context = {
            "trace_id": f"eval:{_required_text(scenario, 'id')}",
            "turn_id": f"eval:{_required_text(scenario, 'id')}",
            "task_type": _required_text(scenario, "task"),
            "agent_id": str(scenario.get("agent_id", "")),
        }
        async with AsyncExitStack() as stack:
            if mode == "live" and self.live_tool_session_factory is not None:
                try:
                    active_live_executor = await stack.enter_async_context(
                        self.live_tool_session_factory(scenario, execution)
                    )
                except Exception as exc:
                    raise TrajectoryEvaluationError(
                        "Live runtime fixture could not be opened safely"
                    ) from exc
            if (
                mode == "live"
                and execution.get("runtime_fixture") is not None
                and active_live_executor is None
                and not scripted_results
            ):
                raise TrajectoryEvaluationError(
                    "Live runtime_fixture requires an isolated runtime tool session"
                )
            outcome = await self.turn_engine.run(
                llm=llm,
                messages=[dict(item) for item in messages],
                tools=[dict(item) for item in tools],
                execute_tool=execute_tool,
                terminal_predicate=lambda name, result: (
                    name in terminal_names
                    and bool(result.get("ok", True))
                    and bool(result.get("accepted", True))
                ),
                trace_context=trace_context,
                budget=budget,
                temperature=float(execution.get("temperature", 0.0)),
            )
        return outcome, runtime_evidence

    def _ensure_live_ready(self, scenarios: Sequence[Any]) -> None:
        if self.gateway is None:
            raise TrajectoryEvaluationError("Live evaluation requires a ModelGateway")
        missing: list[str] = []
        for raw in scenarios:
            if not isinstance(raw, Mapping):
                continue
            task = str(raw.get("task", ""))
            override = str(raw.get("profile_override", ""))
            credential_probe = getattr(self.gateway, "credentials_available", None)
            if credential_probe is None:
                missing.append(task or "unknown")
                continue
            if self.strict_profile:
                available = credential_probe(task, override, strict_profile=True)
            else:
                available = credential_probe(task, override)
            if not available:
                missing.append(task or "unknown")
        if missing:
            tasks = ", ".join(sorted(set(missing)))
            raise TrajectoryEvaluationError(
                f"Live evaluation credentials are not configured for tasks: {tasks}"
            )


def _budget(raw: Any) -> TurnBudget:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise TrajectoryEvaluationError("execution.budget must be an object")
    allowed = {"max_rounds", "max_tool_calls", "max_tokens", "max_seconds", "no_progress_rounds"}
    return TurnBudget(**{key: raw[key] for key in allowed if key in raw})


def _score_outcome(
    scenario_id: str,
    outcome: TurnOutcome,
    expect: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[list[TrajectoryCheck], str, str]:
    checks: list[TrajectoryCheck] = []
    calls = [call for round_item in outcome.trace for call in round_item.get("calls", [])]
    called_tools = [str(call.get("tool", "")) for call in calls]

    checks.extend(
        _tool_argument_contract_checks(
            scenario_id,
            calls,
            expect.get("tool_argument_contracts"),
        )
    )

    allowed_raw = expect.get("allowed_tools")
    if allowed_raw is not None:
        if not isinstance(allowed_raw, list):
            raise TrajectoryEvaluationError(f"{scenario_id}.allowed_tools must be a list")
        allowed = {str(item) for item in allowed_raw}
        illegal = sorted({name for name in called_tools if name not in allowed})
        checks.append(_check("tool_legality", "tools", not illegal, "legal" if not illegal else "illegal tools: " + ", ".join(illegal)))

    required_tools_raw = expect.get("required_tools")
    if required_tools_raw is not None:
        if not isinstance(required_tools_raw, list):
            raise TrajectoryEvaluationError(f"{scenario_id}.required_tools must be a list")
        required_tools = [str(item) for item in required_tools_raw]
        missing_tools = [name for name in required_tools if name not in called_tools]
        checks.append(_check(
            "required_tools",
            "tools",
            not missing_tools,
            "all required tools called" if not missing_tools else "missing required tools: " + ", ".join(missing_tools),
        ))

    sequence_raw = expect.get("required_tool_sequence")
    if sequence_raw is not None:
        if not isinstance(sequence_raw, list):
            raise TrajectoryEvaluationError(
                f"{scenario_id}.required_tool_sequence must be a list"
            )
        expected_sequence = [str(item) for item in sequence_raw]
        checks.append(_check(
            "tool_sequence",
            "tools",
            called_tools == expected_sequence,
            "expected " + " -> ".join(expected_sequence) + ", got " + (" -> ".join(called_tools) or "none"),
        ))

    terminal = expect.get("required_terminal_tool")
    if terminal is not None:
        expected_terminal = str(terminal)
        checks.append(_check("terminal_tool", "termination", outcome.terminal_tool == expected_terminal, f"expected {expected_terminal}, got {outcome.terminal_tool or 'none'}"))
    checks.append(_check("termination", "termination", outcome.completed, f"stop_reason={outcome.stop_reason}"))

    _maximum_check(checks, "max_rounds", "budget", outcome.rounds, expect.get("max_rounds"))
    _maximum_check(checks, "max_tool_calls", "budget", outcome.tool_calls, expect.get("max_tool_calls"))
    _maximum_check(checks, "max_tokens", "budget", outcome.total_tokens, expect.get("max_tokens"))
    _maximum_check(checks, "max_latency_ms", "latency", outcome.elapsed_ms, expect.get("max_latency_ms"))
    _maximum_check(checks, "max_model_latency_ms", "latency", outcome.model_latency_ms, expect.get("max_model_latency_ms"))

    route_target = _route_target(outcome, evidence)
    if "route_target" in expect:
        expected_route = str(expect.get("route_target", ""))
        checks.append(_check("routing_accuracy", "routing", route_target == expected_route, f"expected {expected_route}, got {route_target or 'none'}"))

    recalled = evidence.get("memory_recalled", [])
    if not isinstance(recalled, list):
        raise TrajectoryEvaluationError(f"{scenario_id}.memory_recalled must be a list")
    if "memory_must_include" in expect:
        required_memories = expect.get("memory_must_include")
        if not isinstance(required_memories, list):
            raise TrajectoryEvaluationError(f"{scenario_id}.memory_must_include must be a list")
        missing = [str(item) for item in required_memories if str(item) not in {str(value) for value in recalled}]
        checks.append(_check("memory_recall", "memory", not missing, "recalled" if not missing else "missing memory ids: " + ", ".join(missing)))

    commitment_status = str(evidence.get("commitment_status", ""))
    if "commitment_status" in expect:
        expected_status = str(expect.get("commitment_status", ""))
        checks.append(_check("commitment_outcome", "memory", commitment_status == expected_status, f"expected {expected_status}, got {commitment_status or 'none'}"))

    if bool(expect.get("resource_conservation", False)):
        resource = evidence.get("resource")
        if not isinstance(resource, Mapping):
            raise TrajectoryEvaluationError(f"{scenario_id}.resource evidence must be an object")
        before = _finite_float(resource.get("before_base_value"), "before_base_value")
        after = _finite_float(resource.get("after_base_value"), "after_base_value")
        external = _finite_float(resource.get("external_delta", 0), "external_delta")
        tolerance = max(0.0, _finite_float(resource.get("tolerance", 1e-6), "tolerance"))
        delta = abs((before + external) - after)
        checks.append(_check("resource_conservation", "world", delta <= tolerance, f"base-value delta={delta:.6g}"))

    human_score = evidence.get("human_score")
    if human_score is not None:
        minimum = _score(expect.get("minimum_human_score", 0), "minimum_human_score")
        actual = _score(human_score, "human_score")
        checks.append(_check("human_score", "human", actual >= minimum, f"human_score={actual:g}", required="minimum_human_score" in expect))
    return checks, route_target, commitment_status


def _route_target(outcome: TurnOutcome, evidence: Mapping[str, Any]) -> str:
    # Prefer what the model actually submitted. Scripted results and static evidence
    # are useful fixtures, but neither may mask an incorrect routing argument.
    for round_item in reversed(outcome.trace):
        for call in reversed(round_item.get("calls", [])):
            args = call.get("args", {})
            if isinstance(args, Mapping):
                for key in ("target", "route_target", "routed_to"):
                    if args.get(key) is not None:
                        return str(args[key])
    result = outcome.terminal_result or {}
    for key in ("target", "route_target", "routed_to"):
        if result.get(key) is not None:
            return str(result[key])
    if evidence.get("route_target") is not None:
        return str(evidence.get("route_target", ""))
    return ""


def _tool_argument_contract_checks(
    scenario_id: str,
    calls: Sequence[Any],
    raw_contracts: Any,
) -> list[TrajectoryCheck]:
    if raw_contracts is None:
        return []
    if not isinstance(raw_contracts, list):
        raise TrajectoryEvaluationError(
            f"{scenario_id}.tool_argument_contracts must be a list"
        )

    checks: list[TrajectoryCheck] = []
    for index, raw_contract in enumerate(raw_contracts):
        label = f"{scenario_id}.tool_argument_contracts[{index}]"
        if not isinstance(raw_contract, Mapping):
            raise TrajectoryEvaluationError(f"{label} must be an object")

        tool = _contract_tool(raw_contract.get("tool"), f"{label}.tool")
        occurrence = _contract_occurrence(
            raw_contract.get("occurrence", 1),
            f"{label}.occurrence",
        )
        path = _contract_path(raw_contract.get("path"), f"{label}.path")
        has_literal = "equals" in raw_contract
        has_reference = "equals_tool_result" in raw_contract
        if has_literal == has_reference:
            raise TrajectoryEvaluationError(
                f"{label} must define exactly one comparison source"
            )

        call = _find_tool_occurrence(calls, tool, occurrence)
        found_actual = False
        actual: Any = None
        if call is not None:
            args = call.get("args", {})
            found_actual, actual = _resolve_value_path(args, path)

        found_expected = True
        expected: Any
        if has_literal:
            expected = raw_contract.get("equals")
        else:
            reference = raw_contract.get("equals_tool_result")
            if not isinstance(reference, Mapping):
                raise TrajectoryEvaluationError(
                    f"{label}.equals_tool_result must be an object"
                )
            reference_tool = _contract_tool(
                reference.get("tool"),
                f"{label}.equals_tool_result.tool",
            )
            reference_occurrence = _contract_occurrence(
                reference.get("occurrence", 1),
                f"{label}.equals_tool_result.occurrence",
            )
            reference_path = _contract_path(
                reference.get("path"),
                f"{label}.equals_tool_result.path",
            )
            reference_call = _find_tool_occurrence(
                calls,
                reference_tool,
                reference_occurrence,
            )
            if reference_call is None:
                found_expected = False
                expected = None
            else:
                found_expected, expected = _resolve_value_path(
                    reference_call.get("result", {}),
                    reference_path,
                )

        if not found_actual or not found_expected:
            checks.append(
                _check(
                    "tool_argument_contract",
                    "tool_arguments",
                    False,
                    "tool/path missing",
                )
            )
        elif actual != expected:
            checks.append(
                _check(
                    "tool_argument_contract",
                    "tool_arguments",
                    False,
                    "value mismatch",
                )
            )
        else:
            checks.append(
                _check(
                    "tool_argument_contract",
                    "tool_arguments",
                    True,
                    "matched",
                )
            )
    return checks


def _contract_tool(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrajectoryEvaluationError(f"{label} must be non-empty text")
    return value.strip()


def _contract_occurrence(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TrajectoryEvaluationError(f"{label} must be a positive integer")
    return value


def _contract_path(value: Any, label: str) -> tuple[str | int, ...]:
    if isinstance(value, list):
        if not value:
            raise TrajectoryEvaluationError(f"{label} must not be empty")
        parts: list[str | int] = []
        for part in value:
            if isinstance(part, bool) or not isinstance(part, (str, int)):
                raise TrajectoryEvaluationError(
                    f"{label} items must be text keys or non-negative indexes"
                )
            if isinstance(part, int):
                if part < 0:
                    raise TrajectoryEvaluationError(
                        f"{label} list indexes must be non-negative"
                    )
                parts.append(part)
            elif not part:
                raise TrajectoryEvaluationError(f"{label} keys must not be empty")
            else:
                parts.append(part)
        return tuple(parts)

    if not isinstance(value, str) or not value.strip():
        raise TrajectoryEvaluationError(f"{label} must be a non-empty path")
    raw = value.strip()
    parts = []
    index = 0
    expect_segment = True
    while index < len(raw):
        char = raw[index]
        if char == ".":
            if expect_segment:
                raise TrajectoryEvaluationError(f"{label} is malformed")
            expect_segment = True
            index += 1
            continue
        if char == "[":
            if expect_segment:
                raise TrajectoryEvaluationError(f"{label} is malformed")
            close = raw.find("]", index + 1)
            if close < 0:
                raise TrajectoryEvaluationError(f"{label} is malformed")
            token = raw[index + 1:close]
            if not token.isdigit():
                raise TrajectoryEvaluationError(f"{label} is malformed")
            parts.append(int(token))
            index = close + 1
            expect_segment = False
            continue
        if char == "]" or not expect_segment:
            raise TrajectoryEvaluationError(f"{label} is malformed")
        end = index
        while end < len(raw) and raw[end] not in ".[":
            if raw[end] == "]":
                raise TrajectoryEvaluationError(f"{label} is malformed")
            end += 1
        token = raw[index:end]
        if not token:
            raise TrajectoryEvaluationError(f"{label} is malformed")
        parts.append(int(token) if token.isdigit() else token)
        index = end
        expect_segment = False
    if expect_segment or not parts:
        raise TrajectoryEvaluationError(f"{label} is malformed")
    return tuple(parts)


def _find_tool_occurrence(
    calls: Sequence[Any],
    tool: str,
    occurrence: int,
) -> Mapping[str, Any] | None:
    matched = 0
    for raw_call in calls:
        if not isinstance(raw_call, Mapping):
            continue
        if str(raw_call.get("tool", "")) != tool:
            continue
        matched += 1
        if matched == occurrence:
            return raw_call
    return None


def _resolve_value_path(
    root: Any,
    path: Sequence[str | int],
) -> tuple[bool, Any]:
    current = root
    for part in path:
        if isinstance(part, int):
            if (
                not isinstance(current, Sequence)
                or isinstance(current, (str, bytes, bytearray))
                or part >= len(current)
            ):
                return False, None
            current = current[part]
        else:
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
    return True, current


def _aggregate_metrics(results: Sequence[TrajectoryScenarioResult]) -> dict[str, Any]:
    def rate(check_id: str) -> float | None:
        checks = [check for result in results for check in result.checks if check.check_id == check_id]
        if not checks:
            return None
        return round(sum(1 for item in checks if item.passed) / len(checks), 4)

    metrics: dict[str, Any] = {
        "scenario_count": len(results),
        "passed_scenarios": sum(1 for item in results if item.passed),
        "tool_legality_rate": rate("tool_legality"),
        "tool_argument_contract_rate": rate("tool_argument_contract"),
        "termination_rate": rate("termination"),
        "routing_accuracy": rate("routing_accuracy"),
        "resource_conservation_rate": rate("resource_conservation"),
        "memory_recall_rate": rate("memory_recall"),
        "commitment_outcome_rate": rate("commitment_outcome"),
        "round_count_total": sum(item.rounds for item in results),
        "round_count_mean": round(sum(item.rounds for item in results) / len(results), 3),
        "prompt_tokens": sum(item.input_tokens for item in results),
        "completion_tokens": sum(item.output_tokens for item in results),
        "total_tokens": sum(item.total_tokens for item in results),
        "latency_ms_total": round(sum(item.elapsed_ms for item in results), 3),
        "model_latency_ms_total": round(sum(item.model_latency_ms for item in results), 3),
    }
    return metrics


def load_trajectory_suite(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrajectoryEvaluationError(f"Cannot load trajectory suite: {source}") from exc
    if not isinstance(data, dict):
        raise TrajectoryEvaluationError("Trajectory suite root must be an object")
    return data


def write_report_atomic(path: str | Path, report: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _required_text(source: Mapping[str, Any], key: str) -> str:
    value = str(source.get(key, "")).strip()
    if not value:
        raise TrajectoryEvaluationError(f"Missing required text field: {key}")
    return value


def _score(value: Any, label: str) -> float:
    number = _finite_float(value, label)
    if not 0 <= number <= 100:
        raise TrajectoryEvaluationError(f"{label} must be between 0 and 100")
    return number


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryEvaluationError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise TrajectoryEvaluationError(f"{label} must be finite")
    return number


def _nonnegative_float(value: Any, label: str) -> float:
    number = _finite_float(value, label)
    if number < 0:
        raise TrajectoryEvaluationError(f"{label} must be non-negative")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryEvaluationError(f"{label} must be an integer") from exc
    if number < 0:
        raise TrajectoryEvaluationError(f"{label} must be non-negative")
    return number


def _check(check_id: str, category: str, passed: bool, message: str, *, required: bool = True) -> TrajectoryCheck:
    safe = re.sub(r"(?i)bearer\s+\S+|sk-[a-z0-9_-]{8,}", "[REDACTED]", str(message))
    return TrajectoryCheck(check_id=check_id, category=category, passed=bool(passed), message=safe[:500], required=required)


def _maximum_check(checks: list[TrajectoryCheck], check_id: str, category: str, actual: float, maximum: Any) -> None:
    if maximum is None:
        return
    limit = _nonnegative_float(maximum, check_id)
    checks.append(_check(check_id, category, actual <= limit, f"actual={actual:g}, maximum={limit:g}"))


async def _run_cli(args: argparse.Namespace) -> int:
    suite = load_trajectory_suite(args.suite)
    gateway = None
    try:
        if args.mode == "live":
            gateway = ModelGateway.from_files(args.models, args.legacy)
        runtime_factory = None
        if args.mode == "live" and args.runtime_tools:
            from .trajectory_runtime import IsolatedTrajectoryRuntimeFactory

            runtime_factory = IsolatedTrajectoryRuntimeFactory()
        evaluator = TrajectoryEvaluator(
            gateway=gateway,
            live_tool_session_factory=runtime_factory,
        )
        report = await evaluator.evaluate_suite(suite, mode=args.mode, source=str(args.suite))
        payload = report.to_dict()
        if args.output:
            write_report_atomic(args.output, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if report.passed else 1
    finally:
        if gateway is not None:
            await gateway.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate multi-round model trajectories")
    parser.add_argument("suite", help="Trajectory suite JSON path")
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--models", default="config/models.json")
    parser.add_argument("--legacy", default="config/llm.json")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--runtime-tools",
        action="store_true",
        help="Execute allowlisted live tools against an isolated runtime_fixture",
    )
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_cli(args))
    except TrajectoryEvaluationError as exc:
        print(f"trajectory evaluation error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
