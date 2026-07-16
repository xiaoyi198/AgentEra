from __future__ import annotations

import copy

import pytest

from multiagent_framework.lifecycle_contract import (
    LifecycleContract,
    LifecycleContractError,
    normalize_lifecycle_document,
)


def _full_document() -> dict:
    return {
        "enabled": True,
        "recent_feedback_limit": 12,
        "actor_templates": [
            {
                "template_id": "patrol_guard",
                "display_name": "巡防员",
                "actor": {
                    "display_name": "新任巡防员",
                    "actor_type": "person",
                    "attributes": {"merit": 8, "age": 20},
                    "states": {"alive": True},
                    "memberships": [
                        {
                            "organization_id": "巡防营",
                            "role": "guard",
                            "rank": 1,
                            "primary": True,
                        }
                    ],
                },
                "entity": {
                    "entity_type": "person",
                    "name": "巡防员",
                    "location_id": "边境哨站",
                    "metadata": {"tags": ["human", "guard"]},
                },
                "agent": {
                    "source_agent_id": "哨站守备官",
                    "display_name": "巡防智能体",
                    "system_prompt": "维护边境秩序。",
                },
                "metadata": {"dlc_owned": True},
            }
        ],
        "positions": [
            {
                "position_id": "patrol_captain",
                "display_name": "巡防队长",
                "organization_id": "巡防营",
                "role": "captain",
                "rank": 10,
                "capacity": 1,
                "incumbent_actor_ids": ["旧队长"],
                "candidate_actor_ids": ["候选甲", "候选乙"],
                "scores": [
                    {"source": "attribute", "name": "merit", "weight": 2, "direction": "desc"},
                    {"source": "membership_rank", "weight": 1, "direction": "desc"},
                    {"source": "actor_id", "weight": 0, "direction": "asc"},
                ],
                "metadata": {"succession": "merit"},
            }
        ],
        "transitions": [
            {
                "transition_id": "weekly_recruitment",
                "display_name": "每周征募",
                "kind": "birth",
                "trigger": "week_start",
                "cycle_id": "frontier_patrol",
                "mode": "all",
                "conditions": [
                    {
                        "kind": "entity_state",
                        "entity_id": "边境哨站",
                        "name": "operational",
                        "operator": "eq",
                        "value": True,
                    }
                ],
                "once": False,
                "cooldown_days": 7,
                "edge_triggered": True,
                "operations": [
                    {
                        "operation": "spawn_actor",
                        "template_id": "patrol_guard",
                        "actor_id_pattern": "guard_{sequence}",
                        "entity_id_pattern": "guard_entity_{actor_id}",
                        "agent_id_pattern": "guard_agent_{actor_id}",
                    },
                    {
                        "operation": "appoint_position",
                        "position_id": "patrol_captain",
                        "actor_id": "{actor_id}",
                    },
                ],
                "feedback": {"success": "巡防营迎来新成员。"},
            }
        ],
    }


def test_empty_document_is_disabled_and_unconfigured():
    contract = LifecycleContract.from_mapping({})

    assert contract.enabled is False
    assert contract.configured is False
    assert contract.actor_templates == ()
    assert contract.positions == ()
    assert contract.transitions == ()
    assert normalize_lifecycle_document(None) == {}


def test_complete_document_parses_templates_positions_transitions_and_unicode_references():
    contract = LifecycleContract.from_mapping(_full_document())

    assert contract.configured is True
    assert contract.actor_templates[0].agent["source_agent_id"] == "哨站守备官"
    assert contract.actor_templates[0].actor["memberships"][0]["organization_id"] == "巡防营"
    assert contract.positions[0].incumbent_actor_ids == ("旧队长",)
    assert contract.positions[0].candidate_actor_ids == ("候选甲", "候选乙")
    transition = contract.transitions[0]
    assert transition.trigger == "week_start"
    assert transition.conditions[0].entity_id == "边境哨站"
    assert transition.operations[0].data["actor_id_pattern"] == "guard_{sequence}"
    assert transition.operations[1].data["actor_id"] == "{actor_id}"


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        ("actor_templates", "lifecycle.duplicate_actor_template"),
        ("positions", "lifecycle.duplicate_position"),
        ("transitions", "lifecycle.duplicate_transition"),
    ],
)
def test_duplicate_contract_ids_are_rejected(field, expected_code):
    document = _full_document()
    document[field].append(copy.deepcopy(document[field][0]))

    with pytest.raises(LifecycleContractError) as exc_info:
        LifecycleContract.from_mapping(document)

    assert exc_info.value.code == expected_code


def test_transition_rejects_missing_actor_template_reference():
    document = _full_document()
    document["transitions"][0]["operations"][0]["template_id"] = "missing"

    with pytest.raises(LifecycleContractError) as exc_info:
        LifecycleContract.from_mapping(document)

    assert exc_info.value.code == "lifecycle.operation.missing_template"
    assert exc_info.value.path.endswith(".template_id")


def test_transition_rejects_missing_position_reference():
    document = _full_document()
    document["transitions"][0]["operations"][1]["position_id"] = "missing"

    with pytest.raises(LifecycleContractError) as exc_info:
        LifecycleContract.from_mapping(document)

    assert exc_info.value.code == "lifecycle.operation.missing_position"
    assert exc_info.value.path.endswith(".position_id")


def test_transition_requires_at_least_one_operation():
    document = _full_document()
    document["transitions"][0]["operations"] = []

    with pytest.raises(LifecycleContractError) as exc_info:
        LifecycleContract.from_mapping(document)

    assert exc_info.value.code == "lifecycle.transition.operations_required"


def test_spawn_patterns_reject_unknown_placeholder():
    document = _full_document()
    document["transitions"][0]["operations"][0]["actor_id_pattern"] = "guard_{random}"

    with pytest.raises(LifecycleContractError) as exc_info:
        LifecycleContract.from_mapping(document)

    assert exc_info.value.code == "lifecycle.operation.invalid_pattern"


def test_fingerprint_is_stable_and_changes_with_authoritative_content():
    first = LifecycleContract.from_mapping(_full_document())
    reordered = copy.deepcopy(_full_document())
    reordered["actor_templates"][0]["actor"] = {
        "states": {"alive": True},
        "attributes": {"age": 20, "merit": 8},
        "memberships": reordered["actor_templates"][0]["actor"]["memberships"],
        "actor_type": "person",
        "display_name": "新任巡防员",
    }
    second = LifecycleContract.from_mapping(reordered)
    changed = copy.deepcopy(_full_document())
    changed["positions"][0]["capacity"] = 2
    changed["positions"][0]["incumbent_actor_ids"] = []
    third = LifecycleContract.from_mapping(changed)

    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() != third.fingerprint()


def test_public_state_includes_initial_positions_and_runtime_vacancy_state():
    contract = LifecycleContract.from_mapping(_full_document())

    initial = contract.public_state(generation=3)
    position = initial["positions"][0]
    assert initial["generation"] == 3
    assert position["incumbent_actor_ids"] == ["旧队长"]
    assert position["vacant"] is False

    runtime = contract.public_state(
        generation=4,
        runtime_state={
            "positions": {
                "patrol_captain": {
                    "position_id": "patrol_captain",
                    "display_name": "巡防队长",
                    "capacity": 1,
                    "incumbent_actor_ids": [],
                    "vacant": True,
                    "version": 2,
                }
            },
            "dynamic_actors": {"guard_1": {"actor_id": "guard_1"}},
            "dynamic_agents": {"guard_agent_1": {"agent_id": "guard_agent_1"}},
            "recent_feedback": [{"kind": "birth", "message": "已征募"}],
        },
    )
    assert runtime["counts"]["dynamic_actors"] == 1
    assert runtime["counts"]["dynamic_agents"] == 1
    assert runtime["positions"][0]["vacant"] is True
    assert runtime["recent_feedback"][-1]["message"] == "已征募"
