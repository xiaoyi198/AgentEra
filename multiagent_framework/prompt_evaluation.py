"""Deterministic scenario evaluation for versioned gameplay prompts.

The evaluator intentionally checks prompt contracts rather than calling a model. It
makes prompt versions, hashes, variable use, budgets, and explicit gameplay policy
language regression-testable in CI. Model-behaviour evals can consume the same
scenario IDs later without weakening this deterministic gate.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dlc_registry import DLCRegistry
from .prompt_registry import PromptRegistry, PromptRegistryError, PromptRenderResult


class PromptEvaluationError(ValueError):
    """Raised when an evaluation suite or assertion is malformed."""


@dataclass(frozen=True)
class PromptEvaluationCheck:
    check_id: str
    category: str
    passed: bool
    message: str
    weight: float = 1.0
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptScenarioResult:
    scenario_id: str
    prompt_name: str
    version: str
    content_hash: str
    char_count: int
    estimated_tokens: int
    score: float
    minimum_score: float
    passed: bool
    checks: tuple[PromptEvaluationCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = [check.to_dict() for check in self.checks]
        return data


@dataclass(frozen=True)
class PromptSuiteResult:
    suite_id: str
    dlc_id: str
    score: float
    minimum_score: float
    passed: bool
    scenarios: tuple[PromptScenarioResult, ...] = ()
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scenarios"] = [scenario.to_dict() for scenario in self.scenarios]
        return data


@dataclass
class PromptScenarioEvaluator:
    """Evaluate declarative scenarios against one PromptRegistry."""

    registry: PromptRegistry
    default_minimum_score: float = 100.0

    def evaluate_suite(
        self,
        suite: Mapping[str, Any],
        *,
        source: str = "",
    ) -> PromptSuiteResult:
        schema_version = str(suite.get("schema_version", ""))
        if schema_version != "1.0":
            raise PromptEvaluationError(
                f"Unsupported prompt evaluation schema_version: {schema_version!r}"
            )
        suite_id = _required_text(suite, "suite_id")
        dlc_id = _required_text(suite, "dlc_id")
        raw_scenarios = suite.get("scenarios")
        if not isinstance(raw_scenarios, list) or not raw_scenarios:
            raise PromptEvaluationError("Prompt evaluation suite requires scenarios")
        minimum_score = _score_value(
            suite.get("minimum_score", self.default_minimum_score),
            "suite.minimum_score",
        )

        scenarios: list[PromptScenarioResult] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_scenarios):
            if not isinstance(raw, Mapping):
                raise PromptEvaluationError(f"scenarios[{index}] must be an object")
            scenario_id = _required_text(raw, "id")
            if scenario_id in seen:
                raise PromptEvaluationError(f"Duplicate scenario id: {scenario_id}")
            seen.add(scenario_id)
            scenarios.append(self.evaluate_scenario(raw))

        score = round(sum(item.score for item in scenarios) / len(scenarios), 2)
        passed = score >= minimum_score and all(item.passed for item in scenarios)
        return PromptSuiteResult(
            suite_id=suite_id,
            dlc_id=dlc_id,
            score=score,
            minimum_score=minimum_score,
            passed=passed,
            scenarios=tuple(scenarios),
            source=source,
        )

    def evaluate_scenario(self, scenario: Mapping[str, Any]) -> PromptScenarioResult:
        scenario_id = _required_text(scenario, "id")
        prompt_name = _required_text(scenario, "prompt")
        variables = scenario.get("variables", {})
        if not isinstance(variables, Mapping):
            raise PromptEvaluationError(f"Scenario {scenario_id} variables must be an object")
        expectation = scenario.get("expect", {})
        if not isinstance(expectation, Mapping):
            raise PromptEvaluationError(f"Scenario {scenario_id} expect must be an object")
        minimum_score = _score_value(
            scenario.get("minimum_score", self.default_minimum_score),
            f"{scenario_id}.minimum_score",
        )

        try:
            report = self.registry.render(prompt_name, variables, strict=False)
        except PromptRegistryError as exc:
            raise PromptEvaluationError(
                f"Scenario {scenario_id} cannot render {prompt_name!r}: {exc}"
            ) from exc

        checks: list[PromptEvaluationCheck] = []
        render_errors = [issue for issue in report.issues if issue.severity == "error"]
        checks.append(PromptEvaluationCheck(
            "render_contract",
            "render",
            not render_errors and not report.missing_variables,
            "Prompt renders with all referenced variables"
            if not render_errors and not report.missing_variables
            else "; ".join(
                [issue.message for issue in render_errors]
                + [f"Missing: {', '.join(report.missing_variables)}"]
            ),
            weight=2.0,
        ))

        required_variables = _text_list(
            expectation.get("required_variables", []),
            f"{scenario_id}.expect.required_variables",
        )
        if required_variables:
            used = set(report.used_variables)
            missing_required = sorted(set(required_variables) - used)
            checks.append(PromptEvaluationCheck(
                "required_variables",
                "variables",
                not missing_required,
                "All required scenario variables are used"
                if not missing_required
                else f"Required variables not used: {', '.join(missing_required)}",
                weight=2.0,
            ))

        if "allowed_variables" in expectation:
            allowed_variables = _text_list(
                expectation["allowed_variables"],
                f"{scenario_id}.expect.allowed_variables",
            )
            unknown = [
                issue.message
                for issue in self.registry.lint(
                    prompt_name,
                    values=variables,
                    allowed_variables=allowed_variables,
                )
                if issue.code == "prompt.unknown_variable"
            ]
            checks.append(PromptEvaluationCheck(
                "allowed_variables",
                "variables",
                not unknown,
                "Prompt only references allowed variables"
                if not unknown
                else "; ".join(unknown),
                weight=2.0,
            ))

        if "version" in expectation:
            expected = str(expectation["version"])
            checks.append(PromptEvaluationCheck(
                "prompt_version",
                "identity",
                report.version == expected,
                f"Expected version {expected}, observed {report.version}",
                weight=2.0,
            ))
        if "content_hash" in expectation:
            expected_hash = str(expectation["content_hash"])
            checks.append(PromptEvaluationCheck(
                "prompt_hash",
                "identity",
                report.content_hash == expected_hash,
                f"Expected hash {expected_hash}, observed {report.content_hash}",
                weight=2.0,
            ))
        if "max_chars" in expectation:
            max_chars = _positive_number(
                expectation["max_chars"], f"{scenario_id}.expect.max_chars"
            )
            checks.append(PromptEvaluationCheck(
                "character_budget",
                "budget",
                report.char_count <= max_chars,
                f"Rendered chars {report.char_count} / {max_chars:g}",
            ))
        if "max_estimated_tokens" in expectation:
            max_tokens = _positive_number(
                expectation["max_estimated_tokens"],
                f"{scenario_id}.expect.max_estimated_tokens",
            )
            checks.append(PromptEvaluationCheck(
                "token_budget",
                "budget",
                report.estimated_tokens <= max_tokens,
                f"Estimated tokens {report.estimated_tokens} / {max_tokens:g}",
            ))

        assertions = expectation.get("assertions", [])
        if not isinstance(assertions, list):
            raise PromptEvaluationError(f"{scenario_id}.expect.assertions must be a list")
        for index, assertion in enumerate(assertions):
            checks.append(_evaluate_assertion(
                report,
                assertion,
                path=f"{scenario_id}.expect.assertions[{index}]",
            ))

        if not checks:
            raise PromptEvaluationError(f"Scenario {scenario_id} has no checks")
        total_weight = sum(check.weight for check in checks)
        earned_weight = sum(check.weight for check in checks if check.passed)
        score = round(100.0 * earned_weight / total_weight, 2)
        passed = (
            score >= minimum_score
            and all(check.passed for check in checks if check.required)
        )
        return PromptScenarioResult(
            scenario_id=scenario_id,
            prompt_name=prompt_name,
            version=report.version,
            content_hash=report.content_hash,
            char_count=report.char_count,
            estimated_tokens=report.estimated_tokens,
            score=score,
            minimum_score=minimum_score,
            passed=passed,
            checks=tuple(checks),
        )


def load_prompt_evaluation_suite(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromptEvaluationError(f"Cannot read prompt evaluation suite {source}: {exc}") from exc
    if not isinstance(data, dict):
        raise PromptEvaluationError(f"Prompt evaluation suite root must be an object: {source}")
    return data


def evaluate_prompt_fixture(
    path: str | Path,
    *,
    project_root: str | Path = ".",
) -> PromptSuiteResult:
    """Load one suite and evaluate it against its declared DLC prompt catalog."""

    suite = load_prompt_evaluation_suite(path)
    dlc_id = _required_text(suite, "dlc_id")
    dlc_registry = DLCRegistry(project_root)
    content = dlc_registry.load_content(dlc_id)
    registry = PromptRegistry(content.get("prompts", {}))
    evaluator = PromptScenarioEvaluator(
        registry,
        default_minimum_score=_score_value(
            suite.get("minimum_score", 100.0), "suite.minimum_score"
        ),
    )
    return evaluator.evaluate_suite(suite, source=str(Path(path)))


def _evaluate_assertion(
    report: PromptRenderResult,
    raw: Any,
    *,
    path: str,
) -> PromptEvaluationCheck:
    if not isinstance(raw, Mapping):
        raise PromptEvaluationError(f"{path} must be an object")
    check_id = _required_text(raw, "id")
    kind = str(raw.get("kind", "contains_all"))
    patterns = _text_list(raw.get("patterns", []), f"{path}.patterns")
    if not patterns:
        raise PromptEvaluationError(f"{path}.patterns cannot be empty")
    match_mode = str(raw.get("match", "literal"))
    if match_mode not in {"literal", "regex"}:
        raise PromptEvaluationError(f"{path}.match must be literal or regex")
    weight = _positive_number(raw.get("weight", 1.0), f"{path}.weight")
    required = bool(raw.get("required", True))
    description = str(raw.get("description", check_id)).strip() or check_id

    spans = [_first_match(report.text, pattern, match_mode, path) for pattern in patterns]
    if kind == "contains_all":
        passed = all(span is not None for span in spans)
    elif kind == "contains_any":
        passed = any(span is not None for span in spans)
    elif kind == "not_contains":
        passed = all(span is None for span in spans)
    elif kind == "ordered":
        passed = all(span is not None for span in spans)
        if passed:
            starts = [span[0] for span in spans if span is not None]
            passed = starts == sorted(starts) and len(set(starts)) == len(starts)
    else:
        raise PromptEvaluationError(
            f"{path}.kind must be contains_all, contains_any, not_contains, or ordered"
        )
    matched = sum(span is not None for span in spans)
    message = f"{description}: matched {matched}/{len(patterns)} patterns"
    return PromptEvaluationCheck(
        check_id=check_id,
        category="behavior",
        passed=passed,
        message=message,
        weight=weight,
        required=required,
    )


def _first_match(
    text: str,
    pattern: str,
    mode: str,
    path: str,
) -> tuple[int, int] | None:
    if mode == "literal":
        start = text.find(pattern)
        return None if start < 0 else (start, start + len(pattern))
    try:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    except re.error as exc:
        raise PromptEvaluationError(f"Invalid regex in {path}: {pattern!r}: {exc}") from exc
    return None if match is None else match.span()


def _required_text(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PromptEvaluationError(f"{key} must be a non-empty string")
    return value.strip()


def _text_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PromptEvaluationError(f"{path} must be a list of strings")
    return list(value)


def _positive_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise PromptEvaluationError(f"{path} must be a positive number")
    return float(value)


def _score_value(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PromptEvaluationError(f"{path} must be a number between 0 and 100")
    score = float(value)
    if score < 0 or score > 100:
        raise PromptEvaluationError(f"{path} must be between 0 and 100")
    return score


def _human_report(result: PromptSuiteResult) -> str:
    lines = [
        f"Prompt suite {result.suite_id} ({result.dlc_id}): "
        f"{'PASS' if result.passed else 'FAIL'} {result.score:.2f}%"
    ]
    for scenario in result.scenarios:
        lines.append(
            f"  {'PASS' if scenario.passed else 'FAIL'} {scenario.scenario_id}: "
            f"{scenario.score:.2f}% {scenario.prompt_name}@{scenario.version} "
            f"chars={scenario.char_count} tokens~={scenario.estimated_tokens}"
        )
        for check in scenario.checks:
            if not check.passed:
                lines.append(f"    FAIL {check.check_id}: {check.message}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic prompt scenarios")
    parser.add_argument("fixtures", nargs="+", help="Prompt evaluation JSON fixtures")
    parser.add_argument("--project-root", default=".", help="World simulator project root")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    results = [
        evaluate_prompt_fixture(path, project_root=args.project_root)
        for path in args.fixtures
    ]
    if args.json:
        print(json.dumps([item.to_dict() for item in results], ensure_ascii=False, indent=2))
    else:
        print("\n".join(_human_report(item) for item in results))
    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
