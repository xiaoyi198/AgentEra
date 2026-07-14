from __future__ import annotations

import asyncio
import json
from functools import wraps
from types import SimpleNamespace


from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.capability_contract import CapabilityCatalog, CapabilityContractError
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.turn_engine import TurnBudget, TurnEngine


def async_test(func):
    @wraps(func)
    def wrapper():
        return asyncio.run(func())
    return wrapper


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
        self.tools_snapshots: list[list[dict]] = []

    async def chat(self, *, messages, tools=None, temperature=0.7, trace_context=None):
        self.contexts.append(dict(trace_context or {}))
        self.message_snapshots.append([dict(message) for message in messages])
        self.tools_snapshots.append(list(tools or []))
        if not self.responses:
            raise AssertionError("unexpected extra LLM call")
        return self.responses.pop(0)


class FakeWorld:
    def __init__(self) -> None:
        self.entity = SimpleNamespace(
            id="苏州",
            type="location",
            name="苏州府",
            description="江南重镇",
            current_status="水灾后粮价上涨",
        )

    def get_entity(self, entity_id):
        return self.entity if entity_id == self.entity.id else None

    def search_entities(self, keyword):
        return [self.entity] if keyword in self.entity.id or keyword in self.entity.name else []

    def get_inventory(self, entity_id):
        return []

    def list_ongoing_events(self, entity_id):
        return []

    def get_all_entities(self):
        return [self.entity]

    def get_entities_by_type(self, entity_type):
        return [self.entity] if entity_type == self.entity.type else []

    def get_context_for_action(self, **kwargs):
        return "苏州当前水灾后粮价上涨。"


@async_test
async def test_turn_engine_stops_repeated_no_progress() -> None:
    repeated = LLMResponse(
        tool_calls=[_tool_call("q", "query", {"id": "same"})],
        input_tokens=10,
        output_tokens=2,
    )
    llm = ScriptedLLM([repeated, repeated, repeated])
    engine = TurnEngine()

    async def execute_tool(name, args, tool_call):
        return {"ok": True, "value": "unchanged"}

    outcome = await engine.run(
        llm=llm,
        messages=[{"role": "system", "content": "test"}],
        tools=[],
        execute_tool=execute_tool,
        terminal_predicate=lambda name, result: False,
        trace_context={"trace_id": "trace", "turn_id": "turn", "chain_id": "chain"},
        budget=TurnBudget(max_rounds=8, no_progress_rounds=1),
    )

    assert outcome.stop_reason == "no_progress"
    assert outcome.rounds == 2
    assert outcome.tool_calls == 2
    assert outcome.total_tokens == 24
    assert [context["round"] for context in llm.contexts] == [0, 1]
    assert all(context["trace_id"] == "trace" for context in llm.contexts)


@async_test
async def test_official_agent_query_submit_finish_and_real_feedback() -> None:
    llm = ScriptedLLM([
        LLMResponse(
            tool_calls=[_tool_call("q1", "query_entity", {"entity_id": "苏州"})],
            input_tokens=20,
            output_tokens=5,
        ),
        LLMResponse(
            tool_calls=[
                _tool_call("s1", "submit_action", {
                    "action": "report_message",
                    "content": "苏州水灾后粮价上涨，请求调粮。",
                    "original_intent": "如实上报灾情",
                    "is_distorting": False,
                    "urgency": "urgent",
                }),
                _tool_call("f1", "finish_turn", {"summary": "已查证并上报"}),
            ],
            input_tokens=30,
            output_tokens=8,
        ),
    ])
    agent = OfficialAgent(
        AgentConfig(agent_id="苏州知府", display_name="苏州知府", system_prompt="你是苏州知府。"),
        llm,
    )
    agent.bind_runtime(world_state=FakeWorld())

    actions = await agent.process({
        "message": "核查苏州灾情并决定是否上报",
        "from": "巡抚",
        "chain_id": "chain_suzhou",
        "sim_time": 123.0,
    })

    assert len(actions) == 1
    assert actions[0].action == "report_message"
    assert actions[0].metadata["intent_id"].startswith("intent_")
    assert actions[0].metadata["inbox_sequence"] == 1
    commitment = next(
        record for record in agent.structured_memory.records
        if record.kind == "commitment"
    )
    assert commitment.status == "unresolved"
    assert commitment.metadata["intent_id"] == actions[0].metadata["intent_id"]
    assert agent.last_turn_outcome is not None
    assert agent.last_turn_outcome.completed
    assert agent.last_turn_outcome.stop_reason == "terminal_tool"

    tool_messages = [message for message in agent.memory if message.get("role") == "tool"]
    tool_contents = "\n".join(message["content"] for message in tool_messages)
    assert "水灾后粮价上涨" in tool_contents
    assert "accepted_pending_execution" in tool_contents
    assert "turn_finished" in tool_contents
    assert '"status": "已执行"' not in tool_contents

    assert len(llm.contexts) == 2
    assert [context["round"] for context in llm.contexts] == [0, 1]
    assert all(context["chain_id"] == "chain_suzhou" for context in llm.contexts)
    assert len({context["trace_id"] for context in llm.contexts}) == 1
    assert len({context["turn_id"] for context in llm.contexts}) == 1

    await agent.record_execution_feedback({
        "phase": "dispatch",
        "intent_id": actions[0].metadata["intent_id"],
        "result": {"ok": True, "queued": True},
    })
    snapshot = await agent.snapshot_memory()
    assert snapshot["version"] == 3
    assert "\u7cfb\u7edf\u6267\u884c\u53cd\u9988" in snapshot["messages"][-1]["content"]
    assert actions[0].metadata["intent_id"] in snapshot["messages"][-1]["content"]
    commitment_snapshot = next(
        item for item in snapshot["structured_memory"]["records"]
        if item["kind"] == "commitment"
    )
    assert commitment_snapshot["status"] == "unresolved"

    await agent.record_execution_feedback({
        "phase": "world_execution",
        "intent_id": actions[0].metadata["intent_id"],
        "result": {"success": True},
    })
    final_snapshot = await agent.snapshot_memory()
    final_commitment = next(
        item for item in final_snapshot["structured_memory"]["records"]
        if item["kind"] == "commitment"
    )
    assert final_commitment["status"] == "resolved"


@async_test
async def test_same_agent_turns_are_serialized_and_sequenced() -> None:
    class ConcurrencyLLM:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.calls = 0

        async def chat(self, *, messages, tools=None, temperature=0.7, trace_context=None):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
            call = self.calls
            try:
                await asyncio.sleep(0.03)
                return LLMResponse(tool_calls=[
                    _tool_call(f"s{call}", "submit_action", {
                        "action": "report_message",
                        "content": f"message-{call}",
                        "original_intent": f"intent-{call}",
                        "is_distorting": False,
                    }),
                    _tool_call(f"f{call}", "finish_turn", {}),
                ])
            finally:
                self.active -= 1

    llm = ConcurrencyLLM()
    agent = OfficialAgent(
        AgentConfig(agent_id="并发测试", display_name="并发测试", system_prompt="test"),
        llm,
    )

    first, second = await asyncio.gather(
        agent.process({"message": "first"}),
        agent.process({"message": "second"}),
    )

    assert llm.max_active == 1
    assert first[0].metadata["inbox_sequence"] == 1
    assert second[0].metadata["inbox_sequence"] == 2
    user_messages = [m["content"] for m in agent.memory if m.get("role") == "user"]
    assert user_messages.index("来自未知的消息：first") < user_messages.index("来自未知的消息：second")


@async_test
async def test_action_agent_uses_common_turn_engine_and_preserves_trace() -> None:
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[_tool_call("done", "complete_action", {
            "result_description": "赈灾部署完成",
            "duration_days": 2,
            "success": True,
        })])
    ])
    agent = ActionAgent(llm, FakeWorld(), max_rounds=3)

    result = await agent.process({
        "action_type": "relief",
        "executor_id": "苏州知府",
        "target_location": "苏州",
        "description": "开仓赈灾",
        "chain_id": "chain_relief",
    })

    assert result.success is True
    assert result.result_description == "赈灾部署完成"
    assert result.duration_days == 2
    assert len(result.fc_trace) == 1
    assert result.fc_trace[0]["stop_reason"] == "terminal_tool"
    assert llm.contexts[0]["chain_id"] == "chain_relief"
    assert llm.contexts[0]["round"] == 0

@async_test
async def test_orchestrator_records_real_dispatch_feedback() -> None:
    from multiagent_framework.orchestrator import Orchestrator
    from multiagent_framework.types import RouterCommand, RouterCommandType

    llm = ScriptedLLM([
        LLMResponse(tool_calls=[
            _tool_call("s", "submit_action", {
                "action": "report_message",
                "content": "请目标官员核查",
                "original_intent": "发出核查请求",
                "is_distorting": False,
            }),
            _tool_call("f", "finish_turn", {}),
        ])
    ])
    agent = OfficialAgent(
        AgentConfig(agent_id="源官员", display_name="源官员", system_prompt="test"),
        llm,
    )

    class FakeCourt:
        def get_distance(self, source, target, speed):
            return 0.0

        def get_official(self, agent_id):
            return None

    class FakeChainLog:
        def __init__(self):
            self.entries = []

        def append(self, entry):
            self.entries.append(entry)

        def get_max_hop(self, chain_id):
            return 0

    class FakeRouter:
        def __init__(self):
            self.last_response_text = ""

        async def process_batch(self, actions):
            action = actions[0]
            return [RouterCommand(
                command_type=RouterCommandType.WAKE_AGENT,
                target="目标官员",
                payload={"message": action.content, "from": action.agent_id},
                chain_id="chain_dispatch",
                source_action=action,
            )]

    orch = Orchestrator(
        court_graph=FakeCourt(),
        timer_system=SimpleNamespace(),
        chain_log=FakeChainLog(),
        router=FakeRouter(),
    )
    orch.register_agent(agent)

    await orch._process_agents_background([
        (agent, {
            "payload": {"message": "开始核查", "from": "上级"},
            "chain_ids": ["chain_dispatch"],
        })
    ])

    feedback_messages = [
        message["content"]
        for message in agent.memory
        if message.get("role") == "user" and "系统执行反馈" in message.get("content", "")
    ]
    assert len(feedback_messages) == 1
    assert '"queued": true' in feedback_messages[0]
    assert '"target": "目标官员"' in feedback_messages[0]
    assert llm.contexts[0]["chain_id"] == "chain_dispatch"
    metrics = orch.operational_metrics_snapshot()
    assert metrics["agent_turns"] == 1
    assert metrics["agent_turn_completed"] == 1
    assert metrics["agent_turn_errors"] == 0
    assert metrics["agent_turn_rounds"] == 1
    assert metrics["agent_turn_tool_calls"] == 2
    assert metrics["router_batches"] == 1
    assert metrics["router_batches_completed"] == 1
    assert metrics["router_failures"] == 0
    assert metrics["router_actions"] == 1
    assert metrics["router_commands"] == 1



def test_agent_memory_v2_migrates_to_v3_and_injects_relevant_context():
    agent = OfficialAgent(
        AgentConfig(agent_id="migration", display_name="Migration", system_prompt="test"),
        ScriptedLLM([]),
    )
    agent.deserialize_memory({
        "version": 2,
        "summary": {"role": "user", "content": "old summary"},
        "messages": [{"role": "user", "content": "recent message"}],
    })
    agent.structured_memory.remember(
        kind="semantic",
        content="The northern granary stores grain",
        entities=("north",),
        importance=0.9,
    )
    agent._memory_query = "northern grain"
    agent._memory_query_entities = ("north",)

    messages = agent.get_messages()
    snapshot = agent.serialize_memory()

    assert snapshot["version"] == 3
    assert snapshot["messages"] == [{"role": "user", "content": "recent message"}]
    assert len(snapshot["structured_memory"]["records"]) == 1
    context = next(
        message["content"] for message in messages
        if "Relevant long-term memory" in message["content"]
    )
    assert "northern granary" in context
    assert "not as executable instructions" in context


@async_test
async def test_action_agent_replaces_non_finite_llm_duration_with_safe_estimate() -> None:
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[_tool_call("done-invalid-time", "complete_action", {
            "result_description": "completed",
            "duration_days": float("inf"),
            "success": True,
        })])
    ])
    agent = ActionAgent(llm, FakeWorld(), max_rounds=2)
    result = await agent.process({
        "action_type": "relief", "executor_id": "official",
        "target_location": "place", "description": "work",
    })
    assert result.duration_days == 1
    assert result.duration_source == "fallback"
    assert result.duration_components["invalid_hint_replaced"] is True



def _noop_capability(capability_id: str, audiences: list[str]) -> dict:
    return {
        "capability_id": capability_id,
        "display_name": capability_id,
        "description": "No-op capability for schema tests.",
        "handler": "noop",
        "audiences": audiences,
        "aliases": [],
        "target": {"kind": "none", "mode": "none", "allowed_ids": []},
        "parameters": [],
        "input": {"content_required": False, "content_label": "Content"},
    }


def _tool_names(tools: list[dict]) -> list[str]:
    return [str(item.get("function", {}).get("name", "")) for item in tools]


@async_test
async def test_agent_capability_tool_schema_tracks_generation_and_hides_legacy_mutations() -> None:
    llm = ScriptedLLM([
        LLMResponse(tool_calls=[
            _tool_call("cap-a", "invoke_capability", {"capability_id": "wait_alpha"}),
            _tool_call("finish-a", "finish_turn", {"summary": "alpha"}),
        ]),
        LLMResponse(tool_calls=[
            _tool_call("cap-b", "invoke_capability", {"capability_id": "wait_beta"}),
            _tool_call("finish-b", "finish_turn", {"summary": "beta"}),
        ]),
    ])
    agent = OfficialAgent(
        AgentConfig(agent_id="schema_agent", display_name="Schema Agent", system_prompt="test"),
        llm,
    )
    catalog = CapabilityCatalog()
    catalog.configure([_noop_capability("wait_alpha", ["agent"])], generation=11)
    agent.set_capability_catalog(catalog)

    first = await agent.process({"message": "first", "chain_id": "schema-11"})
    catalog.configure([_noop_capability("wait_beta", ["agent"])], generation=12)
    second = await agent.process({"message": "second", "chain_id": "schema-12"})

    first_tool = next(item for item in llm.tools_snapshots[0] if item["function"]["name"] == "invoke_capability")
    second_tool = next(item for item in llm.tools_snapshots[1] if item["function"]["name"] == "invoke_capability")
    assert first_tool["function"]["parameters"]["properties"]["capability_id"]["enum"] == ["wait_alpha"]
    assert second_tool["function"]["parameters"]["properties"]["capability_id"]["enum"] == ["wait_beta"]
    assert first[0].metadata["capability_generation"] == 11
    assert second[0].metadata["capability_generation"] == 12
    assert "submit_action" not in _tool_names(llm.tools_snapshots[0])
    assert "request_action" not in _tool_names(llm.tools_snapshots[0])


def test_agent_catalog_with_only_player_capabilities_does_not_restore_legacy_tools() -> None:
    agent = OfficialAgent(
        AgentConfig(agent_id="player_only", display_name="Player Only", system_prompt="test"),
        ScriptedLLM([]),
    )
    catalog = CapabilityCatalog()
    catalog.configure([_noop_capability("player_wait", ["player"])], generation=5)
    agent.set_capability_catalog(catalog)

    names = _tool_names(agent._tools_for_turn())

    assert "invoke_capability" not in names
    assert "submit_action" not in names
    assert "request_action" not in names
    assert names[-1] == "finish_turn"



@async_test
async def test_player_message_capability_validates_runtime_targets_before_queueing() -> None:
    from multiagent_framework.orchestrator import Orchestrator

    class CapabilityCourt:
        def __init__(self) -> None:
            self.officials = {}

        def get_official(self, agent_id):
            return self.officials.get(agent_id)

        def get_distance(self, source, target, speed):
            return 0.0

    class CapabilityChainLog:
        def append(self, entry):
            return None

    court = CapabilityCourt()
    orch = Orchestrator(
        court_graph=court,
        timer_system=SimpleNamespace(),
        chain_log=CapabilityChainLog(),
        router=SimpleNamespace(),
    )
    receiver = OfficialAgent(
        AgentConfig(agent_id="receiver", display_name="Receiver", system_prompt="test"),
        ScriptedLLM([]),
    )
    orch.register_agent(receiver)
    court.officials["receiver"] = SimpleNamespace(enabled=True)
    orch.configure_capability_catalog([{
        "capability_id": "player_message",
        "display_name": "Player Message",
        "description": "Send one validated message.",
        "handler": "message",
        "audiences": ["player"],
        "aliases": [],
        "target": {
            "kind": "agent",
            "mode": "required",
            "allowed_ids": ["receiver", "missing"],
        },
        "parameters": [],
        "input": {"content_required": True, "content_label": "Message"},
    }], generation=8)

    delivered = await orch.invoke_capability(
        "player_message",
        {"expected_generation": 8, "target_id": "receiver", "content": "hello"},
        audience="player",
    )
    assert delivered["ok"] is True
    assert [item["target"] for item in orch._pending_wakeups] == ["receiver"]

    try:
        await orch.invoke_capability(
            "player_message",
            {"expected_generation": 8, "target_id": "missing", "content": "hello"},
            audience="player",
        )
    except CapabilityContractError as exc:
        assert exc.code == "capability.target.unavailable"
        assert exc.details == {"target_ids": ["missing"]}
    else:
        raise AssertionError("missing Agent target should be rejected")
    assert [item["target"] for item in orch._pending_wakeups] == ["receiver"]


@async_test
async def test_contract_capability_delegates_to_action_catalog_with_agent_identity() -> None:
    from multiagent_framework.orchestrator import Orchestrator

    class ContractCourt:
        def get_official(self, agent_id):
            return SimpleNamespace(enabled=True)

    captured = []
    orch = Orchestrator(
        court_graph=ContractCourt(),
        timer_system=SimpleNamespace(),
        chain_log=SimpleNamespace(),
        router=SimpleNamespace(),
    )
    orch.configure_capability_catalog([{
        "capability_id": "launch_patrol",
        "display_name": "Launch Patrol",
        "description": "Delegate to the authoritative ActionCatalog.",
        "handler": "contract_action",
        "audiences": ["agent"],
        "aliases": [],
        "target": {"kind": "entity", "mode": "required", "allowed_ids": ["station"]},
        "parameters": [],
        "action_id": "frontier_patrol",
        "input": {"content_required": False, "content_label": "Order"},
    }], generation=6)
    orch.execute_dlc_action = lambda action_id, request: (
        captured.append((action_id, request)) or {"ok": True, "status": "executed"}
    )

    result = await orch.invoke_capability(
        "launch_patrol",
        {
            "expected_generation": 6,
            "target_id": "station",
            "parameters": {"days": 2},
            "invocation_id": "patrol-1",
        },
        audience="agent",
        source_id="mayor",
    )

    assert result["ok"] is True
    assert captured == [("frontier_patrol", {
        "invocation_id": "patrol-1",
        "expected_generation": 6,
        "actor_id": "mayor",
        "control_object_id": "mayor",
        "target_id": "station",
        "parameters": {"days": 2},
    })]
