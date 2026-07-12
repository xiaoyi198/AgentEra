from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace

import pytest

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.chain_log import ChainLog
from multiagent_framework.court_graph import CourtGraph, Official
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.world_state import Entity, WorldState


OFFICIAL_ID = "relief_official"
SOURCE_LOCATION = "capital_storehouse"
TARGET_LOCATION = "flooded_county"
GRAIN = "\u7cae\u8349"


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
        self.contexts: list[dict] = []
        self.message_snapshots: list[list[dict]] = []

    async def chat(self, *, messages, tools=None, temperature=0.7, trace_context=None):
        self.contexts.append(dict(trace_context or {}))
        self.message_snapshots.append([dict(message) for message in messages])
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0)


class _TokenCounter:
    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class NoopRouter:
    def __init__(self) -> None:
        self.llm = SimpleNamespace(token_counter=_TokenCounter())
        self.last_response_text = ""
        self.last_stats: dict = {}
        self._system_prompt = "noop router"

    async def process_batch(self, actions):
        raise AssertionError(f"request_action must bypass Router, got {actions!r}")

    def update_time_scale(self, _seconds_per_day: float) -> None:
        return None


async def _drain_background_tasks(orchestrator: Orchestrator) -> None:
    """Drain parent Agent turns and child ActionAgent tasks spawned by them."""
    for _ in range(20):
        await asyncio.sleep(0)
        pending = [task for task in tuple(orchestrator._agent_tasks) if not task.done()]
        if pending:
            await asyncio.gather(*pending)
            continue
        await asyncio.sleep(0)
        if not any(not task.done() for task in tuple(orchestrator._agent_tasks)):
            return
    raise AssertionError("orchestrator background tasks did not drain")


def _quantity(world: WorldState, entity_id: str, resource_name: str) -> float:
    return next(
        item.quantity
        for item in world.get_inventory(entity_id)
        if item.item_name == resource_name
    )


def test_mock_llm_world_scenario_runs_full_event_action_timer_memory_pipeline(tmp_path) -> None:
    asyncio.run(_run_mock_world_scenario(tmp_path))


async def _run_mock_world_scenario(tmp_path) -> None:
    world_db = tmp_path / "world.db"
    court_db = tmp_path / "court.db"
    timer_db = tmp_path / "timers.db"

    world = WorldState(str(world_db))
    world.upsert_entity(Entity(
        id=OFFICIAL_ID,
        type="official",
        name="Relief Official",
        metadata={"x": 0.0, "y": 0.0, "region": "capital"},
    ))
    world.upsert_entity(Entity(
        id=SOURCE_LOCATION,
        type="location",
        name="Capital Storehouse",
        metadata={"x": 0.0, "y": 0.0, "region": "capital"},
    ))
    world.upsert_entity(Entity(
        id=TARGET_LOCATION,
        type="location",
        name="Flooded County",
        current_status="Flood relief is pending",
        metadata={"x": 300.0, "y": 0.0, "region": "south"},
    ))
    world.set_inventory(OFFICIAL_ID, GRAIN, GRAIN, 100, command_id="seed-grain")

    court = CourtGraph(str(court_db))
    court.set_world_state(world)
    court.add_official(Official(
        id=OFFICIAL_ID,
        name="Relief Official",
        rank=5,
        location_id=SOURCE_LOCATION,
        role="official",
        agent_type="official",
        superior_id=None,
        personality_seed="careful",
    ))

    official_llm = ScriptedLLM([
        LLMResponse(
            tool_calls=[_tool_call("official-query", "query_entity", {
                "entity_id": TARGET_LOCATION,
            })],
            input_tokens=20,
            output_tokens=5,
        ),
        LLMResponse(
            tool_calls=[
                _tool_call("official-request", "request_action", {
                    "action_type": "relief",
                    "target_location": TARGET_LOCATION,
                    "description": "Send grain and organize flood relief",
                    "resources": "20 units of grain",
                    "severity": 7,
                }),
                _tool_call("official-finish", "finish_turn", {
                    "summary": "Relief action requested",
                }),
            ],
            input_tokens=30,
            output_tokens=8,
        ),
        LLMResponse(
            tool_calls=[_tool_call("receipt-finish", "finish_turn", {
                "summary": "Action receipt acknowledged",
            })],
            input_tokens=12,
            output_tokens=3,
        ),
    ])
    official = OfficialAgent(
        AgentConfig(
            agent_id=OFFICIAL_ID,
            display_name="Relief Official",
            system_prompt="Use verified world state and explicit tools.",
            max_rounds=5,
        ),
        official_llm,
    )

    action_llm = ScriptedLLM([
        LLMResponse(tool_calls=[_tool_call("action-query", "query_entity", {
            "entity_id": TARGET_LOCATION,
        })], input_tokens=18, output_tokens=4),
        LLMResponse(tool_calls=[_tool_call("action-spend", "update_inventory", {
            "entity_id": OFFICIAL_ID,
            "resource_name": GRAIN,
            "quantity_delta": -20,
            "reason": "Dispatch relief grain",
        })], input_tokens=18, output_tokens=4),
        LLMResponse(tool_calls=[_tool_call("action-event", "add_ongoing_event", {
            "entity_id": TARGET_LOCATION,
            "event_type": "inventory_arrival",
            "description": "Relief grain is in transit",
            "duration_days": 99,
            "expected_result": {
                "new_status": "Relief grain arrived and distribution started",
                "inventory_deltas": [{
                    "resource": GRAIN,
                    "delta": 20,
                    "reason": "Relief shipment arrived",
                }],
            },
        })], input_tokens=22, output_tokens=5),
        LLMResponse(tool_calls=[_tool_call("action-complete", "complete_action", {
            "result_description": "Grain dispatched and relief operation scheduled",
            "duration_days": 4,
            "success": True,
        })], input_tokens=16, output_tokens=4),
    ])
    action_agent = ActionAgent(
        action_llm,
        world,
        court=court,
        prompt_template=(
            "Execute {action_type} for {executor_name} at {target_location}. "
            "Resources: {resources}. Context: {world_context}"
        ),
    )

    timers = TimerSystem(str(timer_db))
    chain_log = ChainLog(str(tmp_path / "chain.jsonl"))
    orchestrator = Orchestrator(
        court_graph=court,
        timer_system=timers,
        chain_log=chain_log,
        router=NoopRouter(),
        action_agent=action_agent,
    )
    orchestrator.register_agent(official)
    orchestrator.configure_time_rules([
        {
            "rule_id": "relief.authoritative",
            "action_types": ["relief"],
            "base_days": 2,
            "workload_factor": 1,
            "severity_factor": 0,
            "distance_factor": 1,
            "resource_factor": 0.1,
            "min_days": 1,
            "max_days": 30,
            "priority": 100,
        },
        {
            "rule_id": "arrival.authoritative",
            "action_types": ["inventory_arrival"],
            "base_days": 2,
            "workload_factor": 0,
            "severity_factor": 0,
            "distance_factor": 0,
            "resource_factor": 0,
            "min_days": 2,
            "max_days": 2,
            "priority": 100,
        },
    ])
    orchestrator.configure_random_events([
        {
            "template_id": "flood_relief_request",
            "event_type": "flood",
            "target_agents": [OFFICIAL_ID],
            "initial_payload": {"location_id": TARGET_LOCATION, "summary": "Flood reported"},
            "severity_min": 7,
            "severity_max": 7,
            "min_interval_days": 1,
            "max_interval_days": 1,
            "probability": 1,
            "max_occurrences": 1,
        }
    ], preserve_state=False)

    first_due = orchestrator.random_event_status()["next_due"]
    assert first_due == pytest.approx(86400.0)
    orchestrator._sim_time = first_due
    first_tick = await orchestrator._tick()
    await _drain_background_tasks(orchestrator)

    assert len(first_tick.generated_events) == 1
    generated_metadata = first_tick.generated_events[0]
    assert generated_metadata["event_type"] == "flood"
    assert generated_metadata["target_count"] == 1
    assert "initial_payload" not in generated_metadata
    assert OFFICIAL_ID not in json.dumps(generated_metadata, ensure_ascii=False)

    assert len(official_llm.message_snapshots) == 2
    assert [context["round"] for context in official_llm.contexts[:2]] == [0, 1]
    assert len(action_llm.message_snapshots) == 4
    assert [context["round"] for context in action_llm.contexts] == [0, 1, 2, 3]
    assert all(context["chain_id"] for context in action_llm.contexts)

    assert _quantity(world, OFFICIAL_ID, GRAIN) == pytest.approx(80)
    ongoing = world.list_ongoing_events(TARGET_LOCATION)
    assert len(ongoing) == 1
    event = ongoing[0]
    assert event.end_time == pytest.approx(first_due + 2 * 86400.0)

    assert len(orchestrator.action_log) == 1
    action_record = orchestrator.action_log[0]
    expected_receipt_days = round(2 + 4 + 1 - 0.1 * math.log1p(80), 6)
    assert action_record["duration_rule_id"] == "relief.authoritative"
    assert action_record["duration_source"] == "deterministic"
    assert action_record["duration_days"] == pytest.approx(expected_receipt_days)
    assert action_record["duration_days"] != 4
    assert action_record["duration_components"]["distance_days"] == pytest.approx(1)
    assert action_record["duration_components"]["resource_discount_days"] == pytest.approx(
        round(0.1 * math.log1p(80), 6)
    )
    assert action_record["fc_rounds"] == 4

    pending_timers = timers.get_in_transit()
    assert len(pending_timers) == 1
    receipt_timer = pending_timers[0]
    assert receipt_timer.payload["type"] == "action_result"
    assert receipt_timer.payload["intent_id"].startswith("intent_")
    assert receipt_timer.fire_at == pytest.approx(
        first_due + expected_receipt_days * 86400.0
    )

    commitments = [
        record for record in official.serialize_memory()["structured_memory"]["records"]
        if record["kind"] == "commitment"
    ]
    assert len(commitments) == 1
    assert commitments[0]["status"] == "unresolved"
    assert commitments[0]["metadata"]["intent_id"] == receipt_timer.payload["intent_id"]
    assert any(entry.fc_called == "request_action" for entry in chain_log.all_entries())
    assert any(entry.fc_called == "complete_action" for entry in chain_log.all_entries())

    # Restart both SQLite-backed stores before either delayed effect fires.
    restarted_world = WorldState(str(world_db))
    restarted_timers = TimerSystem(str(timer_db))
    assert restarted_timers.load_from_db() == 1
    orchestrator.action_agent.world_state = restarted_world
    orchestrator.timers = restarted_timers
    court.set_world_state(restarted_world)
    official.bind_runtime(
        world_state=restarted_world,
        court=court,
        sim_time_provider=lambda: orchestrator._sim_time,
        seconds_per_day_provider=lambda: 86400.0,
    )

    orchestrator._sim_time = event.end_time
    matured_tick = await orchestrator._tick()
    await _drain_background_tasks(orchestrator)
    assert matured_tick.generated_events == []
    assert restarted_world.list_ongoing_events(TARGET_LOCATION) == []
    assert restarted_world.get_entity(TARGET_LOCATION).current_status == (
        "Relief grain arrived and distribution started"
    )
    assert _quantity(restarted_world, TARGET_LOCATION, GRAIN) == pytest.approx(20)

    orchestrator._sim_time = receipt_timer.fire_at
    receipt_tick = await orchestrator._tick()
    await _drain_background_tasks(orchestrator)
    assert receipt_tick.agent_errors == []
    assert restarted_timers.count() == 0
    assert len(official_llm.message_snapshots) == 3
    assert any(
        "\u771f\u5b9e\u884c\u52a8\u7ed3\u679c" in str(message.get("content", ""))
        for message in official.memory
    )

    resolved_commitments = [
        record for record in official.serialize_memory()["structured_memory"]["records"]
        if record["kind"] == "commitment"
    ]
    assert len(resolved_commitments) == 1
    assert resolved_commitments[0]["status"] == "resolved"
    assert resolved_commitments[0]["metadata"]["success"] is True

    metrics = orchestrator.operational_metrics_snapshot()
    assert metrics["agent_turns"] == 2
    assert metrics["agent_turn_completed"] == 2
    assert metrics["agent_turn_errors"] == 0
    assert metrics["agent_turn_rounds"] == 3
    assert metrics["agent_turn_tool_calls"] == 4
