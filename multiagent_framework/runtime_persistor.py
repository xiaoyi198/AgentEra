"""RuntimePersistor — 把 Orchestrator 的 in-memory 状态落盘到 runtime/ 目录."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .runtime_paths import STATE_FILE, AGENTS_DIR

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: str) -> None:
    """原子写入：先写 .tmp 再 rename，防崩溃损坏."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)


class RuntimePersistor:
    """负责把 Orchestrator 的 in-memory 状态落盘到 runtime/ 目录.

    设计要点：
    - mark_*_dirty 只标记集合，不做 IO
    - flush() 批量序列化并原子写入
    - run_flush_loop 每 interval 秒执行一次 flush
    """

    def __init__(self, orch: Orchestrator) -> None:
        self._orch = orch
        self._dirty_agents: set[str] = set()
        self._state_dirty = False
        self._flush_task: asyncio.Task | None = None

    def mark_agent_dirty(self, agent_id: str) -> None:
        """标记某个 Agent 的记忆需要落盘."""
        self._dirty_agents.add(agent_id)

    def mark_state_dirty(self) -> None:
        """标记 state.json 需要落盘（sim_time / seconds_per_day / memorials 变化时）."""
        self._state_dirty = True

    async def flush(self) -> None:
        """把所有 dirty 状态原子写入磁盘."""
        # 1. state.json
        if self._state_dirty:
            try:
                state_data: dict[str, Any] = {
                    "sim_time": self._orch._sim_time,
                    "seconds_per_day": self._orch._seconds_per_day,
                    "memorials": self._orch.memorials,
                    "random_events": (
                        self._orch.random_event_generator.snapshot()
                        if hasattr(self._orch, "random_event_generator")
                        else {}
                    ),
                    "causal_events": (
                        self._orch.causal_event_engine.snapshot()
                        if hasattr(self._orch, "causal_event_engine")
                        else {}
                    ),
                    "time_rules": (
                        self._orch.time_rule_status()
                        if hasattr(self._orch, "time_rule_status")
                        else {}
                    ),
                    "action_runtime": (
                        self._orch.action_catalog.snapshot()
                        if hasattr(self._orch, "action_catalog")
                        else {}
                    ),
                }
                _atomic_write(
                    STATE_FILE,
                    json.dumps(state_data, ensure_ascii=False, indent=2),
                )
                self._state_dirty = False
            except Exception as e:
                logger.error("Failed to flush state.json: %s", e)

        # 2. agents/*.json
        if self._dirty_agents:
            dirty_copy = set(self._dirty_agents)
            self._dirty_agents.clear()
            for agent_id in dirty_copy:
                agent = self._orch.agents.get(agent_id)
                if not agent:
                    continue
                try:
                    mem_data = await agent.snapshot_memory()
                    path = AGENTS_DIR / f"{agent_id}.json"
                    _atomic_write(
                        path,
                        json.dumps(mem_data, ensure_ascii=False, indent=2),
                    )
                except Exception as e:
                    logger.error("Failed to flush agent %s: %s", agent_id, e)
                    # 重新标脏，下次再试
                    self._dirty_agents.add(agent_id)

    async def run_flush_loop(self, interval: float = 2.0) -> None:
        """后台任务：每 interval 秒 flush 一次."""
        try:
            while True:
                await asyncio.sleep(interval)
                await self.flush()
        except asyncio.CancelledError:
            # 退出前做最后一次 flush
            await self.flush()

    def start(self) -> None:
        """启动后台 flush loop 任务."""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self.run_flush_loop())

    def stop(self) -> None:
        """停止后台 flush loop."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
