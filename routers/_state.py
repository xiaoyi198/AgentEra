"""全局共享状态——所有 Router 通过此模块访问 orchestrator 等单例。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import WebSocket

if TYPE_CHECKING:
    from multiagent_framework.orchestrator import Orchestrator
    from multiagent_framework.tianji_agent import TianJiAgent

orchestrator: Orchestrator | None = None
tianji_agent: TianJiAgent | None = None
ws_clients: set[WebSocket] = set()
_orch_task: asyncio.Task | None = None
