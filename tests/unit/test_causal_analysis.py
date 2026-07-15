from __future__ import annotations

from multiagent_framework.causal_analysis import (
    CAUSAL_REPLAY_FIXTURE_FORMAT,
    analyze_causal_rules,
    dry_run_causal_rules,
    replay_causal_fixture,
)
from multiagent_framework.causal_events import CausalEventError


def _content() -> dict:
    return {
        "schema_version": "1.0",
        "resources": [
            {"name": "grain", "kind": "asset", "anchor_rate": 2},
            {"name": "stability", "kind": "metric"},
        ],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "grain", "quantity": 40}],
        "entity_values": [{"entity_id": "city", "name": "stability", "kind": "metric", "value": 3}],
        "agents": [{"id": "governor", "name": "Governor"}],
        "initial_events": [{"event_id": "seed", "event_type": "shortage", "severity": 4, "target_agents": ["governor"]}],
        "random_events": [],
        "ongoing_events": [],
        "relations": [],
        "time_rules": [],
        "personas": {},
        "prompts": {},
        "causal_event_rules": [
            {
                "rule_id": "shortage_to_unrest",
                "source_event_types": ["shortage"],
                "child_event_type": "unrest",
                "delay_days": 2,
                "probability": 1,
                "severity_delta": 2,
                "conditions": [{
                    "kind": "anchored_inventory", "entity_id": "city", "name": "grain",
                    "operator": "lt", "value": 100,
                }],
                "inherit_payload_keys": ["region"],
                "max_depth": 4,
                "max_fires_per_chain": 1,
            },
            {
                "rule_id": "unrest_to_crisis",
                "source_event_types": ["unrest"],
                "child_event_type": "crisis",
                "delay_days": 1,
                "probability": 1,
                "conditions": [{
                    "kind": "metric", "entity_id": "city", "name": "stability",
                    "operator": "lte", "value": 3,
                }],
                "max_depth": 4,
                "max_fires_per_chain": 1,
            },
        ],
    }


def test_analysis_builds_stable_graph_and_origin_metadata():
    first = analyze_causal_rules(_content())
    second = analyze_causal_rules(_content())

    assert first == second
    assert first["summary"] == {
        "rule_count": 2,
        "enabled_count": 2,
        "event_type_count": 3,
        "edge_count": 2,
        "cycle_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "info_count": 0,
        "world_effect_count": 0,
        "anchored_transfer_count": 0,
        "ongoing_event_effect_count": 0,
    }
    assert [node["event_type"] for node in first["nodes"]] == ["crisis", "shortage", "unrest"]
    shortage = next(node for node in first["nodes"] if node["event_type"] == "shortage")
    assert shortage["origins"] == ["initial"]


def test_analysis_reports_duplicate_semantics_cycles_and_external_sources():
    content = _content()
    content["initial_events"] = []
    content["causal_event_rules"].extend([
        {**content["causal_event_rules"][0], "rule_id": "duplicate"},
        {
            "rule_id": "crisis_to_shortage", "source_event_types": ["crisis"],
            "child_event_type": "shortage", "delay_days": 0, "probability": 1,
        },
        {
            "rule_id": "wildcard", "source_event_types": ["*"],
            "child_event_type": "audit", "delay_days": 0, "probability": 1,
        },
    ])
    report = analyze_causal_rules(content)
    codes = [item["code"] for item in report["issues"]]

    assert "causal_analysis.duplicate_semantics" in codes
    assert "causal_analysis.cycle" in codes
    assert "causal_analysis.immediate_wildcard_loop" in codes


def test_dry_run_uses_anchored_seed_facts_and_is_deterministic():
    request = {
        "event_id": "manual_shortage",
        "event_type": "shortage",
        "severity": 4,
        "target_agents": ["governor"],
        "initial_payload": {"region": "north"},
    }
    first = dry_run_causal_rules(_content(), request, horizon_days=10, max_events=16)
    second = dry_run_causal_rules(_content(), request, horizon_days=10, max_events=16)

    assert first == second
    assert [node["event_type"] for node in first["nodes"]] == ["shortage", "unrest", "crisis"]
    assert [node["day"] for node in first["nodes"]] == [0, 2, 3]
    assert first["nodes"][1]["severity"] == 6
    assert first["nodes"][1]["initial_payload"]["region"] == "north"
    assert first["summary"]["generated_count"] == 2
    assert first["summary"]["truncated"] is False


def test_dry_run_respects_horizon_and_failed_conditions():
    short = dry_run_causal_rules(
        _content(), {"event_type": "shortage", "severity": 5}, horizon_days=1,
    )
    assert [node["event_type"] for node in short["nodes"]] == ["shortage"]
    assert short["pending"][0]["within_horizon"] is False

    content = _content()
    content["inventory"][0]["quantity"] = 60  # base value 120, so the <100 condition fails
    failed = dry_run_causal_rules(content, {"event_type": "shortage", "severity": 5})
    assert failed["summary"]["generated_count"] == 0


def test_dry_run_rejects_spoofed_causal_metadata_and_invalid_limits():
    content = _content()
    try:
        dry_run_causal_rules(content, {
            "event_type": "shortage",
            "initial_payload": {"causal_chain_id": "cause_fake"},
        })
    except CausalEventError as exc:
        assert str(exc) == "causal_dry_run_source_invalid"
    else:
        raise AssertionError("expected spoofed metadata rejection")

    try:
        dry_run_causal_rules(content, {"event_type": "shortage"}, max_events=257)
    except CausalEventError as exc:
        assert str(exc) == "causal_dry_run_max_events_invalid"
    else:
        raise AssertionError("expected max-event rejection")



def test_analysis_reports_draft_reference_errors_before_save():
    content = _content()
    rule = content["causal_event_rules"][0]
    rule["target_agents"] = ["missing_agent"]
    rule["conditions"] = [
        {"kind": "anchored_inventory", "entity_id": "missing_city", "name": "stability", "operator": "lt", "value": 1},
        {"kind": "metric", "entity_id": "city", "name": "missing_metric", "operator": "gt", "value": 0},
    ]
    report = analyze_causal_rules(content)
    codes = {item["code"] for item in report["issues"]}
    assert report["summary"]["error_count"] == 4
    assert codes == {
        "causal_analysis.missing_agent",
        "causal_analysis.missing_entity",
        "causal_analysis.missing_resource",
        "causal_analysis.resource_kind",
    }


def test_dry_run_applies_world_effects_before_downstream_conditions():
    content = _content()
    content["causal_event_rules"][0]["world_effects"] = [{
        "effect_type": "set_resource",
        "entity_id": "city",
        "resource_name": "stability",
        "quantity": 10,
    }]

    report = dry_run_causal_rules(
        content, {"event_type": "shortage", "severity": 5}, horizon_days=10,
    )

    assert [node["event_type"] for node in report["nodes"]] == ["shortage", "unrest"]
    unrest = report["nodes"][1]
    assert unrest["world_effect_status"] == "applied"
    assert unrest["world_command_count"] == 1
    assert unrest["world_commands"] == [{
        "command_type": "set_resource",
        "entity_id": "city",
        "resource_name": "stability",
        "quantity": 10.0,
    }]
    assert report["analysis"]["summary"]["world_effect_count"] == 1


def test_dry_run_rejected_effect_stops_downstream_chain_atomically():
    content = _content()
    content["resources"][0]["min_quantity"] = 0
    content["causal_event_rules"][0]["world_effects"] = [{
        "effect_type": "adjust_resource",
        "entity_id": "city",
        "resource_name": "grain",
        "quantity_delta": -100,
    }]

    report = dry_run_causal_rules(
        content, {"event_type": "shortage", "severity": 5}, horizon_days=10,
    )

    assert [node["event_type"] for node in report["nodes"]] == ["shortage", "unrest"]
    assert report["nodes"][1]["world_effect_status"] == "rejected"
    assert report["nodes"][1]["world_effect_error_code"] == "insufficient_balance"
    assert "crisis" not in [node["event_type"] for node in report["nodes"]]


def test_analysis_and_dry_run_expand_reusable_templates_and_condition_groups():
    content = _content()
    content["causal_condition_groups"] = [{
        "group_id": "grain_low",
        "conditions": [{
            "kind": "anchored_inventory", "entity_id": "city", "name": "grain",
            "operator": "lt", "value": 100,
        }],
    }]
    content["causal_rule_templates"] = [{
        "template_id": "delayed", "rule": {"delay_days": 2, "probability": 1, "max_depth": 4},
    }]
    content["causal_event_rules"] = [{
        "rule_id": "shortage_to_unrest", "template_id": "delayed",
        "condition_group_ids": ["grain_low"], "source_event_types": ["shortage"],
        "child_event_type": "unrest", "target_agents": ["governor"],
    }]

    analysis = analyze_causal_rules(content)
    assert analysis["edges"][0]["delay_days"] == 2
    assert analysis["edges"][0]["condition_count"] == 1
    report = dry_run_causal_rules(
        content,
        {"event_id": "source", "event_type": "shortage", "severity": 4, "target_agents": ["governor"]},
        horizon_days=10,
    )
    assert [item["event_type"] for item in report["nodes"]] == ["shortage", "unrest"]
    assert report["nodes"][1]["day"] == 2


def test_analysis_edges_include_authoring_provenance_and_node_routes():
    content = _content()
    content["causal_condition_groups"] = [{
        "group_id": "low_reserve",
        "conditions": [{
            "kind": "anchored_inventory", "entity_id": "city", "name": "grain",
            "operator": "lt", "value": 100,
        }],
    }]
    content["causal_rule_templates"] = [{
        "template_id": "delayed",
        "rule": {"delay_days": 2, "probability": 1},
    }]
    content["causal_event_rules"][0].update({
        "template_id": "delayed",
        "condition_group_ids": ["low_reserve"],
        "conditions": [],
    })

    report = analyze_causal_rules(content)
    edge = next(item for item in report["edges"] if item["rule_id"] == "shortage_to_unrest")
    assert edge["template_id"] == "delayed"
    assert edge["condition_group_ids"] == ["low_reserve"]
    assert edge["inline_condition_count"] == 0
    assert len(edge["edge_id"]) == 20
    shortage = next(item for item in report["nodes"] if item["event_type"] == "shortage")
    assert shortage["outgoing_rules"] == ["shortage_to_unrest"]


def test_causal_replay_fixture_matches_and_reports_rule_drift():
    report = dry_run_causal_rules(
        _content(),
        {"event_id": "fixture_seed", "event_type": "shortage", "severity": 4},
        horizon_days=10,
        max_events=16,
    )
    fixture = report["replay_fixture"]

    assert fixture["format"] == CAUSAL_REPLAY_FIXTURE_FORMAT
    assert fixture["input"]["source_event"]["event_type"] == "shortage"
    assert len(fixture["expected"]["result_digest"]) == 64
    same = replay_causal_fixture(_content(), fixture)
    assert same["comparison"]["matches"] is True
    assert same["comparison"]["drift_fields"] == []

    changed = _content()
    changed["causal_event_rules"][0]["delay_days"] = 3
    drift = replay_causal_fixture(changed, fixture)
    assert drift["comparison"]["matches"] is False
    assert drift["comparison"]["analysis_matches"] is False
    assert "analysis_fingerprint" in drift["comparison"]["drift_fields"]
    assert "result_digest" in drift["comparison"]["drift_fields"]


def test_causal_replay_detects_seed_fact_drift_without_rule_drift():
    report = dry_run_causal_rules(
        _content(), {"event_type": "shortage", "severity": 4}, horizon_days=10,
    )
    changed = _content()
    changed["inventory"][0]["quantity"] = 100

    replay = replay_causal_fixture(changed, report["replay_fixture"])
    assert replay["comparison"]["matches"] is False
    assert replay["comparison"]["analysis_matches"] is True
    assert replay["comparison"]["result_matches"] is False
    assert "summary.event_count" in replay["comparison"]["drift_fields"]


def test_causal_replay_rejects_invalid_fixture_digest():
    report = dry_run_causal_rules(_content(), {"event_type": "shortage"})
    fixture = report["replay_fixture"]
    fixture["expected"]["result_digest"] = "not-a-digest"

    try:
        replay_causal_fixture(_content(), fixture)
    except CausalEventError as exc:
        assert str(exc) == "causal_replay_digest_invalid"
    else:
        raise AssertionError("invalid replay digest should fail")



def test_dry_run_generic_conditions_share_runtime_semantics_and_observe_applied_world_effects():
    content = _content()
    content["entities"] = [
        {
            "id": "city", "type": "location", "name": "City",
            "current_status": "stable", "metadata": {"tags": ["coastal"], "tier": "capital"},
        },
        {"id": "fort", "type": "location", "name": "Fort"},
    ]
    content["actors"] = [{
        "actor_id": "governor",
        "display_name": "Governor",
        "actor_type": "person",
        "entity_id": "city",
        "attributes": {"influence": 75},
        "states": {"mood": "focused"},
    }]
    content["relationship_types"] = [{
        "relationship_type_id": "allied", "display_name": "Allied",
        "direction": "undirected", "minimum_strength": 0, "maximum_strength": 100,
        "default_strength": 50, "default_visibility": "public",
    }]
    content["relations"] = [{
        "parent_id": "fort", "child_id": "city", "rel_type": "allied",
        "strength": 80, "visibility": "public", "starts_at": 0,
    }]
    content["initial_events"] = [{
        "event_id": "known_shortage", "event_type": "shortage",
        "generated_at": 2 * 86400, "severity": 4, "target_agents": ["governor"],
    }]
    content["causal_event_rules"] = [
        {
            "rule_id": "seed_to_response",
            "source_event_types": ["shortage"],
            "child_event_type": "response_started",
            "delay_days": 1,
            "probability": 1,
            "conditions": [
                {"kind": "entity_tag", "entity_id": "city", "name": "coastal", "operator": "eq", "value": True},
                {"kind": "entity_field", "entity_id": "city", "field": "metadata.tier", "operator": "eq", "value": "capital"},
                {"kind": "actor_attribute", "entity_id": "governor", "name": "influence", "operator": "gte", "value": 70},
                {"kind": "relationship", "entity_id": "city", "target_entity_id": "fort", "name": "allied", "field": "strength", "operator": "gte", "value": 70},
                {"kind": "time", "name": "sim_day", "operator": "gte", "value": 2},
                {"kind": "event_history", "name": "shortage", "field": "count", "window_days": 1, "operator": "gte", "value": 1},
            ],
            "world_effects": [
                {"effect_type": "update_status", "entity_id": "city", "new_status": "mobilized"},
                {
                    "effect_type": "create_ongoing_event", "entity_id": "city",
                    "event_type": "relief_operation", "description": "Emergency relief",
                    "duration_days": 5,
                },
            ],
        },
        {
            "rule_id": "response_to_stabilization",
            "source_event_types": ["response_started"],
            "child_event_type": "stabilization",
            "delay_days": 1,
            "probability": 1,
            "conditions": [
                {"kind": "entity_state", "entity_id": "city", "operator": "eq", "value": "mobilized"},
                {"kind": "ongoing_event", "entity_id": "city", "name": "relief_operation", "field": "exists", "operator": "eq", "value": True},
            ],
        },
    ]

    report = dry_run_causal_rules(
        content,
        {
            "event_id": "manual_shortage", "event_type": "shortage",
            "generated_at": 2 * 86400, "severity": 4, "target_agents": ["governor"],
        },
        horizon_days=10,
    )

    assert [node["event_type"] for node in report["nodes"]] == [
        "shortage", "response_started", "stabilization",
    ]
    assert [node["day"] for node in report["nodes"]] == [2, 3, 4]
    assert report["nodes"][1]["world_effect_status"] == "applied"
    assert report["nodes"][2]["world_effect_status"] == "none"



def test_dry_run_projects_generic_effects_before_observing_next_rule():
    content = _content()
    content["actors"] = [{
        "actor_id": "governor", "display_name": "Governor", "actor_type": "person",
        "entity_id": "city", "attributes": {}, "states": {},
    }]
    content["relationship_types"] = [{
        "relationship_type_id": "commands", "display_name": "Commands",
        "direction": "directed", "minimum_strength": 0, "maximum_strength": 100,
        "default_strength": 50, "default_visibility": "public",
    }]
    content["causal_event_rules"] = [
        {
            "rule_id": "seed_to_expansion",
            "source_event_types": ["seed"],
            "child_event_type": "expanded",
            "delay_days": 1,
            "world_effects": [
                {"effect_type": "create_entity", "entity_id": "outpost", "entity_type": "location", "name": "Outpost"},
                {"effect_type": "move_entity", "entity_id": "outpost", "region": "north"},
                {"effect_type": "modify_relationship", "parent_id": "city", "child_id": "outpost", "rel_type": "commands", "strength": 80},
                {"effect_type": "modify_membership", "actor_id": "governor", "organization_id": "city", "role": "leader"},
                {"effect_type": "reveal_information", "entity_id": "outpost", "knowledge_id": "route", "value": "known"},
                {"effect_type": "switch_phase", "phase_id": "expansion", "metadata": {"chapter": 2}},
            ],
        },
        {
            "rule_id": "expansion_to_ready",
            "source_event_types": ["expanded"],
            "child_event_type": "ready",
            "delay_days": 1,
            "conditions": [
                {"kind": "entity_field", "entity_id": "outpost", "field": "metadata.region", "operator": "eq", "value": "north"},
                {"kind": "relationship", "entity_id": "city", "target_entity_id": "outpost", "name": "commands", "field": "strength", "operator": "gte", "value": 80},
                {"kind": "actor_membership", "entity_id": "governor", "name": "city", "field": "role", "operator": "eq", "value": "leader"},
                {"kind": "player_knowledge", "entity_id": "outpost", "name": "route", "operator": "eq", "value": "known"},
                {"kind": "phase", "field": "phase_id", "operator": "eq", "value": "expansion"},
                {"kind": "phase", "field": "metadata.chapter", "operator": "eq", "value": 2},
            ],
        },
    ]

    report = dry_run_causal_rules(
        content, {"event_id": "manual_seed", "event_type": "seed", "severity": 4},
        horizon_days=5,
    )

    assert [node["event_type"] for node in report["nodes"]] == ["seed", "expanded", "ready"]
    assert report["nodes"][1]["world_effect_status"] == "applied"



def test_dry_run_uses_unsaved_draft_derived_metric_definitions():
    content = _content()
    content["derived_metrics"] = [{
        "metric_id": "supply_value",
        "display_name": "Supply value",
        "audiences": ["rule", "objective"],
        "formula": {
            "op": "source", "source": "anchored_inventory",
            "entity_id": "city", "name": "grain",
        },
    }]
    content["causal_event_rules"] = [{
        "rule_id": "draft_metric_rule",
        "source_event_types": ["shortage"],
        "child_event_type": "draft_metric_passed",
        "delay_days": 0,
        "probability": 1,
        "conditions": [{
            "kind": "derived_metric", "name": "supply_value",
            "operator": "gte", "value": 80,
        }],
    }]

    report = dry_run_causal_rules(
        content, {"event_type": "shortage", "severity": 4}, horizon_days=1,
    )

    assert [node["event_type"] for node in report["nodes"]] == [
        "shortage", "draft_metric_passed",
    ]
