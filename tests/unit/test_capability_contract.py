from __future__ import annotations

import pytest

from multiagent_framework.capability_contract import (
    CapabilityCatalog,
    CapabilityContractError,
    CapabilityDefinition,
    validate_capability_definitions,
)


def _capability(**overrides):
    value = {
        "capability_id": "frontier_report",
        "display_name": "Frontier report",
        "description": "Report current frontier conditions.",
        "handler": "message",
        "audiences": ["player", "agent", "router"],
        "aliases": ["report"],
        "target": {"kind": "agent", "mode": "optional", "allowed_ids": ["guard"]},
        "parameters": [
            {
                "name": "severity",
                "type": "integer",
                "display_name": "Severity",
                "required": True,
                "minimum": 1,
                "maximum": 10,
            },
            {
                "name": "verified",
                "type": "boolean",
                "display_name": "Verified",
                "default": False,
            },
            {
                "name": "category",
                "type": "string",
                "display_name": "Category",
                "options": ["security", "supply"],
                "default": "security",
            },
        ],
        "action_id": "",
        "default_urgency": "urgent",
        "input": {
            "content_required": True,
            "content_label": "Report",
            "content_placeholder": "Describe observations",
        },
        "enabled": True,
    }
    value.update(overrides)
    return value


def _error_code(callable_):
    with pytest.raises(CapabilityContractError) as caught:
        callable_()
    return caught.value.code


def test_definition_parses_framework_owned_handlers_and_public_shape():
    definition = CapabilityDefinition.parse(_capability())
    assert definition.capability_id == "frontier_report"
    assert definition.handler == "message"
    assert definition.target.allowed_ids == ("guard",)
    assert definition.parameters[0].minimum == 1
    assert definition.to_dict()["input"] == {
        "content_required": True,
        "content_label": "Report",
        "content_placeholder": "Describe observations",
    }

    assert _error_code(lambda: CapabilityDefinition.parse(_capability(handler="python_callback"))) == "capability.invalid_handler"
    assert _error_code(lambda: CapabilityDefinition.parse(_capability(audiences=[]))) == "capability.audiences_required"
    assert _error_code(lambda: CapabilityDefinition.parse(_capability(audiences=["admin"]))) == "capability.invalid_audiences"


def test_handler_target_and_action_shapes_are_enforced():
    noop = _capability(
        capability_id="frontier_wait",
        handler="noop",
        aliases=[],
        target={"kind": "none", "mode": "none"},
        parameters=[],
        input={"content_required": False, "content_label": "Content"},
    )
    assert CapabilityDefinition.parse(noop).handler == "noop"

    assert _error_code(
        lambda: CapabilityDefinition.parse({**noop, "target": {"kind": "agent", "mode": "optional"}})
    ) == "capability.noop.invalid_shape"
    assert _error_code(
        lambda: CapabilityDefinition.parse(_capability(target={"kind": "entity", "mode": "optional"}))
    ) == "capability.target.agent_required"
    assert _error_code(
        lambda: CapabilityDefinition.parse(_capability(target={"kind": "none", "mode": "optional"}))
    ) == "capability.target.mode_mismatch"

    contract = _capability(
        capability_id="launch_patrol",
        handler="contract_action",
        aliases=[],
        action_id="frontier_patrol",
        target={"kind": "entity", "mode": "required", "allowed_ids": ["station"]},
        input={"content_required": False, "content_label": "Order"},
    )
    assert CapabilityDefinition.parse(contract).action_id == "frontier_patrol"
    assert _error_code(lambda: CapabilityDefinition.parse({**contract, "action_id": ""})) == "capability.action_id_required"
    assert _error_code(lambda: CapabilityDefinition.parse(_capability(action_id="frontier_patrol"))) == "capability.action_id_forbidden"


def test_validation_reports_alias_action_and_target_contract_failures():
    duplicate = _capability(capability_id="second_report", aliases=["frontier_report"])
    contract = _capability(
        capability_id="launch_patrol",
        handler="contract_action",
        aliases=[],
        action_id="missing_action",
        target={"kind": "entity", "mode": "required", "allowed_ids": ["missing_station"]},
        input={"content_required": False, "content_label": "Order"},
    )
    issues = validate_capability_definitions(
        [_capability(), duplicate, contract],
        action_ids={"frontier_patrol"},
        entity_ids={"station"},
        agent_ids={"guard"},
    )
    codes = [issue.code for issue in issues]
    assert "capabilities.identifier_duplicate" in codes
    assert "capability.action_not_found" in codes
    assert "capability.target_not_found" in codes

    catalog = CapabilityCatalog()
    assert _error_code(lambda: catalog.configure([_capability(), duplicate], generation=1)) == "capabilities.identifier_duplicate"


def test_invocation_normalizes_alias_targets_parameters_and_generation():
    catalog = CapabilityCatalog()
    catalog.configure([_capability()], generation=7)
    prepared = catalog.prepare_invocation(
        "report",
        {
            "expected_generation": 7,
            "invocation_id": "turn-7-report",
            "content": "  Watchtower sighted movement.  ",
            "target_id": "guard",
            "target_ids": ["guard", "guard"],
            "parameters": {"severity": 4},
        },
        audience="agent",
        source_id="scout",
    )
    assert prepared == {
        "capability_id": "frontier_report",
        "handler": "message",
        "action_id": "",
        "generation": 7,
        "expected_generation": 7,
        "invocation_id": "turn-7-report",
        "audience": "agent",
        "source_id": "scout",
        "content": "Watchtower sighted movement.",
        "target_id": "guard",
        "target_ids": ["guard"],
        "target_kind": "agent",
        "parameters": {"severity": 4, "verified": False, "category": "security"},
        "urgency": "urgent",
        "original_intent": "",
        "is_distorting": False,
    }

    assert _error_code(
        lambda: catalog.prepare_invocation("frontier_report", {"expected_generation": 6}, audience="agent")
    ) == "capability.generation_conflict"
    assert _error_code(
        lambda: catalog.prepare_invocation("frontier_report", {"content": "x", "parameters": {"severity": 4}}, audience="system")
    ) == "capability.audience_forbidden"
    assert _error_code(
        lambda: catalog.prepare_invocation("missing", {}, audience="agent")
    ) == "capability.not_found"
    assert _error_code(
        lambda: catalog.prepare_invocation("frontier_report", {"content": "x", "target_ids": ["guard", "other"], "parameters": {"severity": 4}}, audience="agent")
    ) == "capability.target.single_required"
    assert _error_code(
        lambda: catalog.prepare_invocation("frontier_report", {"content": "x", "target_id": "other", "parameters": {"severity": 4}}, audience="agent")
    ) == "capability.target.forbidden"
    assert _error_code(
        lambda: catalog.prepare_invocation("frontier_report", {"content": "x", "parameters": {"severity": 0}}, audience="agent")
    ) == "capability.parameter.below_minimum"
    assert _error_code(
        lambda: catalog.prepare_invocation("frontier_report", {"content": "x", "parameters": {"severity": 4, "unknown": True}}, audience="agent")
    ) == "capability.parameter.unknown"


def test_contract_action_passes_parameters_to_action_catalog_and_tool_enum_is_generation_bound():
    contract = _capability(
        capability_id="launch_patrol",
        handler="contract_action",
        aliases=["patrol"],
        action_id="frontier_patrol",
        target={"kind": "entity", "mode": "required", "allowed_ids": ["station"]},
        parameters=[],
        input={"content_required": False, "content_label": "Order"},
    )
    player_only = _capability(
        capability_id="player_note",
        handler="world_action",
        audiences=["player"],
        aliases=[],
        target={"kind": "none", "mode": "none"},
        parameters=[],
        input={"content_required": True, "content_label": "Note"},
    )
    catalog = CapabilityCatalog()
    catalog.configure([contract, player_only], generation=11)

    prepared = catalog.prepare_invocation(
        "patrol",
        {"target_id": "station", "parameters": {"gain": 5, "mode": "safe"}},
        audience="agent",
    )
    assert prepared["action_id"] == "frontier_patrol"
    assert prepared["parameters"] == {"gain": 5, "mode": "safe"}

    agent_tool = catalog.build_tool(audience="agent", name="invoke_capability", description="Invoke")
    player_tool = catalog.build_tool(audience="player", name="invoke_capability", description="Invoke")
    assert agent_tool["function"]["parameters"]["properties"]["capability_id"]["enum"] == ["launch_patrol"]
    assert player_tool["function"]["parameters"]["properties"]["capability_id"]["enum"] == ["launch_patrol", "player_note"]
    assert catalog.public_state(audience="agent")["generation"] == 11
    assert catalog.public_state(audience="agent")["fingerprint"]
