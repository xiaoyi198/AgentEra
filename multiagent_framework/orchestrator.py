"""Orchestrator — 主循环，驱动整个模拟."""

from __future__ import annotations

import asyncio
import copy
import heapq
import logging
import math
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Awaitable

from .action_agent import ActionAgent
from .action_contract import ActionCatalog
from .actor_contract import ActorDefinition, ActorManager
from .capability_contract import CapabilityCatalog, CapabilityContractError
from .action_causal_bridge import build_action_causal_event
from .agent_runtime import AgentConfig, BaseAgent, OfficialAgent
from .chain_log import ChainLog
from .court_graph import CourtGraph
from .causal_events import CausalConditionContext, CausalEventEngine
from .causal_world_effects import execute_causal_world_commands
from .memory_compressor import MemoryCompressor
from .event_generator import RandomEventGenerator
from .gameplay_contract import GameplayContract
from .gameplay_effect_runtime import GameplayEffectRuntime
from .ui_manifest import UIManifest
from .issue_contract import IssueContractError, IssueManager
from .lifecycle_contract import LifecycleContract
from .lifecycle_runtime import (
    MAX_LIFECYCLE_TRANSITIONS_PER_POLL,
    LifecyclePollResult,
    LifecycleRuntime,
)
from .objective_contract import ObjectiveManager
from .rhythm_contract import RhythmContract
from .rhythm_runtime import RhythmRuntime
from .outcome_feedback_contract import OutcomeFeedbackContract
from .outcome_feedback_runtime import OutcomeFeedbackRuntime
from .derived_metrics import DerivedMetricCatalog, DerivedMetricEvaluationContext
from .fc_handlers import (
    FCContext,
    command_to_fc_args,
    command_type_to_fc_name,
    dispatch_fc,
)
from .router import RouterAgent, MAX_HOPS
from .time_simulator import TimeSimulator
from .temporal_contract import TemporalContract, TemporalRuntime
from .runtime_persistor import RuntimePersistor
from .runtime_bindings import (
    ACTION_TASK,
    MEMORY_TASK,
    ROUTER_TASK,
    TIANJI_TASK,
    RuntimeBindingsHub,
    RuntimeBindingsSnapshot,
    RuntimeTaskBinding,
)
from .timer_system import TimerSystem
from .types import (
    ActionResult,
    AgentAction,
    ChainLogEntry,
    EventObject,
    PlayerAction,
    PlayerActionType,
    RouterCommand,
    RouterCommandType,
    TickResult,
    TimerEntry,
)

logger = logging.getLogger(__name__)

# 1 游戏天 = 86400 游戏秒（和现实一样，方便换算时分秒）
GAME_SECONDS_PER_DAY = 86400


class Orchestrator:
    """模拟主循环 — 实时推进，管理 Agent、Timer、Chain Log.

    关键设计：
    - sim_time 始终以 1:1 对应真实秒数推进（1 real second = 1 sim_time unit）
    - seconds_per_day 仅影响定时器延迟计算（delay_days * seconds_per_day）
    - LLM 调用作为后台任务，不阻塞时间流动
    """

    def __init__(
        self,
        court_graph: CourtGraph,
        timer_system: TimerSystem,
        chain_log: ChainLog,
        router: RouterAgent,
        seconds_per_day: float = 600.0,
        tick_interval: float = 0.25,
        on_tick: Callable[[TickResult], Awaitable[None]] | None = None,
        action_agent: ActionAgent | None = None,
        memory_compressor: MemoryCompressor | None = None,
    ) -> None:
        self.court = court_graph
        self.timers = timer_system
        self.chain_log = chain_log
        self.router = router
        self.action_agent = action_agent

        self.agents: dict[str, BaseAgent] = {}
        self.agent_generation: int = 0
        self._dynamic_agent_ids: set[str] = set()
        self._dynamic_agent_specs: dict[str, tuple[Any, ...]] = {}
        self._last_dynamic_agent_sync_errors: list[dict[str, Any]] = []
        self._gameplay_contract = GameplayContract()
        self._ui_manifest = UIManifest.from_content({})
        self.actor_manager = ActorManager()
        self.capability_catalog = CapabilityCatalog()
        configure_router_capabilities = getattr(self.router, "configure_capability_catalog", None)
        if callable(configure_router_capabilities):
            configure_router_capabilities(self.capability_catalog)
        self.action_catalog = ActionCatalog()
        self.issue_manager = IssueManager()
        self.rhythm_runtime = RhythmRuntime()
        self.issue_manager.set_rhythm_controller(self.rhythm_runtime)
        self.outcome_feedback_runtime = OutcomeFeedbackRuntime()
        self.objective_manager = ObjectiveManager()
        self.derived_metric_catalog = DerivedMetricCatalog()
        self.objective_manager.set_derived_metric_provider(
            self.derived_metric_catalog, actor_manager=self.actor_manager,
        )
        # Objective transitions can be produced by API/action calls between ticks.
        # Buffer them so the next TickResult/WebSocket frame cannot lose them.
        self._pending_objective_transitions: deque[dict[str, Any]] = deque(maxlen=2000)
        self._gameplay_generation: int = 0
        self._gameplay_contract_published: bool = False
        self._ui_manifest_generation: int = 0
        self._memory_compressor = memory_compressor
        initial_bindings: dict[str, RuntimeTaskBinding] = {
            ROUTER_TASK: RuntimeTaskBinding(
                task_id=ROUTER_TASK,
                generation=0,
                prompt=str(getattr(router, "_system_prompt", "") or ""),
                llm=getattr(router, "llm", None),
            )
        }
        if action_agent is not None:
            action_prompt = getattr(action_agent, "_prompt_template", None)
            if action_prompt is None and hasattr(action_agent, "_load_template"):
                action_prompt = action_agent._load_template()
            initial_bindings[ACTION_TASK] = RuntimeTaskBinding(
                task_id=ACTION_TASK, generation=0,
                prompt=str(action_prompt or ""), llm=getattr(action_agent, "llm", None),
            )
        if memory_compressor is not None:
            initial_bindings[MEMORY_TASK] = RuntimeTaskBinding(
                task_id=MEMORY_TASK, generation=0,
                prompt=str(getattr(memory_compressor, "_prompt_template", "") or ""),
                llm=getattr(memory_compressor, "_llm", None),
            )
        self.runtime_bindings = RuntimeBindingsHub(
            RuntimeBindingsSnapshot.create(0, initial_bindings)
        )
        self._bind_runtime_component(router, self.runtime_bindings)
        self._bind_runtime_component(action_agent, self.runtime_bindings)
        self._bind_runtime_component(memory_compressor, self.runtime_bindings)
        self._seconds_per_day = seconds_per_day
        self._tick_interval = max(0.05, float(tick_interval))
        self._on_tick = on_tick

        # 时间状态
        self._sim_time: float = 0.0
        self.random_event_generator = RandomEventGenerator(sim_time=self._sim_time)
        self.causal_event_engine = CausalEventEngine()
        self.gameplay_effect_runtime = GameplayEffectRuntime()
        self.player_knowledge_provider: Any = self.gameplay_effect_runtime
        self._sync_gameplay_effect_runtime(project_issues=False)
        self.time_simulator = (
            action_agent.time_simulator if action_agent is not None else TimeSimulator()
        )
        self.temporal_runtime = TemporalRuntime()
        self.lifecycle_runtime = LifecycleRuntime()
        self._auto_pause_reason: dict[str, Any] | None = None
        self._last_real_time: float = 0.0
        self._running = False
        self._pausing = False
        self._paused = False

        # 后台 Agent 任务追踪
        self._agent_tasks: set[asyncio.Task] = set()

        # 玩家操作队列
        self._player_actions: asyncio.Queue[PlayerAction] = asyncio.Queue()

        # 待处理的 Agent 唤醒（下次 tick 处理）
        self._pending_wakeups: list[dict[str, Any]] = []

        # 奏折列表（持久化到 runtime/state.json）
        self.memorials: list[dict[str, Any]] = []

        # 运行时持久化器（由 main.py 注入）
        self.persistor: RuntimePersistor | None = None

        # Router 决策日志（调试用，限长防 OOM）
        self.router_log: deque[dict[str, Any]] = deque(maxlen=2000)

        # ActionAgent 执行日志（调试用：含多轮 FC trace）
        self.action_log: deque[dict[str, Any]] = deque(maxlen=2000)

        # 已触发定时器历史（调试用）
        self.fired_timers: deque[dict[str, Any]] = deque(maxlen=2000)

        # Monotonic lifecycle counters. Unlike bounded debug logs these counters
        # remain meaningful for external operational monitoring.
        self._operational_metrics_lock = threading.Lock()
        self._operational_metrics = {
            "router_batches": 0,
            "router_batches_completed": 0,
            "router_failures": 0,
            "router_actions": 0,
            "router_commands": 0,
            "agent_turns": 0,
            "agent_turn_completed": 0,
            "agent_turn_errors": 0,
            "agent_turn_rounds": 0,
            "agent_turn_tool_calls": 0,
            "agent_turn_input_tokens": 0,
            "agent_turn_output_tokens": 0,
        }

        # Execution-layer idempotency guard; Router owns the first guard.
        self._executed_command_keys: set[str] = set()
        self._executed_command_order: deque[str] = deque(maxlen=5000)

    def configure_gameplay_contract(
        self, contract: GameplayContract, *, generation: int = 0
    ) -> None:
        """Atomically publish the active DLC player identity/control contract."""
        if not isinstance(contract, GameplayContract):
            raise TypeError("contract must be a GameplayContract")
        self._gameplay_contract = contract
        self._gameplay_generation = max(0, int(generation))
        self._gameplay_contract_published = True

    def gameplay_public_state(self) -> dict[str, Any]:
        return {
            **self._gameplay_contract.to_dict(),
            "generation": self._gameplay_generation,
        }

    def configure_ui_manifest(
        self, manifest: UIManifest, *, generation: int = 0
    ) -> None:
        """Atomically publish the primary DLC's capability-safe UI manifest."""
        if not isinstance(manifest, UIManifest):
            raise TypeError("manifest must be a UIManifest")
        self._ui_manifest = manifest
        self._ui_manifest_generation = max(0, int(generation))

    def ui_manifest_public_state(self) -> dict[str, Any]:
        return {
            **self._ui_manifest.to_dict(),
            "generation": self._ui_manifest_generation,
        }

    def configure_actor_catalog(
        self,
        definitions: list[ActorDefinition | dict[str, Any]],
        *,
        generation: int = 0,
        preserve_state: bool = True,
    ) -> None:
        """Publish immutable Actor components while preserving valid runtime overlays."""
        self.actor_manager.configure(
            definitions, generation=generation, preserve_state=preserve_state
        )
        self._sync_gameplay_effect_runtime(project_issues=False)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def actors_public_state(self) -> dict[str, Any]:
        return self.actor_manager.public_state()

    def configure_capability_catalog(
        self, definitions: list[dict[str, Any]], *, generation: int = 0,
    ) -> None:
        """Publish the current DLC capability registry to Router and all Agents."""
        self.capability_catalog.configure(definitions, generation=generation)
        configure_router = getattr(self.router, "configure_capability_catalog", None)
        if callable(configure_router):
            configure_router(self.capability_catalog)
        for agent in self.agents.values():
            configure_agent = getattr(agent, "set_capability_catalog", None)
            if callable(configure_agent):
                configure_agent(self.capability_catalog)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def capabilities_public_state(self, audience: str | None = None) -> dict[str, Any]:
        return self.capability_catalog.public_state(audience=audience)

    def _player_capability_defaults(self) -> tuple[str, str]:
        player = self._gameplay_contract.player
        if player is None:
            return "player", ""
        actor_id = player.actor_id or player.identity_id or "player"
        control_object_id = player.controlled_objects[0].id if player.controlled_objects else ""
        return actor_id, control_object_id

    def _unavailable_capability_agent_targets(self, target_ids: list[str]) -> list[str]:
        """Return targets that cannot receive a capability message right now."""
        unavailable: list[str] = []
        for target_id in target_ids:
            if target_id not in self.agents:
                unavailable.append(target_id)
                continue
            official = self.court.get_official(target_id)
            if official is not None and not official.enabled:
                unavailable.append(target_id)
        return unavailable

    async def invoke_capability(
        self,
        capability_id: str,
        invocation: dict[str, Any] | None,
        *,
        audience: str,
        source_id: str = "",
        chain_id: str = "",
    ) -> dict[str, Any]:
        """Validate and execute one framework-owned capability handler."""
        raw = dict(invocation or {})
        if audience == "player":
            default_actor, default_control = self._player_capability_defaults()
            raw.setdefault("actor_id", default_actor)
            raw.setdefault("control_object_id", default_control)
            source_id = source_id or str(raw.get("source_id") or default_actor)
        prepared = self.capability_catalog.prepare_invocation(
            capability_id, raw, audience=audience, source_id=source_id,
        )
        if not prepared["invocation_id"]:
            prepared["invocation_id"] = f"cap_{uuid.uuid4().hex}"
        handler = prepared["handler"]
        active_chain = chain_id or f"capability:{prepared['invocation_id']}"
        if handler == "noop":
            result = {"ok": True, "status": "no_op"}
        elif handler in {"message", "broadcast"}:
            unavailable = self._unavailable_capability_agent_targets(prepared["target_ids"])
            if unavailable:
                raise CapabilityContractError(
                    "capability.target.unavailable",
                    details={"target_ids": unavailable},
                )
            ctx = self._build_fc_context()
            ctx.chain_id = active_chain
            target_results: list[dict[str, Any]] = []
            for target_id in prepared["target_ids"]:
                target_results.append(await dispatch_fc("wake_agent", ctx, {
                    "target": target_id,
                    "message": prepared["content"],
                    "from_agent": prepared["source_id"],
                    "urgency": prepared["urgency"],
                    "intent_id": prepared["invocation_id"],
                }))
            result = {
                "ok": bool(target_results) and all(item.get("ok") for item in target_results),
                "status": "dispatched",
                "targets": target_results,
            }
        elif handler == "world_action":
            ctx = self._build_fc_context()
            ctx.chain_id = active_chain
            parameters = prepared.get("parameters", {})
            executor_id = str(raw.get("executor_id") or prepared["source_id"] or "")
            location = str(
                raw.get("location") or raw.get("target_location")
                or parameters.get("location") or parameters.get("target_location")
                or prepared.get("target_id", "")
            )
            result = await dispatch_fc("execute_policy", ctx, {
                "policy": prepared["content"],
                "description": prepared["content"],
                "location": location,
                "executor": executor_id,
                "from_agent": prepared["source_id"],
                "intent_id": prepared["invocation_id"],
                "parameters": parameters,
            })
        elif handler == "contract_action":
            actor_id = str(raw.get("actor_id") or prepared["source_id"] or "")
            control_object_id = str(raw.get("control_object_id") or prepared["source_id"] or "")
            action_invocation = {
                "invocation_id": prepared["invocation_id"],
                "expected_generation": prepared["generation"],
                "actor_id": actor_id,
                "control_object_id": control_object_id,
                "target_id": prepared["target_id"],
                "parameters": prepared["parameters"],
            }
            result = self.execute_dlc_action(prepared["action_id"], action_invocation)
        else:  # pragma: no cover - CapabilityDefinition enforces the whitelist.
            raise CapabilityContractError("capability.invalid_handler")
        return {
            **result,
            "capability_id": prepared["capability_id"],
            "handler": handler,
            "generation": prepared["generation"],
            "invocation_id": prepared["invocation_id"],
        }

    def configure_action_catalog(
        self, definitions: list[dict[str, Any]], *, generation: int = 0,
        gameplay: GameplayContract | None = None,
    ) -> None:
        """Publish DLC action definitions without clearing receipts or cooldowns."""
        self.action_catalog.configure(
            definitions, generation=generation,
            gameplay=gameplay or self._gameplay_contract,
        )
        self._sync_gameplay_effect_runtime(project_issues=False)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def actions_public_state(self) -> dict[str, Any]:
        return self.action_catalog.public_state()

    def configure_issue_catalog(
        self, definitions: list[dict[str, Any]], *, generation: int = 0,
        gameplay: GameplayContract | None = None,
    ) -> None:
        """Publish DLC issue definitions while preserving durable cases and receipts."""
        self.issue_manager.configure(
            definitions, generation=generation,
            action_ids=self.action_catalog.action_ids,
            gameplay=gameplay or self._gameplay_contract,
        )
        self._sync_gameplay_effect_runtime()
        self._sync_legacy_memorials()
        if self.persistor:
            self.persistor.mark_state_dirty()

    def issues_public_state(self) -> dict[str, Any]:
        return self.issue_manager.public_state(sim_time=self._sim_time)

    def configure_derived_metric_catalog(
        self, definitions: list[dict[str, Any]], *, generation: int = 0,
    ) -> None:
        """Publish one immutable derived-metric catalog for rules, objectives and UI."""

        candidate = DerivedMetricCatalog(definitions, generation=generation)
        self.derived_metric_catalog = candidate
        self.objective_manager.set_derived_metric_provider(
            candidate, actor_manager=self.actor_manager,
        )
        for agent in self.agents.values():
            setter = getattr(agent, "set_derived_metric_provider", None)
            if callable(setter):
                setter(candidate, actor_manager=self.actor_manager)

    def derived_metrics_public_state(self) -> dict[str, Any]:
        return self.derived_metric_catalog.public_state(
            DerivedMetricEvaluationContext(
                world_state=self.world_state,
                actor_manager=self.actor_manager,
                sim_time=self._sim_time,
            )
        )

    def derived_metric_public_state(self, metric_id: str) -> dict[str, Any]:
        """Evaluate one player-visible metric without exposing its formula or metadata."""
        return self.derived_metric_catalog.evaluate(
            metric_id,
            DerivedMetricEvaluationContext(
                world_state=self.world_state,
                actor_manager=self.actor_manager,
                sim_time=self._sim_time,
            ),
            audience="player",
        )

    def configure_objective_catalog(
        self, objectives: list[dict[str, Any]], endings: list[dict[str, Any]], *,
        generation: int = 0, gameplay: GameplayContract | None = None,
    ) -> None:
        """Publish the primary DLC campaign contract without clearing receipts."""
        self.objective_manager.configure(
            objectives, endings, generation=generation,
            gameplay=gameplay or self._gameplay_contract,
        )
        self._sync_gameplay_effect_runtime(project_issues=False)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def _evaluate_objectives(self) -> dict[str, Any]:
        result = self.objective_manager.evaluate(
            world_state=self.world_state, sim_time=self._sim_time,
            issue_manager=self.issue_manager, action_catalog=self.action_catalog,
        )
        if result["changed"]:
            self._pending_objective_transitions.extend(result["transitions"])
            if self.persistor:
                self.persistor.mark_state_dirty()
        return result

    def objectives_public_state(self) -> dict[str, Any]:
        return self._evaluate_objectives()["state"]

    def _sync_legacy_memorials(self) -> None:
        self.memorials = self.issue_manager.legacy_memorials(sim_time=self._sim_time)

    def list_issues(self, *, status: str = "") -> list[dict[str, Any]]:
        return self.issue_manager.list_issues(sim_time=self._sim_time, status=status)

    def list_cases(self, *, status: str = "") -> list[dict[str, Any]]:
        return self.issue_manager.list_cases(sim_time=self._sim_time, status=status)

    def list_decision_requests(self, *, status: str = "") -> list[dict[str, Any]]:
        return self.issue_manager.list_decision_requests(sim_time=self._sim_time, status=status)

    def _effective_event_time(self, generated_at: Any) -> float:
        """Clamp an authored event timestamp to the elapsed simulation window."""
        current = float(self._sim_time)
        if not math.isfinite(current) or current < 0:
            current = 0.0
        if isinstance(generated_at, bool) or not isinstance(generated_at, (int, float)):
            return current
        value = float(generated_at)
        if not math.isfinite(value):
            return current
        return min(current, max(0.0, value))

    def _open_issue_at(self, payload: dict[str, Any], *, sim_time: Any) -> dict[str, Any]:
        result = self.issue_manager.open_issue(
            payload, sim_time=self._effective_event_time(sim_time),
        )
        self._sync_legacy_memorials()
        if self.persistor and not result.get("replayed", False):
            self.persistor.mark_state_dirty()
        self._evaluate_objectives()
        return result

    def open_issue(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._open_issue_at(payload, sim_time=self._sim_time)

    def append_issue_evidence(self, issue_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.issue_manager.append_evidence(issue_id, payload, sim_time=self._sim_time)
        self._sync_legacy_memorials()
        if self.persistor and not result.get("replayed", False):
            self.persistor.mark_state_dirty()
        self._evaluate_objectives()
        return result

    def preview_decision(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.issue_manager.preview_decision(
            request_id, payload, sim_time=self._sim_time,
            action_preview=self.preview_dlc_action,
        )

    def resolve_decision(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        metrics_before = self._player_metric_snapshot()
        result = self.issue_manager.resolve_decision(
            request_id, payload, sim_time=self._sim_time,
            action_preview=self.preview_dlc_action,
            action_execute=self.execute_dlc_action,
        )
        metrics_after = self._player_metric_snapshot()
        request = result.get("decision_request", {})
        issue = result.get("issue", {})
        option_id = str(request.get("selected_option_id", ""))
        feedback = self.outcome_feedback_runtime.record(
            source_kind="decision", source_id=request_id,
            receipt_id=f"decision:{request_id}:{option_id}", sim_time=self._sim_time,
            display_name=str(request.get("title", "Decision resolved")),
            action_id=str((result.get("action_result") or {}).get("action_id", "")),
            issue_type_id=str(issue.get("issue_type_id", "")),
            summary=str((request.get("outcome") or {}).get("summary", "Decision resolved.")),
            reason=str((request.get("outcome") or {}).get("summary", "")),
            changes=self._action_change_rows(result.get("action_result") or {}),
            metric_changes=self._metric_change_rows(
                metrics_before, metrics_after, source_kind="decision",
                source_id=request_id, reason=str(request.get("title", "Decision resolved")),
            ),
            details={"decision_id": request_id, "action_id": str((result.get("action_result") or {}).get("action_id", "")), "issue_id": str(issue.get("issue_id", ""))},
            condition_context=self._feedback_condition_context(),
        )
        result["outcome_feedback"] = feedback
        self._sync_legacy_memorials()
        if self.persistor and not result.get("replayed", False):
            self.persistor.mark_state_dirty()
        self._evaluate_objectives()
        return result

    def close_case(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        metrics_before = self._player_metric_snapshot()
        result = self.issue_manager.close_case(case_id, payload, sim_time=self._sim_time)
        metrics_after = self._player_metric_snapshot()
        case = result.get("case", {})
        issues = result.get("issues", [])
        outcome = case.get("outcome", {}) if isinstance(case, dict) else {}
        feedback = self.outcome_feedback_runtime.record(
            source_kind="case_closed", source_id=case_id,
            receipt_id=f"case:{case_id}:{case.get('status', 'closed')}", sim_time=self._sim_time,
            display_name=str(case.get("title", "Case closed")),
            issue_type_id=str(issues[0].get("issue_type_id", "")) if issues else "",
            summary=str(outcome.get("summary", "Case closed.")),
            reason=str(outcome.get("summary", "")),
            decisions=list(outcome.get("decisions", [])) if isinstance(outcome, dict) else [],
            metric_changes=self._metric_change_rows(
                metrics_before, metrics_after, source_kind="case_closed",
                source_id=case_id, reason=str(case.get("title", "Case closed")),
            ),
            details={"case_id": case_id},
            condition_context=self._feedback_condition_context(),
        )
        result["outcome_feedback"] = feedback
        self._sync_legacy_memorials()
        if self.persistor:
            self.persistor.mark_state_dirty()
        self._evaluate_objectives()
        return result

    def _action_agent_ids(self) -> set[str]:
        result = {agent_id for agent_id in self.agents if self.is_agent_enabled(agent_id)}
        player = self._gameplay_contract.player
        if player is not None:
            result.update(
                item.id for item in player.controlled_objects
                if item.kind == "agent" and self.is_agent_enabled(item.id)
            )
        return result

    def preview_dlc_action(self, action_id: str, invocation: dict[str, Any]) -> dict[str, Any]:
        return self.action_catalog.preview(
            action_id, invocation, world_state=self.world_state,
            time_simulator=self.time_simulator, sim_time=self._sim_time,
            available_agent_ids=self._action_agent_ids(),
        )

    def _wake_from_dlc_action(
        self, agent_id: str, payload: dict[str, Any], metadata: dict[str, Any],
    ) -> None:
        self._pending_wakeups.append({
            "target": agent_id, "payload": payload,
            "chain_id": str(metadata.get("chain_id", "")),
            "event_id": "",
        })

    def execute_dlc_action(self, action_id: str, invocation: dict[str, Any]) -> dict[str, Any]:
        metrics_before = self._player_metric_snapshot()
        preview = self.preview_dlc_action(action_id, invocation)
        result = self.action_catalog.execute(
            action_id, invocation, world_state=self.world_state,
            time_simulator=self.time_simulator, sim_time=self._sim_time,
            available_agent_ids=self._action_agent_ids(),
            wake_callback=self._wake_from_dlc_action, emit_callback=self.inject_event,
        )
        metrics_after = self._player_metric_snapshot()
        invocation_id = str(result.get("invocation_id", ""))
        feedback = self.outcome_feedback_runtime.record(
            source_kind="action", source_id=invocation_id,
            receipt_id=f"action:{invocation_id}", sim_time=self._sim_time,
            display_name=str(preview.get("display_name", action_id)), action_id=action_id,
            summary=f"{preview.get('display_name', action_id)} completed.",
            reason=str(preview.get("display_name", action_id)),
            changes=self._action_change_rows(result),
            metric_changes=self._metric_change_rows(
                metrics_before, metrics_after, source_kind="action",
                source_id=invocation_id, reason=str(preview.get("display_name", action_id)),
            ),
            details={"action_id": action_id},
            condition_context=self._feedback_condition_context(),
        )
        result["outcome_feedback"] = feedback
        self.action_log.append({
            "kind": "dlc_action", "action_id": action_id,
            "invocation_id": invocation_id,
            "sim_time": self._sim_time, "generation": result.get("generation", 0),
            "replayed": bool(result.get("replayed", False)),
            "ok": bool(result.get("ok", False)),
        })
        if self.persistor and not result.get("replayed", False):
            self.persistor.mark_state_dirty()
        self._evaluate_objectives()
        return result

    def _is_player_recipient(self, target_id: str, official: Any = None) -> bool:
        if getattr(self, "_gameplay_contract_published", False):
            return self._gameplay_contract.is_player_recipient(target_id)
        # Compatibility for callers that construct Orchestrator without a DLC loader.
        return bool(official and getattr(official, "role", "") == "player")

    @staticmethod
    def _bind_runtime_component(component: Any, hub: RuntimeBindingsHub | None = None) -> None:
        if component is not None and hasattr(component, "bind_runtime_bindings"):
            target_hub = hub
            if target_hub is not None:
                component.bind_runtime_bindings(target_hub)

    def _runtime_component(self, task_id: str) -> Any:
        return {
            ROUTER_TASK: self.router,
            ACTION_TASK: self.action_agent,
            MEMORY_TASK: self._memory_compressor,
            TIANJI_TASK: getattr(self, "tianji_agent", None),
        }.get(task_id)

    @staticmethod
    def _legacy_runtime_values(task_id: str, component: Any) -> tuple[str, Any]:
        if component is None:
            return "", None
        if task_id == ROUTER_TASK:
            return str(getattr(component, "_system_prompt", "") or ""), getattr(component, "llm", None)
        if task_id == ACTION_TASK:
            prompt = getattr(component, "_prompt_template", None)
            if prompt is None and hasattr(component, "_load_template"):
                prompt = component._load_template()
            return str(prompt or ""), getattr(component, "llm", None)
        if task_id == MEMORY_TASK:
            return str(getattr(component, "_prompt_template", "") or ""), getattr(component, "_llm", None)
        if task_id == TIANJI_TASK:
            prompt = getattr(component, "_system_prompt", None)
            if prompt is None and hasattr(component, "_load_system_prompt"):
                prompt = component._load_system_prompt()
            return str(prompt or ""), getattr(component, "llm", None)
        return "", None

    def prepare_runtime_bindings(
        self,
        generation: int,
        *,
        prompts: dict[str, str] | None = None,
        clients: dict[str, Any] | None = None,
        model_profiles: dict[str, str] | None = None,
    ) -> RuntimeBindingsSnapshot:
        """Build a complete candidate without mutating the active runtime binding."""
        prompts = prompts or {}
        clients = clients or {}
        model_profiles = model_profiles or {}
        active = self.runtime_bindings.snapshot.bindings
        prepared: dict[str, RuntimeTaskBinding] = {}
        for task_id in (ROUTER_TASK, ACTION_TASK, MEMORY_TASK, TIANJI_TASK):
            previous = active.get(task_id)
            component = self._runtime_component(task_id)
            legacy_prompt, legacy_llm = self._legacy_runtime_values(task_id, component)
            prompt = previous.prompt if previous is not None else legacy_prompt
            llm = previous.llm if previous is not None else legacy_llm
            profile = previous.model_profile if previous is not None else ""
            if prompts.get(task_id):
                prompt = str(prompts[task_id])
            if clients.get(task_id) is not None:
                llm = clients[task_id]
            if task_id in model_profiles:
                profile = str(model_profiles[task_id] or "")
            if component is None and previous is None and llm is None:
                continue
            if llm is None:
                raise ValueError(f"runtime task has no LLM client: {task_id}")
            prepared[task_id] = RuntimeTaskBinding(
                task_id=task_id, generation=int(generation), prompt=prompt,
                llm=llm, model_profile=profile,
            )
        return RuntimeBindingsSnapshot.create(int(generation), prepared)

    def activate_runtime_bindings(
        self, snapshot: RuntimeBindingsSnapshot
    ) -> RuntimeBindingsSnapshot:
        """Publish all task bindings atomically, then mirror legacy fields for tooling."""
        for task_id in snapshot.bindings:
            component = self._runtime_component(task_id)
            self._bind_runtime_component(component, self.runtime_bindings)
        previous = self.runtime_bindings.activate(snapshot)
        # These mirrors are not used by bound calls. They preserve compatibility for
        # debug views and independent code that still inspects the legacy attributes.
        for task_id, binding in snapshot.bindings.items():
            component = self._runtime_component(task_id)
            if component is None:
                continue
            if task_id == ROUTER_TASK:
                component._system_prompt = binding.prompt
                component.llm = binding.llm
            elif task_id == ACTION_TASK:
                component._prompt_template = binding.prompt
                component.llm = binding.llm
            elif task_id == MEMORY_TASK:
                component._prompt_template = binding.prompt
                component._llm = binding.llm
            elif task_id == TIANJI_TASK:
                component._system_prompt = binding.prompt
                component.llm = binding.llm
        return previous

    def runtime_bindings_public_state(self) -> dict[str, Any]:
        return self.runtime_bindings.public_state()

    def _record_operational_metric(self, name: str, amount: int = 1) -> None:
        with self._operational_metrics_lock:
            self._operational_metrics[name] = (
                self._operational_metrics.get(name, 0) + int(amount)
            )

    def operational_metrics_snapshot(self) -> dict[str, int]:
        with self._operational_metrics_lock:
            return dict(self._operational_metrics)

    @property
    def world_state(self):
        """Return the WorldState bound to ActionAgent, or None."""
        return getattr(self.action_agent, "world_state", None)

    def _sync_gameplay_effect_runtime(self, *, project_issues: bool = True) -> dict[str, Any]:
        """Refresh SQLite-authoritative effects and all runtime projections."""
        state = self.gameplay_effect_runtime.refresh(self.world_state)
        self.gameplay_effect_runtime.sync_managers(
            actor_manager=self.actor_manager,
            issue_manager=self.issue_manager,
            objective_manager=self.objective_manager,
            action_catalog=self.action_catalog,
            sim_time=self._sim_time,
            project_issues=project_issues,
        )
        self._last_dynamic_agent_sync_errors = self._sync_dynamic_agents(state)
        return state

    @staticmethod
    def _dynamic_agent_spec(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(row.get("source_agent_id", "")),
            str(row.get("actor_id", "")),
            str(row.get("display_name", "")),
            str(row.get("system_prompt", "")),
        )

    def _sync_dynamic_agents(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Project enabled lifecycle Agent descriptors into live Agent instances.

        Static Agents always win ID conflicts. Existing dynamic Agent memory is migrated
        when a descriptor or its source Agent changes. Retired descriptors are removed
        from future dispatch while already-running turns keep their direct old reference.
        """
        lifecycle = state.get("lifecycle", {}) if isinstance(state, dict) else {}
        raw_rows = lifecycle.get("dynamic_agents", {}) if isinstance(lifecycle, dict) else {}
        rows = raw_rows if isinstance(raw_rows, dict) else {}
        desired = {
            str(agent_id): dict(row)
            for agent_id, row in rows.items()
            if agent_id and isinstance(row, dict)
            and bool(row.get("enabled", True))
            and not bool(row.get("retired", False))
        }
        errors: list[dict[str, Any]] = []

        for agent_id in sorted(self._dynamic_agent_ids - set(desired)):
            self.agents.pop(agent_id, None)
            self._dynamic_agent_ids.discard(agent_id)
            self._dynamic_agent_specs.pop(agent_id, None)

        for agent_id in sorted(desired):
            row = desired[agent_id]
            source_id = str(row.get("source_agent_id", "")).strip()
            current = self.agents.get(agent_id)
            if current is not None and agent_id not in self._dynamic_agent_ids:
                errors.append({
                    "agent_id": agent_id,
                    "error": "dynamic_agent_id_conflict",
                    "source_agent_id": source_id,
                })
                continue
            source = self.agents.get(source_id)
            if source is None or source_id == agent_id:
                errors.append({
                    "agent_id": agent_id,
                    "error": "dynamic_agent_source_not_found",
                    "source_agent_id": source_id,
                })
                continue
            spec = (*self._dynamic_agent_spec(row), id(source), source.__class__)
            if current is not None and self._dynamic_agent_specs.get(agent_id) == spec:
                continue
            if current is not None and bool(getattr(current, "is_busy", False)):
                errors.append({
                    "agent_id": agent_id,
                    "error": "dynamic_agent_refresh_deferred",
                    "source_agent_id": source_id,
                })
                continue
            source_config = getattr(source, "config", None)
            source_llm = getattr(source, "llm", None)
            if not isinstance(source_config, AgentConfig):
                errors.append({
                    "agent_id": agent_id,
                    "error": "dynamic_agent_source_invalid",
                    "source_agent_id": source_id,
                })
                continue
            config = copy.deepcopy(source_config)
            config.agent_id = agent_id
            config.actor_id = str(row.get("actor_id", ""))
            config.display_name = str(row.get("display_name") or config.display_name or agent_id)
            if row.get("system_prompt") not in (None, ""):
                config.system_prompt = str(row.get("system_prompt"))
            try:
                projected = source.__class__(config, source_llm)
                if current is not None:
                    projected.deserialize_memory(current.serialize_memory())
                self.register_agent(projected)
            except Exception as exc:
                logger.exception("Failed to project dynamic Agent %s", agent_id)
                errors.append({
                    "agent_id": agent_id,
                    "error": "dynamic_agent_projection_failed",
                    "source_agent_id": source_id,
                    "detail": str(exc),
                })
                continue
            self._dynamic_agent_ids.add(agent_id)
            self._dynamic_agent_specs[agent_id] = spec
        return errors

    def is_agent_enabled(self, agent_id: str) -> bool:
        """Return whether an Agent may receive or execute new work."""
        return self.gameplay_effect_runtime.agent_enabled(agent_id)

    def gameplay_effects_public_state(self) -> dict[str, Any]:
        return self.gameplay_effect_runtime.public_state(sim_time=self._sim_time)

    @property
    def sim_time(self) -> float:
        return self._sim_time

    @property
    def sim_day(self) -> float:
        return self._sim_time / GAME_SECONDS_PER_DAY

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Agent 注册 ──

    def register_agent(self, agent: BaseAgent) -> None:
        if self._memory_compressor is not None:
            agent.set_compressor(self._memory_compressor)
        configure_capabilities = getattr(agent, "set_capability_catalog", None)
        if callable(configure_capabilities):
            configure_capabilities(self.capability_catalog)
        agent.bind_runtime(
            world_state=self.world_state,
            court=self.court,
            sim_time_provider=lambda: self._sim_time,
            seconds_per_day_provider=lambda: float(GAME_SECONDS_PER_DAY),
            knowledge_provider=self.gameplay_effect_runtime,
            actor_manager=self.actor_manager,
            derived_metric_provider=self.derived_metric_catalog,
        )
        self.agents[agent.agent_id] = agent
        logger.info("Registered agent: %s (%s)", agent.agent_id, agent.agent_type)

    def get_agent(self, agent_id: str) -> BaseAgent | None:
        return self.agents.get(agent_id)

    def activate_agent_snapshot(
        self,
        agents: dict[str, BaseAgent],
        *,
        replace_agent_ids: set[str] | None = None,
    ) -> dict[str, BaseAgent]:
        """Atomically publish a prepared Agent map for future dispatches.

        Background turns already hold direct references to their old Agent instances and
        therefore finish normally. Pending wakeups resolve against the new map on the next
        tick. The caller is responsible for migrating memory before activation.
        """
        prepared: dict[str, BaseAgent] = {}
        for agent_id, agent in agents.items():
            if agent_id != agent.agent_id:
                raise ValueError(f"agent snapshot key mismatch: {agent_id}")
            if self._memory_compressor is not None:
                agent.set_compressor(self._memory_compressor)
            configure_capabilities = getattr(agent, "set_capability_catalog", None)
            if callable(configure_capabilities):
                configure_capabilities(self.capability_catalog)
            agent.bind_runtime(
                world_state=self.world_state,
                court=self.court,
                sim_time_provider=lambda: self._sim_time,
                seconds_per_day_provider=lambda: float(GAME_SECONDS_PER_DAY),
                knowledge_provider=self.gameplay_effect_runtime,
            actor_manager=self.actor_manager,
            derived_metric_provider=self.derived_metric_catalog,
            )
            prepared[agent_id] = agent

        replace_ids = set(
            replace_agent_ids if replace_agent_ids is not None
            else (set(self.agents) - self._dynamic_agent_ids)
        )
        replace_ids -= self._dynamic_agent_ids
        previous = {
            agent_id: agent for agent_id, agent in self.agents.items()
            if agent_id in replace_ids
        }
        merged = {
            agent_id: agent for agent_id, agent in self.agents.items()
            if agent_id not in replace_ids
        }
        merged.update(prepared)
        self.agents = merged
        self.agent_generation += 1
        logger.info(
            "Activated Agent snapshot generation=%d active=%d replaced=%d",
            self.agent_generation, len(prepared), len(previous),
        )
        self._sync_gameplay_effect_runtime(project_issues=False)
        return previous

    def _fuzzy_find_agent(self, keyword: str) -> tuple[BaseAgent | None, Any]:
        """模糊匹配 Agent — Router 可能返回地名、人名、简称."""
        # 1. agent ID 包含关键词
        for aid, agent in self.agents.items():
            if keyword in aid:
                official = self.court.get_official(aid)
                return agent, official
        # 2. 按地点匹配
        officials = self.court.get_officials_at_location(keyword)
        for o in officials:
            if o.id in self.agents:
                return self.agents[o.id], o
        # 3. 按名字匹配
        for o in self.court.get_all_officials():
            if keyword in o.name and o.id in self.agents:
                return self.agents[o.id], o
        return None, None

    # ── 事件注入 ──

    def configure_random_events(
        self,
        templates: list[dict[str, Any]],
        *,
        preserve_state: bool = True,
    ) -> None:
        """Publish DLC random-event templates at the current simulation time."""
        self.random_event_generator.configure(
            templates,
            sim_time=self._sim_time,
            preserve_state=preserve_state,
        )
        if self.persistor:
            self.persistor.mark_state_dirty()

    def random_event_status(self) -> dict[str, Any]:
        """Return scheduler metadata without payloads or Agent identifiers."""
        return self.random_event_generator.public_status()

    def configure_causal_events(self, rules: list[dict[str, Any]]) -> None:
        """Atomically publish causal rules for future source events.

        Already materialized child events remain queued with their committed
        semantics, so reload cannot rewrite work already caused by an event.
        """
        self.causal_event_engine.configure(rules)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def causal_event_status(self) -> dict[str, Any]:
        """Return aggregate causal scheduler metadata without event payloads."""
        return self.causal_event_engine.public_status()

    def configure_time_rules(self, rules: list[dict[str, Any]]) -> None:
        """Atomically publish DLC action-duration rules for future work."""
        if self.action_agent is not None:
            self.action_agent.configure_time_rules(rules)
            self.time_simulator = self.action_agent.time_simulator
        else:
            self.time_simulator = TimeSimulator(rules)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def time_rule_status(self) -> dict[str, Any]:
        """Return safe rule metadata without action, entity, or prompt content."""
        return {
            "fingerprint": self.time_simulator.fingerprint,
            "rule_count": len(self.time_simulator.rules),
            "enabled_count": sum(1 for rule in self.time_simulator.rules if rule.enabled),
        }

    def configure_time_system(
        self, contract: TemporalContract, *, generation: int,
        preserve_state: bool = True,
    ) -> None:
        """Publish the primary DLC calendar without replaying pre-activation boundaries."""
        self.temporal_runtime.configure(
            contract, generation=generation, sim_time=self._sim_time,
            preserve_state=preserve_state,
        )
        self._auto_pause_reason = None
        if self.persistor:
            self.persistor.mark_state_dirty()

    def time_system_public_state(self) -> dict[str, Any]:
        """Return capability-safe current calendar/cycle state."""
        return self.temporal_runtime.public_state(
            self._sim_time, auto_pause_reason=self._auto_pause_reason,
        )

    def configure_rhythm_system(
        self, contract: RhythmContract, *, generation: int,
        preserve_state: bool = True,
    ) -> None:
        """Publish pacing rules and retain compatible queue/budget state."""
        self.rhythm_runtime.configure(
            contract, generation=generation, sim_time=self._sim_time,
            preserve_state=preserve_state,
        )
        self.issue_manager.set_rhythm_controller(self.rhythm_runtime)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def rhythm_public_state(self) -> dict[str, Any]:
        return self.rhythm_runtime.public_state(
            issue_manager=self.issue_manager, sim_time=self._sim_time,
        )

    def configure_outcome_feedback(
        self, contract: OutcomeFeedbackContract, *, generation: int,
        preserve_state: bool = True,
    ) -> None:
        """Publish player-safe feedback rules while retaining compatible pending reports."""
        self.outcome_feedback_runtime.configure(
            contract, generation=generation, sim_time=self._sim_time,
            preserve_state=preserve_state,
        )
        if self.persistor:
            self.persistor.mark_state_dirty()

    def outcome_feedback_public_state(self) -> dict[str, Any]:
        return self.outcome_feedback_runtime.public_state()

    def _feedback_condition_context(self, *, sim_time: Any = None) -> CausalConditionContext:
        context_time = self._sim_time if sim_time is None else self._effective_event_time(sim_time)
        return CausalConditionContext(
            sim_time=context_time, world_state=self.world_state,
            actor_manager=self.actor_manager, objective_manager=self.objective_manager,
            event_history=tuple(getattr(self.causal_event_engine, "_event_history", ())),
            derived_metric_provider=self.derived_metric_catalog,
            knowledge_provider=self.player_knowledge_provider,
            issue_manager=self.issue_manager, action_catalog=self.action_catalog,
        )

    def _player_metric_snapshot(self) -> dict[str, dict[str, Any]]:
        context = DerivedMetricEvaluationContext(
            world_state=self.world_state, actor_manager=self.actor_manager,
            sim_time=self._sim_time,
        )
        return {
            str(item.get("metric_id", "")): item
            for item in self.derived_metric_catalog.evaluate_all(context, audience="player")
            if item.get("status") == "ok" and item.get("metric_id")
        }

    def _metric_change_rows(
        self, before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
        *, source_kind: str, source_id: str, reason: str,
    ) -> list[dict[str, Any]]:
        epsilon = self.outcome_feedback_runtime.contract.metric_delta_epsilon
        rows: list[dict[str, Any]] = []
        for metric_id in sorted(set(before).intersection(after)):
            try:
                old_value = float(before[metric_id]["value"])
                new_value = float(after[metric_id]["value"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            delta = new_value - old_value
            if abs(delta) + 1e-12 < epsilon:
                continue
            rows.append({
                "kind": "derived_metric", "metric_id": metric_id,
                "display_name": str(after[metric_id].get("display_name", metric_id)),
                "unit": str(after[metric_id].get("unit", "")),
                "before": old_value, "after": new_value, "delta": delta,
                "direction": "up" if delta > 0 else "down",
                "reason": reason, "source_kind": source_kind, "source_id": source_id,
            })
        return rows

    @staticmethod
    def _action_change_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        batch = result.get("batch", {})
        for item in batch.get("results", []) if isinstance(batch, dict) else []:
            payload = item.get("result", {}) if isinstance(item, dict) else {}
            if isinstance(payload, dict):
                rows.append(dict(payload))
        return rows

    def configure_lifecycle_system(
        self, contract: LifecycleContract, *, generation: int,
        preserve_state: bool = True,
    ) -> None:
        """Publish lifecycle transitions while preserving compatible condition latches."""
        self.lifecycle_runtime.configure(
            contract, generation=generation, preserve_state=preserve_state,
        )
        self._sync_gameplay_effect_runtime(project_issues=False)
        if self.persistor:
            self.persistor.mark_state_dirty()

    def lifecycle_public_state(self) -> dict[str, Any]:
        return self.lifecycle_runtime.public_state(world_state=self.world_state)

    def execute_lifecycle_transition(
        self, transition_id: str, *, request_id: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one manual lifecycle transition with receipt idempotency."""
        outcome = self.lifecycle_runtime.execute_manual(
            transition_id, world_state=self.world_state, sim_time=self._sim_time,
            request_id=request_id, actor_manager=self.actor_manager,
            condition_context=CausalConditionContext(
                world_state=self.world_state,
                actor_manager=self.actor_manager,
                objective_manager=self.objective_manager,
                event_history=tuple(getattr(self.causal_event_engine, "_event_history", ())),
                derived_metric_provider=self.derived_metric_catalog,
                knowledge_provider=self.player_knowledge_provider,
                issue_manager=self.issue_manager,
                action_catalog=self.action_catalog,
            ),
            variables=variables,
        )
        if outcome.get("status") == "applied":
            self._sync_gameplay_effect_runtime()
            if self.persistor:
                self.persistor.mark_state_dirty()
        return outcome

    def inject_event(self, event: EventObject) -> None:
        """注入一个事件，为每个 target_agent 创建即时唤醒."""
        event_time = self._effective_event_time(event.generated_at)
        causal_scheduled = self.causal_event_engine.observe(
            event,
            sim_time=event_time,
            condition_context=CausalConditionContext(
                sim_time=event_time,
                world_state=self.world_state,
                actor_manager=self.actor_manager,
                objective_manager=self.objective_manager,
                derived_metric_provider=self.derived_metric_catalog,
                knowledge_provider=self.player_knowledge_provider,
                issue_manager=self.issue_manager,
                action_catalog=self.action_catalog,
            ),
        )
        if causal_scheduled and self.persistor:
            self.persistor.mark_state_dirty()
        for agent_id in event.target_agents:
            if not self.is_agent_enabled(agent_id):
                logger.info("Skipped event %s for disabled agent %s", event.event_id, agent_id)
                continue
            self._pending_wakeups.append({
                "target": agent_id,
                "payload": {
                    **event.initial_payload,
                    "description": f"【{event.event_type}】严重程度{event.severity}/10 — {event.initial_payload}",
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "generated_at": event_time,
                },
                "event_id": event.event_id,
                "chain_id": f"chain_{uuid.uuid4().hex[:8]}",
            })
        logger.info("Injected event %s targeting %d agents", event.event_id, len(event.target_agents))

    # ── 玩家操作 ──

    async def inject_player_action(self, action: PlayerAction) -> None:
        await self._player_actions.put(action)

    # ── 时间控制 ──

    def set_time_scale(self, seconds_per_day: float) -> None:
        """调整时间倍率（仅暂停时可调）."""
        self._seconds_per_day = seconds_per_day
        self.router.update_time_scale(seconds_per_day)

    async def pause(self) -> None:
        """请求暂停 — 等所有正在进行的 Agent 任务完成后生效."""
        self._auto_pause_reason = None
        self._pausing = True
        logger.info("Pause requested, waiting for %d agent tasks...", len(self._agent_tasks))

    def resume(self) -> None:
        self._paused = False
        self._pausing = False
        self._auto_pause_reason = None
        self._last_real_time = time.monotonic()
        logger.info("Simulation resumed at sim_time=%.1f", self._sim_time)

    # ── 存档/读档 ──

    def save_state(self) -> dict[str, Any]:
        if not self._paused:
            raise RuntimeError("只能在暂停状态下存档")
        timer_count = self.timers.save_all()
        # 序列化所有 Agent 记忆（v2 格式：含 summary + messages）
        agent_memories: dict[str, Any] = {}
        for agent_id, agent in self.agents.items():
            agent_memories[agent_id] = agent.serialize_memory()
        return {
            "sim_time": self._sim_time,
            "seconds_per_day": self._seconds_per_day,
            "timer_count": timer_count,
            "random_events": self.random_event_generator.snapshot(),
            "causal_events": self.causal_event_engine.snapshot(),
            "time_rules": self.time_rule_status(),
            "time_system_runtime": self.temporal_runtime.snapshot(),
            "lifecycle_runtime": self.lifecycle_runtime.snapshot(),
            "rhythm_runtime": self.rhythm_runtime.snapshot(),
            "outcome_feedback_runtime": self.outcome_feedback_runtime.snapshot(),
            "auto_pause_reason": self._auto_pause_reason,
            "action_runtime": self.action_catalog.snapshot(),
            "issue_runtime": self.issue_manager.snapshot(),
            "objective_runtime": self.objective_manager.snapshot(),
            "actor_runtime": self.actor_manager.snapshot(),
            "agent_memories": agent_memories,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._sim_time = state.get("sim_time", 0.0)
        self._seconds_per_day = state.get("seconds_per_day", 600.0)
        self.timers.load_from_db()
        self.chain_log.load_from_file()
        self.router.update_time_scale(self._seconds_per_day)
        self.random_event_generator.restore(
            state.get("random_events", {}), sim_time=self._sim_time
        )
        self.causal_event_engine.restore(
            state.get("causal_events", {}), sim_time=self._sim_time
        )
        self.action_catalog.restore(
            state.get("action_runtime", {}), sim_time=self._sim_time
        )
        self.issue_manager.restore(
            state.get("issue_runtime", {}), sim_time=self._sim_time,
            legacy_memorials=state.get("memorials", []),
        )
        self._sync_legacy_memorials()
        self.objective_manager.restore(
            state.get("objective_runtime", {}), sim_time=self._sim_time
        )
        temporal_state = state.get("time_system_runtime")
        if isinstance(temporal_state, dict):
            self.temporal_runtime.restore(temporal_state, sim_time=self._sim_time)
        else:
            self.temporal_runtime.last_polled_time = self._sim_time
        self.lifecycle_runtime.restore(state.get("lifecycle_runtime", {}))
        self.rhythm_runtime.restore(state.get("rhythm_runtime", {}), sim_time=self._sim_time)
        self.issue_manager.set_rhythm_controller(self.rhythm_runtime)
        self.outcome_feedback_runtime.restore(state.get("outcome_feedback_runtime", {}), sim_time=self._sim_time)
        auto_pause_reason = state.get("auto_pause_reason")
        self._auto_pause_reason = dict(auto_pause_reason) if isinstance(auto_pause_reason, dict) else None
        self._sync_gameplay_effect_runtime()
        # 恢复 Agent 记忆
        agent_memories = state.get("agent_memories", {})
        restored = 0
        for agent_id, memory_data in agent_memories.items():
            agent = self.agents.get(agent_id)
            if agent and memory_data:
                agent.deserialize_memory(memory_data)
                restored += 1
        logger.info("State loaded: sim_time=%.1f, %d/%d agents restored",
                    self._sim_time, restored, len(agent_memories))

    # ── 主循环 ──

    async def run(self, tick_interval: float | None = None) -> None:
        """启动主循环.

        时间推进逻辑：
        - 每次 tick 用真实流逝时间推进 sim_time（不受 LLM 阻塞影响）
        - Agent 调用作为后台 asyncio.Task，不阻塞 tick 循环
        - 定时器检查和前端广播每 tick 都执行
        """
        if tick_interval is not None:
            self._tick_interval = max(0.05, float(tick_interval))
        self._running = True
        self._paused = False
        self._last_real_time = time.monotonic()
        logger.info("Orchestrator started, %.0f seconds/day", self._seconds_per_day)

        # 启动 runtime 持久化后台任务
        if self.persistor:
            self.persistor.start()

        try:
            while self._running:
                if self._paused:
                    await asyncio.sleep(0.1)
                    continue

                # 用真实流逝时间推进 sim_time
                now = time.monotonic()
                elapsed = now - self._last_real_time
                self._last_real_time = now
                # 速度倍率：seconds_per_day 真实秒 = 1 游戏天
                # speed = 86400 / seconds_per_day 游戏秒/真实秒
                speed = GAME_SECONDS_PER_DAY / self._seconds_per_day
                self._sim_time += elapsed * speed

                # 标记 state 需要持久化
                if self.persistor:
                    self.persistor.mark_state_dirty()

                # 构建本次 tick 结果
                tick_result = await self._tick()

                # 广播给前端
                if self._on_tick and tick_result:
                    await self._on_tick(tick_result)

                # 暂停检查：等所有后台 Agent 任务完成
                if self._pausing:
                    if self._agent_tasks:
                        logger.info("Waiting for %d agent tasks to finish...", len(self._agent_tasks))
                        await asyncio.gather(*self._agent_tasks, return_exceptions=True)
                        self._agent_tasks.clear()
                    self._paused = True
                    self._pausing = False
                    logger.info("Simulation paused at sim_time=%.1f (day %.1f)",
                                self._sim_time, self.sim_day)

                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            logger.info("Orchestrator cancelled")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
        # 停止 flush loop 并做最后一次落盘
        if self.persistor:
            self.persistor.stop()

    async def drain_agent_tasks(self, timeout: float = 30.0) -> None:
        """等待所有后台 Agent 任务完成，确保不再有写文件操作.

        必须在覆盖 chain_log.jsonl / DB 之前调用，否则旧任务完成后会
        向已替换的文件追加条目，导致读档后出现幽灵对话时间线。
        超时后才强制 cancel，作为最后兜底。
        """
        if not self._agent_tasks:
            return
        logger.info("Waiting for %d agent tasks to finish before load...", len(self._agent_tasks))
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._agent_tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Agent tasks did not finish within %.1fs, force-cancelling.", timeout)
            for task in self._agent_tasks:
                task.cancel()
            await asyncio.gather(*self._agent_tasks, return_exceptions=True)
        self._agent_tasks.clear()
        logger.info("All agent tasks drained.")
        # drain 后同步落盘一次
        if self.persistor:
            await self.persistor.flush()

    async def _tick(self) -> TickResult:
        """一次 tick — 检查定时器、派发唤醒、处理玩家操作、收集状态."""
        result = TickResult(sim_time=self._sim_time)
        tick_feedback_deliveries: list[dict[str, Any]] = []

        # 清理已完成的后台任务
        done = {t for t in self._agent_tasks if t.done()}
        self._agent_tasks -= done

        rhythm_context = CausalConditionContext(
            sim_time=self._sim_time,
            world_state=self.world_state,
            actor_manager=self.actor_manager,
            objective_manager=self.objective_manager,
            event_history=tuple(getattr(self.causal_event_engine, "_event_history", ())),
            derived_metric_provider=self.derived_metric_catalog,
            knowledge_provider=self.player_knowledge_provider,
            issue_manager=self.issue_manager,
            action_catalog=self.action_catalog,
        )
        rhythm_poll = self.rhythm_runtime.poll(
            self._sim_time, issue_manager=self.issue_manager,
            condition_context=rhythm_context,
        )
        result.rhythm = rhythm_poll.to_dict()
        for index, item in enumerate(rhythm_poll.feedback):
            feedback = self.outcome_feedback_runtime.record(
                source_kind="rhythm",
                source_id=str(item.get("issue_id", item.get("rule_id", f"rhythm-{index}"))),
                receipt_id=f"rhythm:{item.get('kind', '')}:{item.get('issue_id', '')}:{item.get('rule_id', '')}:{item.get('sim_time', self._sim_time)}",
                sim_time=self._sim_time,
                display_name="Rhythm update",
                issue_type_id=str(item.get("issue_type_id", "")),
                summary=str(item.get("message", "Pacing state changed.")),
                reason=str(item.get("kind", "rhythm")),
                details={"issue_id": str(item.get("issue_id", "")), "rule_id": str(item.get("rule_id", ""))},
                condition_context=rhythm_context,
            )
            tick_feedback_deliveries.extend(feedback["delivered"])
        if (rhythm_poll.activated or rhythm_poll.transitions) and self.persistor:
            self.persistor.mark_state_dirty()

        # Poll deterministic producers first, then execute their output through one
        # chronological queue. Same-time ties retain the legacy source precedence:
        # temporal boundary -> lifecycle marker -> random event -> causal event.
        temporal_poll = self.temporal_runtime.poll(
            self._sim_time,
            condition_context=self._feedback_condition_context(),
        )
        if temporal_poll.pause_requests:
            requests = [dict(item) for item in temporal_poll.pause_requests]
            self._auto_pause_reason = {**requests[0], "requests": requests}
            self._pausing = True
            result.auto_pause = dict(self._auto_pause_reason)
        result.time_system = self.time_system_public_state()
        if (temporal_poll.markers or temporal_poll.events or temporal_poll.pause_requests) and self.persistor:
            self.persistor.mark_state_dirty()

        random_poll = self.random_event_generator.poll(self._sim_time)
        if random_poll.attempts and self.persistor:
            self.persistor.mark_state_dirty()

        timeline: list[tuple[float, int, int, str, Any, Any]] = []
        timeline_sequence = 0

        def enqueue_timeline(
            kind: str, occurrence_time: Any, source_rank: int, payload: Any, metadata: Any = None,
        ) -> None:
            nonlocal timeline_sequence
            event_time = self._effective_event_time(occurrence_time)
            heapq.heappush(
                timeline,
                (event_time, source_rank, timeline_sequence, kind, payload, metadata),
            )
            timeline_sequence += 1

        for event, event_metadata in zip(temporal_poll.events, temporal_poll.event_metadata):
            enqueue_timeline("temporal", event.generated_at, 0, event, dict(event_metadata))
        for marker in temporal_poll.markers:
            enqueue_timeline("lifecycle_marker", marker.get("sim_time", self._sim_time), 1, dict(marker))
        for event in random_poll.events:
            enqueue_timeline("random", event.generated_at, 2, event)

        lifecycle_applied: list[dict[str, Any]] = []
        lifecycle_skipped: list[dict[str, Any]] = []
        lifecycle_failed: list[dict[str, Any]] = []
        lifecycle_truncated = False
        lifecycle_runtime_error = ""
        lifecycle_remaining = MAX_LIFECYCLE_TRANSITIONS_PER_POLL

        def consume_lifecycle_poll(poll: LifecyclePollResult, *, poll_time: float) -> None:
            nonlocal lifecycle_remaining, lifecycle_truncated

            def stamp(rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
                return [{**dict(item), "sim_time": poll_time} for item in rows]

            applied = stamp(poll.applied)
            skipped = stamp(poll.skipped)
            failed = stamp(poll.failed)
            lifecycle_applied.extend(applied)
            lifecycle_skipped.extend(skipped)
            lifecycle_failed.extend(failed)
            lifecycle_remaining = max(
                0, lifecycle_remaining - len(applied) - len(skipped) - len(failed),
            )
            lifecycle_truncated = lifecycle_truncated or poll.truncated
            if applied:
                self._sync_gameplay_effect_runtime()
                if self.persistor:
                    self.persistor.mark_state_dirty()
                result.agent_errors.extend(self._last_dynamic_agent_sync_errors)
            for index, item in enumerate(applied):
                transition_id = str(item.get("transition_id", f"lifecycle-{index}"))
                feedback = self.outcome_feedback_runtime.record(
                    source_kind="lifecycle", source_id=transition_id,
                    receipt_id=f"lifecycle:{transition_id}:{item.get('receipt_id', item.get('marker_id', poll_time))}",
                    sim_time=poll_time, display_name=str(item.get("display_name", transition_id)),
                    summary=str(item.get("summary", "A lifecycle transition was applied.")),
                    reason=str(item.get("reason", transition_id)),
                    changes=list(item.get("changes", [])) if isinstance(item.get("changes", []), list) else [],
                    details={"transition_id": transition_id},
                    condition_context=self._feedback_condition_context(sim_time=poll_time),
                )
                tick_feedback_deliveries.extend(feedback["delivered"])

        def run_lifecycle_poll(
            poll_time: float, *, markers: tuple[dict[str, Any], ...] = (),
            evaluate_conditions: bool,
        ) -> None:
            nonlocal lifecycle_truncated, lifecycle_runtime_error
            if lifecycle_remaining <= 0:
                lifecycle_truncated = True
                return
            try:
                poll = self.lifecycle_runtime.poll(
                    poll_time, markers=markers,
                    world_state=self.world_state, actor_manager=self.actor_manager,
                    condition_context=self._feedback_condition_context(sim_time=poll_time),
                    max_transitions=lifecycle_remaining,
                    evaluate_conditions=evaluate_conditions,
                )
            except Exception as exc:
                logger.exception("Lifecycle poll crashed")
                lifecycle_runtime_error = str(exc)
                result.agent_errors.append({
                    "agent_id": "lifecycle_system",
                    "error": "lifecycle_poll_failed",
                    "detail": str(exc),
                })
                return
            consume_lifecycle_poll(poll, poll_time=poll_time)

        causal_processed = 0
        causal_budget = 128

        # The hard causal budget still bounds zero-delay storms. Strict cross-source
        # ordering is guaranteed while due causal work fits this tick's budget. Once
        # exhausted, already-materialized temporal/random entries finish draining and
        # older causal work remains pending for the next tick rather than being dropped.
        while timeline or (
            causal_processed < causal_budget
            and self.causal_event_engine.next_due_key(self._sim_time) is not None
        ):
            causal_key = (math.inf, "")
            if causal_processed < causal_budget:
                causal_key = self.causal_event_engine.next_due_key(self._sim_time) or causal_key
            next_timeline_key = timeline[0][:2] if timeline else (math.inf, math.inf)
            process_causal = causal_key[0] < next_timeline_key[0] or (
                causal_key[0] == next_timeline_key[0] and 3 < next_timeline_key[1]
            )

            if process_causal:
                causal_poll = self.causal_event_engine.poll(self._sim_time, max_events=1)
                if not causal_poll.events:
                    break
                causal_processed += causal_poll.due_count
                event = causal_poll.events[0]
                event_time = self._effective_event_time(event.generated_at)
                effect_count = len(event.world_commands)
                rhythm_event = self.rhythm_runtime.allow_event(
                    event.event_type, sim_time=event_time, issue_manager=self.issue_manager,
                )
                if not rhythm_event.get("allowed", True):
                    result.generated_events.append({
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "severity": event.severity,
                        "target_count": 0,
                        "generated_at": event.generated_at,
                        "source": "causal",
                        "world_effect_count": effect_count,
                        "world_effect_status": "suppressed",
                        "rhythm_status": "suppressed",
                        "rhythm_reason": str(rhythm_event.get("reason", "")),
                        "rhythm_rule_id": str(rhythm_event.get("rule_id", "")),
                    })
                    if self.persistor:
                        self.persistor.mark_state_dirty()
                    continue

                effect_status = "none"
                effect_ok = True
                if effect_count:
                    self._record_operational_metric("causal_effect_batches")
                    try:
                        effect_result = execute_causal_world_commands(
                            self.world_state, event, sim_time=event_time,
                        ) if self.world_state is not None else None
                    except Exception:
                        effect_ok = False
                        self._record_operational_metric("causal_effect_failures")
                        logger.exception("Causal world effect batch crashed for event %s", event.event_id)
                        result.generated_events.append({
                            "event_id": event.event_id,
                            "event_type": event.event_type,
                            "severity": event.severity,
                            "target_count": 0,
                            "generated_at": event.generated_at,
                            "source": "causal",
                            "world_effect_count": effect_count,
                            "world_effect_status": "error",
                            "error_code": "causal_effect_execution_error",
                        })
                    else:
                        if effect_result is None or not effect_result.ok:
                            effect_ok = False
                            self._record_operational_metric("causal_effect_rejections")
                            rejected = next(
                                (item for item in (effect_result.results if effect_result else []) if not item.ok),
                                None,
                            )
                            result.generated_events.append({
                                "event_id": event.event_id,
                                "event_type": event.event_type,
                                "severity": event.severity,
                                "target_count": 0,
                                "generated_at": event.generated_at,
                                "source": "causal",
                                "world_effect_count": effect_count,
                                "world_effect_status": "rejected",
                                "error_code": rejected.error_code if rejected else "world_state_unavailable",
                            })
                        else:
                            effect_status = "replayed" if effect_result.replayed_count == len(effect_result.results) else "applied"
                            self._sync_gameplay_effect_runtime()
                            self._record_operational_metric("causal_effect_commands", effect_count)
                            if self.persistor and effect_result.applied_count:
                                self.persistor.mark_state_dirty()
                if effect_ok:
                    self.inject_event(event)
                    result.generated_events.append({
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "severity": event.severity,
                        "target_count": len(event.target_agents),
                        "generated_at": event.generated_at,
                        "source": "causal",
                        "world_effect_count": effect_count,
                        "world_effect_status": effect_status,
                        "rhythm_status": "allowed",
                        "rhythm_reason": "",
                        "rhythm_rule_id": str(rhythm_event.get("rule_id", "")),
                    })
                if self.persistor:
                    self.persistor.mark_state_dirty()
                continue

            event_time, _source_rank, _sequence, kind, payload, metadata = heapq.heappop(timeline)
            if kind == "lifecycle_marker":
                run_lifecycle_poll(
                    event_time, markers=(dict(payload),), evaluate_conditions=False,
                )
                continue

            event = payload
            rhythm_event = self.rhythm_runtime.allow_event(
                event.event_type, sim_time=event_time, issue_manager=self.issue_manager,
            )
            allowed = rhythm_event.get("allowed", True)
            if kind == "temporal":
                event_metadata = dict(metadata or {})
                generated_metadata = {
                    **event_metadata,
                    "rhythm_status": "allowed" if allowed else "suppressed",
                    "rhythm_reason": str(rhythm_event.get("reason", "")),
                    "rhythm_rule_id": str(rhythm_event.get("rule_id", "")),
                }
                result.generated_events.append(generated_metadata)
                if not allowed:
                    continue
                self.inject_event(event)
                issue_type_id = str(event_metadata.get("issue_type_id", ""))
                if issue_type_id:
                    issue_payload = dict(event.initial_payload)
                    try:
                        opened = self._open_issue_at({
                            "external_id": f"time_system:{event.event_id}",
                            "issue_type_id": issue_type_id,
                            "type": event.event_type,
                            "source": {"kind": "system", "id": "time_system", "display_name": "????"},
                            "title": str(issue_payload.get("title", event.event_type)),
                            "summary": str(issue_payload.get("summary", issue_payload.get("description", event.event_type))),
                            "details": {"temporal_boundary": issue_payload.get("temporal_boundary", {}), "payload": issue_payload},
                            "urgency": issue_payload.get("urgency", "normal"),
                            "metadata": {"event_id": event.event_id, "boundary_event_id": issue_payload.get("boundary_event_id", "")},
                        }, sim_time=event_time)
                        result.new_issues.append(opened["issue"])
                        result.new_cases.append(opened["case"])
                        result.new_decision_requests.extend(opened["decision_requests"])
                    except IssueContractError as exc:
                        result.agent_errors.append({
                            "agent_id": "time_system", "error": "issue_contract_rejected",
                            "code": exc.code, "event_id": event.event_id,
                        })
                continue

            if allowed:
                self.inject_event(event)
            result.generated_events.append({
                "event_id": event.event_id,
                "event_type": event.event_type,
                "severity": event.severity,
                "target_count": len(event.target_agents) if allowed else 0,
                "generated_at": event.generated_at,
                "source": "random",
                "rhythm_status": "allowed" if allowed else "suppressed",
                "rhythm_reason": str(rhythm_event.get("reason", "")),
                "rhythm_rule_id": str(rhythm_event.get("rule_id", "")),
            })

        # Condition-triggered lifecycle rules belong to the current simulation
        # instant and run after all same-time authored/random/causal occurrences.
        run_lifecycle_poll(
            self._sim_time, markers=(), evaluate_conditions=True,
        )
        lifecycle_poll = LifecyclePollResult(
            applied=tuple(lifecycle_applied),
            skipped=tuple(lifecycle_skipped),
            failed=tuple(lifecycle_failed),
            truncated=lifecycle_truncated,
        )
        result.lifecycle = lifecycle_poll.to_dict()
        if lifecycle_runtime_error:
            result.lifecycle["runtime_error"] = lifecycle_runtime_error

        due_timers = self.timers.pop_due(self._sim_time)
        for timer in due_timers:
            self._pending_wakeups.append({
                "target": timer.target_agent,
                "payload": timer.payload,
                "chain_id": timer.chain_id,
                "timer_id": timer.timer_id,
            })
            self.fired_timers.append({
                "timer_id": timer.timer_id,
                "target": timer.target_agent,
                "from": timer.payload.get("from", ""),
                "message": timer.payload.get("message", "")[:100],
                "created_at": timer.created_at,
                "fired_at": self._sim_time,
            })

        # 1.5 Apply matured world events transactionally.
        if self.world_state is not None:
            matured_results = self.world_state.apply_matured_events(self._sim_time)
            for matured_result in matured_results:
                if not matured_result.ok:
                    logger.warning(
                        "Matured event rejected: command=%s code=%s error=%s",
                        matured_result.command_id,
                        matured_result.error_code,
                        matured_result.error,
                    )
            if any(
                matured_result.ok and not matured_result.replayed
                for matured_result in matured_results
            ):
                self._sync_gameplay_effect_runtime()
                if self.persistor:
                    self.persistor.mark_state_dirty()

        # 2. 处理待唤醒 — 拆分出皇帝奏折 vs Agent 调用
        if self._pending_wakeups:
            # 先做模糊匹配，统一 target_id
            for wakeup in self._pending_wakeups:
                target_id = wakeup["target"]
                agent = self.agents.get(target_id)
                official = self.court.get_official(target_id)
                if not agent and not official:
                    agent, official = self._fuzzy_find_agent(target_id)
                    if agent:
                        logger.info("Fuzzy matched '%s' → '%s'", target_id, agent.agent_id)
                        wakeup["target"] = agent.agent_id

            # 合并同目标的多条 Agent 消息（节省链路；玩家奏折保持独立）
            merged_agent_wakeups: dict[str, dict] = {}
            player_wakeups: list[dict] = []
            unresolved: list[str] = []

            for wakeup in self._pending_wakeups:
                target_id = wakeup["target"]
                official = self.court.get_official(target_id)
                if self._is_player_recipient(target_id, official):
                    player_wakeups.append(wakeup)
                elif self.agents.get(target_id) and self.is_agent_enabled(target_id):
                    if target_id not in merged_agent_wakeups:
                        merged_agent_wakeups[target_id] = {
                            "target": target_id,
                            "payload": dict(wakeup.get("payload", {})),
                            "chain_ids": [wakeup.get("chain_id", "")],
                        }
                    else:
                        # 合并消息内容，收集所有 chain_id
                        base = merged_agent_wakeups[target_id]
                        base["chain_ids"].append(wakeup.get("chain_id", ""))
                        base_payload = base["payload"]
                        new_msg = wakeup.get("payload", {}).get("message", "")
                        if new_msg:
                            base_payload["message"] = f"{base_payload.get('message', '')}\n\n---\n{new_msg}"
                        new_from = wakeup.get("payload", {}).get("from", "")
                        if new_from and new_from not in base_payload.get("from", ""):
                            base_payload["from"] = f"{base_payload.get('from', '')}, {new_from}".strip(", ")
                elif self.agents.get(target_id):
                    logger.info("Dropped wakeup for disabled agent %s", target_id)
                    result.agent_errors.append({"agent_id": target_id, "error": "disabled"})
                else:
                    unresolved.append(target_id)

            agent_wakeups = []
            for wakeup in player_wakeups:
                payload = dict(wakeup.get("payload", {}))
                chain_id = str(wakeup.get("chain_id", ""))
                event_id = str(payload.get("event_id", wakeup.get("event_id", "")))
                source_id = str(payload.get("source_id", payload.get("from", "")))
                external_id = str(payload.get("external_id", ""))
                if not external_id and (chain_id or event_id):
                    external_id = f"wakeup:{chain_id}:{event_id}:{wakeup.get('target', '')}"
                issue_payload = {
                    "external_id": external_id,
                    "issue_type_id": payload.get("issue_type_id", ""),
                    "type": payload.get("type", payload.get("event_type", "")),
                    "source": {
                        "kind": str(payload.get("source_kind", "agent" if source_id else "system")),
                        "id": source_id,
                        "display_name": str(payload.get("from", source_id)),
                    },
                    "summary": str(payload.get("summary", payload.get("message", ""))),
                    "details": dict(payload.get("details", payload)),
                    "evidence": list(payload.get("evidence", [])),
                    "urgency": payload.get("urgency", "normal"),
                    "metadata": {
                        **dict(payload.get("metadata", {})),
                        "chain_id": chain_id,
                        "event_id": event_id,
                        "target_id": str(wakeup.get("target", "")),
                    },
                }
                if payload.get("deadline_days") is not None:
                    issue_payload["deadline_days"] = payload["deadline_days"]
                issue_time = self._effective_event_time(
                    payload.get("generated_at", self._sim_time),
                )
                try:
                    opened = self._open_issue_at(issue_payload, sim_time=issue_time)
                except IssueContractError as exc:
                    logger.warning(
                        "Player wakeup rejected by issue contract: target=%s code=%s",
                        wakeup.get("target", ""), exc.code,
                    )
                    result.agent_errors.append({
                        "agent_id": str(wakeup.get("target", "")),
                        "error": "issue_contract_rejected",
                        "code": exc.code,
                        "path": exc.path,
                        "chain_id": chain_id,
                        "event_id": event_id,
                    })
                    continue
                issue = opened["issue"]
                result.new_issues.append(issue)
                result.new_cases.append(opened["case"])
                result.new_decision_requests.extend(opened["decision_requests"])
                memorial = next(
                    (item for item in self.memorials if item.get("memorial_id") == issue["issue_id"]),
                    None,
                )
                if memorial is not None:
                    result.new_memorials.append(memorial)
            for target_id, wakeup in merged_agent_wakeups.items():
                agent = self.agents[target_id]
                agent_wakeups.append((agent, wakeup))
            for target_id in unresolved:
                logger.warning("Agent not found: %s", target_id)
                result.agent_errors.append({"agent_id": target_id, "error": "not_found"})

            self._pending_wakeups.clear()

            # Agent 调用作为后台任务，不阻塞 tick
            if agent_wakeups:
                task = asyncio.create_task(
                    self._process_agents_background(agent_wakeups)
                )
                self._agent_tasks.add(task)
                task.add_done_callback(self._agent_tasks.discard)

        # 3. 处理玩家操作
        while not self._player_actions.empty():
            try:
                player_action = self._player_actions.get_nowait()
                await self._handle_player_action(player_action, result)
            except asyncio.QueueEmpty:
                break

        # 4. 在途公文
        for timer in self.timers.get_in_transit():
            remaining = max(0, timer.fire_at - self._sim_time)
            result.in_transit.append({
                "timer_id": timer.timer_id,
                "target": timer.target_agent,
                "from": timer.payload.get("from", ""),
                "message_preview": timer.payload.get("message", "")[:50],
                "remaining_seconds": round(remaining, 1),
                "fire_at": timer.fire_at,
            })

        # 5. Token 统计 + 后台任务数
        self._evaluate_objectives()
        if self._pending_objective_transitions:
            result.objective_transitions.extend(self._pending_objective_transitions)
            self._pending_objective_transitions.clear()

        for item in result.generated_events:
            if item.get("source") != "causal" or not item.get("event_id"):
                continue
            event_time = self._effective_event_time(item.get("generated_at", self._sim_time))
            feedback = self.outcome_feedback_runtime.record(
                source_kind="causal_event", source_id=str(item.get("event_id", "")),
                receipt_id=f"causal_event:{item.get('event_id', '')}", sim_time=event_time,
                display_name=str(item.get("event_type", "Causal event")),
                event_type=str(item.get("event_type", "")),
                summary=f"{item.get('event_type', 'Causal event')} occurred.",
                reason=str(item.get("world_effect_status", item.get("source", "causal"))),
                details={"event_type": str(item.get("event_type", ""))},
                condition_context=self._feedback_condition_context(sim_time=event_time),
            )
            tick_feedback_deliveries.extend(feedback["delivered"])
        feedback_poll = self.outcome_feedback_runtime.poll(
            sim_time=self._sim_time, condition_context=self._feedback_condition_context(),
        )
        tick_feedback_deliveries.extend(feedback_poll["delivered"])
        feedback_state = self.outcome_feedback_runtime.public_state()
        result.outcome_feedback = {
            "configured": feedback_state.get("configured", False),
            "enabled": feedback_state.get("enabled", False),
            "generation": feedback_state.get("generation", 0),
            "delivered": tick_feedback_deliveries,
            "pending_count": feedback_state.get("pending_count", 0),
        }
        if tick_feedback_deliveries and self.persistor:
            self.persistor.mark_state_dirty()

        result.token_usage = self.router.llm.token_counter.to_dict()
        result.token_usage["pending_tasks"] = len(self._agent_tasks)

        return result

    # ── 后台 Agent 处理 ──

    async def _process_agents_background(
        self,
        agent_wakeups: list[tuple[BaseAgent, dict[str, Any]]],
    ) -> None:
        """后台任务：并行调用 Agent → Router 批量处理 → 执行指令.

        这个方法在后台运行，不阻塞主循环的时间推进。
        """
        # 并行调用所有 Agent
        tasks = [
            self._call_agent_safe(agent, wakeup)
            for agent, wakeup in agent_wakeups
            if self.is_agent_enabled(agent.agent_id)
        ]
        results = await asyncio.gather(*tasks)

        # 每个 result 是 list[AgentAction]，展平后过滤
        actions: list[AgentAction] = []
        for action_list in results:
            for a in action_list:
                if a.action != "do_nothing":
                    actions.append(a)

        # Router 批量处理
        if actions:
            self._record_operational_metric("router_batches")
            self._record_operational_metric("router_actions", len(actions))
            try:
                commands = await self.router.process_batch(actions)
                self._record_operational_metric("router_commands", len(commands))

                # 记录 Router 决策日志
                self.router_log.append({
                    "sim_time": self._sim_time,
                    "input_actions": [
                        {"agent": a.agent_id, "action": a.action,
                         "content": a.content[:80], "urgency": a.urgency}
                        for a in actions
                    ],
                    "output_commands": [
                        {"type": cmd.command_type.value, "target": cmd.target or ",".join(cmd.targets),
                         "delay": cmd.delay_seconds,
                         "payload_preview": str(cmd.payload)[:80]}
                        for cmd in commands
                    ],
                    "command_count": len(commands),
                    "router_text": self.router.last_response_text[:200] if self.router.last_response_text else "",
                    "router_stats": dict(getattr(self.router, "last_stats", {}) or {}),
                })

                execution_results = await self._execute_commands(commands)
                for command, execution_result in execution_results:
                    source_action = command.source_action
                    source_id = source_action.agent_id if source_action else ""
                    source_agent = self.agents.get(source_id)
                    if source_agent is None:
                        continue
                    await source_agent.record_execution_feedback({
                        "phase": "dispatch",
                        "intent_id": (source_action.metadata or {}).get("intent_id", ""),
                        "chain_id": command.chain_id,
                        "command": command.command_type.value,
                        "target": command.target,
                        "targets": list(command.targets),
                        "result": execution_result,
                    })
                    if self.persistor:
                        self.persistor.mark_agent_dirty(source_id)
                self._record_operational_metric("router_batches_completed")
            except Exception as e:
                self._record_operational_metric("router_failures")
                logger.error("Router batch processing failed: %s", e)

    async def _call_agent_safe(
        self,
        agent: BaseAgent,
        wakeup: dict[str, Any],
    ) -> list[AgentAction]:
        """调用单个 Agent，记录 chain log，异常安全.

        返回需要进入 Router 批处理的 action 列表（request_action 已在此处直接分发）。
        """
        if not self.is_agent_enabled(agent.agent_id):
            logger.info("Skipped background turn for disabled agent %s", agent.agent_id)
            return []
        self._record_operational_metric("agent_turns")
        previous_outcome = getattr(agent, "last_turn_outcome", None)
        turn_error_recorded = False
        try:
            chain_ids = wakeup.get("chain_ids") or [
                wakeup.get("chain_id", f"chain_{uuid.uuid4().hex[:8]}")
            ]
            chain_ids = [c or f"chain_{uuid.uuid4().hex[:8]}" for c in chain_ids]
            agent_payload = dict(wakeup.get("payload", {}))
            agent_payload.setdefault("chain_id", chain_ids[0])
            agent_payload.setdefault("chain_ids", list(chain_ids))
            agent_payload.setdefault("sim_time", self._sim_time)
            agent_payload.setdefault("seconds_per_day", float(GAME_SECONDS_PER_DAY))
            result = await agent.process(agent_payload)
            outcome = getattr(agent, "last_turn_outcome", None)
            if outcome is not None and outcome is not previous_outcome:
                self._record_operational_metric("agent_turn_rounds", outcome.rounds)
                self._record_operational_metric(
                    "agent_turn_tool_calls", outcome.tool_calls
                )
                self._record_operational_metric(
                    "agent_turn_input_tokens", outcome.input_tokens
                )
                self._record_operational_metric(
                    "agent_turn_output_tokens", outcome.output_tokens
                )
                if outcome.error:
                    self._record_operational_metric("agent_turn_errors")
                    turn_error_recorded = True
                else:
                    self._record_operational_metric("agent_turn_completed")
            else:
                self._record_operational_metric("agent_turn_completed")
            # 兼容旧式单返回值
            if isinstance(result, AgentAction):
                actions = [result]
            elif isinstance(result, list):
                actions = result
            else:
                actions = [AgentAction(
                    agent_id=agent.agent_id,
                    action="do_nothing",
                    content=str(result),
                    original_intent="非标准返回",
                    is_distorting=False,
                )]

            # 合并 wakeup 携带 chain_ids 列表；未合并的旧格式兼容 chain_id 字段
            event_id = agent_payload.get("event_id", "")
            max_existing = max(self.chain_log.get_max_hop(c) for c in chain_ids)
            hop_count = max_existing + 1

            router_actions: list[AgentAction] = []
            for action in actions:
                action.metadata = dict(action.metadata or {})
                action.metadata.setdefault("chain_id", chain_ids[0])
                action.metadata.setdefault("parent_chain_ids", list(chain_ids))
                action.metadata.setdefault("hop_count", hop_count)
                action.metadata.setdefault("schema_version", "2.0")
                # request_action：绕过 Router，直接分发给 ActionAgent
                if action.metadata.get("fc_type") == "request_action":
                    for chain_id in chain_ids:
                        self.chain_log.append(ChainLogEntry(
                            chain_id=chain_id, event_id=event_id,
                            sim_time=self._sim_time, caller=agent.agent_id,
                            caller_agent_type=agent.agent_type,
                            fc_called="request_action",
                            original_text=action.original_intent,
                            transmitted_text=action.content,
                            is_distorted=False,
                            routed_to="action_agent",
                            status="delivered",
                        ))
                    if self.action_agent is not None:
                        task = asyncio.create_task(
                            self._run_action(
                                payload=action.metadata,
                                source_agent_id=agent.agent_id,
                                chain_id=chain_ids[0],
                                event_id=event_id,
                            )
                        )
                        self._agent_tasks.add(task)
                        task.add_done_callback(self._agent_tasks.discard)
                        await agent.record_execution_feedback({
                            "phase": "dispatch",
                            "intent_id": (action.metadata or {}).get("intent_id", ""),
                            "chain_id": chain_ids[0],
                            "command": "request_action",
                            "result": {"ok": True, "scheduled": True},
                        })
                    else:
                        logger.warning(
                            "request_action from %s but no ActionAgent registered",
                            agent.agent_id,
                        )
                        await agent.record_execution_feedback({
                            "phase": "dispatch",
                            "intent_id": (action.metadata or {}).get("intent_id", ""),
                            "chain_id": chain_ids[0],
                            "command": "request_action",
                            "result": {"ok": False, "error": "ActionAgent not registered"},
                        })
                    continue  # 不进入 Router 批处理

                router_actions.append(action)

            if not router_actions:
                if self.persistor:
                    self.persistor.mark_agent_dirty(agent.agent_id)
                return []

            # 对进入 Router 的 action 做 hop 检查
            if hop_count > MAX_HOPS:
                for action in router_actions:
                    for chain_id in chain_ids:
                        self.chain_log.append(ChainLogEntry(
                            chain_id=chain_id, event_id=event_id,
                            sim_time=self._sim_time, caller=agent.agent_id,
                            caller_agent_type=agent.agent_type, fc_called="submit_action",
                            original_text=action.original_intent,
                            transmitted_text=action.content,
                            is_distorted=action.is_distorting,
                            hop_count=hop_count, status="loop_detected",
                        ))
                logger.warning("Loop detected chains %s hop %d", chain_ids, hop_count)
                return []

            for action in router_actions:
                for chain_id in chain_ids:
                    self.chain_log.append(ChainLogEntry(
                        chain_id=chain_id, event_id=event_id,
                        sim_time=self._sim_time, caller=agent.agent_id,
                        caller_agent_type=agent.agent_type, fc_called="submit_action",
                        original_text=action.original_intent,
                        transmitted_text=action.content,
                        is_distorted=action.is_distorting,
                        hop_count=hop_count,
                        timer_id=wakeup.get("timer_id"),
                        wakeup_type="timer" if wakeup.get("timer_id") else "direct",
                        status="delivered",
                    ))

            # Agent 处理完毕，标记记忆脏
            if self.persistor:
                self.persistor.mark_agent_dirty(agent.agent_id)

            return router_actions

        except Exception as e:
            if not turn_error_recorded:
                self._record_operational_metric("agent_turn_errors")
            logger.error("Agent %s failed: %s", agent.agent_id, e)
            return []

    def _apply_matured_event(self, ev) -> None:
        """Compatibility wrapper; authoritative application remains transactional."""
        if self.world_state is None:
            return
        result = self.world_state.apply_matured_event(ev.event_id, self._sim_time)
        if not result.ok:
            logger.warning(
                "Matured event rejected: event=%s code=%s error=%s",
                ev.event_id, result.error_code, result.error,
            )
            return
        if self.persistor:
            self.persistor.mark_state_dirty()

    async def _run_action(
        self,
        payload: dict[str, Any],
        source_agent_id: str,
        chain_id: str,
        event_id: str = "",
    ) -> None:
        """运行 ActionAgent 模拟行动，完成后创建定时器把结果送回发起者.

        payload 期待字段：action_type / executor_id / target_location / description / resources
        （executor_id 缺省时用 source_agent_id 补齐）
        """
        if self.action_agent is None:
            return

        payload.setdefault("executor_id", source_agent_id)
        payload.setdefault("sim_time", self._sim_time)
        payload.setdefault("seconds_per_day", float(GAME_SECONDS_PER_DAY))
        payload.setdefault("chain_id", chain_id)
        try:
            result: ActionResult = await self.action_agent.process(payload)
        except Exception as e:
            logger.exception("ActionAgent.process failed: %s", e)
            self.chain_log.append(ChainLogEntry(
                chain_id=chain_id, event_id=event_id,
                sim_time=self._sim_time, caller="action_agent",
                caller_agent_type="world_sim",
                fc_called="complete_action",
                original_text=str(payload),
                transmitted_text=f"行动失败：{e}",
                status="delivered",
            ))
            self.action_log.append({
                "sim_time": self._sim_time,
                "chain_id": chain_id,
                "action_type": payload.get("action_type", ""),
                "executor_id": payload.get("executor_id", ""),
                "target_location": payload.get("target_location", ""),
                "success": False,
                "duration_days": 0.0,
                "result_description": f"行动失败：{e}",
                "error": str(e),
                "changes_made": [],
                "fc_trace": [],
                "fc_rounds": 0,
            })
            return

        # chain log：记录 ActionAgent 产出
        self.chain_log.append(ChainLogEntry(
            chain_id=chain_id, event_id=event_id,
            sim_time=self._sim_time, caller="action_agent",
            caller_agent_type="world_sim",
            fc_called="complete_action",
            original_text=str(result.changes_made),
            transmitted_text=result.result_description,
            routed_to=source_agent_id,
            status="delivered",
        ))

        # action log：记录 ActionAgent 内部 FC 轨迹（上帝视角展示用）
        self.action_log.append({
            "sim_time": self._sim_time,
            "chain_id": chain_id,
            "action_type": result.action_type,
            "executor_id": result.executor_id,
            "target_location": result.target_location,
            "success": result.success,
            "duration_days": result.duration_days,
            "duration_source": result.duration_source,
            "duration_rule_id": result.duration_rule_id,
            "duration_components": result.duration_components,
            "duration_clamped": result.duration_clamped,
            "runtime_generation": result.runtime_generation,
            "time_rule_fingerprint": result.time_rule_fingerprint,
            "result_description": result.result_description,
            "error": result.error,
            "changes_made": result.changes_made,
            "fc_trace": result.fc_trace,
            "fc_rounds": len(result.fc_trace),
        })

        # 若无接收方，直接结束
        try:
            causal_source = build_action_causal_event(
                result, payload, chain_id=chain_id, source_event_id=event_id,
                generated_at=self._sim_time,
            )
        except ValueError as exc:
            self._record_operational_metric("action_causal_event_rejections")
            logger.warning("Action causal event rejected: %s", exc)
        else:
            if causal_source is not None:
                self.inject_event(causal_source)
                self._record_operational_metric("action_causal_events")

        if not source_agent_id:
            return

        # 结果以消息形式送回发起者：延迟 = duration_days
        duration_seconds = max(0.0, float(result.duration_days)) * GAME_SECONDS_PER_DAY
        timer = TimerEntry(
            fire_at=self._sim_time + duration_seconds,
            target_agent=source_agent_id,
            payload={
                "message": f"行动结果：{result.result_description}",
                "from": "世界模拟",
                "type": "action_result",
                "action_type": result.action_type,
                "target_location": result.target_location,
                "success": result.success,
                "changes": result.changes_made,
                "intent_id": payload.get("intent_id", ""),
            },
            chain_id=chain_id,
            created_at=self._sim_time,
        )
        self.timers.schedule(timer)
        logger.info(
            "Action completed: %s @ %s → %s in %.1f days (→ %s)",
            result.action_type, result.target_location,
            "成功" if result.success else "失败",
            result.duration_days, source_agent_id,
        )

    async def _execute_commands(
        self, commands: list[RouterCommand],
    ) -> list[tuple[RouterCommand, dict[str, Any]]]:
        """Execute routed commands with a second idempotency guard and chain logging."""
        ctx = self._build_fc_context()
        results: list[tuple[RouterCommand, dict[str, Any]]] = []
        for cmd in commands:
            source_id = cmd.source_agent_id or (cmd.source_action.agent_id if cmd.source_action else "router")
            route_targets = (
                list(cmd.metadata.get("route_target_ids") or [])
                if cmd.command_type == RouterCommandType.INVOKE_CAPABILITY
                else (list(cmd.targets) if cmd.targets else ([cmd.target] if cmd.target else []))
            )
            has_route_edge = getattr(self.chain_log, "has_route_edge", None)
            repeated_target = None
            if callable(has_route_edge):
                repeated_target = next((
                    target_id for target_id in route_targets
                    if target_id and target_id != source_id
                    and has_route_edge(cmd.chain_id, source_id, target_id)
                ), None)
            if repeated_target is not None:
                execution_result = {
                    "ok": False,
                    "status": "loop_detected",
                    "error": f"persisted chain edge already exists: {source_id} -> {repeated_target}",
                    "command_id": cmd.command_id,
                }
                reject_command = getattr(self.router, "reject_command", None)
                if callable(reject_command):
                    reject_command(cmd, "loop_detected", execution_result["error"])
                results.append((cmd, execution_result))
                continue
            execution_key = cmd.idempotency_key or cmd.command_id
            if execution_key and execution_key in self._executed_command_keys:
                execution_result = {
                    "ok": False,
                    "status": "duplicate",
                    "error": "command was already claimed by the execution layer",
                    "command_id": cmd.command_id,
                }
                reject_command = getattr(self.router, "reject_command", None)
                if callable(reject_command):
                    reject_command(cmd, "duplicate", execution_result["error"])
                results.append((cmd, execution_result))
                continue
            if execution_key:
                if len(self._executed_command_order) == self._executed_command_order.maxlen:
                    oldest = self._executed_command_order[0]
                    self._executed_command_keys.discard(oldest)
                self._executed_command_order.append(execution_key)
                self._executed_command_keys.add(execution_key)
            ctx.chain_id = cmd.chain_id
            ctx.tool_call_id = cmd.command_id
            ctx.source_action = cmd.source_action
            fc_name = command_type_to_fc_name(cmd.command_type)
            args = command_to_fc_args(cmd)
            try:
                if cmd.command_type == RouterCommandType.INVOKE_CAPABILITY:
                    fc_name = "invoke_capability"
                    invocation = dict(cmd.payload or {})
                    invocation["expected_generation"] = invocation.get("generation")
                    execution_result = await self.invoke_capability(
                        str(invocation.get("capability_id", "")),
                        invocation,
                        audience="agent",
                        source_id=source_id,
                        chain_id=cmd.chain_id,
                    )
                else:
                    execution_result = await dispatch_fc(fc_name, ctx, args)
            except Exception as e:
                logger.exception("%s failed: %s", fc_name, e)
                execution_result = {"ok": False, "error": str(e)}
            source_action = cmd.source_action
            log_targets = route_targets or [None]
            for log_target in log_targets:
                self.chain_log.append(ChainLogEntry(
                    chain_id=cmd.chain_id, sim_time=self._sim_time,
                    caller=source_id, caller_agent_type="router", fc_called=fc_name,
                    original_text=source_action.original_intent if source_action else "",
                    transmitted_text=str(cmd.payload),
                    is_distorted=bool(source_action.is_distorting) if source_action else False,
                    hop_count=cmd.hop_count, routed_to=log_target,
                    status="delivered" if execution_result.get("ok") else "dead_letter",
                ))
            results.append((cmd, execution_result))
        return results

    def _build_fc_context(self) -> FCContext:
        """组装 Orchestrator 主模拟路径的 FCContext."""
        return FCContext(
            world_state=getattr(self.action_agent, "world_state", None),
            court=self.court,
            timers=self.timers,
            chain_log=self.chain_log,
            action_agent=self.action_agent,
            pending_wakeups=self._pending_wakeups,
            run_action=self._run_action,
            agent_tasks=self._agent_tasks,
            sim_time=self._sim_time,
            seconds_per_day=GAME_SECONDS_PER_DAY,
            caller_id="orchestrator",
            duration_estimator=(
                self.action_agent.estimate_duration if self.action_agent is not None else None
            ),
        )

    async def _handle_player_action(
        self, action: PlayerAction, result: TickResult,
    ) -> None:
        """处理皇帝的操作.

        确定性逻辑：
        - hold：不触发任何唤醒
        - approve/reject：找太监 → 唤醒太监传达旨意
        - decree：找太监 → 太监传达 → 广播给内阁/六部
        """
        chain_id = f"chain_{uuid.uuid4().hex[:8]}"

        if action.action_type == PlayerActionType.HOLD:
            logger.info("Emperor holds memorial %s", action.memorial_id)
            self.chain_log.append(ChainLogEntry(
                chain_id=chain_id, sim_time=self._sim_time,
                caller="皇帝", caller_agent_type="player",
                fc_called="hold", original_text=f"留中奏折{action.memorial_id}",
                transmitted_text="留中不发", status="delivered",
            ))
            # 留中也标记 handled，从活跃奏折列表中移除
            self.memorials = [m for m in self.memorials if m.get("memorial_id") != action.memorial_id]
            if self.persistor:
                self.persistor.mark_state_dirty()
            return

        # 找太监（皇帝的直属下级中 role=eunuch 的）
        eunuch = None
        emperor = self.court.get_official("皇帝")
        if emperor:
            subs = self.court.get_subordinates("皇帝")
            for s in subs:
                if s.role == "eunuch":
                    eunuch = s
                    break
        # 回退：找不到太监就找皇帝的任何直属下级
        if not eunuch and emperor:
            subs = self.court.get_subordinates("皇帝")
            if subs:
                eunuch = subs[0]

        if action.action_type == PlayerActionType.ISSUE_DECREE:
            decree_text = action.content
            self.chain_log.append(ChainLogEntry(
                chain_id=chain_id, sim_time=self._sim_time,
                caller="皇帝", caller_agent_type="player",
                fc_called="issue_decree", original_text=decree_text,
                transmitted_text=decree_text, status="delivered",
            ))
            if eunuch and eunuch.id in self.agents:
                # 唤醒太监，太监 Agent 会决定怎么传达
                self._pending_wakeups.append({
                    "target": eunuch.id,
                    "payload": {
                        "message": f"皇帝下旨：{decree_text}",
                        "from": "皇帝",
                        "type": "imperial_decree",
                    },
                    "chain_id": chain_id,
                })
                logger.info("Decree dispatched to eunuch %s", eunuch.id)
            else:
                # 没太监，直接丢给 Router 广播
                agent_action = AgentAction(
                    agent_id="皇帝", action="issue_decree",
                    content=decree_text, original_intent=decree_text,
                    is_distorting=False, urgency="critical",
                    metadata={"chain_id": chain_id, "parent_chain_ids": [chain_id], "hop_count": 0, "schema_version": "2.0"},
                )
                commands = await self.router.process_batch([agent_action])
                await self._execute_commands(commands)
        else:
            # approve / reject
            verb = "批准" if action.action_type == PlayerActionType.APPROVE else "驳回"
            response_text = f"{verb}：{action.note}" if action.note else verb
            self.chain_log.append(ChainLogEntry(
                chain_id=chain_id, sim_time=self._sim_time,
                caller="皇帝", caller_agent_type="player",
                fc_called=action.action_type.value,
                original_text=f"{verb}奏折{action.memorial_id}",
                transmitted_text=response_text, status="delivered",
            ))
            # 从活跃奏折列表中移除
            self.memorials = [m for m in self.memorials if m.get("memorial_id") != action.memorial_id]
            if self.persistor:
                self.persistor.mark_state_dirty()
            if eunuch and eunuch.id in self.agents:
                self._pending_wakeups.append({
                    "target": eunuch.id,
                    "payload": {
                        "message": f"皇帝{verb}了奏折：{response_text}。传达下去。",
                        "from": "皇帝",
                        "type": "imperial_response",
                        "memorial_id": action.memorial_id,
                    },
                    "chain_id": chain_id,
                })
                logger.info("Emperor %s memorial %s, dispatched to eunuch %s",
                            verb, action.memorial_id, eunuch.id)
            else:
                logger.warning("No eunuch found, emperor response not dispatched")
