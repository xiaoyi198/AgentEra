from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.action_causal_bridge import build_action_causal_event
from multiagent_framework.causal_analysis import dry_run_causal_rules, replay_causal_fixture
from multiagent_framework.causal_events import CausalEventEngine
from multiagent_framework.causal_world_effects import execute_causal_world_commands
from multiagent_framework.chain_log import ChainLog
from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.time_simulator import TimeSimulator
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.types import ActionResult, EventObject
from multiagent_framework.world_state import Entity, ResourceDefinition, WorldState


RULES = [
    {
        "rule_id": "agent_market_branch",
        "source_event_types": ["agent_economic_action"],
        "child_event_type": "market_distribution",
        "delay_days": 1,
        "probability": 1,
        "conditions": [{
            "kind": "anchored_inventory", "entity_id": "warehouse",
            "name": "grain", "operator": "gte", "value": 80,
        }],
        "world_effects": [
            {
                "effect_type": "transfer_anchored_value",
                "from_entity_id": "warehouse", "from_resource": "grain",
                "to_entity_id": "treasury", "to_resource": "coin",
                "base_value": 10, "reason": "market settlement",
            },
            {
                "effect_type": "create_ongoing_event",
                "entity_id": "warehouse", "event_type": "settlement",
                "description": "market settlement yield matures",
                "duration_days": 2,
                "expected_result": {
                    "new_status": "market cycle settled",
                    "inventory_deltas": [{
                        "resource": "grain", "delta": 2,
                        "reason": "settlement yield",
                    }],
                },
            },
        ],
    },
    {
        "rule_id": "agent_spoilage_branch",
        "source_event_types": ["agent_economic_action"],
        "child_event_type": "spoilage",
        "delay_days": 1,
        "probability": 1,
        "world_effects": [{
            "effect_type": "adjust_resource",
            "entity_id": "warehouse", "resource_name": "grain",
            "quantity_delta": -2, "reason": "storage spoilage",
        }],
    },
    {
        "rule_id": "market_tax_branch",
        "source_event_types": ["market_distribution"],
        "child_event_type": "tax_administration",
        "delay_days": 0,
        "probability": 1,
        "conditions": [{
            "kind": "anchored_inventory", "entity_id": "treasury",
            "name": "coin", "operator": "gte", "value": 20,
        }],
        "world_effects": [{
            "effect_type": "adjust_resource",
            "entity_id": "treasury", "resource_name": "coin",
            "quantity_delta": -4, "reason": "tax administration",
        }],
    },
    {
        "rule_id": "spoilage_replenishment_branch",
        "source_event_types": ["spoilage"],
        "child_event_type": "replenishment",
        "delay_days": 1,
        "probability": 1,
        "world_effects": [{
            "effect_type": "adjust_resource",
            "entity_id": "warehouse", "resource_name": "grain",
            "quantity_delta": 3, "reason": "replacement harvest",
        }],
    },
    {
        "rule_id": "tax_audit_branch",
        "source_event_types": ["tax_administration"],
        "child_event_type": "audit_complete",
        "delay_days": 1,
        "probability": 1,
        "world_effects": [{
            "effect_type": "update_status",
            "entity_id": "treasury", "new_status": "audit complete",
        }],
    },
]


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

    async def chat(self, *, messages, tools=None, temperature=0.7, trace_context=None):
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0)


def _script() -> list[LLMResponse]:
    return [
        LLMResponse(tool_calls=[
            _tool_call("query-warehouse", "query_entity", {"entity_id": "warehouse"}),
            _tool_call("query-treasury", "query_entity", {"entity_id": "treasury"}),
        ]),
        LLMResponse(tool_calls=[
            _tool_call("agent-harvest", "produce_resource", {
                "entity_id": "warehouse", "resource_name": "grain",
                "quantity": 5, "reason": "agent harvest",
            }),
            _tool_call("agent-distribution", "transfer_anchored_value", {
                "from_entity_id": "warehouse", "from_resource": "grain",
                "to_entity_id": "treasury", "to_resource": "coin",
                "base_value": 20, "reason": "agent distribution",
            }),
            _tool_call("agent-upkeep", "consume_resource", {
                "entity_id": "treasury", "resource_name": "coin",
                "quantity": 4, "reason": "agent upkeep",
            }),
        ]),
        LLMResponse(tool_calls=[
            _tool_call("agent-complete", "complete_action", {
                "result_description": "Economic action committed",
                "duration_days": 1,
                "success": True,
            }),
        ]),
    ]


def _run_agent(world: WorldState):
    agent = ActionAgent(
        ScriptedLLM(_script()), world,
        prompt_template=(
            "Execute {action_type} for {executor_name} at {target_location}. "
            "Resources: {resources}. Context: {world_context}"
        ),
        max_rounds=5,
    )
    return asyncio.run(agent.process({
        "action_type": "economic_cycle",
        "executor_id": "warehouse",
        "target_location": "treasury",
        "description": "Produce, distribute and consume anchored resources",
        "resources": "grain and coin",
        "chain_id": "chain-long-agent-economy",
        "trace_id": "trace-long-agent-economy",
        "turn_id": "turn-long-agent-economy",
        "sim_time": 0,
    }))


def _quantity(world: WorldState, entity_id: str, resource_name: str) -> float:
    return next(
        item.quantity for item in world.get_inventory(entity_id)
        if item.item_name == resource_name
    )


def _execute_events(
    engine: CausalEventEngine,
    world: WorldState,
    sim_time: float,
) -> list[EventObject]:
    events = engine.poll(sim_time).events
    for event in events:
        result = execute_causal_world_commands(world, event, sim_time=sim_time)
        assert result is not None and result.ok
        engine.observe(event, sim_time=sim_time, world_state=world)
    return events


def _replay_content(grain_quantity: float = 45) -> dict:
    return {
        "schema_version": "1.0",
        "resources": [
            {"name": "grain", "kind": "asset", "anchor_rate": 2},
            {"name": "coin", "kind": "asset", "anchor_rate": 0.5},
        ],
        "entities": [
            {"id": "warehouse", "type": "location", "name": "Warehouse"},
            {"id": "treasury", "type": "location", "name": "Treasury"},
        ],
        "inventory": [
            {"entity_id": "warehouse", "item_name": "grain", "quantity": grain_quantity},
            {"entity_id": "treasury", "item_name": "coin", "quantity": 36},
        ],
        "entity_values": [],
        "agents": [{"id": "steward", "name": "Steward"}],
        "initial_events": [],
        "random_events": [],
        "ongoing_events": [],
        "relations": [],
        "time_rules": [],
        "personas": {},
        "prompts": {},
        "causal_event_rules": copy.deepcopy(RULES),
    }


def test_multi_round_agent_economic_causal_replay_survives_restart(tmp_path):
    db_path = tmp_path / "agent-economic-replay.db"
    world = WorldState(str(db_path))
    world.upsert_resource_definition(ResourceDefinition(
        name="grain", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=2.0,
    ))
    world.upsert_resource_definition(ResourceDefinition(
        name="coin", kind="asset", default_quantity=0,
        min_quantity=0, anchor_rate=0.5,
    ))
    world.upsert_entity(Entity(id="warehouse", type="location", name="Warehouse"))
    world.upsert_entity(Entity(id="treasury", type="location", name="Treasury"))
    world.set_inventory("warehouse", "grain", "grain", 50, command_id="seed-grain")
    world.set_inventory("treasury", "coin", "coin", 0, command_id="seed-coin")

    action = _run_agent(world)
    assert action.success
    assert [item["economic_operation"] for item in action.changes_made] == [
        "production", "transfer", "consumption",
    ]
    assert len(action.fc_trace) == 3
    assert _quantity(world, "warehouse", "grain") == pytest.approx(45)
    assert _quantity(world, "treasury", "coin") == pytest.approx(36)

    source = build_action_causal_event(
        action, {
            "causal_event_type": "agent_economic_action",
            "severity": 5,
            "intent_id": "intent-long-agent-economy",
        },
        chain_id="chain-long-agent-economy",
        source_event_id="request-agent-economy",
        generated_at=0,
    )
    assert source is not None
    assert source.initial_payload["economic_operations"] == [
        "consumption", "production", "transfer",
    ]
    assert source.initial_payload["net_base_value_delta"] == pytest.approx(8)
    assert "result_description" not in source.initial_payload
    engine = CausalEventEngine(RULES)
    assert engine.observe(source, sim_time=0, world_state=world) == 2

    day_one = GAME_SECONDS_PER_DAY
    first_branches = _execute_events(engine, world, day_one)
    assert sorted(event.event_type for event in first_branches) == [
        "market_distribution", "spoilage",
    ]
    tax_events = _execute_events(engine, world, day_one)
    assert [event.event_type for event in tax_events] == ["tax_administration"]
    assert _quantity(world, "warehouse", "grain") == pytest.approx(38)
    assert _quantity(world, "treasury", "coin") == pytest.approx(52)
    assert len(world.list_ongoing_events("warehouse")) == 1

    snapshot = engine.snapshot()
    assert len(snapshot["pending"]) == 2
    ledger_before_restart = world.get_inventory_ledger(limit=100)
    del world

    restarted_world = WorldState(str(db_path))
    restarted_engine = CausalEventEngine(RULES)
    restarted_engine.restore(snapshot, sim_time=day_one)

    # Replaying the exact same three-round Agent turn after restart resolves all
    # economic commands from receipts and does not claim duplicate changes.
    replayed_action = _run_agent(restarted_world)
    assert replayed_action.success
    assert replayed_action.changes_made == []
    assert _quantity(restarted_world, "warehouse", "grain") == pytest.approx(38)
    assert _quantity(restarted_world, "treasury", "coin") == pytest.approx(52)
    assert len(restarted_world.get_inventory_ledger(limit=100)) == len(ledger_before_restart)

    day_two = 2 * GAME_SECONDS_PER_DAY
    second_branches = _execute_events(restarted_engine, restarted_world, day_two)
    assert sorted(event.event_type for event in second_branches) == [
        "audit_complete", "replenishment",
    ]
    assert _quantity(restarted_world, "warehouse", "grain") == pytest.approx(41)
    assert restarted_world.get_entity("treasury").current_status == "audit complete"

    day_three = 3 * GAME_SECONDS_PER_DAY
    matured = restarted_world.apply_matured_events(day_three)
    assert len(matured) == 1 and matured[0].ok
    assert _quantity(restarted_world, "warehouse", "grain") == pytest.approx(43)
    assert restarted_world.get_entity("warehouse").current_status == "market cycle settled"

    invariants = restarted_world.get_economic_invariants()
    assert invariants["inventory"]["total_base_value"] == pytest.approx(112)
    assert invariants["ledger"]["issued_base_value"] == pytest.approx(120)
    assert invariants["ledger"]["destroyed_base_value"] == pytest.approx(8)
    assert invariants["ledger"]["net_non_transfer_base_value"] == pytest.approx(112)
    assert invariants["transfers"]["batch_count"] == 2
    assert invariants["transfers"]["entry_count"] == 4
    assert invariants["transfers"]["net_base_value"] == pytest.approx(0)
    assert invariants["transfers"]["conserved"] is True
    final_ledger = restarted_world.get_inventory_ledger(limit=100)
    assert len({item["entry_id"] for item in final_ledger}) == len(final_ledger)

    replay_report = dry_run_causal_rules(
        _replay_content(), {
            "event_id": "agent-economic-source-1",
            "event_type": "agent_economic_action",
            "severity": 5,
        }, horizon_days=5, max_events=32,
    )
    fixture = replay_report["replay_fixture"]
    same = replay_causal_fixture(_replay_content(), fixture)
    assert same["comparison"]["matches"] is True

    seed_drift = replay_causal_fixture(_replay_content(grain_quantity=10), fixture)
    assert seed_drift["comparison"]["analysis_matches"] is True
    assert seed_drift["comparison"]["result_matches"] is False

    rule_drift_content = _replay_content()
    rule_drift_content["causal_event_rules"][0]["delay_days"] = 2
    rule_drift = replay_causal_fixture(rule_drift_content, fixture)
    assert rule_drift["comparison"]["analysis_matches"] is False
    assert rule_drift["comparison"]["result_matches"] is False


class StubCompletedActionAgent:
    def __init__(self) -> None:
        self.time_simulator = TimeSimulator()
        self._prompt_template = "stub action prompt"
        self.llm = SimpleNamespace()

    async def process(self, payload):
        return ActionResult(
            action_type=payload.get("action_type", "economic_cycle"),
            executor_id=payload.get("executor_id", "warehouse"),
            target_location=payload.get("target_location", "treasury"),
            result_description="completed",
            duration_days=1,
            changes_made=[{
                "economic_operation": "production",
                "base_value_delta": 4,
            }],
            success=True,
        )


def test_orchestrator_only_publishes_opted_in_action_causal_events(tmp_path):
    court = CourtGraph(str(tmp_path / "court.db"))
    timer = TimerSystem(str(tmp_path / "timer.db"))
    chain_log = ChainLog(str(tmp_path / "chain.jsonl"))
    router = SimpleNamespace(_system_prompt="stub router", llm=SimpleNamespace())
    orchestrator = Orchestrator(
        court_graph=court,
        timer_system=timer,
        chain_log=chain_log,
        router=router,
        action_agent=StubCompletedActionAgent(),
    )
    orchestrator.configure_causal_events([{
        "rule_id": "completed_to_followup",
        "source_event_types": ["agent_economic_action"],
        "child_event_type": "economic_followup",
        "delay_days": 0,
        "probability": 1,
    }])

    asyncio.run(orchestrator._run_action(
        payload={
            "action_type": "economic_cycle",
            "executor_id": "warehouse",
            "target_location": "treasury",
            "causal_event_type": "agent_economic_action",
            "severity": 6,
            "intent_id": "intent-orchestrator-bridge",
        },
        source_agent_id="",
        chain_id="chain-orchestrator-bridge",
        event_id="source-request",
    ))

    due = orchestrator.causal_event_engine.poll(0).events
    assert [event.event_type for event in due] == ["economic_followup"]
    assert due[0].initial_payload["causal_parent_event_id"].startswith("action_result_")
    assert orchestrator.operational_metrics_snapshot()["action_causal_events"] == 1
    assert len(orchestrator.action_log) == 1

    # A second action without causal_event_type preserves the old behavior.
    asyncio.run(orchestrator._run_action(
        payload={
            "action_type": "economic_cycle",
            "executor_id": "warehouse",
            "target_location": "treasury",
        },
        source_agent_id="",
        chain_id="chain-no-causal-bridge",
        event_id="source-no-bridge",
    ))
    assert not orchestrator.causal_event_engine.poll(0).events
    assert orchestrator.operational_metrics_snapshot()["action_causal_events"] == 1
