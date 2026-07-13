from __future__ import annotations

import asyncio
import json

import pytest

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.action_causal_bridge import build_action_causal_event
from multiagent_framework.fc_handlers import (
    FCContext,
    handle_consume_resource,
    handle_produce_resource,
    handle_transfer_anchored_value,
)
from multiagent_framework.fc_tools import ACTION_AGENT_TOOLS, ALL_TOOLS
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.types import ActionResult
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.tools_seen: list[list[str]] = []

    async def chat(self, *, messages, tools=None, temperature=0.7, trace_context=None):
        self.tools_seen.append([item["function"]["name"] for item in (tools or [])])
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0)


def make_world(tmp_path) -> WorldState:
    world = WorldState(str(tmp_path / "agent-economy.db"))
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", unit="sack", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=2.0,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="coin", unit="piece", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=0.5,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="morale", unit="point", kind="metric", default_quantity=50,
        min_quantity=0, max_quantity=100, anchor_rate=1.0,
    ))
    world.upsert_entity(Entity(id="warehouse", type="place", name="Warehouse"))
    world.upsert_entity(Entity(id="treasury", type="place", name="Treasury"))
    world.set_inventory(
        "warehouse", "grain", "grain", 50, command_id="seed-grain", reason="seed",
    )
    world.set_inventory(
        "treasury", "coin", "coin", 0, command_id="seed-coin", reason="seed",
    )
    return world


def quantity(world: WorldState, entity_id: str, resource_name: str) -> float:
    return next(
        item.quantity for item in world.get_inventory(entity_id)
        if item.item_name == resource_name
    )


def test_action_agent_uses_typed_economic_tools_and_keeps_legacy_tool_compatible():
    action_names = {item["function"]["name"] for item in ACTION_AGENT_TOOLS}
    all_names = {item["function"]["name"] for item in ALL_TOOLS}
    assert {"produce_resource", "consume_resource", "transfer_anchored_value"} <= action_names
    assert "update_inventory" not in action_names
    assert "update_inventory" in all_names


def test_production_and_consumption_are_anchored_idempotent_and_reject_invalid_assets(tmp_path):
    world = make_world(tmp_path)
    changes: list[dict] = []
    ctx = FCContext(
        world_state=world, caller_id="official", chain_id="chain-economic",
        tool_call_id="produce-1", sim_time=10, changes_made=changes,
    )
    args = {
        "entity_id": "warehouse", "resource_name": "grain",
        "quantity": 5, "reason": "harvest",
    }
    first = handle_produce_resource(ctx, args)
    replay = handle_produce_resource(ctx, args)

    assert first["ok"] and first["base_value_delta"] == 10
    assert replay["ok"] and replay["replayed"] is True
    assert quantity(world, "warehouse", "grain") == 55
    assert len(changes) == 1
    assert len(world.get_inventory_ledger(command_id=first["command_id"])) == 1

    ctx.tool_call_id = "consume-1"
    consumed = handle_consume_resource(ctx, {
        "entity_id": "warehouse", "resource_name": "grain",
        "quantity": 3, "reason": "rations",
    })
    assert consumed["ok"] and consumed["base_value_delta"] == -6
    assert quantity(world, "warehouse", "grain") == 52

    ctx.tool_call_id = "consume-too-much"
    rejected = handle_consume_resource(ctx, {
        "entity_id": "warehouse", "resource_name": "grain",
        "quantity": 1000, "reason": "impossible",
    })
    assert not rejected["ok"]
    assert rejected["error_code"] == "insufficient_balance"
    assert quantity(world, "warehouse", "grain") == 52

    ctx.tool_call_id = "produce-metric"
    invalid = handle_produce_resource(ctx, {
        "entity_id": "warehouse", "resource_name": "morale",
        "quantity": 1, "reason": "invalid",
    })
    assert not invalid["ok"]
    assert "resource_kind_invalid" in invalid["error"]


@pytest.mark.parametrize("bad_value", [0, -1, float("inf"), float("nan"), True])
def test_economic_tools_reject_non_positive_or_non_finite_values(tmp_path, bad_value):
    world = make_world(tmp_path)
    ctx = FCContext(world_state=world, chain_id="chain", tool_call_id="bad")
    result = handle_produce_resource(ctx, {
        "entity_id": "warehouse", "resource_name": "grain",
        "quantity": bad_value, "reason": "invalid",
    })
    assert not result["ok"]
    assert "quantity_invalid" in result["error"]


def test_anchored_transfer_is_atomic_conserved_and_idempotent(tmp_path):
    world = make_world(tmp_path)
    changes: list[dict] = []
    ctx = FCContext(
        world_state=world, caller_id="official", chain_id="chain-transfer",
        tool_call_id="transfer-1", sim_time=20, changes_made=changes,
    )
    args = {
        "from_entity_id": "warehouse", "from_resource": "grain",
        "to_entity_id": "treasury", "to_resource": "coin",
        "base_value": 20, "reason": "distribution",
    }
    first = handle_transfer_anchored_value(ctx, args)
    replay = handle_transfer_anchored_value(ctx, args)

    assert first["ok"] and first["committed"]
    assert first["from_quantity_delta"] == -10
    assert first["to_quantity_delta"] == 40
    assert first["base_value_delta"] == 0
    assert replay["ok"] and replay["replayed"] is True
    assert quantity(world, "warehouse", "grain") == 40
    assert quantity(world, "treasury", "coin") == 40
    assert len(changes) == 1
    transfer_entries = [
        item for item in world.get_inventory_ledger(limit=100)
        if item["metadata"].get("transfer_id") == first["transfer_id"]
    ]
    assert len(transfer_entries) == 2
    invariants = world.get_economic_invariants()
    assert invariants["transfers"]["conserved"] is True
    assert invariants["transfers"]["net_base_value"] == 0


def test_anchored_transfer_rolls_back_credit_when_debit_is_insufficient(tmp_path):
    world = make_world(tmp_path)
    ctx = FCContext(
        world_state=world, chain_id="chain-transfer", tool_call_id="too-large",
    )
    result = handle_transfer_anchored_value(ctx, {
        "from_entity_id": "warehouse", "from_resource": "grain",
        "to_entity_id": "treasury", "to_resource": "coin",
        "base_value": 200, "reason": "overdraft",
    })
    assert not result["ok"]
    assert result["error_code"] == "insufficient_balance"
    assert result["committed"] is False
    assert quantity(world, "warehouse", "grain") == 50
    assert quantity(world, "treasury", "coin") == 0
    assert not [
        item for item in world.get_inventory_ledger(limit=100)
        if item["metadata"].get("transfer_id") == result["transfer_id"]
    ]


def test_action_agent_runs_multi_round_economic_plan_with_auditable_trace(tmp_path):
    world = make_world(tmp_path)
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[
            _tool_call("query-source", "query_entity", {"entity_id": "warehouse"}),
            _tool_call("query-target", "query_entity", {"entity_id": "treasury"}),
        ]),
        LLMResponse(tool_calls=[
            _tool_call("harvest", "produce_resource", {
                "entity_id": "warehouse", "resource_name": "grain",
                "quantity": 5, "reason": "harvest output",
            }),
            _tool_call("distribution", "transfer_anchored_value", {
                "from_entity_id": "warehouse", "from_resource": "grain",
                "to_entity_id": "treasury", "to_resource": "coin",
                "base_value": 20, "reason": "market distribution",
            }),
            _tool_call("upkeep", "consume_resource", {
                "entity_id": "treasury", "resource_name": "coin",
                "quantity": 4, "reason": "administrative upkeep",
            }),
        ]),
        LLMResponse(tool_calls=[
            _tool_call("complete", "complete_action", {
                "result_description": "Harvest distributed and upkeep paid",
                "duration_days": 2,
                "success": True,
            }),
        ]),
    ])
    agent = ActionAgent(
        llm, world, prompt_template=(
            "Execute {action_type} for {executor_name} at {target_location}. "
            "Resources: {resources}. Context: {world_context}"
        ), max_rounds=5,
    )

    result = asyncio.run(agent.process({
        "action_type": "economic_cycle",
        "executor_id": "warehouse",
        "target_location": "treasury",
        "description": "Run one anchored economic cycle",
        "resources": "grain and coin",
        "chain_id": "chain-agent-economy",
        "trace_id": "trace-agent-economy",
        "turn_id": "turn-agent-economy",
        "sim_time": 100,
    }))

    assert result.success is True
    assert result.result_description == "Harvest distributed and upkeep paid"
    assert [item["economic_operation"] for item in result.changes_made] == [
        "production", "transfer", "consumption",
    ]
    assert quantity(world, "warehouse", "grain") == 45
    assert quantity(world, "treasury", "coin") == 36
    assert len(result.fc_trace) == 3
    assert sum(len(round_item["calls"]) for round_item in result.fc_trace) == 6
    assert result.fc_trace[-1]["calls"][0]["tool"] == "complete_action"
    assert all("update_inventory" not in names for names in llm.tools_seen)
    invariants = world.get_economic_invariants()
    assert invariants["inventory"]["total_base_value"] == 108
    assert invariants["transfers"]["conserved"] is True


def test_action_causal_bridge_is_opt_in_deterministic_and_bounded():
    result = ActionResult(
        action_type="economic_cycle", executor_id="warehouse",
        target_location="treasury", result_description="private narrative",
        duration_days=2, success=True,
        changes_made=[
            {"economic_operation": "production", "base_value_delta": 10, "secret": "hidden"},
            {"economic_operation": "transfer", "base_value_delta": 0},
            {"economic_operation": "consumption", "base_value_delta": -2},
        ],
        fc_trace=[{"private": "trace"}],
    )
    assert build_action_causal_event(result, {}, chain_id="chain") is None
    request = {"causal_event_type": "agent_economic_action", "severity": 7}
    first = build_action_causal_event(
        result, request, chain_id="chain", source_event_id="source", generated_at=12,
    )
    second = build_action_causal_event(
        result, request, chain_id="chain", source_event_id="source", generated_at=12,
    )
    assert first == second
    assert first is not None
    assert first.target_agents == []
    assert first.initial_payload["economic_change_count"] == 3
    assert first.initial_payload["net_base_value_delta"] == 8
    serialized = json.dumps(first.initial_payload, ensure_ascii=False)
    assert "private narrative" not in serialized
    assert "hidden" not in serialized
    assert "trace" not in serialized

    with pytest.raises(ValueError, match="action_causal_event_type_invalid"):
        build_action_causal_event(
            result, {"causal_event_type": "invalid event type"}, chain_id="chain",
        )
