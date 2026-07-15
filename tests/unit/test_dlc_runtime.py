from __future__ import annotations

from pathlib import Path
import asyncio
from types import SimpleNamespace

import pytest

from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_registry import DLCRegistry, DLCRegistryError
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.world_state import Entity, WorldState


class FakeLLM:
    def __init__(self, agent_id: str = "") -> None:
        self.agent_id = agent_id

    def for_agent(self, agent_id: str):
        return FakeLLM(agent_id)


class FakeOrchestrator:
    def __init__(self) -> None:
        self.agents = {}
        self.events = []
        self.random_event_configurations = []
        self.time_rule_configurations = []
        self.causal_event_configurations = []
        self.operations = []
        self.gameplay_configurations = []
        self.action_configurations = []
        self.capability_configurations = []
        self.issue_configurations = []
        self.objective_configurations = []
        self.ui_manifest_configurations = []

    def register_agent(self, agent) -> None:
        self.agents[agent.agent_id] = agent

    def inject_event(self, event) -> None:
        self.operations.append(("event", event.event_id))
        self.events.append(event)

    def configure_random_events(self, templates, *, preserve_state=True) -> None:
        self.random_event_configurations.append((templates, preserve_state))

    def configure_time_rules(self, rules) -> None:
        self.time_rule_configurations.append(rules)

    def configure_causal_events(self, rules) -> None:
        self.operations.append(("causal", [item["rule_id"] for item in rules]))
        self.causal_event_configurations.append(rules)

    def configure_gameplay_contract(self, contract, *, generation=0) -> None:
        self.gameplay_configurations.append((contract, generation))

    def configure_action_catalog(self, definitions, *, generation=0, gameplay=None) -> None:
        self.operations.append(("actions", [item["action_id"] for item in definitions]))
        self.action_configurations.append((definitions, generation, gameplay))

    def configure_capability_catalog(self, definitions, *, generation=0) -> None:
        self.operations.append(("capabilities", [item["capability_id"] for item in definitions]))
        self.capability_configurations.append((definitions, generation))

    def configure_issue_catalog(self, definitions, *, generation=0, gameplay=None) -> None:
        self.operations.append(("issues", [item["issue_type_id"] for item in definitions]))
        self.issue_configurations.append((definitions, generation, gameplay))

    def configure_objective_catalog(
        self, objectives, endings, *, generation=0, gameplay=None
    ) -> None:
        self.operations.append(("objectives", [item["objective_id"] for item in objectives]))
        self.objective_configurations.append((objectives, endings, generation, gameplay))

    def configure_ui_manifest(self, manifest, *, generation=0) -> None:
        self.operations.append(("ui_manifest", manifest.package_id))
        self.ui_manifest_configurations.append((manifest, generation))


def make_registry(tmp_path: Path) -> DLCRegistry:
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "sample", "name": "Sample", "version": "1.0.0"})
    registry.save_content("sample", {
        "resources": [
            {"name": "credits", "kind": "asset", "anchor_rate": 2.5},
            {"name": "morale", "kind": "metric"},
            {"name": "tax", "kind": "parameter"},
        ],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "credits", "quantity": 4}],
        "entity_values": [
            {"entity_id": "city", "name": "morale", "kind": "metric", "value": 70},
            {"entity_id": "city", "name": "tax", "kind": "parameter", "value": 0.1},
        ],
        "relations": [],
        "agents": [
            {"id": "player", "name": "Player", "rank": 10, "location_id": "city", "role": "operator"},
            {"id": "mayor", "name": "Mayor", "rank": 2, "location_id": "city", "superior_id": "player", "personality_seed": "steady", "role": "player"},
            {"id": "office", "name": "Office", "location_id": "city", "agent_type": "institution", "superior_id": "mayor"},
            {"id": "disabled", "name": "Disabled", "location_id": "city", "superior_id": "mayor", "enabled": False},
        ],
        "initial_events": [{"event_id": "evt", "event_type": "arrival", "target_agents": ["mayor"]}],
        "random_events": [{
            "template_id": "market_cycle", "event_type": "market_shift",
            "target_agents": ["mayor"], "initial_payload": {"district": "city"},
            "severity_min": 2, "severity_max": 6,
            "min_interval_days": 20, "max_interval_days": 40,
            "probability": 0.5, "max_occurrences": 0, "enabled": True,
        }],
        "time_rules": [{
            "rule_id": "policy_time", "action_types": ["execute_policy"],
            "base_days": 2, "min_days": 1, "max_days": 30,
        }],
        "causal_event_rules": [{
            "rule_id": "arrival_to_welcome", "source_event_types": ["arrival"],
            "child_event_type": "welcome", "target_agents": ["mayor"],
            "delay_days": 0, "probability": 1,
            "conditions": [{
                "kind": "anchored_inventory", "entity_id": "city", "name": "credits",
                "operator": "gte", "value": 10,
            }],
        }],
        "ongoing_events": [{"event_id": "ongoing", "entity_id": "city", "end_time": 10}],
        "gameplay": {
            "player": {
                "identity_id": "station_commander",
                "display_name": "Station Commander",
                "actor_id": "player",
                "controlled_objects": [
                    {"kind": "entity", "id": "city", "mode": "direct"},
                    {"kind": "agent", "id": "player", "mode": "direct"},
                    {"kind": "agent", "id": "mayor", "mode": "command"},
                ],
            }
        },
        "personas": {"steady": "Stay calm."},
        "prompts": {"official_template": "You are {name} at {location}. {personality} Superior: {superior}."},
    })
    registry.publish("sample")
    registry.set_enabled("sample", True)
    return registry


def test_runtime_loader_materializes_world_agents_prompts_and_events(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())

    reports = loader.load_active(seed_world=True)

    assert len(reports) == 1
    report = reports[0]
    assert report.entities == 1 and report.agents == 2 and report.seeded_world
    assert report.random_events == 1 and report.time_rules == 1
    assert report.causal_event_rules == 1
    assert orchestrator.random_event_configurations[-1][1] is False
    assert orchestrator.random_event_configurations[-1][0][0]["template_id"] == "market_cycle"
    assert orchestrator.time_rule_configurations[-1][0]["rule_id"] == "policy_time"
    assert orchestrator.causal_event_configurations[-1][0]["rule_id"] == "arrival_to_welcome"
    assert orchestrator.operations.index(("causal", ["arrival_to_welcome"])) < orchestrator.operations.index(("event", "evt"))
    item = world.get_inventory("city")[0]
    assert item.anchor_rate == 2.5 and item.base_value == 10
    values = {item.name: item.value for item in world.get_entity_values("city")}
    assert values["morale"] == 70
    assert values["tax"] == 0.1
    assert world.list_ongoing_events("city")[0].event_id == "ongoing"
    assert set(orchestrator.agents) == {"mayor", "office"}
    contract, generation = orchestrator.gameplay_configurations[-1]
    assert generation == 1
    assert contract.player.identity_id == "station_commander"
    assert contract.direct_agent_ids == {"player"}
    assert orchestrator.agents["mayor"].llm.agent_id == "mayor"
    assert "Stay calm." in orchestrator.agents["mayor"].config.system_prompt
    assert orchestrator.events[0].event_id == "evt"


def test_runtime_restore_mode_registers_agents_without_reseeding_world(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())

    report = loader.load_active(seed_world=False)[0]

    assert not report.seeded_world and report.initial_events == 0
    assert world.get_entity("city") is None
    assert set(orchestrator.agents) == {"mayor", "office"}
    assert not orchestrator.events


class ProfileAwareFakeLLM:
    profile_names = ("fast", "deep", "special")

    def __init__(self, agent_id: str = "", profile_override: str = "") -> None:
        self.agent_id = agent_id
        self.profile_override = profile_override

    def with_profile(self, profile_name: str):
        return ProfileAwareFakeLLM(self.agent_id, profile_name)

    def for_agent(self, agent_id: str):
        return ProfileAwareFakeLLM(agent_id, self.profile_override)


def test_runtime_model_binding_priority_agent_source_over_manifest_agent_over_task(tmp_path: Path):
    registry = make_registry(tmp_path)
    package = registry.get("sample")
    manifest = package.manifest.to_dict()
    manifest["model_bindings"] = {
        "tasks": {"agent.turn": "fast"},
        "agents": {"mayor": "deep", "office": "deep"},
    }
    registry.save_manifest("sample", manifest)
    content = registry.load_content("sample")
    for agent in content["agents"]:
        if agent["id"] == "mayor":
            agent["model_profile"] = "special"
    registry.save_content("sample", content)

    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, ProfileAwareFakeLLM())

    report = loader.load_active(seed_world=False)[0]

    assert orchestrator.agents["mayor"].llm.profile_override == "special"
    assert orchestrator.agents["office"].llm.profile_override == "deep"
    assert report.model_profiles == {"mayor": "special", "office": "deep"}
    assert set(report.prompt_hashes) == {"mayor", "office"}


def test_runtime_task_model_binding_applies_when_agent_has_no_override(tmp_path: Path):
    registry = make_registry(tmp_path)
    package = registry.get("sample")
    manifest = package.manifest.to_dict()
    manifest["model_bindings"] = {"tasks": {"agent.turn": "fast"}}
    registry.save_manifest("sample", manifest)

    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, ProfileAwareFakeLLM())

    report = loader.load_active(seed_world=False)[0]

    assert report.model_profiles == {"mayor": "fast", "office": "fast"}
    assert all(agent.llm.profile_override == "fast" for agent in orchestrator.agents.values())


def test_runtime_rejects_unknown_model_profile_before_world_seed(tmp_path: Path):
    registry = make_registry(tmp_path)
    package = registry.get("sample")
    manifest = package.manifest.to_dict()
    manifest["model_bindings"] = {"agents": {"mayor": "missing"}}
    registry.save_manifest("sample", manifest)

    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, ProfileAwareFakeLLM())

    from multiagent_framework.dlc_registry import DLCRegistryError
    import pytest

    with pytest.raises(DLCRegistryError, match="unknown model profile"):
        loader.load_active(seed_world=True)
    assert world.get_entity("city") is None
    assert not orchestrator.agents

def test_runtime_uses_catalog_anchor_even_if_raw_inventory_contains_override(tmp_path: Path):
    registry = make_registry(tmp_path)
    content = registry.load_content("sample")
    content["inventory"][0]["anchor_rate"] = 999
    registry.save_content("sample", content)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    loader = DLCBundleLoader(registry, world, court, FakeOrchestrator(), FakeLLM())
    # Defense in depth: bypass registry validation to prove raw content still cannot
    # alter the canonical resource-level exchange rate during materialization.
    registry.validate = lambda _dlc_id: []

    loader.load_package(registry.get("sample"), seed_world=True)

    item = world.get_inventory("city")[0]
    assert item.anchor_rate == 2.5
    assert item.base_value == 10



def test_runtime_hot_reload_preserves_memory_world_state_and_initial_events(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)

    old_mayor = orchestrator.agents["mayor"]
    old_mayor.memory.append({"role": "user", "content": "preserve-this-memory"})
    world.set_inventory("city", "credits", "credits", 17, sim_time=1.0)
    world.update_status("city", "dynamic-live-status", 2.0)

    content = registry.load_content("sample")
    content["prompts"]["official_template"] = (
        "Reloaded {name} at {location}. {personality} Superior: {superior}."
    )
    content["agents"] = [
        item for item in content["agents"] if item["id"] != "office"
    ]
    content["agents"].append({
        "id": "scribe", "name": "Scribe", "rank": 3,
        "location_id": "city", "superior_id": "mayor",
    })
    content["entities"][0]["current_status"] = "seed-status-must-not-overwrite"
    content["causal_event_rules"][0]["child_event_type"] = "reloaded_welcome"
    content["gameplay"]["player"]["display_name"] = "Reloaded Commander"
    content["entities"].append({
        "id": "harbor", "type": "location", "name": "Harbor",
        "current_status": "new-location-status",
    })
    content["inventory"].append({
        "entity_id": "harbor", "item_name": "credits", "quantity": 3,
    })
    registry.save_content("sample", content)

    preview = loader.preview_reload()
    assert preview["safe"] is True
    assert preview["added_agents"] == ["scribe"]
    assert preview["removed_agents"] == ["office"]
    assert preview["changed_agents"] == ["mayor"]
    assert preview["gameplay_changed"] is True
    assert preview["world_changes"]["entities_added"] == 1
    assert "system_prompt" not in str(preview)
    assert "preserve-this-memory" not in str(preview)

    result = asyncio.run(loader.reload_active())

    assert result["generation"] == 2
    assert set(orchestrator.agents) == {"mayor", "scribe"}
    assert orchestrator.agents["mayor"] is not old_mayor
    assert "Reloaded Mayor" in orchestrator.agents["mayor"].config.system_prompt
    assert any(
        item.get("content") == "preserve-this-memory"
        for item in orchestrator.agents["mayor"].memory
    )
    assert world.get_inventory("city")[0].quantity == 17
    assert world.get_entity("city").current_status == "dynamic-live-status"
    assert world.get_entity("harbor").current_status == "new-location-status"
    assert world.get_inventory("harbor")[0].quantity == 3
    assert len(orchestrator.events) == 1
    assert orchestrator.causal_event_configurations[-1][0]["child_event_type"] == "reloaded_welcome"
    reloaded_contract, reloaded_generation = orchestrator.gameplay_configurations[-1]
    assert reloaded_generation == 2
    assert reloaded_contract.player.display_name == "Reloaded Commander"
    assert court.get_official("office") is None
    assert court.get_official("scribe") is not None
    status = loader.public_runtime()
    assert status["generation"] == 2
    assert status["metrics"]["attempts"] == 1
    assert status["metrics"]["successes"] == 1
    assert status["metrics"]["failures"] == 0
    assert status["metrics"]["world_rows_added_total"]["entities"] == 1
    assert status["metrics"]["world_rows_added_total"]["inventory"] == 1
    assert result["operation"]["world_rows_added"]["entities"] == 1
    assert result["operation"]["duration_ms"] >= 0



def test_runtime_generation_rollback_replays_config_without_rewinding_world(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)

    orchestrator.agents["mayor"].memory.append({
        "role": "user", "content": "memory-before-generation-two",
    })
    orchestrator.agents["office"].memory.append({
        "role": "user", "content": "archived-office-memory",
    })
    assert not loader._generation_history[1].agents["mayor"].memory
    assert not loader._generation_history[1].agents["office"].memory
    content = registry.load_content("sample")
    content["prompts"]["official_template"] = (
        "Generation two {name} at {location}. Superior: {superior}."
    )
    content["causal_event_rules"][0]["child_event_type"] = "generation_two_welcome"
    content["gameplay"]["player"]["display_name"] = "Generation Two Commander"
    content["agents"] = [
        item for item in content["agents"] if item["id"] != "office"
    ]
    content["agents"].append({
        "id": "scribe", "name": "Scribe", "rank": 3,
        "location_id": "city", "superior_id": "mayor",
    })
    content["entities"].append({
        "id": "harbor", "type": "location", "name": "Harbor",
        "current_status": "harbor-live-status",
    })
    registry.save_content("sample", content)
    asyncio.run(loader.reload_active())

    orchestrator.agents["mayor"].memory.append({
        "role": "assistant", "content": "memory-created-in-generation-two",
    })
    world.set_inventory("city", "credits", "credits", 22, sim_time=3.0)
    preview = loader.preview_rollback(1)

    assert preview["safe"] is True
    assert preview["activation_kind"] == "rollback"
    assert preview["rollback_source_generation"] == 1
    assert preview["target_generation"] == 3
    assert preview["added_agents"] == ["office"]
    assert preview["removed_agents"] == ["scribe"]
    assert "world_additions_are_not_deleted" in preview["warnings"]
    assert "system_prompt" not in str(preview)
    assert "memory-created-in-generation-two" not in str(preview)

    result = asyncio.run(loader.rollback_active(1))

    assert result["generation"] == 3
    assert result["activation_kind"] == "rollback"
    assert result["source_generation"] == 1
    assert orchestrator.causal_event_configurations[-1][0]["child_event_type"] == "welcome"
    restored_contract, restored_generation = orchestrator.gameplay_configurations[-1]
    assert restored_generation == 3
    assert restored_contract.player.display_name == "Station Commander"
    assert set(orchestrator.agents) == {"mayor", "office"}
    assert "You are Mayor" in orchestrator.agents["mayor"].config.system_prompt
    mayor_memory = list(orchestrator.agents["mayor"].memory)
    assert any(
        item.get("content") == "memory-before-generation-two"
        for item in mayor_memory
    )
    assert any(
        item.get("content") == "memory-created-in-generation-two"
        for item in mayor_memory
    )
    assert any(
        item.get("content") == "archived-office-memory"
        for item in orchestrator.agents["office"].memory
    )
    assert world.get_inventory("city")[0].quantity == 22
    assert world.get_entity("harbor").current_status == "harbor-live-status"
    assert court.get_official("scribe") is None
    assert court.get_official("office") is not None
    status = loader.public_runtime()
    assert [item["generation"] for item in status["available_generations"]] == [
        1, 2, 3,
    ]
    assert status["available_generations"][-1]["active"] is True
    assert status["recent_events"][-1]["event"] == "dlc_runtime_rolled_back"




def test_runtime_generation_history_is_bounded_and_keeps_latest_blueprints(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader._generation_history_limit = 3
    loader.load_active(seed_world=True)

    for _ in range(4):
        asyncio.run(loader.reload_active())

    status = loader.public_runtime()
    assert loader.generation == 5
    assert [item["generation"] for item in status["available_generations"]] == [
        3, 4, 5,
    ]
    assert 1 not in loader._generation_history
    assert status["available_generations"][-1]["active"] is True


def test_runtime_rollback_rejects_unavailable_generation_without_activation(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)
    old_agents = dict(orchestrator.agents)

    with pytest.raises(DLCRegistryError):
        asyncio.run(loader.rollback_active(99))

    assert loader.generation == 1
    assert orchestrator.agents == old_agents
    status = loader.public_runtime()
    assert status["metrics"]["rollback_attempts"] == 1
    assert status["metrics"]["rollback_successes"] == 0
    assert status["metrics"]["rollback_failures"] == 1
    assert status["recent_events"][-1]["failure_code"] == (
        "generation_not_available"
    )


def test_runtime_hot_reload_rejects_anchor_change_without_partial_activation(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)
    old_agents = dict(orchestrator.agents)
    old_generation = loader.generation
    old_quantity = world.get_inventory("city")[0].quantity

    content = registry.load_content("sample")
    content["resources"][0]["anchor_rate"] = 9.0
    content["prompts"]["official_template"] = "Secret changed prompt for {name}."
    registry.save_content("sample", content)

    preview = loader.preview_reload()
    assert preview["safe"] is False
    assert preview["errors"] == ["anchor_rate_change:credits"]
    assert "Secret changed prompt" not in str(preview)

    with pytest.raises(DLCRegistryError):
        asyncio.run(loader.reload_active())

    assert loader.generation == old_generation
    assert orchestrator.agents == old_agents
    assert world.get_inventory("city")[0].quantity == old_quantity
    assert world.get_resource_definition("credits").anchor_rate == 2.5
    status = loader.public_runtime()
    assert status["metrics"]["attempts"] == 1
    assert status["metrics"]["successes"] == 0
    assert status["metrics"]["failures"] == 1
    assert status["metrics"]["unsafe_rejections"] == 1
    assert status["metrics"]["anchor_migration_rejections"] == 1
    assert status["recent_events"][-1]["failure_code"] == (
        "anchor_migration_required"
    )



def test_runtime_hot_reload_waits_for_inflight_agent_and_then_retires_it(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)
    old_agent = orchestrator.agents["mayor"]

    content = registry.load_content("sample")
    content["prompts"]["official_template"] = (
        "Generation two {name} at {location}. Superior: {superior}."
    )
    registry.save_content("sample", content)

    async def scenario():
        await old_agent._turn_lock.acquire()
        reload_task = asyncio.create_task(loader.reload_active())
        await asyncio.sleep(0)
        assert reload_task.done() is False
        assert orchestrator.agents["mayor"] is old_agent
        live_status = loader.public_runtime()
        assert live_status["metrics"]["reload_in_progress"] is True
        assert live_status["metrics"]["current_waiting_inflight_agents"] == 1
        old_agent.memory.append({"role": "user", "content": "inflight-finished"})
        old_agent._turn_lock.release()
        result = await reload_task
        return result

    result = asyncio.run(scenario())

    assert result["generation"] == 2
    assert result["operation"]["waiting_inflight_agents"] == 1
    status = loader.public_runtime()
    assert status["metrics"]["last_waiting_inflight_agents"] == 1
    assert status["metrics"]["max_waiting_inflight_agents"] == 1
    assert status["metrics"]["last_memory_migration_ms"] >= 0
    assert status["metrics"]["reload_in_progress"] is False
    assert status["metrics"]["current_waiting_inflight_agents"] == 0
    assert orchestrator.agents["mayor"] is not old_agent
    assert any(
        item.get("content") == "inflight-finished"
        for item in orchestrator.agents["mayor"].memory
    )




def test_runtime_hot_reload_snapshots_multiple_agents_concurrently(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)

    content = registry.load_content("sample")
    content["prompts"]["official_template"] = (
        "Concurrent generation {name} at {location}. Superior: {superior}."
    )
    registry.save_content("sample", content)

    async def scenario():
        entered: set[str] = set()
        both_entered = asyncio.Event()
        release = asyncio.Event()
        originals = {
            agent_id: orchestrator.agents[agent_id].snapshot_memory
            for agent_id in ("mayor", "office")
        }

        def wrapper(agent_id):
            async def snapshot():
                entered.add(agent_id)
                if len(entered) == 2:
                    both_entered.set()
                await release.wait()
                return await originals[agent_id]()

            return snapshot

        for agent_id in originals:
            orchestrator.agents[agent_id].snapshot_memory = wrapper(agent_id)

        reload_task = asyncio.create_task(loader.reload_active())
        await asyncio.wait_for(both_entered.wait(), timeout=0.5)
        assert entered == {"mayor", "office"}
        release.set()
        return await reload_task

    result = asyncio.run(scenario())
    assert result["generation"] == 2
    assert loader.public_runtime()["metrics"]["successes"] == 1


def test_runtime_hot_reload_unknown_model_profile_keeps_active_generation(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(
        registry, world, court, orchestrator, ProfileAwareFakeLLM()
    )
    loader.load_active(seed_world=True)
    old_agents = dict(orchestrator.agents)
    old_generation = loader.generation

    registry.save_manifest("sample", {
        "model_bindings": {"agents": {"mayor": "missing-profile"}},
    })

    with pytest.raises(DLCRegistryError):
        loader.preview_reload()
    with pytest.raises(DLCRegistryError):
        asyncio.run(loader.reload_active())

    assert loader.generation == old_generation
    assert orchestrator.agents == old_agents
    assert world.get_inventory("city")[0].quantity == 4



def test_dlc_schema_transaction_rolls_back_world_when_official_activation_fails(tmp_path: Path):
    world = WorldState(str(tmp_path / "world.db"))
    broken_official = SimpleNamespace(
        id="broken", name="Broken", rank=1, location_id="new-city",
        role="official", agent_type="official", superior_id=None,
        personality_seed="", enabled=True,
        # extra_prompt intentionally absent to fail after world rows were staged.
    )

    with pytest.raises(AttributeError):
        world.apply_dlc_schema_update(
            [Entity(id="new-city", type="location", name="New City")],
            [],
            officials=[broken_official],
            replace_official_ids=set(),
        )

    assert world.get_entity("new-city") is None



def test_runtime_hot_reload_updates_primary_router_action_and_tianji_prompts(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    orchestrator.router = SimpleNamespace(_system_prompt="old-router", llm=FakeLLM())
    orchestrator.action_agent = SimpleNamespace(_prompt_template="old-action", llm=FakeLLM())
    orchestrator._memory_compressor = SimpleNamespace(_llm=FakeLLM())
    orchestrator.tianji_agent = SimpleNamespace(
        _system_prompt="old-tianji", llm=FakeLLM(),
        messages=[{"role": "system", "content": "old-tianji"}, {"role": "user", "content": "keep-session"}],
    )
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)

    content = registry.load_content("sample")
    content["prompts"].update({
        "router": "router-generation-two",
        "action_agent": "action-generation-two",
        "tianji": "tianji-generation-two",
    })
    registry.save_content("sample", content)

    preview = loader.preview_reload()
    assert preview["changed_runtime_tasks"] == ["action_agent", "router", "tianji"]
    serialized = str(preview)
    assert "router-generation-two" not in serialized
    assert "action-generation-two" not in serialized
    assert "tianji-generation-two" not in serialized

    asyncio.run(loader.reload_active())

    assert orchestrator.router._system_prompt == "router-generation-two"
    assert orchestrator.action_agent._prompt_template == "action-generation-two"
    assert orchestrator.tianji_agent._system_prompt == "tianji-generation-two"
    assert orchestrator.tianji_agent.messages[0] == {
        "role": "system", "content": "tianji-generation-two"
    }
    assert orchestrator.tianji_agent.messages[1]["content"] == "keep-session"



class ProfileAwareFakeGateway:
    def for_task(self, task_type: str, *, agent_id: str = "", profile_override: str = ""):
        return ProfileAwareFakeLLM(agent_id=agent_id, profile_override=profile_override)


def test_runtime_hot_reload_updates_primary_task_model_binding(tmp_path: Path):
    registry = make_registry(tmp_path)
    registry.save_manifest("sample", {
        "model_bindings": {"tasks": {"router.ambiguous": "fast"}},
    })
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    orchestrator.model_gateway = ProfileAwareFakeGateway()
    orchestrator.router = SimpleNamespace(_system_prompt="router", llm=ProfileAwareFakeLLM())
    orchestrator.action_agent = None
    orchestrator._memory_compressor = None
    orchestrator.tianji_agent = None
    loader = DLCBundleLoader(
        registry, world, court, orchestrator, ProfileAwareFakeLLM()
    )
    loader.load_active(seed_world=True)

    registry.save_manifest("sample", {
        "model_bindings": {"tasks": {"router.ambiguous": "special"}},
    })

    preview = loader.preview_reload()
    assert preview["runtime_model_profiles"]["router"] == "special"
    assert preview["changed_runtime_tasks"] == ["router"]

    asyncio.run(loader.reload_active())

    assert orchestrator.router.llm.profile_override == "special"
    assert orchestrator.router.llm.agent_id == "router"



def test_cross_dlc_duplicate_causal_rule_rejects_reload_without_partial_activation(tmp_path: Path):
    registry = make_registry(tmp_path)
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)
    old_generation = loader.generation
    old_rules = orchestrator.causal_event_configurations[-1]

    registry.create({"id": "collision", "name": "Collision", "version": "1.0.0"})
    registry.save_content("collision", {
        "resources": [], "entities": [], "inventory": [], "entity_values": [],
        "relations": [], "agents": [], "initial_events": [], "ongoing_events": [],
        "random_events": [], "time_rules": [], "personas": {}, "prompts": {},
        "causal_event_rules": [{
            "rule_id": "arrival_to_welcome",
            "source_event_types": ["other"],
            "child_event_type": "collision_child",
        }],
    })
    registry.publish("collision")
    registry.set_enabled("collision", True)

    with pytest.raises(DLCRegistryError, match="Duplicate active causal event rule id"):
        loader.preview_reload()
    with pytest.raises(DLCRegistryError, match="Duplicate active causal event rule id"):
        asyncio.run(loader.reload_active())

    assert loader.generation == old_generation
    assert orchestrator.causal_event_configurations[-1] == old_rules


def test_runtime_materializes_templates_before_configuring_causal_engine(tmp_path: Path):
    registry = make_registry(tmp_path)
    content = registry.load_content("sample")
    content["causal_condition_groups"] = [{
        "group_id": "funded",
        "conditions": [{
            "kind": "anchored_inventory", "entity_id": "city", "name": "credits",
            "operator": "gte", "value": 10,
        }],
    }]
    content["causal_rule_templates"] = [{
        "template_id": "delayed", "rule": {"delay_days": 3, "probability": 1, "max_depth": 5},
    }]
    content["causal_event_rules"] = [{
        "rule_id": "arrival_to_welcome", "template_id": "delayed",
        "condition_group_ids": ["funded"], "source_event_types": ["arrival"],
        "child_event_type": "welcome", "target_agents": ["mayor"],
    }]
    registry.save_content("sample", content)
    world = WorldState(str(tmp_path / "templated-world.db"))
    court = CourtGraph(str(tmp_path / "templated-world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())

    loader.load_active(seed_world=False)

    rule = orchestrator.causal_event_configurations[-1][0]
    assert rule["delay_days"] == 3
    assert rule["max_depth"] == 5
    assert rule["conditions"][0]["name"] == "credits"
    assert "template_id" not in rule
    assert "condition_group_ids" not in rule


def test_runtime_publishes_issue_definitions_and_preserves_them_across_reload_and_rollback(tmp_path: Path):
    registry = make_registry(tmp_path)
    content = registry.load_content("sample")
    content["issue_types"] = [{
        "issue_type_id": "city_report",
        "display_name": "City report v1",
        "source": {"allowed_kinds": ["agent"], "allowed_ids": ["mayor"]},
        "match": {"payload_types": ["city_report"], "source_ids": ["mayor"]},
        "decision_requests": [{
            "request_id": "review",
            "title": "Review report",
            "options": [{
                "option_id": "monitor",
                "display_name": "Monitor",
                "resolution": {"issue_status": "monitoring", "case_status": "monitoring"},
            }],
        }],
    }]
    registry.save_content("sample", content)

    world = WorldState(str(tmp_path / "issues-world.db"))
    court = CourtGraph(str(tmp_path / "issues-world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())

    report = loader.load_active(seed_world=True)[0]
    assert report.issue_types == 1
    assert orchestrator.issue_configurations[-1][0][0]["display_name"] == "City report v1"
    assert orchestrator.issue_configurations[-1][1] == 1
    assert loader.public_runtime()["issues"]["issue_types"][0]["issue_type_id"] == "city_report"

    content = registry.load_content("sample")
    content["issue_types"][0]["display_name"] = "City report v2"
    registry.save_content("sample", content)
    asyncio.run(loader.reload_active())
    assert orchestrator.issue_configurations[-1][0][0]["display_name"] == "City report v2"
    assert orchestrator.issue_configurations[-1][1] == 2

    asyncio.run(loader.rollback_active(1))
    assert orchestrator.issue_configurations[-1][0][0]["display_name"] == "City report v1"
    assert orchestrator.issue_configurations[-1][1] == 3
    assert loader.public_runtime()["issues"]["issue_types"][0]["display_name"] == "City report v1"


def test_cross_dlc_duplicate_issue_type_rejects_reload_without_partial_activation(tmp_path: Path):
    registry = make_registry(tmp_path)
    content = registry.load_content("sample")
    issue_definition = {
        "issue_type_id": "shared_report",
        "display_name": "Shared report",
        "source": {"allowed_kinds": ["agent"], "allowed_ids": ["mayor"]},
        "match": {"payload_types": ["shared_report"], "source_ids": ["mayor"]},
    }
    content["issue_types"] = [issue_definition]
    registry.save_content("sample", content)

    world = WorldState(str(tmp_path / "issue-collision.db"))
    court = CourtGraph(str(tmp_path / "issue-collision.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)
    old_generation = loader.generation
    old_definitions = orchestrator.issue_configurations[-1][0]

    registry.create({"id": "collision", "name": "Collision", "version": "1.0.0"})
    registry.save_content("collision", {
        "resources": [], "entities": [], "inventory": [], "entity_values": [],
        "relations": [], "agents": [], "initial_events": [], "ongoing_events": [],
        "random_events": [], "time_rules": [], "personas": {}, "prompts": {},
        "issue_types": [{
            "issue_type_id": "shared_report",
            "display_name": "Collision report",
            "source": {"allowed_kinds": ["system"]},
            "match": {"payload_types": ["other"]},
        }],
    })
    registry.publish("collision")
    registry.set_enabled("collision", True)

    with pytest.raises(DLCRegistryError, match="Duplicate active issue type id"):
        loader.preview_reload()
    with pytest.raises(DLCRegistryError, match="Duplicate active issue type id"):
        asyncio.run(loader.reload_active())

    assert loader.generation == old_generation
    assert orchestrator.issue_configurations[-1][0] == old_definitions


def test_runtime_publishes_objectives_and_preserves_definitions_across_reload_and_rollback(tmp_path: Path):
    registry = make_registry(tmp_path)
    content = registry.load_content("sample")
    content["objectives"] = [{
        "objective_id": "secure_city",
        "display_name": "Secure city v1",
        "milestones": [{
            "milestone_id": "funded",
            "display_name": "Funded",
            "condition": {
                "kind": "anchored_inventory",
                "entity_id": "city",
                "name": "credits",
                "operator": "gte",
                "value": 10,
            },
        }],
        "completion": {
            "kind": "metric",
            "entity_id": "city",
            "name": "morale",
            "operator": "gte",
            "value": 80,
        },
    }]
    content["endings"] = [{
        "ending_id": "city_safe",
        "display_name": "City safe v1",
        "outcome": "victory",
        "trigger": {"objective_ids": ["secure_city"]},
    }]
    registry.save_content("sample", content)

    world = WorldState(str(tmp_path / "objectives-world.db"))
    court = CourtGraph(str(tmp_path / "objectives-world.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())

    report = loader.load_active(seed_world=True)[0]
    objectives, endings, generation, _gameplay = orchestrator.objective_configurations[-1]
    assert report.objectives == 1 and report.endings == 1
    assert objectives[0]["display_name"] == "Secure city v1"
    assert endings[0]["display_name"] == "City safe v1"
    assert generation == 1
    runtime = loader.public_runtime()["objectives"]
    assert runtime["objectives"][0]["objective_id"] == "secure_city"
    assert runtime["endings"][0]["ending_id"] == "city_safe"

    content = registry.load_content("sample")
    content["objectives"][0]["display_name"] = "Secure city v2"
    content["endings"][0]["display_name"] = "City safe v2"
    registry.save_content("sample", content)
    asyncio.run(loader.reload_active())
    objectives, endings, generation, _gameplay = orchestrator.objective_configurations[-1]
    assert objectives[0]["display_name"] == "Secure city v2"
    assert endings[0]["display_name"] == "City safe v2"
    assert generation == 2

    asyncio.run(loader.rollback_active(1))
    objectives, endings, generation, _gameplay = orchestrator.objective_configurations[-1]
    assert objectives[0]["display_name"] == "Secure city v1"
    assert endings[0]["display_name"] == "City safe v1"
    assert generation == 3
    assert loader.public_runtime()["objectives"]["objectives"][0]["display_name"] == "Secure city v1"


def test_only_explicit_primary_dlc_owns_campaign_objectives(tmp_path: Path):
    registry = make_registry(tmp_path)
    registry.save_manifest("sample", {
        "primary": True,
        "dependencies": ["support"],
    })
    sample_content = registry.load_content("sample")
    sample_content["objectives"] = [{
        "objective_id": "primary_goal",
        "display_name": "Primary goal",
        "completion": {"kind": "sim_day", "operator": "gte", "value": 1},
    }]
    sample_content["endings"] = [{
        "ending_id": "primary_ending",
        "display_name": "Primary ending",
        "trigger": {"objective_ids": ["primary_goal"]},
    }]
    registry.save_content("sample", sample_content)

    registry.create({"id": "support", "name": "Support", "version": "1.0.0"})
    registry.save_content("support", {
        "resources": [], "entities": [], "inventory": [], "entity_values": [],
        "relations": [], "agents": [], "initial_events": [], "ongoing_events": [],
        "random_events": [], "time_rules": [], "personas": {}, "prompts": {},
        "objectives": [{
            "objective_id": "support_goal",
            "display_name": "Support goal",
            "completion": {"kind": "sim_day", "operator": "gte", "value": 0},
        }],
        "endings": [{
            "ending_id": "support_ending",
            "display_name": "Support ending",
            "trigger": {"objective_ids": ["support_goal"]},
        }],
    })
    registry.publish("support")
    registry.set_enabled("support", True)

    world = WorldState(str(tmp_path / "primary-objectives.db"))
    court = CourtGraph(str(tmp_path / "primary-objectives.db"))
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)

    objectives, endings, _generation, _gameplay = orchestrator.objective_configurations[-1]
    assert [item["objective_id"] for item in objectives] == ["primary_goal"]
    assert [item["ending_id"] for item in endings] == ["primary_ending"]



def test_primary_dlc_owns_ui_manifest_across_reload_and_rollback(tmp_path: Path):
    registry = make_registry(tmp_path)
    registry.save_manifest("sample", {"primary": True, "dependencies": ["support"]})
    sample_content = registry.load_content("sample")
    sample_content["ui_manifest"] = {
        "title": "Primary Command v1",
        "terminology": {"player": "Commander"},
    }
    registry.save_content("sample", sample_content)

    registry.create({"id": "support", "name": "Support", "version": "1.0.0"})
    registry.save_content("support", {
        "resources": [], "entities": [], "inventory": [], "entity_values": [],
        "relations": [], "agents": [], "initial_events": [], "ongoing_events": [],
        "random_events": [], "time_rules": [], "personas": {}, "prompts": {},
        "ui_manifest": {"title": "Dependency Must Not Win"},
    })
    registry.publish("support")
    registry.set_enabled("support", True)

    world = WorldState(str(tmp_path / "primary-ui.db"))
    court = CourtGraph(str(tmp_path / "primary-ui.db"))
    court.set_world_state(world)
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)

    manifest, generation = orchestrator.ui_manifest_configurations[-1]
    assert manifest.package_id == "sample"
    assert manifest.to_dict()["title"] == "Primary Command v1"
    assert generation == 1
    assert loader.public_runtime()["ui_manifest"]["title"] == "Primary Command v1"

    sample_content = registry.load_content("sample")
    sample_content["ui_manifest"]["title"] = "Primary Command v2"
    registry.save_content("sample", sample_content)
    reload_result = asyncio.run(loader.reload_active())

    assert reload_result["ui_manifest_changed"] is True
    manifest, generation = orchestrator.ui_manifest_configurations[-1]
    assert manifest.to_dict()["title"] == "Primary Command v2"
    assert generation == 2

    rollback = asyncio.run(loader.rollback_active(1))
    assert rollback["source_generation"] == 1
    manifest, generation = orchestrator.ui_manifest_configurations[-1]
    assert manifest.to_dict()["title"] == "Primary Command v1"
    assert generation == 3



def _runtime_capability(display_name: str) -> dict:
    return {
        "capability_id": "city_report",
        "display_name": display_name,
        "description": "Report city status.",
        "handler": "message",
        "audiences": ["player", "agent", "router"],
        "aliases": ["report_city"],
        "target": {"kind": "agent", "mode": "required", "allowed_ids": ["mayor"]},
        "parameters": [],
        "input": {"content_required": True, "content_label": "Report"},
    }


def test_runtime_publishes_capabilities_across_reload_and_rollback(tmp_path: Path):
    registry = make_registry(tmp_path)
    content = registry.load_content("sample")
    content["capabilities"] = [_runtime_capability("Generation One Report")]
    registry.save_content("sample", content)

    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    orchestrator = FakeOrchestrator()
    loader = DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())
    loader.load_active(seed_world=True)
    first_definitions, first_generation = orchestrator.capability_configurations[-1]
    assert first_generation == 1
    assert first_definitions[0]["display_name"] == "Generation One Report"

    content = registry.load_content("sample")
    content["capabilities"] = [_runtime_capability("Generation Two Report")]
    registry.save_content("sample", content)
    asyncio.run(loader.reload_active())
    assert orchestrator.capability_configurations[-1][0][0]["display_name"] == "Generation Two Report"

    rollback = asyncio.run(loader.rollback_active(1))
    assert rollback["source_generation"] == 1
    assert orchestrator.capability_configurations[-1][0][0]["display_name"] == "Generation One Report"
    assert loader.public_runtime()["capabilities"]["capabilities"][0]["capability_id"] == "city_report"
