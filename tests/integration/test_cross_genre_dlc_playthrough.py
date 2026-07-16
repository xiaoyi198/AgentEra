from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from multiagent_framework.action_agent import ActionAgent
from multiagent_framework.court_graph import CourtGraph
from multiagent_framework.dlc_registry import DLCRegistry
from multiagent_framework.dlc_runtime import DLCBundleLoader
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.llm_client import LLMResponse
from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.timer_system import TimerSystem
from multiagent_framework.world_state import WorldState


ROOT = Path(__file__).resolve().parents[2]
DAY = GAME_SECONDS_PER_DAY


class TokenCounter:
    def to_dict(self):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


class LoopLLM:
    profile_names: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.token_counter = TokenCounter()

    def for_agent(self, _agent_id: str, profile_override: str = ""):
        return self

    def with_profile(self, _profile_name: str):
        return self

    async def chat(self, **_kwargs):
        return LLMResponse(content="已接收任务并完成本轮记录。")


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

    async def process_batch(self, _actions):
        return []


class NullChainLog:
    def append(self, _entry) -> None:
        return None

    def get_max_hop(self, _chain_id: str) -> int:
        return 0


def _runtime(tmp_path: Path):
    package_root = tmp_path / "dlcs" / "example_orbital"
    shutil.copytree(ROOT / "dlcs" / "example_orbital", package_root)
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.save_manifest("example_orbital", {"enabled": True, "primary": True, "status": "published"})

    llm = LoopLLM()
    world = WorldState(str(tmp_path / "world.db"))
    court = CourtGraph(str(tmp_path / "world.db"))
    court.set_world_state(world)
    router = NoopRouter(llm)
    action_agent = ActionAgent(llm, world, court=court, prompt_template="{action_type} {executor_name} {target_location} {action_description} {resources} {world_context}")
    orchestrator = Orchestrator(
        court_graph=court,
        timer_system=TimerSystem(str(tmp_path / "timers.db")),
        chain_log=NullChainLog(),
        router=router,
        action_agent=action_agent,
    )
    loader = DLCBundleLoader(registry, world, court, orchestrator, llm)
    return registry, world, orchestrator, loader


def test_orbital_dlc_completes_the_seven_stage_loop_without_core_customization(tmp_path: Path) -> None:
    asyncio.run(_playthrough(tmp_path))


async def _playthrough(tmp_path: Path) -> None:
    registry, world, orchestrator, loader = _runtime(tmp_path)
    assert registry.validate("example_orbital") == []
    audit = registry.audit_playability("example_orbital").to_dict()
    assert audit["playable"] is True
    assert audit["passed_count"] == 7

    reports = loader.load_active(seed_world=True)
    assert reports[0].package_id == "example_orbital"
    gameplay = orchestrator.gameplay_public_state()
    assert gameplay["player"]["display_name"] == "轨道站长"
    assert orchestrator.ui_manifest_public_state()["title"] == "曙光中继站指挥台"

    opening_tick = await orchestrator._tick()
    assert len(opening_tick.new_issues) == 1
    issue = opening_tick.new_issues[0]
    assert issue["issue_type_id"] == "orbital_security_report"
    assert issue["source"]["id"] == "维护工程师"
    assert issue["evidence"][0]["kind"] == "patrol_report"
    request_id = opening_tick.new_decision_requests[0]["decision_request_id"]

    inventory_before = {item.item_name: item.quantity for item in world.get_inventory("曙光中继站")}
    decision = orchestrator.resolve_decision(request_id, {
        "decision_id": "orbital-repair-decision-1",
        "expected_generation": loader.generation,
        "option_id": "patrol",
        "note": "立即派维护工程师处理冷却环。",
    })
    assert decision["action_result"]["ok"] is True
    assert decision["issue"]["status"] == "resolved"
    assert any(item["target"] == "维护工程师" for item in orchestrator._pending_wakeups)
    inventory_after = {item.item_name: item.quantity for item in world.get_inventory("曙光中继站")}
    assert inventory_after["维修额度"] < inventory_before["维修额度"]
    assert decision["outcome_feedback"]["delivered"]

    orchestrator._sim_time = DAY
    causal_tick = await orchestrator._tick()
    await orchestrator.drain_agent_tasks(timeout=2)
    causal_rows = [item for item in causal_tick.generated_events if item.get("source") == "causal"]
    assert any(item["event_type"] == "orbital_response_started" for item in causal_rows)
    assert any(item.get("world_effect_status") == "applied" for item in causal_rows), causal_rows
    feedback = orchestrator.outcome_feedback_runtime.public_state()
    assert feedback["recent"]

    objective_state = orchestrator.objectives_public_state()
    assert any(item["objective_id"] == "secure_orbital" and item["status"] == "completed" for item in objective_state["objectives"])
    assert objective_state["current_ending"]["ending_id"] == "orbital_safe"
    assert objective_state["current_ending"]["outcome"] == "victory"
