from __future__ import annotations

import copy

import pytest

from multiagent_framework.causal_templates import CausalTemplateError, expand_causal_rule_document


def content() -> dict:
    return {
        "causal_condition_groups": [{
            "group_id": "treasury_ready",
            "name": "Treasury ready",
            "conditions": [{
                "kind": "anchored_inventory", "entity_id": "treasury", "name": "coin",
                "operator": "gte", "value": 20,
            }],
        }],
        "causal_rule_templates": [{
            "template_id": "delayed",
            "name": "Delayed follow-up",
            "rule": {
                "delay_days": 2, "probability": 0.75, "max_depth": 3,
                "conditions": [{
                    "kind": "metric", "entity_id": "court", "name": "stability",
                    "operator": "gte", "value": 50,
                }],
                "world_effects": [{
                    "effect_type": "update_status", "entity_id": "court", "new_status": "alert",
                }],
            },
        }],
        "causal_event_rules": [{
            "rule_id": "tax_follow_up", "template_id": "delayed",
            "condition_group_ids": ["treasury_ready"],
            "source_event_types": ["distribution"], "child_event_type": "tax",
            "probability": 1,
            "conditions": [{
                "kind": "parameter", "entity_id": "court", "name": "tax_rate",
                "operator": "gt", "value": 0,
            }],
        }],
    }


def test_expand_causal_rule_document_applies_precedence_and_condition_order_without_mutation():
    source = content()
    before = copy.deepcopy(source)

    result = expand_causal_rule_document(source)

    assert source == before
    assert result == [{
        "delay_days": 2,
        "probability": 1,
        "max_depth": 3,
        "world_effects": [{
            "effect_type": "update_status", "entity_id": "court", "new_status": "alert",
        }],
        "rule_id": "tax_follow_up",
        "source_event_types": ["distribution"],
        "child_event_type": "tax",
        "conditions": [
            before["causal_rule_templates"][0]["rule"]["conditions"][0],
            before["causal_condition_groups"][0]["conditions"][0],
            before["causal_event_rules"][0]["conditions"][0],
        ],
    }]
    assert "template_id" not in result[0]
    assert "condition_group_ids" not in result[0]


def test_expand_causal_rule_document_preserves_legacy_rules():
    legacy = {"causal_event_rules": [{
        "rule_id": "legacy", "source_event_types": ["a"], "child_event_type": "b",
        "conditions": [],
    }]}
    assert expand_causal_rule_document(legacy) == legacy["causal_event_rules"]


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda value: value["causal_condition_groups"].append(copy.deepcopy(value["causal_condition_groups"][0])), "causal_condition_group_duplicate"),
    (lambda value: value["causal_rule_templates"].append(copy.deepcopy(value["causal_rule_templates"][0])), "causal_rule_template_duplicate"),
    (lambda value: value["causal_event_rules"][0].update(template_id="missing"), "causal_rule_template_missing"),
    (lambda value: value["causal_event_rules"][0].update(condition_group_ids=["missing"]), "causal_condition_group_missing"),
    (lambda value: value["causal_event_rules"][0].update(condition_group_ids=["treasury_ready", "treasury_ready"]), "causal_condition_group_reference_duplicate"),
    (lambda value: value["causal_rule_templates"][0]["rule"].update(template_id="nested"), "causal_rule_template_nested_reference"),
])
def test_expand_causal_rule_document_rejects_invalid_authoring_references(mutate, code):
    value = content()
    mutate(value)
    with pytest.raises(CausalTemplateError) as error:
        expand_causal_rule_document(value)
    assert error.value.code == code
    assert error.value.path
