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
        "causal_event_rules": [],
        "time_rules": [],
        "ongoing_events": [{"event_id": "work", "entity_id": entity_id}],
        "personas": {},
        "prompts": {},
    }


def lifecycle_content() -> dict:
    return {
        "enabled": True,
        "actor_templates": [
            {
                "template_id": "guard",
                "display_name": "Guard",
                "actor": {
                    "actor_type": "person",
                    "memberships": [{"organization_id": "guild", "role": "member"}],
                },
                "entity": {"entity_type": "person", "location_id": "place"},
                "agent": {"source_agent_id": "agent"},
            }
        ],
        "positions": [
            {
                "position_id": "captain",
                "display_name": "Captain",
                "organization_id": "guild",
                "role": "captain",
                "incumbent_actor_ids": ["agent"],
                "candidate_actor_ids": ["agent"],
            }
        ],
        "transitions": [
            {
                "transition_id": "recruit",
                "display_name": "Recruit",
                "kind": "birth",
                "trigger": "manual",
                "operations": [
                    {
                        "operation": "spawn_actor",
                        "template_id": "guard",
                        "actor_id_pattern": "guard_{sequence}",
                    },
                    {
                        "operation": "appoint_position",
                        "position_id": "captain",
                        "actor_id": "{actor_id}",
                    },
                ],
            }
        ],
    }


def derived_metric(metric_id: str = "readiness", **overrides) -> dict:
    value = {
        "metric_id": metric_id,
        "display_name": metric_id,
        "audiences": ["player", "ui", "agent", "rule", "objective"],
        "formula": {
            "op": "source",
            "source": "anchored_inventory",
            "entity_id": "place",
            "name": "coin",
        },
    }
    value.update(overrides)
    return value

def outcome_feedback_content() -> dict:
    return {
        "enabled": True,
        "recent_limit": 64,
        "pending_limit": 128,
        "receipt_limit": 512,
        "metric_delta_epsilon": 0.1,
        "immediate": {
            "enabled": True,
            "include_command_changes": True,
            "include_metric_changes": True,
            "max_changes": 6,
            "max_metric_changes": 4,
        },
        "case_summaries": {
            "enabled": True,
            "include_decisions": True,
            "include_changes": True,
            "include_metric_changes": True,
        },
        "rules": [{
            "rule_id": "inspection_result",
            "display_name": "Inspection result",
            "priority": 10,
            "source_kinds": ["action", "case_closed"],
            "delivery": "causal_reveal",
            "action_ids": ["inspect"],
            "issue_type_ids": ["followup"],
            "event_types": ["inspection_complete"],
            "metric_ids": ["readiness"],
            "delay_days": 0.5,
            "conditions": [
                {
                    "kind": "anchored_inventory",
                    "entity_id": "place",
                    "name": "coin",
                    "operator": "gte",
                    "value": 1,
                },
                {
                    "kind": "derived_metric",
                    "name": "readiness",
                    "operator": "gte",
                    "value": 1,
                },
            ],
            "title": "Inspection complete",
            "summary": "The command result is now visible.",
            "reason": "Readiness and supplies met the configured threshold.",
            "reveal_text": "The earlier inspection enabled this outcome.",
            "create_issue_type_id": "followup",
            "attach_evidence": True,
        }],
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


def test_lifecycle_document_is_normalized_and_validated_with_static_references(registry: DLCRegistry):
    registry.create(manifest("lifecycle"))
    content = valid_content()
    content["entities"].append({"id": "guild", "type": "organization", "name": "Guild"})
    content["lifecycle"] = lifecycle_content()

    registry.save_content("lifecycle", content)

    loaded = registry.load_content("lifecycle")
    assert loaded["lifecycle"]["actor_templates"][0]["template_id"] == "guard"
    assert registry.validate("lifecycle") == []


def test_lifecycle_validation_reports_contract_and_world_reference_errors(registry: DLCRegistry):
    registry.create(manifest("broken_lifecycle"))
    content = valid_content()
    lifecycle = lifecycle_content()
    lifecycle["actor_templates"][0]["agent"]["source_agent_id"] = "missing-agent"
    lifecycle["actor_templates"][0]["actor"]["memberships"][0]["organization_id"] = "missing-org"
    lifecycle["positions"][0]["organization_id"] = "missing-org"
    lifecycle["positions"][0]["candidate_actor_ids"] = ["missing-actor"]
    lifecycle["transitions"][0].update({
        "trigger": "cycle_start",
        "cycle_id": "missing-cycle",
        "conditions": [
            {
                "kind": "anchored_inventory",
                "entity_id": "place",
                "name": "missing-resource",
                "operator": "gte",
                "value": 1,
            },
            {
                "kind": "objective_progress",
                "entity_id": "missing-objective",
                "operator": "gte",
                "value": 1,
            },
        ],
    })
    lifecycle["transitions"][0]["operations"].append({
        "operation": "transform_entity",
        "entity_id": "missing-entity",
    })
    content["lifecycle"] = lifecycle
    registry.save_content("broken_lifecycle", content)

    codes = {issue.code for issue in registry.validate("broken_lifecycle")}

    assert {
        "lifecycle.missing_source_agent",
        "lifecycle.missing_organization",
        "lifecycle.missing_actor",
        "lifecycle.missing_cycle",
        "lifecycle.missing_resource",
        "lifecycle.missing_objective",
        "lifecycle.missing_entity",
    } <= codes


def test_lifecycle_dynamic_actor_patterns_are_not_treated_as_static_actor_ids(registry: DLCRegistry):
    registry.create(manifest("dynamic_lifecycle"))
    content = valid_content()
    content["entities"].append({"id": "guild", "type": "organization", "name": "Guild"})
    lifecycle = lifecycle_content()
    lifecycle["positions"][0]["incumbent_actor_ids"] = []
    lifecycle["positions"][0]["candidate_actor_ids"] = ["guard_{sequence}"]
    content["lifecycle"] = lifecycle
    registry.save_content("dynamic_lifecycle", content)

    codes = {issue.code for issue in registry.validate("dynamic_lifecycle")}

    assert "lifecycle.missing_actor" not in codes


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


def _noop_capability_definition(capability_id: str, *, aliases: list[str] | None = None) -> dict:
    return {
        "capability_id": capability_id,
        "display_name": capability_id,
        "description": "Registry conflict fixture.",
        "handler": "noop",
        "audiences": ["player", "agent", "router"],
        "aliases": list(aliases or []),
        "target": {"kind": "none", "mode": "none", "allowed_ids": []},
        "parameters": [],
        "input": {"content_required": False, "content_label": "Content"},
    }

def test_cross_published_package_conflicts_are_reported(registry: DLCRegistry):
    registry.create(manifest("first"))
    first = valid_content()
    first["capabilities"] = [_noop_capability_definition("first_wait", aliases=["shared_wait"])]
    first["derived_metrics"] = [derived_metric("shared_readiness")]
    registry.save_content("first", first)
    registry.publish("first")
    registry.create(manifest("second"))
    second = valid_content()
    second["capabilities"] = [_noop_capability_definition("second_wait", aliases=["shared_wait"])]
    second["derived_metrics"] = [derived_metric("shared_readiness")]
    registry.save_content("second", second)
    codes = {issue.code for issue in registry.validate("second")}
    assert "cross_package.entity_conflict" in codes
    assert "cross_package.agent_conflict" in codes
    assert "cross_package.resource_conflict" in codes
    assert "cross_package.capability_conflict" in codes
    assert "cross_package.derived_metric_conflict" in codes


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



def causal_rule(**overrides):
    value = {
        "rule_id": "shortage_chain",
        "source_event_types": ["drought"],
        "child_event_type": "shortage",
        "target_agents": ["agent"],
        "delay_days": 1,
        "probability": 1,
        "conditions": [
            {"kind": "anchored_inventory", "entity_id": "place", "name": "coin", "operator": "lt", "value": 10},
            {"kind": "metric", "entity_id": "place", "name": "morale", "operator": "lte", "value": 50},
        ],
    }
    value.update(overrides)
    return value


def test_causal_event_rule_validation_accepts_valid_references(registry: DLCRegistry):
    registry.create(manifest("sample"))
    content = valid_content()
    content["causal_event_rules"] = [causal_rule()]
    registry.save_content("sample", content)
    assert not registry.validate("sample")


def test_causal_event_rule_validation_reports_duplicate_and_missing_references(registry: DLCRegistry):
    registry.create(manifest("broken"))
    content = valid_content()
    broken = causal_rule(
        target_agents=["missing-agent"],
        conditions=[
            {"kind": "anchored_inventory", "entity_id": "missing-place", "name": "missing-resource", "operator": "lt", "value": 1},
        ],
    )
    content["causal_event_rules"] = [broken, dict(broken)]
    registry.save_content("broken", content)
    codes = {issue.code for issue in registry.validate("broken")}
    assert {
        "causal_events.duplicate",
        "causal_event.missing_agent",
        "causal_event.missing_entity",
        "causal_event.missing_resource",
    } <= codes


@pytest.mark.parametrize("condition", [
    {"kind": "anchored_inventory", "entity_id": "place", "name": "morale", "operator": "lt", "value": 1},
    {"kind": "metric", "entity_id": "place", "name": "coin", "operator": "lt", "value": 1},
    {"kind": "parameter", "entity_id": "place", "name": "morale", "operator": "lt", "value": 1},
])
def test_causal_event_condition_requires_matching_resource_kind(registry: DLCRegistry, condition):
    registry.create(manifest("broken"))
    content = valid_content()
    content["causal_event_rules"] = [causal_rule(conditions=[condition])]
    registry.save_content("broken", content)
    assert "causal_event.invalid_resource_kind" in {
        issue.code for issue in registry.validate("broken")
    }


def test_causal_event_reserved_payload_is_rejected(registry: DLCRegistry):
    registry.create(manifest("broken"))
    content = valid_content()
    content["causal_event_rules"] = [causal_rule(initial_payload={"causal_depth": 99})]
    registry.save_content("broken", content)
    issues = registry.validate("broken")
    assert any(
        issue.code == "causal_event.invalid_rule" and "causal_payload_reserved" in issue.message
        for issue in issues
    )



def test_initial_and_random_events_cannot_forge_causal_metadata(registry: DLCRegistry):
    registry.create(manifest("forged-causality"))
    content = valid_content()
    content["initial_events"][0]["initial_payload"] = {"causal_depth": 1}
    content["random_events"] = [{
        "template_id": "storm", "event_type": "storm", "target_agents": ["agent"],
        "initial_payload": {"causal_chain_id": "cause_deadbeefdeadbeef"},
        "severity_min": 1, "severity_max": 2,
        "min_interval_days": 1, "max_interval_days": 2,
        "probability": 1, "max_occurrences": 1, "enabled": True,
    }]
    registry.save_content("forged-causality", content)
    codes = {issue.code for issue in registry.validate("forged-causality")}
    assert "event.reserved_payload_field" in codes
    assert "random_event.reserved_payload_field" in codes


def test_causal_world_effect_references_and_asset_kinds_are_validated(registry: DLCRegistry):
    registry.create(manifest("effect-errors"))
    content = valid_content()
    content["causal_event_rules"] = [causal_rule(world_effects=[
        {
            "effect_type": "transfer_anchored_value",
            "from_entity_id": "missing-place", "from_resource": "morale",
            "to_entity_id": "place", "to_resource": "missing-resource",
            "base_value": 5,
        },
        {
            "effect_type": "create_ongoing_event", "entity_id": "place",
            "event_type": "work", "duration_days": 1,
            "expected_result": {"inventory_deltas": [{"resource": "morale", "delta": 1}]},
        },
    ])]
    registry.save_content("effect-errors", content)

    codes = {issue.code for issue in registry.validate("effect-errors")}
    assert "causal_event.effect_missing_entity" in codes
    assert "causal_event.effect_missing_resource" in codes
    assert "causal_event.effect_invalid_resource_kind" in codes


def test_causal_authoring_templates_validate_expanded_references(registry: DLCRegistry):
    registry.create(manifest("templated"))
    content = valid_content()
    content["causal_condition_groups"] = [{
        "group_id": "has_coin",
        "conditions": [{
            "kind": "anchored_inventory", "entity_id": "place", "name": "coin",
            "operator": "gte", "value": 2,
        }],
    }]
    content["causal_rule_templates"] = [{
        "template_id": "delayed", "rule": {"delay_days": 2, "probability": 1},
    }]
    content["causal_event_rules"] = [{
        "rule_id": "templated_rule", "template_id": "delayed",
        "condition_group_ids": ["has_coin"], "source_event_types": ["seed"],
        "child_event_type": "follow_up", "target_agents": ["agent"],
    }]
    registry.save_content("templated", content)
    assert registry.validate("templated") == []

    content["causal_event_rules"][0]["template_id"] = "missing"
    registry.save_content("templated", content)
    issues = registry.validate("templated")
    issue = next(item for item in issues if item.code == "causal_authoring.invalid")
    assert issue.message == "causal_rule_template_missing"
    assert issue.path == "causal_event_rules[0].template_id"


def test_explicit_gameplay_player_contract_validates_theme_neutral_references(registry: DLCRegistry):
    registry.create(manifest("orbital_station"))
    content = valid_content(entity_id="orbital_station", agent_id="operations_ai")
    content["agents"][0]["role"] = "autonomous"
    content["gameplay"] = {
        "player": {
            "identity_id": "station_commander",
            "display_name": "Station Commander",
            "actor_id": "commander_actor",
            "controlled_objects": [
                {"kind": "entity", "id": "orbital_station", "mode": "direct"},
                {"kind": "agent", "id": "operations_ai", "mode": "command"},
            ],
        }
    }
    registry.save_content("orbital_station", content)

    assert not registry.validate("orbital_station")
    loaded = registry.load_content("orbital_station")
    assert loaded["gameplay"]["player"]["identity_id"] == "station_commander"


def test_gameplay_contract_reports_invalid_modes_duplicates_and_references(registry: DLCRegistry):
    registry.create(manifest("broken_gameplay"))
    content = valid_content()
    content["gameplay"] = {
        "player": {
            "identity_id": "operator",
            "display_name": "Operator",
            "controlled_objects": [
                {"kind": "agent", "id": "missing", "mode": "pilot"},
                {"kind": "agent", "id": "missing", "mode": "direct"},
                {"kind": "entity", "id": "missing_place", "mode": "direct"},
            ],
        }
    }
    registry.save_content("broken_gameplay", content)

    codes = {issue.code for issue in registry.validate("broken_gameplay")}
    assert "gameplay.control.invalid_mode" in codes
    assert "gameplay.control.duplicate" in codes
    assert "gameplay.control.missing_agent" in codes
    assert "gameplay.control.missing_entity" in codes



def test_action_validation_reports_cross_reference_and_permission_errors(registry: DLCRegistry):
    registry.create(manifest("broken_actions"))
    content = valid_content()
    content["gameplay"] = {
        "player": {
            "identity_id": "operator",
            "actor_id": "player_actor",
            "controlled_objects": [
                {"kind": "entity", "id": "place", "mode": "direct"}
            ],
        }
    }
    content["actions"] = [
        {
            "action_id": "broken_action",
            "display_name": "Broken Action",
            "target": {
                "kind": "entity",
                "required": True,
                "allowed_ids": ["missing_target"],
            },
            "permissions": {
                "control_modes": ["direct"],
                "control_kinds": ["entity"],
                "control_ids": ["missing_control"],
                "actor_ids": ["wrong_actor"],
            },
            "preconditions": [
                {
                    "kind": "metric",
                    "entity_id": "missing_entity",
                    "name": "coin",
                    "operator": "gte",
                    "value": 1,
                }
            ],
            "costs": [
                {
                    "entity_id": "missing_entity",
                    "resource_name": "morale",
                    "base_value": 1,
                }
            ],
            "duration": {"action_type": "broken_action"},
            "result": {
                "mode": "wake_agent",
                "agent_id": "missing_agent",
                "payload": {},
            },
        }
    ]
    registry.save_content("broken_actions", content)

    codes = {issue.code for issue in registry.validate("broken_actions")}
    assert {
        "action.target.unknown_id",
        "action.permissions.unknown_control",
        "action.permissions.unknown_actor",
        "action.precondition.resource_kind",
        "action.precondition.unknown_entity",
        "action.cost.resource_kind",
        "action.cost.unknown_entity",
        "action.result.unknown_agent",
    } <= codes



def _generic_causal_reference_content() -> dict:
    content = valid_content()
    content["entities"].extend([
        {"id": "fort", "type": "location", "name": "Fort"},
        {"id": "council", "type": "organization", "name": "Council"},
    ])
    content["actors"] = [{
        "actor_id": "mayor",
        "display_name": "Mayor",
        "actor_type": "person",
        "entity_id": "place",
        "attributes": {"influence": 75},
        "states": {"mood": "focused"},
        "traits": [{"trait_id": "careful", "display_name": "Careful"}],
        "memberships": [{"organization_id": "council", "role": "leader"}],
    }]
    content["relationship_types"] = [{
        "relationship_type_id": "allied",
        "display_name": "Allied",
        "direction": "undirected",
        "minimum_strength": 0,
        "maximum_strength": 100,
        "default_strength": 50,
        "default_visibility": "public",
    }]
    content["objectives"] = [{
        "objective_id": "secure_frontier",
        "display_name": "Secure Frontier",
        "completion": {"kind": "sim_day", "operator": "gte", "value": 30},
    }]
    content["causal_event_rules"] = [{
        "rule_id": "generic_references",
        "source_event_types": ["seed"],
        "child_event_type": "follow_up",
        "target_agents": ["agent"],
        "conditions": [
            {"kind": "actor_attribute", "entity_id": "mayor", "name": "influence", "operator": "gte", "value": 50},
            {"kind": "actor_state", "entity_id": "mayor", "name": "mood", "operator": "eq", "value": "focused"},
            {"kind": "actor_trait", "entity_id": "mayor", "name": "careful", "operator": "eq", "value": True},
            {"kind": "actor_membership", "entity_id": "mayor", "name": "council", "field": "role", "operator": "eq", "value": "leader"},
            {"kind": "relationship", "entity_id": "place", "target_entity_id": "fort", "name": "allied", "field": "strength", "operator": "gte", "value": 20},
            {"kind": "objective_progress", "entity_id": "secure_frontier", "field": "progress", "operator": "gte", "value": 0},
            {"kind": "aggregate_metric", "field": "anchored_inventory", "name": "coin", "aggregate": "sum", "operator": "gte", "value": 1},
            {"kind": "aggregate_metric", "field": "actor_attribute", "entity_id": "mayor", "name": "influence", "aggregate": "avg", "operator": "gte", "value": 50},
            {"kind": "aggregate_metric", "field": "relation", "name": "allied", "aggregate": "count", "operator": "gte", "value": 0},
        ],
    }]
    return content


def test_generic_causal_condition_cross_references_accept_valid_actor_relationship_objective_and_aggregate(registry: DLCRegistry):
    registry.create(manifest("generic-valid"))
    registry.save_content("generic-valid", _generic_causal_reference_content())

    assert registry.validate("generic-valid") == []


def test_generic_causal_condition_cross_references_report_missing_components_and_invalid_aggregate_kind(registry: DLCRegistry):
    registry.create(manifest("generic-broken"))
    content = _generic_causal_reference_content()
    conditions = content["causal_event_rules"][0]["conditions"]
    conditions.extend([
        {"kind": "actor_attribute", "entity_id": "missing_actor", "name": "influence", "operator": "gte", "value": 1},
        {"kind": "actor_state", "entity_id": "mayor", "name": "missing_state", "operator": "eq", "value": "ready"},
        {"kind": "relationship", "entity_id": "missing_place", "target_entity_id": "fort", "name": "missing_relation", "operator": "exists"},
        {"kind": "objective_progress", "entity_id": "missing_objective", "operator": "gte", "value": 0},
        {"kind": "aggregate_metric", "field": "metric", "name": "coin", "aggregate": "sum", "operator": "gte", "value": 1},
    ])
    registry.save_content("generic-broken", content)

    codes = {issue.code for issue in registry.validate("generic-broken")}

    assert {
        "causal_event.missing_actor",
        "causal_event.missing_actor_component",
        "causal_event.missing_entity",
        "causal_event.missing_relationship_type",
        "causal_event.missing_objective",
        "causal_event.invalid_resource_kind",
    } <= codes


def test_generic_causal_effect_cross_references_and_rule_local_entities(registry: DLCRegistry):
    registry.create(manifest("generic-effects"))
    content = valid_content()
    content["relationship_types"] = [
        {"relationship_type_id": "route", "display_name": "Route", "direction": "directed"},
    ]
    content["causal_event_rules"] = [{
        "rule_id": "spawn_then_change",
        "source_event_types": ["seed"],
        "child_event_type": "changed",
        "world_effects": [
            {
                "effect_type": "create_entity", "entity_id": "new_place",
                "entity_type": "location", "name": "New Place", "resources": {"coin": 5},
            },
            {"effect_type": "move_entity", "entity_id": "new_place", "region": "north"},
            {"effect_type": "modify_relationship", "parent_id": "place", "child_id": "new_place", "rel_type": "route"},
            {"effect_type": "reveal_information", "entity_id": "new_place", "knowledge_id": "known"},
        ],
    }]
    registry.save_content("generic-effects", content)

    codes = {issue.code for issue in registry.validate("generic-effects")}

    assert "causal_event.effect_missing_entity" not in codes
    assert "causal_event.effect_missing_resource" not in codes
    assert "causal_event.effect_missing_relationship_type" not in codes


def test_generic_causal_effect_registry_reports_all_reference_kinds(registry: DLCRegistry):
    registry.create(manifest("generic-effect-errors"))
    content = valid_content()
    content["causal_event_rules"] = [{
        "rule_id": "broken_effects",
        "source_event_types": ["seed"],
        "child_event_type": "broken",
        "world_effects": [
            {"effect_type": "create_entity", "entity_id": "place", "entity_type": "location", "name": "Duplicate"},
            {"effect_type": "create_entity", "entity_id": "twice", "entity_type": "location", "name": "First"},
            {"effect_type": "create_entity", "entity_id": "twice", "entity_type": "location", "name": "Second", "resources": {"missing": 1}},
            {"effect_type": "move_entity", "entity_id": "missing_entity", "region": "north"},
            {"effect_type": "enable_agent", "agent_id": "missing_agent", "actor_id": "missing_actor"},
            {"effect_type": "modify_relationship", "parent_id": "place", "child_id": "missing_entity", "rel_type": "missing_relation"},
            {"effect_type": "modify_membership", "actor_id": "missing_actor", "organization_id": "missing_org"},
            {"effect_type": "create_issue", "issue_type_id": "missing_issue"},
            {"effect_type": "reveal_information", "entity_id": "missing_entity", "knowledge_id": "secret"},
            {"effect_type": "advance_objective", "objective_id": "missing_objective", "amount": 1},
            {"effect_type": "unlock_action", "action_id": "missing_action", "scope_id": "missing_scope"},
        ],
    }]
    registry.save_content("generic-effect-errors", content)

    codes = {issue.code for issue in registry.validate("generic-effect-errors")}

    assert {
        "causal_event.effect_duplicate_entity",
        "causal_event.effect_missing_resource",
        "causal_event.effect_missing_entity",
        "causal_event.effect_missing_agent",
        "causal_event.effect_missing_actor",
        "causal_event.effect_missing_relationship_type",
        "causal_event.effect_missing_issue_type",
        "causal_event.effect_missing_objective",
        "causal_event.effect_missing_action",
        "causal_event.effect_missing_scope",
    } <= codes

def test_knowledge_conditions_and_effects_validate_theme_neutral_references(registry: DLCRegistry):
    registry.create(manifest("knowledge-valid"))
    content = valid_content()
    content["causal_event_rules"] = [causal_rule(
        conditions=[
            {"kind": "world_truth", "entity_id": "place", "name": "route", "operator": "exists"},
            {
                "kind": "actor_belief", "entity_id": "place", "name": "route",
                "target_entity_id": "agent", "operator": "not_exists",
            },
            {
                "kind": "knowledge_confidence", "entity_id": "place", "name": "route",
                "target_entity_id": "agent", "filters": {"layer": "actor_belief"},
                "operator": "gte", "value": 0.5,
            },
        ],
        world_effects=[
            {"effect_type": "declare_world_truth", "entity_id": "place", "knowledge_id": "route", "value": "north"},
            {"effect_type": "set_actor_belief", "holder_id": "agent", "entity_id": "place", "knowledge_id": "route", "value": "east"},
            {
                "effect_type": "propagate_knowledge", "source_layer": "world_truth",
                "target_layer": "player_knowledge", "target_holder_id": "player",
                "entity_id": "place", "knowledge_id": "route",
            },
            {
                "effect_type": "start_investigation", "investigation_id": "inspect-route",
                "entity_id": "place", "knowledge_id": "route",
                "investigator_layer": "actor_belief", "investigator_id": "agent",
                "result_policy": {
                    "source_layer": "world_truth", "target_layer": "actor_belief",
                    "target_holder_id": "agent",
                },
            },
            {"effect_type": "advance_investigation", "investigation_id": "inspect-route", "amount": 1},
        ],
    )]
    registry.save_content("knowledge-valid", content)

    assert not registry.validate("knowledge-valid")


def test_knowledge_registry_reports_unknown_actor_holders_without_treating_belief_as_component(registry: DLCRegistry):
    registry.create(manifest("knowledge-invalid"))
    content = valid_content()
    content["causal_event_rules"] = [causal_rule(
        conditions=[
            {
                "kind": "actor_belief", "entity_id": "place", "name": "route",
                "target_entity_id": "missing-holder", "operator": "exists",
            },
        ],
        world_effects=[
            {"effect_type": "set_actor_belief", "holder_id": "missing-holder", "entity_id": "place", "knowledge_id": "route", "value": "east"},
            {
                "effect_type": "propagate_knowledge", "source_layer": "actor_belief",
                "source_holder_id": "missing-holder", "target_layer": "player_knowledge",
                "target_holder_id": "player", "entity_id": "place", "knowledge_id": "route",
            },
        ],
    )]
    registry.save_content("knowledge-invalid", content)

    codes = {issue.code for issue in registry.validate("knowledge-invalid")}
    assert "causal_event.missing_knowledge_holder" in codes
    assert "causal_event.effect_missing_knowledge_holder" in codes
    assert "causal_event.missing_actor_component" not in codes



def test_derived_metrics_validate_objective_and_causal_references(registry: DLCRegistry):
    registry.create(manifest("derived-valid"))
    content = valid_content()
    content["derived_metrics"] = [derived_metric()]
    content["objectives"] = [{
        "objective_id": "ready",
        "display_name": "Ready",
        "completion": {"kind": "derived_metric", "name": "readiness", "operator": "gte", "value": 1},
    }]
    content["causal_event_rules"] = [causal_rule(conditions=[
        {"kind": "derived_metric", "name": "readiness", "operator": "gte", "value": 1},
    ])]

    registry.save_content("derived-valid", content)

    assert registry.validate("derived-valid") == []


def test_derived_metric_registry_reports_contract_and_cross_reference_errors(registry: DLCRegistry):
    registry.create(manifest("derived-broken"))
    content = valid_content()
    content["derived_metrics"] = [
        derived_metric("first", formula={"op": "derived_metric", "metric_id": "second"}),
        derived_metric("second", formula={"op": "derived_metric", "metric_id": "first"}),
    ]
    content["objectives"] = [{
        "objective_id": "ready",
        "display_name": "Ready",
        "completion": {"kind": "derived_metric", "name": "missing_metric", "operator": "gte", "value": 1},
    }]
    content["causal_event_rules"] = [causal_rule(conditions=[
        {"kind": "derived_metric", "name": "missing_metric", "operator": "gte", "value": 1},
    ])]

    registry.save_content("derived-broken", content)
    codes = {issue.code for issue in registry.validate("derived-broken")}

    assert "derived_metric.cycle" in codes
    assert "objective.condition.unknown_derived_metric" in codes
    assert "causal_event.missing_derived_metric" in codes


def test_derived_metric_registry_reports_missing_world_references(registry: DLCRegistry):
    registry.create(manifest("derived-refs"))
    content = valid_content()
    content["derived_metrics"] = [
        derived_metric("missing_entity", formula={
            "op": "source", "source": "metric", "entity_id": "nowhere", "name": "morale",
        }),
        derived_metric("missing_resource", formula={
            "op": "source", "source": "metric", "entity_id": "place", "name": "unknown",
        }),
        derived_metric("wrong_kind", formula={
            "op": "source", "source": "parameter", "entity_id": "place", "name": "morale",
        }),
    ]

    registry.save_content("derived-refs", content)
    codes = {issue.code for issue in registry.validate("derived-refs")}

    assert "derived_metric.missing_entity" in codes
    assert "derived_metric.missing_resource" in codes
    assert "derived_metric.invalid_resource_kind" in codes


def test_rhythm_document_is_normalized_and_validated(registry: DLCRegistry):
    registry.create(manifest("rhythm"))
    content = valid_content()
    content["issue_types"] = [{
        "issue_type_id": "market_report",
        "display_name": "Market report",
        "default_urgency": "normal",
        "source": {"allowed_kinds": ["system"]},
    }]
    content["rhythm"] = {
        "enabled": True,
        "max_active_issues": 2,
        "attention_budget": 6,
        "rules": [{
            "rule_id": "market_pressure",
            "issue_type_ids": ["market_report"],
            "event_types": ["market_shift"],
            "priority": 5,
            "max_active": 1,
            "deescalation": {
                "enabled": True,
                "target_level": 0,
                "conditions": [{
                    "kind": "anchored_inventory",
                    "entity_id": "place",
                    "name": "coin",
                    "operator": "gte",
                    "value": 1,
                }],
            },
        }],
    }
    registry.save_content("rhythm", content)

    loaded = registry.load_content("rhythm")
    assert loaded["rhythm"]["rules"][0]["rule_id"] == "market_pressure"
    assert registry.validate("rhythm") == []


def test_rhythm_validation_reports_issue_and_condition_reference_errors(registry: DLCRegistry):
    registry.create(manifest("broken-rhythm"))
    content = valid_content()
    content["rhythm"] = {
        "enabled": True,
        "rules": [{
            "rule_id": "broken",
            "issue_type_ids": ["missing_issue"],
            "deescalation": {
                "enabled": True,
                "conditions": [{
                    "kind": "anchored_inventory",
                    "entity_id": "missing_entity",
                    "name": "missing_resource",
                    "operator": "gte",
                    "value": 1,
                }],
            },
            "climax": {
                "enabled": True,
                "level": 2,
                "conditions": [{
                    "kind": "derived_metric",
                    "name": "missing_metric",
                    "operator": "gte",
                    "value": 1,
                }],
            },
        }],
    }
    registry.save_content("broken-rhythm", content)

    codes = {issue.code for issue in registry.validate("broken-rhythm")}
    assert {
        "rhythm.missing_issue_type",
        "rhythm.missing_entity",
        "rhythm.missing_resource",
        "rhythm.missing_derived_metric",
    } <= codes

def test_outcome_feedback_document_is_normalized_and_validated(registry: DLCRegistry):
    registry.create(manifest("outcome-feedback"))
    content = valid_content()
    content["actions"] = [{"action_id": "inspect", "display_name": "Inspect"}]
    content["issue_types"] = [{
        "issue_type_id": "followup",
        "display_name": "Follow-up",
        "default_urgency": "normal",
        "source": {"allowed_kinds": ["system"]},
    }]
    content["derived_metrics"] = [derived_metric()]
    content["outcome_feedback"] = outcome_feedback_content()
    registry.save_content("outcome-feedback", content)

    loaded = registry.load_content("outcome-feedback")
    rule = loaded["outcome_feedback"]["rules"][0]
    assert rule["action_ids"] == ["inspect"]
    assert rule["issue_type_ids"] == ["followup"]
    assert rule["metric_ids"] == ["readiness"]
    assert rule["conditions"][1]["kind"] == "derived_metric"
    assert rule["reveal_text"] == "The earlier inspection enabled this outcome."
    assert registry.validate("outcome-feedback") == []


def test_outcome_feedback_validation_reports_missing_static_references(registry: DLCRegistry):
    registry.create(manifest("broken-outcome-feedback"))
    content = valid_content()
    feedback = outcome_feedback_content()
    rule = feedback["rules"][0]
    rule["action_ids"] = ["missing_action"]
    rule["issue_type_ids"] = ["missing_issue"]
    rule["create_issue_type_id"] = "missing_followup"
    rule["metric_ids"] = ["missing_metric"]
    rule["conditions"] = [
        {
            "kind": "metric",
            "entity_id": "missing_entity",
            "name": "missing_resource",
            "operator": "gte",
            "value": 1,
        },
        {
            "kind": "actor_attribute",
            "entity_id": "missing_actor",
            "name": "influence",
            "operator": "gte",
            "value": 1,
        },
        {
            "kind": "objective_progress",
            "entity_id": "missing_objective",
            "operator": "gte",
            "value": 1,
        },
        {
            "kind": "derived_metric",
            "name": "missing_condition_metric",
            "operator": "gte",
            "value": 1,
        },
    ]
    content["outcome_feedback"] = feedback
    registry.save_content("broken-outcome-feedback", content)

    codes = {issue.code for issue in registry.validate("broken-outcome-feedback")}
    assert {
        "outcome_feedback.missing_action",
        "outcome_feedback.missing_issue_type",
        "outcome_feedback.missing_derived_metric",
        "outcome_feedback.missing_entity",
        "outcome_feedback.missing_resource",
        "outcome_feedback.missing_actor",
        "outcome_feedback.missing_objective",
    } <= codes
