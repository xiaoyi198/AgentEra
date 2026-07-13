from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_registry import DLCRegistry, DLCRegistryError
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.generation_archive import (
    GenerationArchiveError,
    GenerationArchiveStore,
)
from multiagent_framework.world_state import WorldState


class FakeLLM:
    profile_names = ("fast", "deep")

    def __init__(self, agent_id: str = "", profile_override: str = "") -> None:
        self.agent_id = agent_id
        self.profile_override = profile_override

    def for_agent(self, agent_id: str, profile_override: str = ""):
        return FakeLLM(agent_id, profile_override or self.profile_override)

    def with_profile(self, profile_name: str):
        return FakeLLM(self.agent_id, profile_name)


class FakeOrchestrator:
    def __init__(self) -> None:
        self.agents = {}
        self.events = []
        self.time_rule_configurations = []

    def register_agent(self, agent) -> None:
        self.agents[agent.agent_id] = agent

    def get_agent(self, agent_id: str):
        return self.agents.get(agent_id)

    def activate_agent_snapshot(self, agents, *, replace_agent_ids=None) -> None:
        retained = {
            key: value for key, value in self.agents.items()
            if key not in set(replace_agent_ids or ())
        }
        retained.update(dict(agents))
        self.agents = retained

    def inject_event(self, event) -> None:
        self.events.append(event)

    def configure_time_rules(self, rules) -> None:
        self.time_rule_configurations.append(rules)


def _registry(tmp_path: Path) -> DLCRegistry:
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "sample", "name": "Sample", "version": "1.0.0"})
    registry.save_content("sample", {
        "resources": [{"name": "credits", "kind": "asset", "anchor_rate": 2}],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "credits", "quantity": 4}],
        "entity_values": [],
        "relations": [],
        "agents": [
            {"id": "player", "name": "Player", "role": "player", "location_id": "city"},
            {
                "id": "mayor", "name": "Mayor", "rank": 2,
                "location_id": "city", "model_profile": "fast",
            },
        ],
        "initial_events": [],
        "random_events": [],
        "time_rules": [{
            "rule_id": "generation.one",
            "action_types": ["relief"],
            "base_days": 2,
            "workload_factor": 1,
        }],
        "ongoing_events": [],
        "personas": {},
        "prompts": {
            "official_template": "archive-generation-one::{name}",
            "router": "router-generation-one",
        },
    })
    registry.publish("sample")
    registry.set_enabled("sample", True)
    return registry


def _loader(tmp_path: Path, registry: DLCRegistry, orchestrator: FakeOrchestrator):
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    return DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())


def test_archive_round_trip_encrypts_prompt_and_rejects_sensitive_fields(tmp_path: Path):
    key = GenerationArchiveStore.generate_key()
    store = GenerationArchiveStore(tmp_path / "archive", key=key)
    blueprint = {
        "global_prompts": {"router": "sensitive prompt body"},
        "agents": [{"config": {"system_prompt": "private persona"}}],
    }

    store.save(7, blueprint)

    archive_path = tmp_path / "archive" / "generation-00000007.json"
    raw = archive_path.read_text(encoding="utf-8")
    assert "sensitive prompt body" not in raw
    assert "private persona" not in raw
    loaded = store.load_all()
    assert not loaded.issues
    assert loaded.records[0].generation == 7
    assert loaded.records[0].blueprint == blueprint

    with pytest.raises(GenerationArchiveError, match="forbidden_sensitive_field"):
        store.save(8, {"api_key": "must-never-be-persisted"})
    with pytest.raises(GenerationArchiveError, match="forbidden_sensitive_field"):
        store.save(9, {"metadata": {"credential_env": "WORLD_SIM_API_KEY"}})
    with pytest.raises(GenerationArchiveError, match="forbidden_sensitive_field"):
        store.save(10, {"note": "${WORLD_SIM_ADMIN_TOKEN}"})


def test_archive_tamper_is_rejected_and_retention_is_bounded(tmp_path: Path):
    store = GenerationArchiveStore(
        tmp_path / "archive",
        key=GenerationArchiveStore.generate_key(),
        history_limit=2,
    )
    store.save(1, {"value": 1})
    store.save(2, {"value": 2})
    store.save(3, {"value": 3})
    assert [item.generation for item in store.load_all().records] == [2, 3]
    assert not (tmp_path / "archive" / "generation-00000001.json").exists()

    path = tmp_path / "archive" / "generation-00000002.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
    ciphertext[0] ^= 1
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    path.write_text(json.dumps(envelope), encoding="utf-8")

    loaded = store.load_all()
    assert [item.generation for item in loaded.records] == [3]
    assert loaded.issues == [{
        "generation": 2,
        "code": "archive_signature_invalid",
    }]


def test_archive_payload_v0_migrates_to_current_blueprint_shape():
    migrated = GenerationArchiveStore._migrate_payload({
        "generation": 4,
        "snapshot": {"package_ids": ["sample"]},
    })
    assert migrated == {
        "schema_version": 1,
        "generation": 4,
        "blueprint": {"package_ids": ["sample"]},
    }
    with pytest.raises(GenerationArchiveError, match="payload_schema_unsupported"):
        GenerationArchiveStore._migrate_payload({"schema_version": 99})


def test_runtime_restart_restores_generations_and_rolls_back_disk_blueprint(tmp_path: Path):
    registry = _registry(tmp_path)
    first_orchestrator = FakeOrchestrator()
    first = _loader(tmp_path, registry, first_orchestrator)
    first.load_active(seed_world=True)
    assert first.generation == 1

    content = registry.load_content("sample")
    content["prompts"]["official_template"] = "archive-generation-two::{name}"
    content["prompts"]["router"] = "router-generation-two"
    content["time_rules"] = [{
        "rule_id": "generation.two",
        "action_types": ["relief"],
        "base_days": 9,
        "workload_factor": 0,
    }]
    registry.save_content("sample", content)
    asyncio.run(first.reload_active())
    assert first.generation == 2
    assert first_orchestrator.time_rule_configurations[-1][0]["rule_id"] == "generation.two"

    archive_records = first._generation_archive.load_all().records
    assert [item.generation for item in archive_records] == [1, 2]
    serialized = json.dumps(
        [item.blueprint for item in archive_records], ensure_ascii=False
    )
    assert '"memory":' not in serialized.lower()
    assert '"structured_memory":' not in serialized.lower()
    assert '"summary":' not in serialized.lower()
    assert "client" not in serialized.lower()
    assert "api_key" not in serialized.lower()

    second_orchestrator = FakeOrchestrator()
    second = _loader(tmp_path, registry, second_orchestrator)
    assert [
        item["generation"] for item in second.public_runtime()["available_generations"]
    ] == [1, 2]
    second.load_active(seed_world=False)
    assert second.generation == 3
    assert "archive-generation-two" in second_orchestrator.agents["mayor"].config.system_prompt
    assert second_orchestrator.time_rule_configurations[-1][0]["rule_id"] == "generation.two"

    result = asyncio.run(second.rollback_active(1))

    assert result["generation"] == 4
    assert result["source_generation"] == 1
    assert "archive-generation-one" in second_orchestrator.agents["mayor"].config.system_prompt
    assert second_orchestrator.agents["mayor"].llm.profile_override == "fast"
    assert second_orchestrator.time_rule_configurations[-1][0]["rule_id"] == "generation.one"
    assert second.public_runtime()["generation_archive"]["persisted_generations"] == 4


def test_corrupt_archive_does_not_prevent_current_runtime_start(tmp_path: Path):
    registry = _registry(tmp_path)
    first = _loader(tmp_path, registry, FakeOrchestrator())
    first.load_active(seed_world=True)
    path = tmp_path / "dlc_generations" / "generation-00000001.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["signature"] = base64.b64encode(b"x" * 32).decode("ascii")
    path.write_text(json.dumps(envelope), encoding="utf-8")

    orchestrator = FakeOrchestrator()
    restarted = _loader(tmp_path, registry, orchestrator)
    restarted.load_active(seed_world=False)

    assert restarted.generation == 1
    assert "mayor" in orchestrator.agents
    status = restarted.public_runtime()["generation_archive"]
    assert status["issue_count"] >= 1
    assert any(
        issue["code"] == "archive_signature_invalid"
        for issue in status["issues"]
    )



def _archived_action(display_name: str) -> dict:
    return {
        "action_id": "issue_relief",
        "display_name": display_name,
        "target": {"kind": "none", "required": False},
        "permissions": {
            "control_modes": ["direct"],
            "control_kinds": ["agent"],
            "control_ids": ["player"],
        },
        "costs": [
            {
                "entity_id": "city",
                "resource_name": "credits",
                "base_value": 2,
            }
        ],
        "duration": {"action_type": "relief", "proposed_days": 1},
        "result": {"mode": "wake_agent", "agent_id": "mayor", "payload": {}},
    }


def test_generation_archive_restores_action_definitions(tmp_path: Path):
    registry = _registry(tmp_path)
    content = registry.load_content("sample")
    content["actions"] = [_archived_action("Generation One Relief")]
    registry.save_content("sample", content)

    orchestrator = FakeOrchestrator()
    loader = _loader(tmp_path, registry, orchestrator)
    loader.load_active(seed_world=True)
    assert loader.public_runtime()["actions"]["actions"][0]["display_name"] == "Generation One Relief"

    content = registry.load_content("sample")
    content["actions"] = [_archived_action("Generation Two Relief")]
    registry.save_content("sample", content)
    asyncio.run(loader.reload_active())
    assert loader.public_runtime()["actions"]["actions"][0]["display_name"] == "Generation Two Relief"

    result = asyncio.run(loader.rollback_active(1))
    assert result["source_generation"] == 1
    assert loader.public_runtime()["actions"]["actions"][0]["display_name"] == "Generation One Relief"
