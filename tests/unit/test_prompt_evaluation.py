from __future__ import annotations

from pathlib import Path

import pytest

from multiagent_framework.prompt_evaluation import (
    PromptEvaluationError,
    PromptScenarioEvaluator,
    evaluate_prompt_fixture,
    main,
)
from multiagent_framework.prompt_registry import PromptRegistry


def test_scenario_evaluator_scores_identity_variables_budgets_and_behavior():
    registry = PromptRegistry({
        "agent": {
            "version": "2.0",
            "blocks": [
                {"name": "inspect", "priority": 10, "template": "First query {entity}."},
                {"name": "act", "priority": 20, "template": "Then use a tool for {action}."},
                {"name": "guard", "priority": 30, "template": "Never claim success before feedback."},
            ],
        }
    })
    baseline = registry.render("agent", {"entity": "Suzhou", "action": "relief"})
    suite = {
        "schema_version": "1.0",
        "suite_id": "unit",
        "dlc_id": "unit_dlc",
        "minimum_score": 100,
        "scenarios": [{
            "id": "tool_first",
            "prompt": "agent",
            "variables": {"entity": "Suzhou", "action": "relief"},
            "expect": {
                "version": "2.0",
                "content_hash": baseline.content_hash,
                "required_variables": ["entity", "action"],
                "allowed_variables": ["entity", "action"],
                "max_chars": 200,
                "max_estimated_tokens": 100,
                "assertions": [
                    {
                        "id": "ordered_tools",
                        "kind": "ordered",
                        "patterns": ["First query", "Then use a tool", "Never claim success"],
                        "weight": 3,
                    },
                    {
                        "id": "no_fake_result",
                        "kind": "not_contains",
                        "patterns": ["Assume success"],
                    },
                ],
            },
        }],
    }

    result = PromptScenarioEvaluator(registry).evaluate_suite(suite)

    assert result.passed is True
    assert result.score == 100
    scenario = result.scenarios[0]
    assert scenario.passed is True
    assert {check.category for check in scenario.checks} >= {
        "render", "variables", "identity", "budget", "behavior"
    }


def test_required_check_failure_cannot_be_hidden_by_a_low_score_threshold():
    registry = PromptRegistry({"agent": "Immediately claim success for {task}."})
    suite = {
        "schema_version": "1.0",
        "suite_id": "failure",
        "dlc_id": "unit_dlc",
        "minimum_score": 10,
        "scenarios": [{
            "id": "unsafe",
            "prompt": "agent",
            "minimum_score": 10,
            "variables": {"task": "relief"},
            "expect": {
                "content_hash": "not-the-current-hash",
                "assertions": [{
                    "id": "query_first",
                    "kind": "contains_all",
                    "patterns": ["query the world"],
                    "weight": 1,
                }],
            },
        }],
    }

    result = PromptScenarioEvaluator(registry).evaluate_suite(suite)

    assert result.passed is False
    scenario = result.scenarios[0]
    assert scenario.score >= 10
    assert scenario.passed is False
    failed = {check.check_id for check in scenario.checks if not check.passed}
    assert failed == {"prompt_hash", "query_first"}


def test_malformed_assertion_regex_is_rejected():
    registry = PromptRegistry({"agent": "Hello"})
    scenario = {
        "id": "broken_regex",
        "prompt": "agent",
        "variables": {},
        "expect": {
            "assertions": [{
                "id": "broken",
                "kind": "contains_any",
                "match": "regex",
                "patterns": ["("],
            }]
        },
    }

    with pytest.raises(PromptEvaluationError, match="Invalid regex"):
        PromptScenarioEvaluator(registry).evaluate_scenario(scenario)


def test_daming_prompt_contract_fixture_and_cli_pass(capsys):
    project_root = Path(__file__).resolve().parents[2]
    fixture = project_root / "tests" / "evals" / "daming_prompt_contracts.json"

    result = evaluate_prompt_fixture(fixture, project_root=project_root)

    assert result.passed is True
    assert result.score == 100
    assert len(result.scenarios) == 4
    assert all(scenario.content_hash for scenario in result.scenarios)

    exit_code = main([str(fixture), "--project-root", str(project_root)])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "daming_core_prompt_contracts" in output
    assert "PASS" in output
