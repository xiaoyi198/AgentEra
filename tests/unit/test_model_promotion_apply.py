"""Tests for central DLC/Agent model policies and safe promotion application."""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.llm_client import LLMConfig, LLMResponse, TokenCounter
from multiagent_framework.model_gateway import ModelGateway, TraceStore
from multiagent_framework.world_state import WorldState
from multiagent_framework.model_promotion_apply import (
    ModelPromotionApplier,
    ModelPromotionApplyError,
)
from routers import _state as state
from routers.debug import router
from tests.unit.test_dlc_runtime import FakeOrchestrator, make_registry
from tests.unit.test_model_gateway import make_config


POLICY_FP = "a" * 64
MATRIX_FP = "b" * 64


def promotion(profile="deep"):
    return {
        "schema_version": "1.0",
        "gate_id": "promotion-gate-v1",
        "environment_id": "staging",
        "decision": "approved",
        "approved": True,
        "matrix_id": "matrix-v1",
        "matrix_fingerprint": MATRIX_FP,
        "policy_fingerprint": POLICY_FP,
        "policy_patch": {
            "task_policies": {"memory.compress": ["fast"]},
            "cohort_task_policies": {
                "daming-core": {"agent.turn": [profile]},
            },
        },
    }


def mapping(generation=1):
    return {
        "schema_version": "1.0",
        "promotion_id": "promotion-2026-07-12",
        "expected_gateway_generation": generation,
        "expected_policy_fingerprint": POLICY_FP,
        "cohort_targets": {
            "daming-core": {
                "dlcs": ["daming_court"],
                "agents": [
                    {"dlc_id": "daming_court", "agent_id": "cabinet"},
                ],
            }
        },
    }


def gateway(tmp_path):
    config = make_config(tmp_path)
    config.save_to_file()
    return ModelGateway(
        config, trace_store=TraceStore(str(tmp_path / "trace.jsonl"))
    )


def test_central_policy_precedence_is_explicit_agent_dlc_global_default(tmp_path):
    config = make_config(tmp_path)
    config.task_policies["agent.turn"] = ["fast"]
    config.dlc_task_policies = {
        "daming_court": {"agent.turn": ["deep", "fast"]}
    }
    config.agent_task_policies = {
        "daming_court": {"cabinet": {"agent.turn": ["fast", "deep"]}}
    }
    model_gateway = ModelGateway(
        config, trace_store=TraceStore(str(tmp_path / "trace.jsonl"))
    )

    assert model_gateway.candidates(
        "agent.turn", dlc_id="daming_court", agent_id="cabinet"
    ) == ["fast", "deep"]
    assert model_gateway.candidates(
        "agent.turn", dlc_id="daming_court", agent_id="other"
    ) == ["deep", "fast"]
    assert model_gateway.candidates("agent.turn", dlc_id="other") == ["fast"]
    assert model_gateway.candidates(
        "agent.turn",
        "deep",
        dlc_id="daming_court",
        agent_id="cabinet",
        strict_profile=True,
    ) == ["deep"]


def test_dlc_agent_factory_binds_package_scope_before_agent_selection(tmp_path):
    config = make_config(tmp_path)
    config.dlc_task_policies = {"sample": {"agent.turn": ["fast"]}}
    config.agent_task_policies = {
        "sample": {"mayor": {"agent.turn": ["deep"]}}
    }
    model_gateway = ModelGateway(
        config, trace_store=TraceStore(str(tmp_path / "trace.jsonl"))
    )
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(
        registry,
        world,
        court,
        orchestrator,
        model_gateway.for_task("agent.turn"),
    )

    loader.load_active(seed_world=False)

    assert orchestrator.agents["mayor"].llm.dlc_id == "sample"
    assert orchestrator.agents["mayor"].llm.selected_profile == "deep"
    assert orchestrator.agents["office"].llm.selected_profile == "fast"
    assert loader.model_policy_targets() == {"sample": {"mayor", "office"}}

    blueprint = json.loads(json.dumps(
        loader._snapshot_to_archive_blueprint(loader._active_snapshot)
    ))
    blueprint.pop("agent_packages")
    for raw_agent in blueprint["agents"]:
        raw_agent.pop("package_id")
    restored = loader._snapshot_from_archive_blueprint(blueprint)
    assert restored.agent_packages == {"mayor": "sample", "office": "sample"}
    assert all(agent.llm.dlc_id == "sample" for agent in restored.agents.values())


def test_scoped_policies_round_trip_and_reject_invalid_shapes(tmp_path):
    config = make_config(tmp_path)
    config.dlc_task_policies = {"daming_court": {"agent.turn": ["deep"]}}
    config.agent_task_policies = {
        "daming_court": {"cabinet": {"agent.turn": ["fast"]}}
    }
    config.save_to_file()
    loaded = type(config).from_file(str(tmp_path / "models.json"))

    assert loaded.dlc_task_policies == config.dlc_task_policies
    assert loaded.agent_task_policies == config.agent_task_policies

    payload = config.to_dict()
    payload["dlc_task_policies"] = {"daming_court": {"agent.turn": ["deep", "deep"]}}
    with pytest.raises(ValueError, match="duplicate"):
        type(config).from_dict(payload)


def test_preview_and_apply_persist_only_policy_sections_and_advance_generation(tmp_path):
    model_gateway = gateway(tmp_path)
    applier = ModelPromotionApplier(model_gateway)

    preview = applier.preview(promotion(), mapping())
    assert preview["current_generation"] == 1
    assert preview["next_generation"] == 2
    assert preview["dlc_task_changes"] == 1
    assert preview["agent_task_changes"] == 1
    assert len(preview["plan_fingerprint"]) == 64

    result = asyncio.run(applier.apply(
        promotion(), mapping(),
        expected_plan_fingerprint=preview["plan_fingerprint"],
    ))

    assert result["status"] == "applied"
    assert result["generation"] == 2
    assert model_gateway.config.dlc_task_policies["daming_court"]["agent.turn"] == ["deep"]
    assert model_gateway.config.agent_task_policies["daming_court"]["cabinet"]["agent.turn"] == ["deep"]
    loaded = type(model_gateway.config).from_file(model_gateway.config.source_path)
    assert loaded.to_dict() == model_gateway.config.to_dict()
    raw = json.dumps(result).lower()
    for forbidden in ("base_url", "api_key", "api_key_env", "authorization"):
        assert forbidden not in raw


def test_stale_generation_plan_and_unknown_profile_are_rejected(tmp_path):
    model_gateway = gateway(tmp_path)
    applier = ModelPromotionApplier(model_gateway)
    with pytest.raises(ModelPromotionApplyError, match="generation_stale"):
        applier.preview(promotion(), mapping(generation=2))
    with pytest.raises(ModelPromotionApplyError, match="promoted_profile_unknown"):
        applier.preview(promotion("missing"), mapping())


def test_apply_rechecks_plan_fingerprint_and_rolls_back_file_on_reload_failure(
    tmp_path, monkeypatch
):
    model_gateway = gateway(tmp_path)
    applier = ModelPromotionApplier(model_gateway)
    preview = applier.preview(promotion(), mapping())
    source = model_gateway.config.source_path
    before = open(source, "rb").read()

    with pytest.raises(ModelPromotionApplyError, match="plan_fingerprint_mismatch"):
        asyncio.run(applier.apply(
            promotion(), mapping(), expected_plan_fingerprint="c" * 64
        ))

    async def fail_replace(*args, **kwargs):
        raise RuntimeError("private failure")

    monkeypatch.setattr(model_gateway, "replace_config", fail_replace)
    with pytest.raises(ModelPromotionApplyError, match="configuration_reload_failed"):
        asyncio.run(applier.apply(
            promotion(), mapping(),
            expected_plan_fingerprint=preview["plan_fingerprint"],
        ))
    assert open(source, "rb").read() == before
    assert model_gateway.generation == 1


def test_cohort_mapping_must_be_complete_non_overlapping_and_fingerprint_bound(tmp_path):
    applier = ModelPromotionApplier(gateway(tmp_path))
    bad = mapping()
    bad["expected_policy_fingerprint"] = "c" * 64
    with pytest.raises(ModelPromotionApplyError, match="policy_fingerprint_mismatch"):
        applier.preview(promotion(), bad)

    bad = mapping()
    bad["cohort_targets"]["extra"] = {"dlcs": ["other"], "agents": []}
    with pytest.raises(ModelPromotionApplyError, match="cohort_mapping_incomplete"):
        applier.preview(promotion(), bad)


def test_known_dlc_and_agent_targets_are_validated_before_planning(tmp_path):
    model_gateway = gateway(tmp_path)
    applier = ModelPromotionApplier(
        model_gateway, known_targets={"daming_court": {"cabinet"}}
    )
    bad = mapping()
    bad["cohort_targets"]["daming-core"]["agents"][0]["agent_id"] = "missing"
    with pytest.raises(ModelPromotionApplyError, match="target_agent_unknown"):
        applier.preview(promotion(), bad)


def test_promotion_admin_api_preview_and_apply_share_auth_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("WORLD_SIM_ENABLE_ADMIN_API", "1")
    monkeypatch.setenv("WORLD_SIM_ADMIN_TOKEN", "admin-secret")
    model_gateway = gateway(tmp_path)
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(
        model_gateway=model_gateway,
        dlc_loader=SimpleNamespace(
            model_policy_targets=lambda: {"daming_court": {"cabinet"}}
        ),
    )
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            denied = client.post(
                "/api/debug/model-promotion/preview",
                headers={"X-Admin-Token": "wrong"},
                json={"promotion": promotion(), "mapping": mapping()},
            )
            preview = client.post(
                "/api/debug/model-promotion/preview",
                headers={"X-Admin-Token": "admin-secret"},
                json={"promotion": promotion(), "mapping": mapping()},
            )
            applied = client.post(
                "/api/debug/model-promotion/apply",
                headers={"X-Admin-Token": "admin-secret"},
                json={
                    "promotion": promotion(),
                    "mapping": mapping(),
                    "expected_plan_fingerprint": preview.json()["plan_fingerprint"],
                },
            )
        assert denied.status_code == 403
        assert preview.status_code == 200
        assert applied.status_code == 200
        assert applied.json()["generation"] == 2
        serialized = (preview.text + applied.text).lower()
        for forbidden in ("admin-secret", "base_url", "api_key", "authorization"):
            assert forbidden not in serialized
    finally:
        state.orchestrator = previous


class _BlockingClient:
    def __init__(self, model: str, *, block: bool = False):
        self.config = LLMConfig(model=model)
        self.token_counter = TokenCounter()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = block
        self.closed = False
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        self.started.set()
        if self.block:
            await self.release.wait()
        return LLMResponse(content=self.config.model, latency_ms=1.0)

    async def close(self):
        self.closed = True


def test_apply_rejects_external_source_change_after_preview(tmp_path):
    model_gateway = gateway(tmp_path)
    applier = ModelPromotionApplier(model_gateway)
    preview = applier.preview(promotion(), mapping())
    source = model_gateway.config.source_path

    external = copy.deepcopy(model_gateway.config)
    external.task_policies["router.ambiguous"] = ["fast", "deep"]
    external.save_to_file(source)
    externally_written = open(source, "rb").read()

    with pytest.raises(ModelPromotionApplyError, match="source_configuration_changed"):
        asyncio.run(applier.apply(
            promotion(),
            mapping(),
            expected_plan_fingerprint=preview["plan_fingerprint"],
        ))

    assert open(source, "rb").read() == externally_written
    assert model_gateway.generation == 1


def test_promotion_keeps_inflight_call_on_old_generation_and_routes_new_call(tmp_path, monkeypatch):
    async def scenario():
        model_gateway = gateway(tmp_path)
        old_fast = _BlockingClient("old-fast", block=True)
        old_deep = _BlockingClient("old-deep")
        model_gateway._clients = {"fast": old_fast, "deep": old_deep}

        new_fast = _BlockingClient("new-fast")
        new_deep = _BlockingClient("new-deep")
        monkeypatch.setattr(
            model_gateway,
            "_create_clients",
            lambda config, runtime_keys: {"fast": new_fast, "deep": new_deep},
        )
        applier = ModelPromotionApplier(model_gateway)
        preview = applier.preview(promotion(), mapping())
        scoped = model_gateway.for_task(
            "agent.turn", dlc_id="daming_court", agent_id="cabinet"
        )

        in_flight = asyncio.create_task(
            scoped.chat([{"role": "user", "content": "before"}])
        )
        await old_fast.started.wait()

        applied = await applier.apply(
            promotion(),
            mapping(),
            expected_plan_fingerprint=preview["plan_fingerprint"],
        )
        after = await scoped.chat([{"role": "user", "content": "after"}])

        assert applied["generation"] == 2
        assert after.content == "new-deep"
        assert new_deep.calls == 1
        assert new_fast.calls == 0
        assert not old_fast.closed

        old_fast.release.set()
        before = await in_flight
        assert before.content == "old-fast"
        assert old_fast.closed
        assert old_deep.closed
        assert [item["config_generation"] for item in model_gateway.recent_traces()] == [2, 1]

    asyncio.run(scenario())
