from __future__ import annotations

import asyncio
import json

from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.capability_contract import CapabilityCatalog
from multiagent_framework.court_graph import CourtGraph, Official
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.router import MAX_AMBIGUOUS_BATCH, MAX_CANDIDATES_PER_ACTION, RouterAgent
from multiagent_framework.types import AgentAction, RouterCommandType


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


class FakeLLM:
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls = 0
        self.messages: list[list[dict]] = []
        self.contexts: list[dict] = []

    async def chat(self, *, messages, tools=None, temperature=0.7, trace_context=None):
        self.calls += 1
        self.messages.append([dict(item) for item in messages])
        self.contexts.append(dict(trace_context or {}))
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


def _court(tmp_path, *, many: int = 0) -> CourtGraph:
    court = CourtGraph(str(tmp_path / "router.db"))
    officials = [
        Official("emperor", "Emperor", 15, "capital", "player", "official", None, "steady"),
        Official("minister", "Minister", 10, "capital", "official", "official", "emperor", "steady"),
        Official("prefect", "Prefect", 5, "suzhou", "official", "official", "minister", "steady"),
        Official("magistrate", "Magistrate", 1, "wuxian", "official", "official", "prefect", "steady"),
        Official("isolated", "Isolated", 3, "island", "official", "official", None, "steady"),
    ]
    for index in range(many):
        officials.append(Official(
            f"extra_{index:02d}", f"Extra {index:02d}", 2 + index % 6, "capital",
            "official", "official", "minister", "steady",
        ))
    court.bulk_load_officials(officials)
    return court


def _action(**overrides) -> AgentAction:
    values = {
        "agent_id": "magistrate",
        "action": "report_message",
        "content": "grain prices are rising",
        "original_intent": "report facts",
        "urgency": "normal",
        "metadata": {"intent_id": "intent_1", "chain_id": "chain_origin", "hop_count": 2},
    }
    values.update(overrides)
    return AgentAction(**values)


def test_direct_superior_report_does_not_call_llm(tmp_path) -> None:
    llm = FakeLLM()
    router = RouterAgent(llm, _court(tmp_path))
    commands = asyncio.run(router.process_batch([_action()]))
    assert llm.calls == 0
    assert len(commands) == 1
    assert commands[0].command_type == RouterCommandType.WAKE_AGENT
    assert commands[0].target == "prefect"
    assert commands[0].chain_id == "chain_origin"
    assert commands[0].intent_id == "intent_1"
    assert commands[0].metadata["route_strategy"] == "deterministic"


def test_clear_local_policy_does_not_call_llm(tmp_path) -> None:
    llm = FakeLLM()
    router = RouterAgent(llm, _court(tmp_path))
    action = _action(action="do_something", content="inspect the granary", metadata={
        "intent_id": "intent_policy", "chain_id": "chain_policy", "target_location": "wuxian",
    })
    commands = asyncio.run(router.process_batch([action]))
    assert llm.calls == 0
    assert len(commands) == 1
    assert commands[0].command_type == RouterCommandType.EXECUTE_POLICY
    assert commands[0].target == "magistrate"
    assert commands[0].payload["location"] == "wuxian"


def test_ambiguous_action_calls_llm_and_preserves_chain(tmp_path) -> None:
    response = LLMResponse(tool_calls=[_tool_call("wake", "wake_agent", {
        "target": "prefect", "message": "request mediation", "from_agent": "magistrate", "urgency": "normal",
    })])
    llm = FakeLLM([response])
    router = RouterAgent(llm, _court(tmp_path))
    action = _action(action="negotiate_customs", content="request mediation")
    commands = asyncio.run(router.process_batch([action]))
    assert llm.calls == 1
    assert llm.contexts[0]["task_type"] == "router.ambiguous"
    assert len(commands) == 1
    assert commands[0].chain_id == "chain_origin"
    assert commands[0].source_action is action
    assert commands[0].metadata["route_strategy"] == "llm"


def test_invalid_and_unreachable_targets_go_to_dead_letter(tmp_path) -> None:
    router = RouterAgent(FakeLLM(), _court(tmp_path))
    invalid = _action(metadata={"intent_id": "bad", "chain_id": "c_bad", "target_hint": "missing"})
    unreachable = _action(metadata={"intent_id": "far", "chain_id": "c_far", "target_hint": "isolated"})
    assert asyncio.run(router.process_batch([invalid])) == []
    assert asyncio.run(router.process_batch([unreachable])) == []
    statuses = [entry["status"] for entry in router.dead_letters]
    assert "invalid_target" in statuses
    assert "unreachable" in statuses


def test_duplicate_and_repeated_edge_are_rejected(tmp_path) -> None:
    router = RouterAgent(FakeLLM(), _court(tmp_path))
    first = _action()
    duplicate = _action()
    loop = _action(metadata={"intent_id": "intent_2", "chain_id": "chain_origin", "hop_count": 3})
    assert len(asyncio.run(router.process_batch([first]))) == 1
    assert asyncio.run(router.process_batch([duplicate])) == []
    assert asyncio.run(router.process_batch([loop])) == []
    statuses = [entry["status"] for entry in router.dead_letters]
    assert "duplicate" in statuses
    assert "loop_detected" in statuses


def test_ambiguous_batches_and_candidate_context_are_bounded(tmp_path) -> None:
    court = _court(tmp_path, many=30)
    candidate_ids = court.get_routing_candidates("magistrate", limit=MAX_CANDIDATES_PER_ACTION)
    assert len(candidate_ids) == MAX_CANDIDATES_PER_ACTION

    responses = [LLMResponse(content="no command") for _ in range(2)]
    llm = FakeLLM(responses)
    router = RouterAgent(llm, court)
    actions = [
        _action(action=f"custom_{index}", metadata={"intent_id": f"i{index}", "chain_id": f"c{index}"})
        for index in range(MAX_AMBIGUOUS_BATCH + 1)
    ]
    asyncio.run(router.process_batch(actions))
    assert llm.calls == 2
    first_prompt = llm.messages[0][-1]["content"]
    # Each action section can list at most the configured number of candidates.
    assert first_prompt.count("rank=") <= MAX_AMBIGUOUS_BATCH * MAX_CANDIDATES_PER_ACTION
    assert router.last_stats["batch_limit"] == MAX_AMBIGUOUS_BATCH
    assert router.last_stats["candidate_limit"] == MAX_CANDIDATES_PER_ACTION


def test_agent_to_router_chain_continuity(tmp_path) -> None:
    agent_llm = FakeLLM([
        LLMResponse(tool_calls=[
            _tool_call("submit", "submit_action", {
                "action": "report_message", "content": "urgent report", "original_intent": "report facts",
                "is_distorting": False, "urgency": "urgent",
            }),
            _tool_call("finish", "finish_turn", {"summary": "reported"}),
        ])
    ])
    agent = OfficialAgent(AgentConfig(agent_id="magistrate", display_name="Magistrate", system_prompt="test"), agent_llm)
    court = _court(tmp_path)
    agent.bind_runtime(court=court)
    actions = asyncio.run(agent.process({"message": "report now", "chain_id": "chain_e2e", "chain_ids": ["chain_e2e"]}))
    router = RouterAgent(FakeLLM(), court)
    commands = asyncio.run(router.process_batch(actions))
    assert actions[0].metadata["chain_id"] == "chain_e2e"
    assert actions[0].metadata["parent_chain_ids"] == ["chain_e2e"]
    assert commands[0].chain_id == "chain_e2e"
    assert commands[0].intent_id == actions[0].metadata["intent_id"]



def _message_capability(capability_id: str, *, aliases: list[str] | None = None) -> dict:
    return {
        "capability_id": capability_id,
        "display_name": capability_id,
        "description": "Route a bounded message target.",
        "handler": "message",
        "audiences": ["agent", "router"],
        "aliases": list(aliases or []),
        "target": {"kind": "agent", "mode": "required", "allowed_ids": []},
        "parameters": [],
        "input": {"content_required": True, "content_label": "Message"},
    }


def test_configured_catalog_rejects_unknown_capability_without_llm(tmp_path) -> None:
    llm = FakeLLM()
    router = RouterAgent(llm, _court(tmp_path))
    catalog = CapabilityCatalog()
    catalog.configure([_message_capability("known_report")], generation=3)
    router.configure_capability_catalog(catalog)

    commands = asyncio.run(router.process_batch([
        _action(action="invented_capability", metadata={
            "intent_id": "unknown-capability",
            "chain_id": "unknown-chain",
            "capability_generation": 3,
        })
    ]))

    assert commands == []
    assert llm.calls == 0
    assert router.dead_letters[-1]["status"] == "unknown_capability"


def test_router_llm_cannot_replace_source_capability_id(tmp_path) -> None:
    court = _court(tmp_path, many=2)
    response = LLMResponse(tool_calls=[_tool_call("route", "route_capability", {
        "capability_id": "other_message",
        "target_id": "extra_00",
        "intent_id": "stable-capability",
    })])
    llm = FakeLLM([response])
    router = RouterAgent(llm, court)
    catalog = CapabilityCatalog()
    catalog.configure([
        _message_capability("stable_message"),
        _message_capability("other_message"),
    ], generation=4)
    router.configure_capability_catalog(catalog)
    action = _action(
        action="stable_message",
        metadata={
            "intent_id": "stable-capability",
            "chain_id": "stable-chain",
            "capability_id": "stable_message",
            "capability_generation": 4,
            "target_ids": ["Extra"],
        },
    )

    commands = asyncio.run(router.process_batch([action]))

    assert commands == []
    assert llm.calls == 1
    assert router.dead_letters[-1]["status"] == "capability_changed"


def test_capability_target_text_splits_full_width_comma_but_preserves_question_mark() -> None:
    targets = RouterAgent._raw_capability_targets({
        "target_ids": "guard?station\uFF0Cprefect,minister",
    })
    assert targets == ["guard?station", "prefect", "minister"]
