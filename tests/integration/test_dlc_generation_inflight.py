from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_registry import DLCRegistry
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.world_state import WorldState


ACTION_PROMPT_ONE = (
    "generation-one {action_type} {executor_name} {target_location} "
    "{action_description} {resources} {world_context}"
)
ACTION_PROMPT_TWO = (
    "generation-two {action_type} {executor_name} {target_location} "
    "{action_description} {resources} {world_context}"
)


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


class ScriptedProfileClient:
    def __init__(self, responses: list[LLMResponse], *, block_first: bool = False) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not block_first:
            self.release.set()

    async def chat(self, **kwargs):
        recorded = dict(kwargs)
        recorded["messages"] = [dict(item) for item in kwargs.get("messages", [])]
        recorded["trace_context"] = dict(kwargs.get("trace_context") or {})
        self.calls.append(recorded)
        if len(self.calls) == 1:
            self.started.set()
            await self.release.wait()
        if not self.responses:
            raise AssertionError("unexpected model call")
        return self.responses.pop(0)

    def for_agent(self, _agent_id: str, profile_override: str = ""):
        if profile_override:
            raise AssertionError("profile should be resolved by selector")
        return self


class ProfileSelector:
    def __init__(self, profiles: dict[str, ScriptedProfileClient]) -> None:
        self.profiles = profiles
        self.profile_names = set(profiles)

    def with_profile(self, profile: str):
        return self.profiles[profile]

    def for_agent(self, _agent_id: str, profile_override: str = ""):
        return self.with_profile(profile_override) if profile_override else self

    async def chat(self, **_kwargs):
        return LLMResponse(content="no-op")


class ProfileGateway:
    def __init__(self, selector: ProfileSelector) -> None:
        self.selector = selector

    def for_task(self, _task_type: str, *, agent_id: str = "", profile_override: str = ""):
        if profile_override:
            return self.selector.with_profile(profile_override)
        return self.selector.for_agent(agent_id)


class NoopRouter:
    def __init__(self, llm) -> None:
        self.llm = llm
        self._system_prompt = "router"
        self.last_response_text = ""
        self.last_stats = {}

    def bind_runtime_bindings(self, hub) -> None:
        self._runtime_bindings = hub

    def update_time_scale(self, _seconds_per_day: float) -> None:
        return None


class NullChainLog:
    def append(self, _entry) -> None:
        return None


def _registry(tmp_path) -> DLCRegistry:
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({
        "id": "generation-test",
        "name": "Generation Test",
        "version": "1.0.0",
        "model_bindings": {"tasks": {"action.simulation": "generation-one"}},
    })
    registry.save_content("generation-test", {
        "resources": [{"name": "credits", "kind": "asset", "anchor_rate": 1}],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "credits", "quantity": 10}],
        "entity_values": [],
        "relations": [],
        "agents": [{
            "id": "mayor", "name": "Mayor", "rank": 2,
            "location_id": "city", "personality_seed": "steady",
        }],
        "initial_events": [],
        "random_events": [],
        "ongoing_events": [],
        "time_rules": [{
            "rule_id": "generation.one", "action_types": ["shipment", "relief"],
            "base_days": 2, "workload_factor": 0,
        }],
        "personas": {"steady": "steady"},
        "prompts": {
            "official_template": "Agent {name} {location} {personality} {superior}",
            "router": "router-one",
            "action_agent": ACTION_PROMPT_ONE,
        },
    })
    registry.publish("generation-test")
    registry.set_enabled("generation-test", True)
    return registry


def _runtime(tmp_path, registry, selector, *, suffix: str = ""):
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    router = NoopRouter(selector)
    action = ActionAgent(selector, world, court=court, prompt_template="fallback")
    orchestrator = Orchestrator(
        court_graph=court,
        timer_system=TimerSystem(str(tmp_path / "timers.db")),
        chain_log=NullChainLog(),
        router=router,
        action_agent=action,
    )
    orchestrator.model_gateway = ProfileGateway(selector)
    loader = DLCBundleLoader(registry, world, court, orchestrator, selector)
    return world, court, action, orchestrator, loader


def test_generation_reload_freezes_inflight_work_and_survives_restart_and_rollback(tmp_path) -> None:
    asyncio.run(_scenario(tmp_path))


async def _scenario(tmp_path) -> None:
    generation_one_client = ScriptedProfileClient([
        LLMResponse(tool_calls=[_tool_call("event", "add_ongoing_event", {
            "entity_id": "city",
            "event_type": "shipment",
            "description": "generation one shipment",
            "duration_days": 99,
            "expected_result": {"new_status": "generation one shipment arrived"},
        })]),
        LLMResponse(tool_calls=[_tool_call("complete", "complete_action", {
            "result_description": "generation one committed",
            "duration_days": 99,
            "success": True,
        })]),
    ], block_first=True)
    generation_two_client = ScriptedProfileClient([
        LLMResponse(tool_calls=[_tool_call("complete-new", "complete_action", {
            "result_description": "generation two committed",
            "duration_days": 99,
            "success": True,
        })]),
    ])
    selector = ProfileSelector({
        "generation-one": generation_one_client,
        "generation-two": generation_two_client,
    })
    registry = _registry(tmp_path)
    world, _court, action, orchestrator, loader = _runtime(
        tmp_path, registry, selector
    )
    loader.load_active(seed_world=True)
    assert loader.generation == 1

    in_flight = asyncio.create_task(action.process({
        "action_type": "relief",
        "executor_id": "mayor",
        "target_location": "city",
        "description": "start before reload",
        "sim_time": 1000.0,
        "seconds_per_day": 86400.0,
        "chain_id": "generation-one-chain",
    }))
    await asyncio.wait_for(generation_one_client.started.wait(), timeout=2)

    content = registry.load_content("generation-test")
    content["prompts"]["action_agent"] = ACTION_PROMPT_TWO
    content["time_rules"] = [{
        "rule_id": "generation.two", "action_types": ["shipment", "relief"],
        "base_days": 9, "workload_factor": 0,
    }]
    registry.save_content("generation-test", content)
    registry.save_manifest("generation-test", {
        "model_bindings": {"tasks": {"action.simulation": "generation-two"}},
    })
    reload_result = await loader.reload_active()
    assert reload_result["generation"] == 2
    assert orchestrator.runtime_bindings.public_state()["tasks"]["action_agent"][
        "model_profile"
    ] == "generation-two"

    generation_one_client.release.set()
    old_result = await in_flight
    old_event = world.list_ongoing_events("city")[0]
    frozen_end_time = old_event.end_time
    frozen_expected_result = dict(old_event.expected_result)

    assert old_result.runtime_generation == 1
    assert old_result.duration_rule_id == "generation.one"
    assert old_result.duration_days == 2
    assert old_event.end_time == 1000.0 + 2 * 86400.0
    assert old_event.expected_result == {
        "new_status": "generation one shipment arrived"
    }
    assert generation_one_client.calls[0]["messages"][0]["content"].startswith(
        "generation-one"
    )
    assert all(
        call["trace_context"]["runtime_generation"] == 1
        for call in generation_one_client.calls
    )

    new_result = await action.process({
        "action_type": "relief",
        "executor_id": "mayor",
        "target_location": "city",
        "description": "start after reload",
        "sim_time": 1000.0,
    })
    assert new_result.runtime_generation == 2
    assert new_result.duration_rule_id == "generation.two"
    assert new_result.duration_days == 9
    assert generation_two_client.calls[0]["messages"][0]["content"].startswith(
        "generation-two"
    )
    assert generation_two_client.calls[0]["trace_context"]["runtime_generation"] == 2

    world.update_status("city", "live state after generation two", 2000.0)

    restarted_world, _court2, _action2, restarted_orchestrator, restarted_loader = _runtime(
        tmp_path, registry, selector, suffix="restart"
    )
    restarted_loader.load_active(seed_world=False)
    assert restarted_loader.generation == 3
    assert restarted_orchestrator.runtime_bindings.public_state()["tasks"][
        "action_agent"
    ]["model_profile"] == "generation-two"
    restored_event = restarted_world.list_ongoing_events("city")[0]
    assert restored_event.end_time == frozen_end_time
    assert restored_event.expected_result == frozen_expected_result
    assert restarted_world.get_entity("city").current_status == (
        "live state after generation two"
    )

    rollback = await restarted_loader.rollback_active(1)
    assert rollback["generation"] == 4
    assert rollback["source_generation"] == 1
    assert restarted_orchestrator.runtime_bindings.public_state()["tasks"][
        "action_agent"
    ]["model_profile"] == "generation-one"
    assert restarted_orchestrator.time_rule_status()["fingerprint"] != (
        orchestrator.time_rule_status()["fingerprint"]
    )
    rolled_back_event = restarted_world.list_ongoing_events("city")[0]
    assert rolled_back_event.end_time == frozen_end_time
    assert rolled_back_event.expected_result == frozen_expected_result
    assert restarted_world.get_entity("city").current_status == (
        "live state after generation two"
    )

    matured = restarted_world.apply_matured_events(frozen_end_time)
    assert len(matured) == 1
    assert restarted_world.list_ongoing_events("city") == []
    assert restarted_world.get_entity("city").current_status == (
        "generation one shipment arrived"
    )
