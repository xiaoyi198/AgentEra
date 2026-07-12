from __future__ import annotations

import json
from pathlib import Path

import pytest

from multiagent_framework.dlc_registry import DLCRegistry, DLCRegistryError, version_satisfies


def manifest(dlc_id: str, name: str = "Test DLC") -> dict:
    return {"id": dlc_id, "name": name, "version": "0.1.0"}


def valid_content(entity_id: str = "place", agent_id: str = "agent") -> dict:
    return {
        "schema_version": "1.0",
        "resources": [
            {"name": "coin", "kind": "asset", "unit": "piece", "anchor_rate": 2.0},
            {"name": "morale", "kind": "metric"},
        ],
        "entities": [{"id": entity_id, "type": "location", "name": entity_id}],
        "inventory": [{"entity_id": entity_id, "item_name": "coin", "quantity": 3}],
        "entity_values": [{"entity_id": entity_id, "name": "morale", "kind": "metric", "value": 50}],
        "relations": [],
        "agents": [{"id": agent_id, "name": agent_id, "location_id": entity_id}],
        "initial_events": [{"event_id": "intro", "target_agents": [agent_id]}],
        "random_events": [],
        "time_rules": [],
        "ongoing_events": [{"event_id": "work", "entity_id": entity_id}],
        "personas": {},
        "prompts": {},
    }


@pytest.fixture
def registry(tmp_path: Path) -> DLCRegistry:
    return DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])


def test_discovers_and_normalizes_builtin_legacy_dlc():
    project_root = Path(__file__).resolve().parents[2]
    registry = DLCRegistry(project_root)
    package = registry.get("daming_court")
    content = registry.load_content("daming_court")
    assert package.manifest.format == "legacy-v1"
    assert len(content["entities"]) == 13
    assert len(content["agents"]) == 14
    assert content["inventory"]
    assert content["entity_values"]
    assert not registry.validate("daming_court")
    example = registry.get("example_frontier")
    assert example.manifest.format == "bundle-v1"
    assert example.manifest.enabled is False
    assert not registry.validate("example_frontier")


def test_create_draft_and_atomic_content_round_trip(registry: DLCRegistry):
    package = registry.create(manifest("sample"))
    assert package.manifest.status == "draft"
    assert package.manifest.enabled is False
    registry.save_content("sample", valid_content())
    reloaded = DLCRegistry(registry.project_root, manifest_roots=[registry.packages_root])
    assert reloaded.load_content("sample")["entities"][0]["id"] == "place"
    assert not list(package.root.glob("*.tmp"))


def test_entrypoint_cannot_escape_package_root(registry: DLCRegistry):
    registry.create(manifest("sample"))
    with pytest.raises(DLCRegistryError, match="escapes package root"):
        registry.save_manifest("sample", {"entrypoints": {"content": "../../outside.json"}})


def test_duplicate_ids_and_missing_references_are_reported(registry: DLCRegistry):
    registry.create(manifest("broken"))
    content = valid_content()
    content["entities"].append(dict(content["entities"][0]))
    content["inventory"][0]["entity_id"] = "missing"
    content["agents"][0]["superior_id"] = "missing-agent"
    registry.save_content("broken", content)
    codes = {issue.code for issue in registry.validate("broken")}
    assert "entities.duplicate" in codes
    assert "inventory.missing_entity" in codes
    assert "agent.missing_superior" in codes


def test_asset_anchor_rate_must_be_positive_and_finite(registry: DLCRegistry):
    registry.create(manifest("broken"))
    content = valid_content()
    content["resources"][0]["anchor_rate"] = 0
    registry.save_content("broken", content)
    assert "resource.invalid_anchor_rate" in {issue.code for issue in registry.validate("broken")}


def test_publish_requires_valid_content_and_enable_requires_publish(registry: DLCRegistry):
    registry.create(manifest("sample"))
    with pytest.raises(DLCRegistryError, match="Only published"):
        registry.set_enabled("sample", True)
    registry.save_content("sample", valid_content())
    published = registry.publish("sample")
    assert published.manifest.status == "published"
    enabled = registry.set_enabled("sample", True)
    assert enabled.manifest.enabled is True


def test_publish_rejects_invalid_content(registry: DLCRegistry):
    registry.create(manifest("broken"))
    content = valid_content()
    content["inventory"][0]["item_name"] = "unknown"
    registry.save_content("broken", content)
    with pytest.raises(DLCRegistryError, match="validation failed"):
        registry.publish("broken")


def test_keyed_entity_agent_and_resource_crud(registry: DLCRegistry):
    registry.create(manifest("sample"))
    assert registry.upsert_entity("sample", "city", {"type": "location", "name": "City"})
    assert not registry.upsert_entity("sample", "city", {"type": "location", "name": "New City"})
    assert registry.upsert_agent("sample", "mayor", {"name": "Mayor", "location_id": "city"})
    assert registry.upsert_resource("sample", "gold", {"kind": "asset", "anchor_rate": 1})
    content = registry.load_content("sample")
    assert content["entities"][0]["name"] == "New City"
    assert registry.delete_agent("sample", "mayor")
    assert registry.delete_resource("sample", "gold")
    assert registry.delete_entity("sample", "city")
    assert not registry.delete_entity("sample", "city")


def test_cross_published_package_conflicts_are_reported(registry: DLCRegistry):
    registry.create(manifest("first"))
    registry.save_content("first", valid_content())
    registry.publish("first")
    registry.create(manifest("second"))
    registry.save_content("second", valid_content())
    codes = {issue.code for issue in registry.validate("second")}
    assert "cross_package.entity_conflict" in codes
    assert "cross_package.agent_conflict" in codes
    assert "cross_package.resource_conflict" in codes


def test_active_packages_are_dependency_ordered(registry: DLCRegistry):
    registry.create(manifest("base"))
    registry.save_content("base", valid_content("base_place", "base_agent"))
    registry.publish("base")
    registry.set_enabled("base", True)
    registry.create({**manifest("addon"), "dependencies": ["base"]})
    addon = valid_content("addon_place", "addon_agent")
    addon["resources"][0]["name"] = "addon_coin"
    addon["resources"][1]["name"] = "addon_morale"
    addon["inventory"][0]["item_name"] = "addon_coin"
    addon["entity_values"][0]["name"] = "addon_morale"
    registry.save_content("addon", addon)
    registry.publish("addon")
    registry.set_enabled("addon", True)
    assert [item.manifest.id for item in registry.ordered_active_packages()] == ["base", "addon"]


def test_model_binding_sections_and_agent_references_are_validated(registry: DLCRegistry):
    registry.create(manifest("binding-errors"))
    registry.save_content("binding-errors", valid_content())
    registry.save_manifest(
        "binding-errors",
        {
            "model_bindings": {
                "tasks": ["fast"],
                "agents": {"missing-agent": "deep", "agent": ""},
            }
        },
    )

    issues = registry.validate("binding-errors")
    codes = {issue.code for issue in issues}
    paths = {issue.path for issue in issues}

    assert "manifest.invalid_model_binding_section" in codes
    assert "manifest.invalid_model_binding" in codes
    assert "manifest.model_binding_unknown_agent" in codes
    assert "manifest.model_bindings.tasks" in paths
    assert "manifest.model_bindings.agents.missing-agent" in paths


def test_agent_model_profile_must_be_non_empty_when_present(registry: DLCRegistry):
    registry.create(manifest("agent-profile"))
    content = valid_content()
    content["agents"][0]["model_profile"] = "   "
    registry.save_content("agent-profile", content)

    issues = registry.validate("agent-profile")

    assert any(
        issue.code == "agent.invalid_model_profile"
        and issue.path == "agents[0].model_profile"
        for issue in issues
    )


def test_legacy_flat_task_model_bindings_remain_valid(registry: DLCRegistry):
    registry.create(manifest("flat-bindings"))
    registry.save_content("flat-bindings", valid_content())
    registry.save_manifest(
        "flat-bindings",
        {"model_bindings": {"agent.turn": "fast", "router.simple": "cheap"}},
    )

    codes = {issue.code for issue in registry.validate("flat-bindings")}

    assert "manifest.invalid_model_binding" not in codes
    assert "manifest.invalid_model_binding_section" not in codes

def test_relation_event_persona_and_prompt_runtime_constraints_are_validated(registry: DLCRegistry):
    registry.create(manifest("content-errors"))
    content = valid_content()
    content["relations"] = [
        {"parent_id": "place", "child_id": "place", "rel_type": "unknown"},
    ]
    content["initial_events"] = [
        {
            "event_id": "",
            "severity": 11,
            "target_agents": "agent",
            "initial_payload": [],
        },
    ]
    content["ongoing_events"] = [
        {
            "event_id": "work",
            "entity_id": "place",
            "start_time": 20,
            "end_time": 10,
            "expected_result": [],
        },
    ]
    content["personas"] = {"empty": ""}
    content["prompts"] = {
        "official_template": {
            "version": "1.0",
            "blocks": [{"name": "main", "template": "Unknown: {secret}"}],
        },
    }
    registry.save_content("content-errors", content)

    codes = {issue.code for issue in registry.validate("content-errors")}

    assert "relation.invalid_type" in codes
    assert "relation.self_reference" in codes
    assert "event.missing_id" in codes
    assert "event.invalid_severity" in codes
    assert "event.invalid_targets" in codes
    assert "event.invalid_payload" in codes
    assert "ongoing_event.invalid_result" in codes
    assert "ongoing_event.invalid_time_range" in codes
    assert "persona.invalid_text" in codes
    assert "prompt.unknown_variable" in codes


def test_invalid_prompt_definition_blocks_publish(registry: DLCRegistry):
    registry.create(manifest("bad-prompt"))
    content = valid_content()
    content["prompts"] = {
        "official_template": {
            "version": "1.0",
            "blocks": [{"name": "main"}],
        },
    }
    registry.save_content("bad-prompt", content)

    issues = registry.validate("bad-prompt")

    assert any(issue.code == "prompt.invalid_definition" for issue in issues)
    with pytest.raises(DLCRegistryError, match="validation failed"):
        registry.publish("bad-prompt")

def test_inventory_cannot_override_resource_anchor_rate(registry: DLCRegistry):
    registry.create(manifest("anchor-mismatch"))
    content = valid_content()
    content["inventory"][0]["anchor_rate"] = 999
    registry.save_content("anchor-mismatch", content)

    issues = registry.validate("anchor-mismatch")

    assert any(
        issue.code == "inventory.anchor_mismatch"
        and issue.path == "inventory[0].anchor_rate"
        for issue in issues
    )



@pytest.mark.parametrize(
    ("version", "constraint", "expected"),
    [
        ("1.4.2", ">=1.0.0,<2.0.0", True),
        ("2.0.0", ">=1.0.0,<2.0.0", False),
        ("1.4.2", "^1.2.0", True),
        ("2.0.0", "^1.2.0", False),
        ("0.2.5", "^0.2.1", True),
        ("0.3.0", "^0.2.1", False),
        ("1.2.9", "~1.2.0", True),
        ("1.3.0", "~1.2.0", False),
        ("2.1.0", "1.x || >=2.0.0,<3.0.0", True),
    ],
)
def test_semver_dependency_constraints(version: str, constraint: str, expected: bool):
    assert version_satisfies(version, constraint) is expected


def test_dependency_version_mismatch_blocks_publish(registry: DLCRegistry):
    registry.create({**manifest("base"), "version": "1.4.0"})
    registry.save_content("base", valid_content("base_place", "base_agent"))
    registry.publish("base")
    registry.create({
        **manifest("addon"),
        "dependencies": ["base"],
        "dependency_constraints": {"base": ">=2.0.0,<3.0.0"},
    })
    addon = valid_content("addon_place", "addon_agent")
    addon["resources"][0]["name"] = "addon_coin"
    addon["resources"][1]["name"] = "addon_morale"
    addon["inventory"][0]["item_name"] = "addon_coin"
    addon["entity_values"][0]["name"] = "addon_morale"
    registry.save_content("addon", addon)

    issues = registry.validate("addon")

    assert any(issue.code == "dependency.version_mismatch" for issue in issues)
    with pytest.raises(DLCRegistryError, match="validation failed"):
        registry.publish("addon")


def test_dependency_cycle_is_reported_before_activation(registry: DLCRegistry):
    registry.create(manifest("alpha"))
    registry.create(manifest("beta"))
    registry.save_manifest("alpha", {"dependencies": ["beta"]})
    registry.save_manifest("beta", {"dependencies": ["alpha"]})

    alpha_issues = registry.validate("alpha")

    assert any(
        issue.code == "dependency.cycle" and "alpha -> beta -> alpha" in issue.message
        for issue in alpha_issues
    )


def test_dependency_must_be_published_and_enabled_in_lifecycle_order(registry: DLCRegistry):
    registry.create({**manifest("base"), "version": "1.2.0"})
    registry.save_content("base", valid_content("base_place", "base_agent"))
    registry.create({
        **manifest("addon"),
        "dependencies": ["base"],
        "dependency_constraints": {"base": "^1.0.0"},
    })
    addon = valid_content("addon_place", "addon_agent")
    addon["resources"][0]["name"] = "addon_coin"
    addon["resources"][1]["name"] = "addon_morale"
    addon["inventory"][0]["item_name"] = "addon_coin"
    addon["entity_values"][0]["name"] = "addon_morale"
    registry.save_content("addon", addon)

    with pytest.raises(DLCRegistryError, match="must be published first"):
        registry.publish("addon")
    registry.publish("base")
    registry.publish("addon")
    with pytest.raises(DLCRegistryError, match="must be enabled first"):
        registry.set_enabled("addon", True)
    registry.set_enabled("base", True)
    registry.set_enabled("addon", True)


def test_content_migration_preview_apply_and_stale_preview_guard(registry: DLCRegistry):
    registry.create(manifest("old_content"))
    content = valid_content()
    content["schema_version"] = "0.9"
    content["resources"][0]["type"] = content["resources"][0].pop("kind")
    content["inventory"][0]["resource_name"] = content["inventory"][0].pop("item_name")
    registry.save_content("old_content", content)

    assert any(issue.code == "content.migration_required" for issue in registry.validate("old_content"))
    preview = registry.plan_content_migration("old_content")
    assert preview["source_schema_version"] == "0.9"
    assert preview["target_schema_version"] == "1.0"
    assert preview["changed"] is True
    assert preview["content"]["resources"][0]["kind"] == "asset"
    assert preview["content"]["inventory"][0]["item_name"] == "coin"

    applied = registry.migrate_content(
        "old_content", expected_source_schema_version=preview["source_schema_version"],
    )
    assert applied["changed"] is True
    assert registry.load_content("old_content")["schema_version"] == "1.0"
    with pytest.raises(DLCRegistryError, match="changed since preview"):
        registry.migrate_content("old_content", expected_source_schema_version="0.9")


def test_random_event_templates_validate_schedule_targets_and_reserved_payload(registry: DLCRegistry):
    registry.create(manifest("random-events"))
    content = valid_content()
    content["random_events"] = [{
        "template_id": "bad id",
        "event_type": "storm",
        "target_agents": ["missing-agent"],
        "initial_payload": {"occurrence": 99},
        "severity_min": True,
        "severity_max": 11,
        "min_interval_days": 0,
        "max_interval_days": float("inf"),
        "probability": 1.5,
        "max_occurrences": -1,
        "enabled": "yes",
    }]
    registry.save_content("random-events", content)
    codes = {issue.code for issue in registry.validate("random-events")}
    assert {
        "random_event.invalid_template_id",
        "random_event.missing_agent",
        "random_event.reserved_payload_field",
        "random_event.invalid_severity",
        "random_event.invalid_interval",
        "random_event.invalid_probability",
        "random_event.invalid_max_occurrences",
        "random_event.invalid_enabled",
    } <= codes


def test_valid_random_event_template_can_be_published(registry: DLCRegistry):
    registry.create(manifest("random-events"))
    content = valid_content()
    content["random_events"] = [{
        "template_id": "storm_cycle",
        "event_type": "storm",
        "target_agents": ["agent"],
        "initial_payload": {"region": "place"},
        "severity_min": 2,
        "severity_max": 8,
        "min_interval_days": 10,
        "max_interval_days": 30,
        "probability": 0.4,
        "max_occurrences": 3,
        "enabled": True,
    }]
    registry.save_content("random-events", content)
    assert not registry.validate("random-events")
    assert registry.publish("random-events").manifest.status == "published"


def test_time_rules_are_normalized_and_strictly_validated(registry: DLCRegistry):
    registry.create(manifest("timepack"))
    content = valid_content()
    content["time_rules"] = [{
        "rule_id": "policy", "action_types": ["execute_policy"],
        "base_days": 2, "min_days": 1, "max_days": 20,
        "severity_factor": 3, "workload_factor": 0.5,
        "resource_factor": 0.1, "distance_factor": 1,
        "success_factor": 0.8, "priority": 5, "enabled": True,
    }]
    registry.save_content("timepack", content)
    assert not registry.validate("timepack")

    content["time_rules"].append(dict(content["time_rules"][0]))
    content["time_rules"][0]["max_days"] = float("inf")
    registry.save_content("timepack", content)
    codes = {issue.code for issue in registry.validate("timepack")}
    assert "time_rules.duplicate" in codes
    assert "time_rule.invalid_rule" in codes
