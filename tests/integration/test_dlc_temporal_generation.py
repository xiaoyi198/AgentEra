from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_registry import DLCRegistry
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.temporal_contract import TemporalContract
from multiagent_framework.world_state import WorldState
from routers import _state as router_state
from routers.simulation import router as simulation_router


DAY = GAME_SECONDS_PER_DAY


class FakeLLM:
    profile_names = ()

    def for_agent(self, agent_id: str, profile_override: str = ""):
        return self

    def with_profile(self, profile_name: str):
        return self


class TemporalAwareOrchestrator:
    def __init__(self):
        self.agents = {}
        self.events = []
        self.time_configurations = []

    def register_agent(self, agent):
        self.agents[agent.agent_id] = agent

    def get_agent(self, agent_id):
        return self.agents.get(agent_id)

    def activate_agent_snapshot(self, agents, *, replace_agent_ids=None):
        retained = {key: value for key, value in self.agents.items() if key not in set(replace_agent_ids or ())}
        retained.update(dict(agents))
        self.agents = retained

    def inject_event(self, event):
        self.events.append(event)

    def configure_time_system(self, contract, *, generation=0, preserve_state=True):
        self.time_configurations.append((contract, generation, preserve_state))

    def time_system_public_state(self):
        if not self.time_configurations:
            return {"configured": False, "enabled": False, "generation": 0}
        contract, generation, _ = self.time_configurations[-1]
        return contract.public_state(0, generation=generation)


class EmptyTimers:
    def pop_due(self, sim_time):
        return []

    def get_in_transit(self):
        return []


class EmptyCourt:
    def get_official(self, official_id):
        return None


class TokenCounter:
    def to_dict(self):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def time_system(name: str, *, cycle_period: int = 2, with_references: bool = False):
    result = {
        "enabled": True,
        "calendar": {
            "calendar_id": "frontier_calendar",
            "display_name": name,
            "epoch_label": "纪元",
            "start_year": 1,
            "start_month_id": "m1",
            "start_day": 1,
            "weekdays": ["一", "二", "三", "四", "五", "六", "日"],
            "start_weekday": "一",
            "months": [{"month_id": "m1", "display_name": "一月", "days": 10}],
        },
        "seasons": [{"season_id": "dry", "display_name": "旱季", "start_day": 1, "end_day": 5}],
        "cycles": [{
            "cycle_id": "turn",
            "display_name": "指挥回合",
            "kind": "turn",
            "period_days": cycle_period,
            "duration_days": cycle_period,
        }],
        "boundary_events": [{
            "boundary_event_id": "turn_notice",
            "trigger": "cycle_start",
            "cycle_id": "turn",
            "event_type": "cycle_notice",
            "severity": 4,
            "target_agents": ["observer"] if with_references else [],
            "issue_type_id": "temporal_notice" if with_references else "",
            "payload": {"title": "新回合", "summary": "指挥回合已经开始"},
        }],
        "auto_pause_conditions": [{
            "pause_id": "low_readiness",
            "display_name": "战备预警",
            "trigger": "condition",
            "reason": "战备低于安全线",
            "conditions": [{
                "kind": "derived_metric",
                "name": "frontier_readiness",
                "operator": "lt",
                "value": 30,
            }],
        }] if with_references else [],
    }
    return result


def minimal_content(registry: DLCRegistry, dlc_id: str, calendar_name: str, *, with_references: bool = False):
    content = registry.load_content(dlc_id)
    content.update({
        "resources": [],
        "entities": [{"id": "fort", "type": "location", "name": "要塞"}] if with_references else [],
        "inventory": [],
        "entity_values": [],
        "relations": [],
        "agents": [{"id": "observer", "name": "观察员", "location_id": "fort"}] if with_references else [],
        "actors": [],
        "actions": [],
        "capabilities": [],
        "issue_types": [{
            "issue_type_id": "temporal_notice",
            "display_name": "时间通知",
            "source": {"allowed_kinds": ["system"], "allowed_ids": ["time_system"]},
            "match": {"payload_types": ["cycle_notice"]},
        }] if with_references else [],
        "derived_metrics": [{
            "metric_id": "frontier_readiness",
            "display_name": "边境战备",
            "formula": {"constant": 50},
        }] if with_references else [],
        "objectives": [],
        "endings": [],
        "initial_events": [],
        "ongoing_events": [],
        "random_events": [],
        "causal_event_rules": [],
        "time_rules": [],
        "personas": {},
        "prompts": {},
        "time_system": time_system(calendar_name, with_references=with_references),
    })
    return content


def create_runtime(tmp_path: Path, registry: DLCRegistry, orchestrator: TemporalAwareOrchestrator):
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    return DLCBundleLoader(registry, world, court, orchestrator, FakeLLM())


def test_registry_reports_all_temporal_cross_reference_failures(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "broken_time", "name": "Broken", "version": "1.0.0"})
    content = minimal_content(registry, "broken_time", "Broken", with_references=False)
    content["time_system"]["boundary_events"][0]["target_agents"] = ["missing_agent"]
    content["time_system"]["boundary_events"][0]["issue_type_id"] = "missing_issue"
    content["time_system"]["auto_pause_conditions"] = [{
        "pause_id": "broken_pause",
        "display_name": "Broken pause",
        "trigger": "condition",
        "reason": "Broken references",
        "conditions": [
            {"kind": "metric", "entity_id": "missing_entity", "name": "missing_metric", "operator": "lt", "value": 1},
            {"kind": "actor_attribute", "entity_id": "missing_actor", "name": "morale", "operator": "lt", "value": 1},
            {"kind": "derived_metric", "name": "missing_derived", "operator": "lt", "value": 1},
        ],
    }]
    registry.save_content("broken_time", content)

    issues = registry.validate("broken_time")
    codes = {item.code for item in issues}
    assert {
        "time_system.missing_agent",
        "time_system.missing_issue_type",
        "time_system.missing_entity",
        "time_system.missing_resource",
        "time_system.missing_actor",
        "time_system.missing_derived_metric",
    } <= codes
    assert any(item.path.endswith("conditions[0].entity_id") for item in issues)


def test_only_primary_dlc_owns_world_calendar_when_dependency_also_defines_one(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "base_time", "name": "Base", "version": "1.0.0"})
    registry.save_content("base_time", minimal_content(registry, "base_time", "依赖历"))
    registry.publish("base_time")
    registry.set_enabled("base_time", True)

    registry.create({
        "id": "main_time",
        "name": "Main",
        "version": "1.0.0",
        "primary": True,
        "dependencies": ["base_time"],
    })
    registry.save_content("main_time", minimal_content(registry, "main_time", "主世界历"))
    registry.publish("main_time")
    registry.set_enabled("main_time", True)

    orchestrator = TemporalAwareOrchestrator()
    loader = create_runtime(tmp_path, registry, orchestrator)
    reports = {item.package_id: item for item in loader.load_active(seed_world=False)}

    contract, generation, preserve_state = orchestrator.time_configurations[-1]
    assert contract.calendar.display_name == "主世界历"
    assert generation == 1 and preserve_state is False
    assert reports["main_time"].time_system_configured is True
    assert reports["base_time"].time_system_configured is False
    assert reports["base_time"].cycles == 0


def test_temporal_contract_is_archived_hot_reloaded_and_rollbackable(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "timeline", "name": "Timeline", "version": "1.0.0", "primary": True})
    registry.save_content("timeline", minimal_content(registry, "timeline", "第一版历"))
    registry.publish("timeline")
    registry.set_enabled("timeline", True)

    orchestrator = TemporalAwareOrchestrator()
    loader = create_runtime(tmp_path, registry, orchestrator)
    loader.load_active(seed_world=False)
    assert orchestrator.time_configurations[-1][0].calendar.display_name == "第一版历"

    content = registry.load_content("timeline")
    content["time_system"]["calendar"]["display_name"] = "第二版历"
    content["time_system"]["cycles"][0]["period_days"] = 3
    content["time_system"]["cycles"][0]["duration_days"] = 3
    registry.save_content("timeline", content)
    reloaded = asyncio.run(loader.reload_active())

    assert reloaded["time_system_changed"] is True
    assert loader.generation == 2
    assert orchestrator.time_configurations[-1][0].calendar.display_name == "第二版历"
    assert orchestrator.time_configurations[-1][2] is True
    assert [item.generation for item in loader._generation_archive.load_all().records] == [1, 2]

    rolled_back = asyncio.run(loader.rollback_active(1))
    assert rolled_back["source_generation"] == 1
    assert rolled_back["time_system_changed"] is True
    assert loader.generation == 3
    assert orchestrator.time_configurations[-1][0].calendar.display_name == "第一版历"
    assert orchestrator.time_configurations[-1][1] == 3


def test_orchestrator_tick_projects_temporal_event_issue_and_auto_pause():
    router = SimpleNamespace(
        _system_prompt="",
        llm=SimpleNamespace(token_counter=TokenCounter()),
        update_time_scale=lambda value: None,
    )
    orchestrator = Orchestrator(
        court_graph=EmptyCourt(),
        timer_system=EmptyTimers(),
        chain_log=SimpleNamespace(),
        router=router,
    )
    orchestrator.configure_issue_catalog([{
        "issue_type_id": "temporal_notice",
        "display_name": "时间通知",
        "source": {"allowed_kinds": ["system"], "allowed_ids": ["time_system"]},
        "match": {"payload_types": ["cycle_notice"]},
    }], generation=4)
    document = time_system("集成历")
    document["boundary_events"][0]["issue_type_id"] = "temporal_notice"
    document["auto_pause_conditions"] = [{
        "pause_id": "opening_pause",
        "display_name": "开局暂停",
        "trigger": "cycle_start",
        "cycle_id": "turn",
        "reason": "新指挥回合已经开始",
        "conditions": [],
    }]
    orchestrator.configure_time_system(TemporalContract.from_mapping(document), generation=4, preserve_state=False)

    result = asyncio.run(orchestrator._tick())

    assert result.generated_events[0]["source"] == "time_system"
    assert result.generated_events[0]["issue_type_id"] == "temporal_notice"
    assert result.new_issues[0]["issue_type_id"] == "temporal_notice"
    assert result.auto_pause["pause_id"] == "opening_pause"
    assert orchestrator._pausing is True
    assert result.time_system["current_date"]["day"] == 1
    assert result.time_system["auto_pause"]["reason"] == "新指挥回合已经开始"


def test_time_system_api_returns_safe_shape_and_uninitialized_fallback():
    app = FastAPI()
    app.include_router(simulation_router)
    previous = router_state.orchestrator
    try:
        router_state.orchestrator = None
        with TestClient(app) as client:
            empty = client.get("/api/time-system")
        assert empty.status_code == 200
        assert empty.json()["configured"] is False

        contract = TemporalContract.from_mapping(time_system("API 历", with_references=True))
        router_state.orchestrator = SimpleNamespace(
            time_system_public_state=lambda: contract.public_state(2 * DAY, generation=9)
        )
        with TestClient(app) as client:
            response = client.get("/api/time-system")
        body = response.json()
        assert response.status_code == 200
        assert body["generation"] == 9
        assert body["calendar"]["display_name"] == "API 历"
        assert body["current_date"]["day"] == 3
        assert "conditions" not in body["auto_pause_conditions"][0]
        assert "frontier_readiness" not in response.text
    finally:
        router_state.orchestrator = previous
