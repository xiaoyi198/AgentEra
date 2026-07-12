"""行动 Agent — 使用通用 TurnEngine 模拟并落账世界行动。"""

from __future__ import annotations

import logging
import math
import uuid
from pathlib import Path
from typing import Any

from .fc_handlers import FCContext, dispatch_fc
from .fc_tools import ACTION_AGENT_TOOLS
from .court_graph import TRAVEL_SPEED_LI_PER_DAY
from .llm_client import LLMClient
from .runtime_bindings import ACTION_TASK, RuntimeBindingsHub, RuntimeTaskBinding
from .time_simulator import TimeEstimate, TimeSimulator
from .turn_engine import TurnBudget, TurnEngine
from .types import ActionResult
from .world_state import WorldState

logger = logging.getLogger(__name__)


class ActionAgent:
    """无跨会话记忆的世界法则执行者。"""

    def __init__(
        self,
        llm_client: LLMClient,
        world_state: WorldState,
        prompt_template_path: str = "daming_court/prompts/action_agent.txt",
        max_rounds: int = 10,
        court: Any = None,
        max_tool_calls: int = 40,
        max_turn_tokens: int = 50_000,
        max_turn_seconds: float = 120.0,
        no_progress_rounds: int = 2,
        prompt_template: str | None = None,
    ) -> None:
        self.llm = llm_client
        self.world_state = world_state
        self._prompt_template_path = prompt_template_path
        self._prompt_template = prompt_template
        self._runtime_bindings: RuntimeBindingsHub | None = None
        self.max_rounds = max(1, int(max_rounds))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.max_turn_tokens = max(1, int(max_turn_tokens))
        self.max_turn_seconds = max(0.1, float(max_turn_seconds))
        self.no_progress_rounds = max(1, int(no_progress_rounds))
        self.court = court
        self.time_simulator = TimeSimulator()
        self._turn_engine = TurnEngine()

    def configure_time_rules(self, rules: list[dict[str, Any]]) -> None:
        """Atomically publish authoritative action-duration rules.

        A new immutable rule engine is published instead of mutating the active
        instance. Logical calls capture the engine once, so an in-flight action
        keeps the generation that accepted it while later actions see new rules.
        """
        self.time_simulator = TimeSimulator(rules)

    def _available_resource_value(self, executor_id: str) -> float:
        if not executor_id:
            return 0.0
        try:
            values = [
                item.base_value for item in self.world_state.get_inventory(executor_id)
                if item.quantity != -1 and math.isfinite(item.base_value) and item.base_value > 0
            ]
            return float(sum(values))
        except Exception:
            logger.debug("Unable to derive anchored resource capacity", exc_info=True)
            return 0.0

    def _travel_days(self, executor_id: str, target_location: str) -> float:
        if not self.court or not target_location:
            return 0.0
        try:
            official = self.court.get_official(executor_id)
            source_location = official.location_id if official else ""
            if not source_location or source_location == target_location:
                return 0.0
            source = self.world_state.get_coordinates(source_location)
            target = self.world_state.get_coordinates(target_location)
            if not source or not target:
                return 0.0
            distance = math.hypot(source[0] - target[0], source[1] - target[1])
            return max(0.0, distance / TRAVEL_SPEED_LI_PER_DAY)
        except Exception:
            logger.debug("Unable to derive deterministic travel duration", exc_info=True)
            return 0.0

    def estimate_duration(
        self,
        *,
        action_type: str,
        executor_id: str,
        target_location: str,
        proposed_days: Any,
        severity: Any = 5,
        workload_days: Any | None = None,
        success: bool = True,
        simulator: TimeSimulator | None = None,
    ) -> TimeEstimate:
        engine = simulator if simulator is not None else self.time_simulator
        return engine.estimate(
            action_type=action_type,
            proposed_days=proposed_days,
            severity=severity,
            workload_days=workload_days,
            resource_value=self._available_resource_value(executor_id),
            distance_days=self._travel_days(executor_id, target_location),
            success=success,
        )

    def bind_runtime_bindings(self, hub: RuntimeBindingsHub) -> None:
        self._runtime_bindings = hub

    def _capture_runtime_binding(self) -> RuntimeTaskBinding:
        if self._runtime_bindings is not None:
            return self._runtime_bindings.capture(ACTION_TASK)
        return RuntimeTaskBinding(
            task_id=ACTION_TASK,
            generation=0,
            prompt=self._load_template(),
            llm=self.llm,
        )

    @property
    def agent_id(self) -> str:
        return "action_agent"

    @property
    def agent_type(self) -> str:
        return "world_sim"

    async def process(self, payload: dict[str, Any]) -> ActionResult:
        action_type = payload.get("action_type", "unknown")
        executor_id = payload.get("executor_id", "")
        target_location = payload.get("target_location", "")
        description = payload.get("description", "")
        resources = payload.get("resources", "（未指定）")
        sim_time = float(payload.get("sim_time", 0.0))
        seconds_per_day = float(payload.get("seconds_per_day", 86400.0))
        chain_id = str(payload.get("chain_id", "") or "")
        binding = self._capture_runtime_binding()
        # Freeze world-time policy together with the Prompt/client binding. DLC
        # reload may publish a new engine while this multi-round call is awaiting
        # the model, but committed work must retain the accepting generation.
        time_simulator = self.time_simulator

        system_prompt = self._build_system_prompt(
            action_type=action_type,
            executor_id=executor_id,
            target_location=target_location,
            description=description,
            resources=resources,
            sim_time=sim_time,
            seconds_per_day=seconds_per_day,
            prompt_template=binding.prompt,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"请模拟这次行动：{action_type} @ {target_location}。\n"
                    f"执行者：{executor_id}\n"
                    f"描述：{description}\n"
                    "按工作流程调用工具推进，最后用 complete_action 结束。"
                ),
            },
        ]

        changes_made: list[dict[str, Any]] = []
        ctx = FCContext(
            world_state=self.world_state,
            court=self.court,
            changes_made=changes_made,
            caller_id=executor_id,
            sim_time=sim_time,
            seconds_per_day=seconds_per_day,
            chain_id=chain_id,
            duration_estimator=lambda **kwargs: self.estimate_duration(
                **kwargs, simulator=time_simulator
            ),
        )

        async def execute_tool(
            name: str,
            args: dict[str, Any],
            _tool_call: dict[str, Any],
        ) -> dict[str, Any]:
            ctx.tool_call_id = str(_tool_call.get("id", "") or "")
            return await dispatch_fc(name, ctx, args)

        outcome = await self._turn_engine.run(
            llm=binding.llm,
            messages=messages,
            tools=ACTION_AGENT_TOOLS,
            execute_tool=execute_tool,
            terminal_predicate=lambda name, result: name == "complete_action",
            trace_context={
                "trace_id": str(payload.get("trace_id", "") or uuid.uuid4().hex),
                "chain_id": chain_id,
                "turn_id": str(payload.get("turn_id", "") or uuid.uuid4().hex),
                "agent_id": self.agent_id,
                "sim_time_snapshot": sim_time,
                "runtime_generation": binding.generation,
            },
            budget=TurnBudget(
                max_rounds=self.max_rounds,
                max_tool_calls=self.max_tool_calls,
                max_tokens=self.max_turn_tokens,
                max_seconds=self.max_turn_seconds,
                no_progress_rounds=self.no_progress_rounds,
            ),
            temperature=0.7,
        )

        result_description = ""
        proposed_duration: Any = 1.0
        success = True
        error = outcome.error

        if outcome.terminal_result is not None:
            terminal = outcome.terminal_result
            result_description = str(terminal.get("result_description", "") or "")
            proposed_duration = terminal.get("duration_days", 1.0)
            success = bool(terminal.get("success", True))
        elif outcome.stop_reason == "no_tool_calls" and outcome.last_response is not None:
            result_description = outcome.last_response.content or "行动未产生明确结果"
        elif outcome.stop_reason == "llm_error":
            success = False
            result_description = f"行动因系统故障中断: {outcome.error or '未知错误'}"
        else:
            success = False
            reason_text = {
                "max_rounds": "超过最大轮次",
                "max_tool_calls": "超过最大工具调用数",
                "token_budget": "超过 token 预算",
                "time_budget": "超过时间预算",
                "no_progress": "连续多轮无有效进展",
            }.get(outcome.stop_reason, outcome.stop_reason or "未完成")
            result_description = f"行动模拟{reason_text}，未能产出明确结论"
            logger.warning(
                "ActionAgent stopped without complete_action: %s", outcome.stop_reason
            )

        estimate = self.estimate_duration(
            action_type=str(action_type),
            executor_id=str(executor_id),
            target_location=str(target_location),
            proposed_days=proposed_duration,
            severity=payload.get("severity", 5),
            workload_days=payload.get("workload_days"),
            success=success,
            simulator=time_simulator,
        )

        return ActionResult(
            action_type=action_type,
            executor_id=executor_id,
            target_location=target_location,
            result_description=result_description,
            duration_days=estimate.duration_days,
            duration_source=estimate.source,
            duration_rule_id=estimate.rule_id,
            duration_components=estimate.components,
            duration_clamped=estimate.clamped,
            runtime_generation=binding.generation,
            time_rule_fingerprint=time_simulator.fingerprint,
            changes_made=changes_made,
            success=success,
            error=error,
            fc_trace=outcome.trace,
        )

    def _build_system_prompt(
        self,
        action_type: str,
        executor_id: str,
        target_location: str,
        description: str,
        resources: str,
        sim_time: float = 0.0,
        seconds_per_day: float = 86400.0,
        prompt_template: str | None = None,
    ) -> str:
        template = prompt_template if prompt_template is not None else self._load_template()
        executor_name = executor_id
        exec_ent = self.world_state.get_entity(executor_id) if executor_id else None
        if exec_ent:
            executor_name = exec_ent.name
        world_context = self.world_state.get_context_for_action(
            location_id=target_location,
            executor_id=executor_id or None,
            sim_time=sim_time,
            seconds_per_day=seconds_per_day,
        )
        return template.format(
            executor_name=executor_name,
            action_type=action_type,
            target_location=target_location,
            action_description=description,
            resources=resources,
            world_context=world_context,
        )

    def _load_template(self) -> str:
        if self._prompt_template is not None:
            return self._prompt_template
        path = Path(self._prompt_template_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "你是世界模拟器。\n"
            "行动：{action_type}\n执行者：{executor_name}\n"
            "目标：{target_location}\n描述：{action_description}\n"
            "资源：{resources}\n\n{world_context}\n\n"
            "请通过工具调用模拟行动，最后用 complete_action 结束。"
        )
