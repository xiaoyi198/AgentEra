from __future__ import annotations

import pytest

from multiagent_framework.prompt_registry import PromptRegistry, PromptRegistryError


def test_versioned_blocks_render_in_priority_order_with_stable_hash():
    registry = PromptRegistry({
        "agent": {
            "version": "2.1",
            "max_chars": 200,
            "blocks": [
                {"name": "role", "priority": 20, "template": "Role: {role}"},
                {"name": "world", "priority": 10, "template": "World: {world}"},
            ],
        }
    })

    first = registry.render("agent", {"world": "Daming", "role": "official"})
    second = registry.render("agent", {"world": "Daming", "role": "official"})

    assert first.text == "World: Daming\n\nRole: official"
    assert first.version == "2.1"
    assert first.content_hash == second.content_hash
    assert first.used_variables == ("role", "world")
    assert first.char_count == len(first.text)
    assert first.estimated_tokens > 0


def test_lint_reports_missing_unknown_and_unused_variables():
    registry = PromptRegistry({"agent": "Hello {name}, follow {rule}."})

    issues = registry.lint(
        "agent",
        values={"name": "Li", "extra": 1},
        allowed_variables={"name"},
    )
    codes = {item.code for item in issues}

    assert "prompt.missing_variable" in codes
    assert "prompt.unknown_variable" in codes
    assert "prompt.unused_variable" in codes


def test_render_rejects_missing_variable_and_budget_overflow():
    registry = PromptRegistry({
        "missing": "Hello {name}",
        "small": {"template": "123456", "max_chars": 5},
    })

    with pytest.raises(PromptRegistryError, match="Missing render variable"):
        registry.render("missing", {})
    with pytest.raises(PromptRegistryError, match="exceeding budget"):
        registry.render("small", {})


def test_export_preserves_versioned_definition_shape():
    registry = PromptRegistry({
        "agent": {
            "version": "3",
            "template": "Hello",
            "metadata": {"owner": "test"},
        }
    })

    exported = registry.export_definitions()["agent"]
    assert exported["version"] == "3"
    assert exported["metadata"] == {"owner": "test"}
    assert exported["blocks"][0]["template"] == "Hello"
