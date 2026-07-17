"""Tests for isolated real World/Memory trajectory tool sessions."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.trajectory_evaluation import TrajectoryEvaluator, load_trajectory_suite
from multiagent_framework.trajectory_matrix import TrajectoryMatrixRunner
from multiagent_framework.trajectory_runtime import (
    IsolatedTrajectoryRuntimeFactory,
    TrajectoryRuntimeError,
    _TrajectoryRuntimeSession,
)


LIVE_SUITE = "tests/evals/daming_live_trajectory_scenarios.json"


def _scenario(scenario_id):
    suite = load_trajectory_suite(LIVE_SUITE)
    return deepcopy(next(item for item in suite["scenarios"] if item["id"] == scenario_id))


async def _open_and_inspect(factory, scenario):
    async with factory(scenario, scenario["live"]) as execute:
        return await execute(scenario, "inspect_inventory", {"scope": "anchored_transfer"}, {})


def test_isolated_world_session_executes_atomic_anchored_transfer_and_resets():
    scenario = _scenario("live-action-value-conservation")
    factory = IsolatedTrajectoryRuntimeFactory()

    async def run():
        async with factory(scenario, scenario["live"]) as execute:
            inspected = await execute(
                scenario, "inspect_inventory", {"scope": "anchored_transfer"}, {}
            )
            transferred = await execute(scenario, "apply_world_command", {
                "command_type": "transfer_resource",
                "source_entity": inspected["source_entity"],
                "target_entity": inspected["target_entity"],
                "resource_id": inspected["resource_id"],
                "quantity": 10,
                "unit_base_value": inspected["unit_base_value"],
                "total_base_value": 20,
                "evidence_generation": inspected["snapshot_generation"],
            }, {})
            after = await execute(
                scenario, "inspect_inventory", {"scope": "anchored_transfer"}, {}
            )
        fresh = await _open_and_inspect(factory, scenario)
        return inspected, transferred, after, fresh

    inspected, transferred, after, fresh = asyncio.run(run())

    assert inspected["available_quantity"] == 100
    assert transferred["ok"] and transferred["value_conserved"]
    assert transferred["source_quantity"] == 90
    assert transferred["target_quantity"] == 10
    assert transferred["_evaluation_evidence"]["resource"] == {
        "before_base_value": 200,
        "after_base_value": 200,
        "external_delta": 0,
        "tolerance": 1e-6,
    }
    assert after["available_quantity"] == 90
    assert fresh["available_quantity"] == 100


def test_memory_tools_use_structured_retrieval_and_ground_terminal_receipts():
    recall_scenario = _scenario("live-agent-memory-commitment")
    compression_scenario = _scenario("live-memory-compression-commitment")
    factory = IsolatedTrajectoryRuntimeFactory()

    async def run():
        async with factory(recall_scenario, recall_scenario["live"]) as execute:
            recalled = await execute(
                recall_scenario, "recall_memory", {"query": "tax commitment"}, {}
            )
            rejected = await execute(recall_scenario, "submit_report", {
                "commitment_id": "invented-id",
                "status": "resolved",
                "summary": "not grounded",
            }, {})
            accepted = await execute(recall_scenario, "submit_report", {
                "commitment_id": recalled["memory_ids"][0],
                "status": "resolved",
                "summary": "grounded",
            }, {})
        async with factory(compression_scenario, compression_scenario["live"]) as execute:
            inspected = await execute(
                compression_scenario,
                "inspect_memory",
                {"scope": "unresolved_commitments"},
                {},
            )
            memory_id = inspected["memory_ids"][0]
            summarized = await execute(compression_scenario, "submit_memory_summary", {
                "preserved_ids": [memory_id],
                "commitment_id": memory_id,
                "status": "retained",
                "summary": "preserved",
            }, {})
        return recalled, rejected, accepted, inspected, summarized

    recalled, rejected, accepted, inspected, summarized = asyncio.run(run())

    assert recalled["memory_ids"] == ["commitment-tax-v1"]
    assert recalled["records"][0]["state"] == "resolved"
    assert not rejected["ok"] and rejected["error_code"] == "report_not_grounded"
    assert accepted["_evaluation_evidence"]["commitment_status"] == "resolved"
    assert inspected["memory_ids"] == ["commitment-grain-v1"]
    assert all(item["kind"] == "commitment" for item in inspected["records"])
    assert summarized["_evaluation_evidence"]["commitment_status"] == "retained"


def test_fixture_schema_rejects_paths_unknown_fields_and_unallowlisted_tools():
    factory = IsolatedTrajectoryRuntimeFactory()
    unsafe = {
        "schema_version": "1.0",
        "database_path": "production.sqlite3",
    }

    async def open_unsafe():
        async with factory({}, {"runtime_fixture": unsafe}):
            pass

    with pytest.raises(TrajectoryRuntimeError, match="unsupported fields"):
        asyncio.run(open_unsafe())

    scenario = _scenario("live-router-ambiguous-intent")

    async def call_unknown():
        async with factory(scenario, scenario["live"]) as execute:
            return await execute(scenario, "import_handler", {}, {})

    result = asyncio.run(call_unknown())
    assert result == {
        "ok": False,
        "accepted": False,
        "error_code": "tool_not_allowlisted",
        "error": "runtime tool rejected",
    }


def test_fixture_accepts_unicode_dlc_identifiers():
    fixture = {
        "schema_version": "1.0",
        "world": {
            "generation": 2,
            "resources": [{
                "name": "\u7cae\u5238",
                "unit": "\u5f20",
                "kind": "asset",
                "anchor_rate": 1.5,
            }],
            "entities": [
                {"id": "\u7532\u4ed3", "type": "\u5730\u70b9", "name": "\u7532\u4ed3"},
                {"id": "\u4e59\u4ed3", "type": "\u5730\u70b9", "name": "\u4e59\u4ed3"},
            ],
            "inventory": [
                {"entity_id": "\u7532\u4ed3", "resource_id": "\u7cae\u5238", "quantity": 8},
                {"entity_id": "\u4e59\u4ed3", "resource_id": "\u7cae\u5238", "quantity": 0},
            ],
            "transfer_scopes": [{
                "scope": "\u951a\u5b9a\u8f6c\u79fb",
                "source_entity": "\u7532\u4ed3",
                "target_entity": "\u4e59\u4ed3",
                "resource_id": "\u7cae\u5238",
            }],
        },
    }
    factory = IsolatedTrajectoryRuntimeFactory()

    async def inspect():
        async with factory({}, {"runtime_fixture": fixture}) as execute:
            return await execute(
                {}, "inspect_inventory", {"scope": "\u951a\u5b9a\u8f6c\u79fb"}, {}
            )

    result = asyncio.run(inspect())
    assert result["source_entity"] == "\u7532\u4ed3"
    assert result["resource_id"] == "\u7cae\u5238"
    assert result["source_base_value_total"] == 12


def test_router_tool_executes_production_router_and_resets_idempotency_per_session():
    scenario = _scenario("live-router-ambiguous-intent")
    fixture = scenario["live"]["runtime_fixture"]["router"]
    fixture["agents"].append({
        "id": "isolated_office",
        "name": "Isolated Office",
        "rank": 5,
        "location_id": "remote",
        "role": "isolated",
        "agent_type": "institution",
        "superior_id": None,
        "personality_seed": "steady",
        "enabled": True,
    })
    factory = IsolatedTrajectoryRuntimeFactory()

    async def run():
        async with factory(scenario, scenario["live"]) as execute:
            accepted = await execute(scenario, "route_intent", {
                "target": "hubu",
                "reason_code": "fiscal_reserve",
            }, {})
            duplicate = await execute(scenario, "route_intent", {
                "target": "hubu",
                "reason_code": "fiscal_reserve",
            }, {})
            outside = await execute(scenario, "route_intent", {
                "target": "ghost_office",
                "reason_code": "fiscal_reserve",
            }, {})
            self_route = await execute(scenario, "route_intent", {
                "target": "petition_intake",
                "reason_code": "fiscal_reserve",
            }, {})
            unreachable = await execute(scenario, "route_intent", {
                "target": "isolated_office",
                "reason_code": "fiscal_reserve",
            }, {})
        async with factory(scenario, scenario["live"]) as execute:
            fresh = await execute(scenario, "route_intent", {
                "target": "hubu",
                "reason_code": "fiscal_reserve",
            }, {})
        return accepted, duplicate, outside, self_route, unreachable, fresh

    accepted, duplicate, outside, self_route, unreachable, fresh = asyncio.run(run())

    assert accepted["accepted"] is True
    assert accepted["target"] == "hubu"
    assert accepted["command_type"] == "wake_agent"
    assert accepted["route_strategy"] == "llm"
    assert accepted["intent_id"] == "router-eval-intent"
    assert accepted["chain_id"] == "router-eval-chain"
    for rejected in (duplicate, outside, self_route, unreachable):
        assert rejected == {
            "ok": False,
            "accepted": False,
            "error_code": "router_route_rejected",
            "error": "runtime tool rejected",
        }
        assert "dead_letter" not in json.dumps(rejected)
    assert fresh == accepted


def test_router_delivery_receipt_comes_from_real_outbox_and_restore_replays_pending(tmp_path):
    scenario = _scenario("live-router-ambiguous-intent")
    fixture = scenario["live"]["runtime_fixture"]
    session = _TrajectoryRuntimeSession.open(
        fixture, database_path=tmp_path / "first-world.sqlite3",
    )

    async def run():
        accepted = await session.execute(scenario, "route_intent", {
            "target": "hubu",
            "reason_code": "fiscal_reserve",
        }, {})
        duplicate = await session.execute(scenario, "route_intent", {
            "target": "hubu",
            "reason_code": "fiscal_reserve",
        }, {})
        rejected = await session.execute(scenario, "route_intent", {
            "target": "ghost_office",
            "reason_code": "fiscal_reserve",
        }, {})
        return accepted, duplicate, rejected

    accepted, duplicate, rejected = asyncio.run(run())
    runtime = session.router_runtime
    assert runtime is not None
    outbox = runtime.orchestrator._pending_wakeups
    assert accepted["delivery_channel"] == "wakeup_outbox"
    assert accepted["delivery_status"] == "pending"
    assert accepted["delivery_attempts"] == 0
    assert outbox.total_count == 1
    entry = outbox.get(accepted["delivery_id"])
    assert entry is not None
    assert entry["target"] == accepted["target"]
    assert entry["chain_id"] == accepted["chain_id"]
    assert entry["status"] == accepted["delivery_status"]
    assert duplicate["error_code"] == "router_route_rejected"
    assert rejected["error_code"] == "router_route_rejected"
    assert outbox.total_count == 1

    claimed = outbox.claim_pending()
    assert [item["delivery_id"] for item in claimed] == [accepted["delivery_id"]]
    assert outbox.inflight_count == 1
    snapshot = runtime.orchestrator.runtime_snapshot()

    restored_session = _TrajectoryRuntimeSession.open(
        fixture, database_path=tmp_path / "restored-world.sqlite3",
    )
    restored_runtime = restored_session.router_runtime
    assert restored_runtime is not None
    restored_runtime.orchestrator.restore_runtime_snapshot(snapshot)
    restored_outbox = restored_runtime.orchestrator._pending_wakeups
    restored_entry = restored_outbox.get(accepted["delivery_id"])
    assert restored_entry is not None
    assert restored_entry["status"] == "pending"
    assert restored_entry["attempts"] == 1
    assert restored_outbox.pending_count == 1
    assert restored_outbox.inflight_count == 0


def test_router_delivery_uses_real_timer_for_remote_reachable_target(tmp_path):
    scenario = _scenario("live-router-ambiguous-intent")
    fixture = scenario["live"]["runtime_fixture"]
    fixture["router"]["agents"].append({
        "id": "regional_finance",
        "name": "Regional Finance Office",
        "rank": 8,
        "location_id": "remote",
        "role": "finance",
        "agent_type": "institution",
        "superior_id": "central_router",
        "personality_seed": "steady",
        "enabled": True,
    })
    session = _TrajectoryRuntimeSession.open(
        fixture, database_path=tmp_path / "world.sqlite3",
    )

    result = asyncio.run(session.execute(scenario, "route_intent", {
        "target": "regional_finance",
        "reason_code": "fiscal_reserve",
    }, {}))

    runtime = session.router_runtime
    assert runtime is not None
    timers = runtime.orchestrator.timers.get_in_transit()
    assert result["accepted"] is True
    assert result["delivery_channel"] == "timer"
    assert result["delivery_status"] == "scheduled"
    assert result["delivery_attempts"] == 0
    assert len(timers) == 1
    assert timers[0].timer_id == result["delivery_id"]
    assert timers[0].target_agent == "regional_finance"
    assert runtime.orchestrator._pending_wakeups.total_count == 0


def test_router_delivery_failure_is_fixed_and_secret_free(tmp_path):
    scenario = _scenario("live-router-ambiguous-intent")
    session = _TrajectoryRuntimeSession.open(
        scenario["live"]["runtime_fixture"],
        database_path=tmp_path / "world.sqlite3",
    )
    runtime = session.router_runtime
    assert runtime is not None

    async def reject_execution(commands):
        return [(commands[0], {
            "ok": False,
            "error": "provider-secret prompt body C:/private/runtime.sqlite3",
        })]

    runtime.orchestrator._execute_commands = reject_execution
    result = asyncio.run(session.execute(scenario, "route_intent", {
        "target": "hubu",
        "reason_code": "fiscal_reserve",
    }, {}))

    assert result == {
        "ok": False,
        "accepted": False,
        "error_code": "orchestrator_delivery_rejected",
        "error": "runtime tool rejected",
    }
    serialized = json.dumps(result, ensure_ascii=False)
    assert "provider-secret" not in serialized
    assert "prompt body" not in serialized
    assert "runtime.sqlite3" not in serialized
    assert runtime.orchestrator._pending_wakeups.total_count == 0


def test_router_fixture_rejects_invalid_graphs_sources_and_fields():
    base = _scenario("live-router-ambiguous-intent")["live"]["runtime_fixture"]
    invalid_fixtures = []

    duplicate = deepcopy(base)
    duplicate["router"]["agents"][2]["id"] = "petition_intake"
    invalid_fixtures.append(duplicate)

    unknown_superior = deepcopy(base)
    unknown_superior["router"]["agents"][2]["superior_id"] = "missing_parent"
    invalid_fixtures.append(unknown_superior)

    cycle = deepcopy(base)
    cycle["router"]["agents"][0]["superior_id"] = "hubu"
    invalid_fixtures.append(cycle)

    disabled_source = deepcopy(base)
    disabled_source["router"]["agents"][1]["enabled"] = False
    invalid_fixtures.append(disabled_source)

    unknown_field = deepcopy(base)
    unknown_field["router"]["provider_url"] = "https://should-not-be-accepted.invalid"
    invalid_fixtures.append(unknown_field)

    duplicate_location = deepcopy(base)
    duplicate_location["router"]["locations"].append(
        deepcopy(duplicate_location["router"]["locations"][0])
    )
    invalid_fixtures.append(duplicate_location)

    undeclared_location = deepcopy(base)
    undeclared_location["router"]["agents"][2]["location_id"] = "missing_location"
    invalid_fixtures.append(undeclared_location)

    unsafe_location = deepcopy(base)
    unsafe_location["router"]["locations"][0]["database_path"] = "C:/private/world.sqlite3"
    invalid_fixtures.append(unsafe_location)

    async def open_fixture(fixture):
        async with IsolatedTrajectoryRuntimeFactory()({}, {"runtime_fixture": fixture}):
            pass

    for fixture in invalid_fixtures:
        with pytest.raises(TrajectoryRuntimeError):
            asyncio.run(open_fixture(fixture))


class _RouterLLM:
    async def chat(self, **_kwargs):
        return LLMResponse(
            tool_calls=[{
                "id": "route-1",
                "function": {
                    "name": "route_intent",
                    "arguments": json.dumps({
                        "target": "hubu",
                        "reason_code": "fiscal_reserve",
                    }),
                },
            }],
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
        )


class _RouterGateway:
    def __init__(self, *, strict=False):
        self.strict = strict
        self.profiles = {
            "alpha": {
                "profile": "alpha",
                "provider": "test-provider",
                "model": "test-model",
                "supports_tools": True,
            }
        }

    def has_profile(self, profile):
        return profile in self.profiles

    def profile_metadata(self, profile):
        return dict(self.profiles[profile])

    def candidates(self, _task, profile_override="", *, strict_profile=False):
        if self.strict:
            assert strict_profile
            return [profile_override] if profile_override in self.profiles else []
        return ["alpha"]

    def credentials_available(self, _task, _profile_override="", *, strict_profile=False):
        if self.strict:
            assert strict_profile
        return True

    def for_task(self, _task, **kwargs):
        if self.strict:
            assert kwargs.get("strict_profile") is True
        return _RouterLLM()


def test_evaluator_routes_live_scenario_through_production_router():
    scenario = _scenario("live-router-ambiguous-intent")
    suite = {
        "schema_version": "1.0",
        "suite_id": "runtime-router-suite",
        "minimum_score": 100,
        "scenarios": [scenario],
    }
    report = asyncio.run(TrajectoryEvaluator(
        gateway=_RouterGateway(),
        live_tool_session_factory=IsolatedTrajectoryRuntimeFactory(),
    ).evaluate_suite(suite, mode="live"))

    assert report.passed
    assert report.metrics["routing_accuracy"] == 1
    assert all(item.passed for item in report.scenarios[0].checks)
    assert "runtime_fixture" not in json.dumps(report.to_dict(), ensure_ascii=False)


def test_matrix_repetitions_open_fresh_router_sessions():
    scenario = _scenario("live-router-ambiguous-intent")
    suite = {
        "schema_version": "1.0",
        "suite_id": "runtime-router-matrix-suite",
        "minimum_score": 100,
        "scenarios": [scenario],
    }
    matrix = {
        "schema_version": "1.0",
        "matrix_id": "runtime-router-isolation-matrix",
        "tasks": {"router.ambiguous": ["alpha"]},
        "repetitions": 2,
        "cohorts": ["daming-core"],
        "minimum_review_coverage": 1.0,
    }
    result = asyncio.run(TrajectoryMatrixRunner(
        gateway=_RouterGateway(strict=True),
        live_tool_session_factory=IsolatedTrajectoryRuntimeFactory(),
    ).evaluate(matrix, suite))

    assert result.passed
    assert len(result.cells) == 1
    assert result.cells[0].runs == 2
    assert result.cells[0].passed_runs == 2
    assert result.cells[0].routing_accuracy == 1


class _ActionLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            name = "inspect_inventory"
            arguments = {"scope": "anchored_transfer"}
        else:
            name = "apply_world_command"
            arguments = {
                "command_type": "transfer_resource",
                "source_entity": "treasury",
                "target_entity": "relief_granary",
                "resource_id": "grain_token",
                "quantity": 10,
                "unit_base_value": 2,
                "total_base_value": 20,
                "evidence_generation": 4,
            }
        return LLMResponse(
            tool_calls=[{
                "id": f"call-{self.calls}",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
        )


class _ActionGateway:
    def __init__(self, *, strict=False):
        self.strict = strict
        self.profiles = {
            "alpha": {
                "profile": "alpha",
                "provider": "test-provider",
                "model": "test-model",
                "supports_tools": True,
            }
        }

    def has_profile(self, profile):
        return profile in self.profiles

    def profile_metadata(self, profile):
        return dict(self.profiles[profile])

    def candidates(self, _task, profile_override="", *, strict_profile=False):
        if self.strict:
            assert strict_profile
            return [profile_override] if profile_override in self.profiles else []
        return ["alpha"]

    def credentials_available(self, _task, _profile_override="", *, strict_profile=False):
        if self.strict:
            assert strict_profile
        return True

    def for_task(self, _task, **kwargs):
        if self.strict:
            assert kwargs.get("strict_profile") is True
        return _ActionLLM()


def test_evaluator_consumes_runtime_evidence_without_scripted_tool_results():
    scenario = _scenario("live-action-value-conservation")
    suite = {
        "schema_version": "1.0",
        "suite_id": "runtime-world-suite",
        "minimum_score": 100,
        "scenarios": [scenario],
    }
    report = asyncio.run(TrajectoryEvaluator(
        gateway=_ActionGateway(),
        live_tool_session_factory=IsolatedTrajectoryRuntimeFactory(),
    ).evaluate_suite(suite, mode="live"))

    assert report.passed
    assert report.metrics["resource_conservation_rate"] == 1
    assert "runtime_fixture" not in json.dumps(report.to_dict(), ensure_ascii=False)
    assert all(item.passed for item in report.scenarios[0].checks)


def test_matrix_repetitions_open_fresh_world_sessions():
    scenario = _scenario("live-action-value-conservation")
    for item in scenario["live"]["runtime_fixture"]["world"]["inventory"]:
        if item["entity_id"] == "treasury":
            item["quantity"] = 10
    suite = {
        "schema_version": "1.0",
        "suite_id": "runtime-matrix-suite",
        "minimum_score": 100,
        "scenarios": [scenario],
    }
    matrix = {
        "schema_version": "1.0",
        "matrix_id": "runtime-isolation-matrix",
        "tasks": {"action.simulation": ["alpha"]},
        "repetitions": 2,
        "cohorts": ["daming-core"],
        "minimum_review_coverage": 1.0,
    }
    result = asyncio.run(TrajectoryMatrixRunner(
        gateway=_ActionGateway(strict=True),
        live_tool_session_factory=IsolatedTrajectoryRuntimeFactory(),
    ).evaluate(matrix, suite))

    assert result.passed
    assert len(result.cells) == 1
    assert result.cells[0].runs == 2
    assert result.cells[0].passed_runs == 2
    assert result.cells[0].resource_conservation_rate == 1