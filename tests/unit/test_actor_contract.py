from __future__ import annotations

import pytest

from multiagent_framework.actor_contract import (
    ActorContractError,
    ActorDefinition,
    ActorManager,
    effective_actor_documents,
    validate_actor_definitions,
)


def full_actor() -> dict:
    return {
        "actor_id": "frontier_guard",
        "display_name": "Test Actor",
        "description": "Actor contract fixture",
        "actor_type": "person",
        "entity_id": "outpost",
        "attributes": {"rank": 4, "health": 90},
        "traits": [{"trait_id": "vigilant", "intensity": 0.8, "tags": ["guard"]}],
        "needs": [{"need_id": "supply", "current": 40, "target": 80, "minimum": 0, "maximum": 100, "priority": 90}],
        "goals": [{"goal_id": "hold_line", "status": "active", "progress": 5, "target": 20, "priority": 100}],
        "capability_ids": ["send_report"],
        "identities": [{"identity_id": "commander", "kind": "role", "public": True}],
        "memberships": [{"organization_id": "garrison", "role": "commander", "rank": 4, "primary": True}],
        "states": {"alert": True, "phase": "watch"},
        "metadata": {"dlc": {"chapter": 1}},
    }


def test_actor_definition_covers_all_generic_components():
    actor = ActorDefinition.parse(full_actor())
    assert actor.actor_id == "frontier_guard"
    assert actor.attributes["health"] == 90
    assert actor.traits[0]["trait_id"] == "vigilant"
    assert actor.needs[0]["target"] == 80
    assert actor.goals[0]["target"] == 20
    assert actor.capability_ids == ("send_report",)
    assert actor.identities[0]["identity_id"] == "commander"
    assert actor.memberships[0]["organization_id"] == "garrison"
    assert actor.states["alert"] is True


def test_actor_validation_checks_references_and_explicit_agent_actor():
    issues = validate_actor_definitions(
        [full_actor()],
        agents=[{"id": "guard_agent", "actor_id": "missing_actor"}],
        entity_ids={"outpost"},
        capability_ids=set(),
    )
    codes = {issue.code for issue in issues}
    assert "actor.missing_capability" in codes
    assert "actor.membership.missing_organization" in codes
    assert "agent.missing_actor" in codes


def test_legacy_agent_projects_actor_but_explicit_reference_does_not():
    documents = effective_actor_documents([], [
        {"id": "legacy", "name": "Legacy Agent", "rank": 2},
        {"id": "runtime", "actor_id": "declared_elsewhere"},
    ])
    assert [item["actor_id"] for item in documents] == ["legacy"]
    assert documents[0]["metadata"]["legacy_agent_id"] == "legacy"


def test_duplicate_membership_has_stable_error_code():
    raw = full_actor()
    raw["memberships"].append(dict(raw["memberships"][0]))
    with pytest.raises(ActorContractError) as caught:
        ActorDefinition.parse(raw)
    assert caught.value.code == "actor.membership.duplicate_organization"


def test_actor_manager_preserves_only_runtime_values_valid_under_new_schema():
    manager = ActorManager()
    actor = ActorDefinition.parse(full_actor())
    manager.configure([actor], generation=1, preserve_state=False)
    snapshot = manager.snapshot()
    runtime = snapshot["actors"]["frontier_guard"]
    runtime["attributes"]["health"] = 75
    runtime["needs"]["supply"] = 95
    runtime["goals"]["hold_line"] = {"status": "completed", "progress": 20}
    runtime["states"]["alert"] = False
    runtime["version"] = 3
    manager.restore(snapshot)

    narrower = full_actor()
    narrower["needs"][0]["maximum"] = 60
    narrower["needs"][0]["target"] = 55
    narrower["goals"][0]["target"] = 10
    manager.configure([narrower], generation=2, preserve_state=True)
    current = manager.get("frontier_guard")
    assert current is not None
    assert current["runtime"]["attributes"]["health"] == 75
    assert current["runtime"]["needs"]["supply"] == 40
    assert current["runtime"]["goals"]["hold_line"] == {"status": "active", "progress": 5}
    assert current["runtime"]["states"]["alert"] is False
    assert current["runtime"]["version"] == 3
    assert current["generation"] == 2


def test_actor_prompt_values_use_safe_runtime_overlay_without_metadata():
    manager = ActorManager()
    manager.configure([full_actor()], generation=7)
    values = manager.prompt_values("frontier_guard")
    assert values["actor_id"] == "frontier_guard"
    assert '"supply"' in values["actor_needs"]
    assert "metadata" not in values["actor"]
    assert "chapter" not in values["actor"]


def test_actor_rejects_nested_state_and_overdeep_metadata():
    nested = full_actor()
    nested["states"] = {"bad": {"nested": True}}
    with pytest.raises(ActorContractError):
        ActorDefinition.parse(nested)
    deep = full_actor()
    deep["metadata"] = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    with pytest.raises(ActorContractError):
        ActorDefinition.parse(deep)
