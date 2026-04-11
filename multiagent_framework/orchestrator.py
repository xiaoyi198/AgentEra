"""Orchestrator — 主循环，驱动整个模拟."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Callable, Awaitable

from .action_agent import ActionAgent
from .agent_runtime import BaseAgent, OfficialAgent
from .chain_log import ChainLog
from .court_graph import CourtGraph
from .memory_compressor import MemoryCompressor
from .fc_handlers import (
    FCContext,
    command_to_fc_args,
    command_type_to_fc_name,
    dispatch_fc,
)
from .router import RouterAgent, MAX_HOPS
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
        self._memory_compressor = memory_compressor
        self._seconds_per_day = seconds_per_day
        self._on_tick = on_tick

        # 时间状态
        self._sim_time: float = 0.0
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

        # Router 决策日志（调试用，限长防 OOM）
        self.router_log: deque[dict[str, Any]] = deque(maxlen=2000)

        # ActionAgent 执行日志（调试用：含多轮 FC trace）
        self.action_log: deque[dict[str, Any]] = deque(maxlen=2000)

        # 已触发定时器历史（调试用）
        self.fired_timers: deque[dict[str, Any]] = deque(maxlen=2000)

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
        self.agents[agent.agent_id] = agent
        logger.info("Registered agent: %s (%s)", agent.agent_id, agent.agent_type)

    def get_agent(self, agent_id: str) -> BaseAgent | None:
        return self.agents.get(agent_id)

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

    def inject_event(self, event: EventObject) -> None:
        """注入一个事件，为每个 target_agent 创建即时唤醒."""
        for agent_id in event.target_agents:
            self._pending_wakeups.append({
                "target": agent_id,
                "payload": {
                    "description": f"【{event.event_type}】严重程度{event.severity}/10 — {event.initial_payload}",
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    **event.initial_payload,
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
        self._pausing = True
        logger.info("Pause requested, waiting for %d agent tasks...", len(self._agent_tasks))

    def resume(self) -> None:
        self._paused = False
        self._pausing = False
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
            "agent_memories": agent_memories,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._sim_time = state.get("sim_time", 0.0)
        self._seconds_per_day = state.get("seconds_per_day", 600.0)
        self.timers.load_from_db()
        self.chain_log.load_from_file()
        self.router.update_time_scale(self._seconds_per_day)
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

    async def run(self, tick_interval: float = 0.25) -> None:
        """启动主循环.

        时间推进逻辑：
        - 每次 tick 用真实流逝时间推进 sim_time（不受 LLM 阻塞影响）
        - Agent 调用作为后台 asyncio.Task，不阻塞 tick 循环
        - 定时器检查和前端广播每 tick 都执行
        """
        self._running = True
        self._paused = False
        self._last_real_time = time.monotonic()
        logger.info("Orchestrator started, %.0f seconds/day", self._seconds_per_day)

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

                await asyncio.sleep(tick_interval)
        except asyncio.CancelledError:
            logger.info("Orchestrator cancelled")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

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

    async def _tick(self) -> TickResult:
        """一次 tick — 检查定时器、派发唤醒、处理玩家操作、收集状态."""
        result = TickResult(sim_time=self._sim_time)

        # 清理已完成的后台任务
        done = {t for t in self._agent_tasks if t.done()}
        self._agent_tasks -= done

        # 1. 检查到期定时器 → 加入待唤醒队列
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
                if official and official.role == "player":
                    player_wakeups.append(wakeup)
                elif self.agents.get(target_id):
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
                else:
                    unresolved.append(target_id)

            agent_wakeups = []
            for wakeup in player_wakeups:
                result.new_memorials.append({
                    "memorial_id": f"mem_{uuid.uuid4().hex[:8]}",
                    "from": wakeup.get("payload", {}).get("from", "unknown"),
                    "content": wakeup.get("payload", {}).get("message", ""),
                    "chain_id": wakeup.get("chain_id", ""),
                    "event_id": wakeup.get("payload", {}).get("event_id", ""),
                    "received_at": self._sim_time,
                })
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
            try:
                commands = await self.router.process_batch(actions)

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
                })

                await self._execute_commands(commands)
            except Exception as e:
                logger.error("Router batch processing failed: %s", e)

    async def _call_agent_safe(
        self,
        agent: BaseAgent,
        wakeup: dict[str, Any],
    ) -> list[AgentAction]:
        """调用单个 Agent，记录 chain log，异常安全.

        返回需要进入 Router 批处理的 action 列表（request_action 已在此处直接分发）。
        """
        try:
            result = await agent.process(wakeup.get("payload", {}))
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
            chain_ids = wakeup.get("chain_ids") or [wakeup.get("chain_id", f"chain_{uuid.uuid4().hex[:8]}")]
            chain_ids = [c or f"chain_{uuid.uuid4().hex[:8]}" for c in chain_ids]
            event_id = wakeup.get("payload", {}).get("event_id", "")

            router_actions: list[AgentAction] = []
            for action in actions:
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
                    else:
                        logger.warning(
                            "request_action from %s but no ActionAgent registered",
                            agent.agent_id,
                        )
                    continue  # 不进入 Router 批处理

                router_actions.append(action)

            if not router_actions:
                return []

            # 对进入 Router 的 action 做 hop 检查
            max_existing = max(self.chain_log.get_max_hop(c) for c in chain_ids)
            hop_count = max_existing + 1

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
            return router_actions

        except Exception as e:
            logger.error("Agent %s failed: %s", agent.agent_id, e)
            return []

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
            "result_description": result.result_description,
            "error": result.error,
            "changes_made": result.changes_made,
            "fc_trace": result.fc_trace,
            "fc_rounds": len(result.fc_trace),
        })

        # 若无接收方，直接结束
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

    async def _execute_commands(self, commands: list[RouterCommand]) -> None:
        """执行 Router 产生的指令 —— 统一走 fc_handlers.dispatch_fc."""
        ctx = self._build_fc_context()
        for cmd in commands:
            ctx.chain_id = cmd.chain_id
            ctx.source_action = cmd.source_action
            fc_name = command_type_to_fc_name(cmd.command_type)
            args = command_to_fc_args(cmd)
            # RouterCommand 的 delay_seconds 是权威值，直接还原成 delay_days
            if cmd.command_type == RouterCommandType.CREATE_TIMER and cmd.delay_seconds:
                args["delay_days"] = cmd.delay_seconds / GAME_SECONDS_PER_DAY
            try:
                await dispatch_fc(fc_name, ctx, args)
            except Exception as e:
                logger.exception("dispatch_fc(%s) failed: %s", fc_name, e)

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
