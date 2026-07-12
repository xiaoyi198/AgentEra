from __future__ import annotations

import asyncio
from types import SimpleNamespace

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.memory_compressor import LLMMemoryCompressor
from multiagent_framework.router import RouterAgent
from multiagent_framework.runtime_bindings import (
    ACTION_TASK,
    MEMORY_TASK,
    ROUTER_TASK,
    TIANJI_TASK,
    RuntimeBindingsHub,
    RuntimeBindingsSnapshot,
    RuntimeTaskBinding,
)
from multiagent_framework.tianji_agent import TianJiAgent
from multiagent_framework.types import AgentAction
from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_registry import DLCRegistry
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.world_state import WorldState


def snapshot(generation: int, values: dict[str, tuple[str, object]]) -> RuntimeBindingsSnapshot:
    return RuntimeBindingsSnapshot.create(
        generation,
        {
            task_id: RuntimeTaskBinding(
                task_id=task_id,
                generation=generation,
                prompt=prompt,
                llm=llm,
                model_profile=f"profile-{generation}-{task_id}",
            )
            for task_id, (prompt, llm) in values.items()
        },
    )


class BlockingLLM:
    def __init__(self, content: str = "ok", *, blocked: bool = True) -> None:
        self.content = content
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[dict] = []
        if not blocked:
            self.release.set()

    async def chat(self, **kwargs):
        recorded = dict(kwargs)
        if "messages" in recorded:
            recorded["messages"] = [dict(item) for item in recorded["messages"]]
        if "trace_context" in recorded:
            recorded["trace_context"] = dict(recorded["trace_context"])
        self.calls.append(recorded)
        self.started.set()
        await self.release.wait()
        return LLMResponse(content=self.content)


class FakeCourt:
    def get_routing_candidates(self, *_args, **_kwargs):
        return []


class FakeWorld:
    def get_entity(self, _entity_id):
        return None

    def get_context_for_action(self, **_kwargs):
        return "world-context"


class CapturingTurnEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return SimpleNamespace(
            error=None,
            terminal_result={
                "result_description": "done",
                "duration_days": 1,
                "success": True,
            },
            stop_reason="terminal_tool",
            last_response=None,
            trace=[],
        )


def test_runtime_snapshot_is_immutable_and_public_state_is_redacted() -> None:
    llm = object()
    active = snapshot(7, {ROUTER_TASK: ("secret prompt body", llm)})
    hub = RuntimeBindingsHub(active)

    assert hub.capture(ROUTER_TASK).generation == 7
    try:
        active.bindings[ROUTER_TASK] = active.capture(ROUTER_TASK)  # type: ignore[index]
        raise AssertionError("snapshot mapping must be immutable")
    except TypeError:
        pass

    public = hub.public_state()
    serialized = str(public)
    assert public["generation"] == 7
    assert public["tasks"][ROUTER_TASK]["model_profile"] == "profile-7-router"
    assert "secret prompt body" not in serialized
    assert "BlockingLLM" not in serialized


def test_router_inflight_call_keeps_old_prompt_and_client_after_activation() -> None:
    async def scenario() -> None:
        old_llm = BlockingLLM()
        new_llm = BlockingLLM(blocked=False)
        hub = RuntimeBindingsHub(snapshot(1, {ROUTER_TASK: ("router-old", old_llm)}))
        router = RouterAgent(old_llm, FakeCourt())
        router.bind_runtime_bindings(hub)
        action = AgentAction(agent_id="source", action="ambiguous", content="route this")
        action.metadata = {"chain_id": "chain-old"}

        old_task = asyncio.create_task(router._route_ambiguous_chunk([action], None))
        await asyncio.wait_for(old_llm.started.wait(), timeout=2)
        hub.activate(snapshot(2, {ROUTER_TASK: ("router-new", new_llm)}))
        old_llm.release.set()
        await old_task
        await router._route_ambiguous_chunk([action], None)

        assert old_llm.calls[0]["messages"][0]["content"] == "router-old"
        assert old_llm.calls[0]["trace_context"]["runtime_generation"] == 1
        assert new_llm.calls[0]["messages"][0]["content"] == "router-new"
        assert new_llm.calls[0]["trace_context"]["runtime_generation"] == 2

    asyncio.run(scenario())


def test_action_call_captures_prompt_client_and_generation_once() -> None:
    async def scenario() -> None:
        old_llm, new_llm = object(), object()
        hub = RuntimeBindingsHub(snapshot(3, {ACTION_TASK: (
            "old {action_type} {executor_name} {target_location} {action_description} {resources} {world_context}",
            old_llm,
        )}))
        agent = ActionAgent(old_llm, FakeWorld(), prompt_template="fallback")
        agent.bind_runtime_bindings(hub)
        engine = CapturingTurnEngine()
        agent._turn_engine = engine

        task = asyncio.create_task(agent.process({
            "action_type": "relief",
            "executor_id": "mayor",
            "target_location": "city",
            "description": "help",
        }))
        await asyncio.wait_for(engine.started.wait(), timeout=2)
        hub.activate(snapshot(4, {ACTION_TASK: (
            "new {action_type} {executor_name} {target_location} {action_description} {resources} {world_context}",
            new_llm,
        )}))
        engine.release.set()
        await task

        call = engine.calls[0]
        assert call["llm"] is old_llm
        assert call["messages"][0]["content"].startswith("old relief")
        assert call["trace_context"]["runtime_generation"] == 3

    asyncio.run(scenario())


def test_action_call_freezes_time_rules_until_multi_round_work_commits() -> None:
    async def scenario() -> None:
        llm = object()
        prompt = (
            "action {action_type} {executor_name} {target_location} "
            "{action_description} {resources} {world_context}"
        )
        hub = RuntimeBindingsHub(snapshot(7, {ACTION_TASK: (prompt, llm)}))
        agent = ActionAgent(llm, FakeWorld(), prompt_template=prompt)
        agent.bind_runtime_bindings(hub)
        agent.configure_time_rules([{
            "rule_id": "generation.one",
            "action_types": ["relief"],
            "base_days": 2,
            "workload_factor": 0,
        }])
        old_fingerprint = agent.time_simulator.fingerprint
        engine = CapturingTurnEngine()
        agent._turn_engine = engine

        in_flight = asyncio.create_task(agent.process({
            "action_type": "relief",
            "executor_id": "mayor",
            "target_location": "city",
            "description": "commit under generation one",
        }))
        await asyncio.wait_for(engine.started.wait(), timeout=2)

        agent.configure_time_rules([{
            "rule_id": "generation.two",
            "action_types": ["relief"],
            "base_days": 9,
            "workload_factor": 0,
        }])
        new_fingerprint = agent.time_simulator.fingerprint
        assert new_fingerprint != old_fingerprint
        engine.release.set()

        old_result = await in_flight
        new_result = await agent.process({
            "action_type": "relief",
            "executor_id": "mayor",
            "target_location": "city",
            "description": "start under generation two",
        })

        assert old_result.duration_days == 2
        assert old_result.duration_rule_id == "generation.one"
        assert old_result.runtime_generation == 7
        assert old_result.time_rule_fingerprint == old_fingerprint
        assert new_result.duration_days == 9
        assert new_result.duration_rule_id == "generation.two"
        assert new_result.time_rule_fingerprint == new_fingerprint

    asyncio.run(scenario())


def test_memory_compression_inflight_call_keeps_old_binding() -> None:
    async def scenario() -> None:
        old_llm = BlockingLLM(content="old-summary")
        new_llm = BlockingLLM(content="new-summary", blocked=False)
        old_prompt = "OLD {agent_context} {existing_summary_section} {messages_text}"
        new_prompt = "NEW {agent_context} {existing_summary_section} {messages_text}"
        hub = RuntimeBindingsHub(snapshot(5, {MEMORY_TASK: (old_prompt, old_llm)}))
        compressor = LLMMemoryCompressor(old_llm, prompt_template=old_prompt)
        compressor.bind_runtime_bindings(hub)

        task = asyncio.create_task(compressor.compress(
            [{"role": "user", "content": "remember"}], None, "mayor"
        ))
        await asyncio.wait_for(old_llm.started.wait(), timeout=2)
        hub.activate(snapshot(6, {MEMORY_TASK: (new_prompt, new_llm)}))
        old_llm.release.set()
        result = await task
        await compressor.compress([{"role": "user", "content": "next"}], None, "mayor")

        assert old_llm.calls[0]["messages"][0]["content"].startswith("OLD")
        assert old_llm.calls[0]["trace_context"]["runtime_generation"] == 5
        assert result["content"].endswith("old-summary")
        assert new_llm.calls[0]["messages"][0]["content"].startswith("NEW")
        assert new_llm.calls[0]["trace_context"]["runtime_generation"] == 6

    asyncio.run(scenario())


def test_tianji_turn_keeps_one_binding_and_next_turn_gets_new_generation() -> None:
    async def scenario() -> None:
        old_llm = BlockingLLM(content="old-answer")
        new_llm = BlockingLLM(content="new-answer", blocked=False)
        hub = RuntimeBindingsHub(snapshot(10, {TIANJI_TASK: ("tianji-old", old_llm)}))
        tianji = TianJiAgent(
            llm_client=old_llm,
            world_state=SimpleNamespace(),
            court_graph=SimpleNamespace(),
            timer_system=SimpleNamespace(),
            chain_log=SimpleNamespace(append=lambda _entry: None),
            action_agent=SimpleNamespace(),
            system_prompt="fallback",
        )
        tianji.bind_runtime_bindings(hub)

        async def consume(text: str):
            return [event async for event in tianji.chat(text)]

        old_task = asyncio.create_task(consume("first"))
        await asyncio.wait_for(old_llm.started.wait(), timeout=2)
        hub.activate(snapshot(11, {TIANJI_TASK: ("tianji-new", new_llm)}))
        old_llm.release.set()
        await old_task
        await consume("second")

        assert old_llm.calls[0]["messages"][0]["content"] == "tianji-old"
        assert old_llm.calls[0]["trace_context"]["runtime_generation"] == 10
        assert new_llm.calls[0]["messages"][0]["content"] == "tianji-new"
        assert new_llm.calls[0]["trace_context"]["runtime_generation"] == 11
        assert any(item.get("content") == "old-answer" for item in tianji.messages)

    asyncio.run(scenario())


class AgentLLM:
    def __init__(self, agent_id: str = "") -> None:
        self.agent_id = agent_id

    def for_agent(self, agent_id: str):
        return AgentLLM(agent_id)


def make_runtime_registry(tmp_path) -> DLCRegistry:
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "atomic", "name": "Atomic", "version": "1.0.0"})
    registry.save_content("atomic", {
        "resources": [{"name": "coin", "kind": "asset", "anchor_rate": 1}],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "coin", "quantity": 1}],
        "entity_values": [],
        "relations": [],
        "agents": [{
            "id": "mayor", "name": "Mayor", "rank": 2,
            "location_id": "city", "personality_seed": "steady",
        }],
        "initial_events": [],
        "ongoing_events": [],
        "personas": {"steady": "steady"},
        "prompts": {
            "official_template": "Agent {name} {location} {personality} {superior}",
            "router": "router-one",
            "action_agent": "action-one {action_type} {executor_name} {target_location} {action_description} {resources} {world_context}",
            "memory_compressor": "memory-one {agent_context} {existing_summary_section} {messages_text}",
            "tianji": "tianji-one",
        },
    })
    registry.publish("atomic")
    registry.set_enabled("atomic", True)
    return registry


def test_dlc_reload_and_rollback_publish_one_generation_for_all_runtime_tasks(tmp_path) -> None:
    registry = make_runtime_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    base_llm = AgentLLM()
    router = RouterAgent(base_llm, court, "router-base")
    action = ActionAgent(
        base_llm, world,
        prompt_template="action-base {action_type} {executor_name} {target_location} {action_description} {resources} {world_context}",
        court=court,
    )
    compressor = LLMMemoryCompressor(
        base_llm,
        prompt_template="memory-base {agent_context} {existing_summary_section} {messages_text}",
    )
    orchestrator = Orchestrator(
        court_graph=court,
        timer_system=SimpleNamespace(),
        chain_log=SimpleNamespace(append=lambda _entry: None),
        router=router,
        action_agent=action,
        memory_compressor=compressor,
    )
    tianji = TianJiAgent(
        llm_client=base_llm,
        world_state=world,
        court_graph=court,
        timer_system=SimpleNamespace(),
        chain_log=SimpleNamespace(append=lambda _entry: None),
        action_agent=action,
        router=router,
        system_prompt="tianji-base",
    )
    tianji.messages.append({"role": "user", "content": "preserve-session"})
    orchestrator.tianji_agent = tianji
    loader = DLCBundleLoader(registry, world, court, orchestrator, base_llm)

    loader.load_active(seed_world=True)
    generation_one = orchestrator.runtime_bindings.snapshot
    assert generation_one.generation == 1
    assert {item.generation for item in generation_one.bindings.values()} == {1}

    content = registry.load_content("atomic")
    content["prompts"].update({
        "router": "router-two",
        "action_agent": "action-two {action_type} {executor_name} {target_location} {action_description} {resources} {world_context}",
        "memory_compressor": "memory-two {agent_context} {existing_summary_section} {messages_text}",
        "tianji": "tianji-two",
    })
    registry.save_content("atomic", content)
    asyncio.run(loader.reload_active())

    generation_two = orchestrator.runtime_bindings.snapshot
    assert generation_two.generation == 2
    assert {item.generation for item in generation_two.bindings.values()} == {2}
    assert generation_two.capture(ROUTER_TASK).prompt == "router-two"
    assert generation_two.capture(ACTION_TASK).prompt.startswith("action-two")
    assert generation_two.capture(MEMORY_TASK).prompt.startswith("memory-two")
    assert generation_two.capture(TIANJI_TASK).prompt == "tianji-two"
    assert tianji.messages[1]["content"] == "preserve-session"

    asyncio.run(loader.rollback_active(1))
    generation_three = orchestrator.runtime_bindings.snapshot
    assert loader.generation == 3
    assert generation_three.generation == 3
    assert {item.generation for item in generation_three.bindings.values()} == {3}
    assert generation_three.capture(ROUTER_TASK).prompt == "router-one"
    assert generation_three.capture(ACTION_TASK).prompt.startswith("action-one")
    assert generation_three.capture(MEMORY_TASK).prompt.startswith("memory-one")
    assert generation_three.capture(TIANJI_TASK).prompt == "tianji-one"

    public = str(loader.public_runtime())
    for secret_prompt in ("router-one", "action-one", "memory-one", "tianji-one"):
        assert secret_prompt not in public
