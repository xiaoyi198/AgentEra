"""核心类型定义 — 框架层所有数据结构."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── 时间 ──

@dataclass
class TimerEntry:
    """定时器条目，heapq 按 fire_at 排序."""
    timer_id: str = field(default_factory=lambda: f"tmr_{uuid.uuid4().hex[:8]}")
    fire_at: float = 0.0          # 模拟时间（秒）
    target_agent: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    chain_id: str = ""
    parent_timer_id: str | None = None
    created_at: float = 0.0       # 创建时的模拟时间
    cancelled: bool = False

    def __lt__(self, other: TimerEntry) -> bool:
        return self.fire_at < other.fire_at


# ── 调用链 ──

@dataclass
class ChainLogEntry:
    """一条调用链记录."""
    chain_id: str = ""
    entry_id: str = field(default_factory=lambda: f"cle_{uuid.uuid4().hex[:8]}")
    event_id: str = ""
    sim_time: float = 0.0         # 模拟时间
    caller: str = ""
    caller_agent_type: str = ""   # official / institution / router / world_sim 等
    fc_called: str = ""
    original_text: str = ""       # 原始意图
    transmitted_text: str = ""    # 实际发出的版本
    is_distorted: bool = False
    distortion_note: str | None = None
    hop_count: int = 0
    routed_to: str | None = None
    timer_id: str | None = None
    trigger_at: float | None = None  # 触发的模拟时间
    wakeup_type: str = "direct"   # "direct" / "timer"
    status: str = "delivered"     # "delivered" / "withheld" / "loop_detected"


# ── Agent 动作 ──

@dataclass
class AgentAction:
    """Agent 通过 submit_action 表达的意图."""
    agent_id: str = ""
    action: str = ""              # 自由文本（v1），如 report_to_superior / execute_local_policy
    content: str = ""             # 实际传递的内容
    original_intent: str = ""     # 原始意图（用于 chain log 对比）
    is_distorting: bool = False   # Agent 自己声明是否失真
    urgency: str = "normal"       # critical / urgent / normal
    metadata: dict[str, Any] = field(default_factory=dict)


# ── 事件 ──

@dataclass
class EventObject:
    """系统事件（蝗灾、旱灾等）."""
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    event_type: str = ""
    severity: int = 5
    target_agents: list[str] = field(default_factory=list)
    initial_payload: dict[str, Any] = field(default_factory=dict)
    generated_at: float = 0.0     # 模拟时间


# ── Router 指令 ──

class RouterCommandType(str, Enum):
    """Router 输出的指令类型."""
    WAKE_AGENT = "wake_agent"
    CREATE_TIMER = "create_timer"
    EXECUTE_POLICY = "execute_policy"
    RELAY_DECREE = "relay_decree"
    BROADCAST = "broadcast"
    MARK_WITHHELD = "mark_withheld"
    NO_OP = "no_op"


@dataclass
class RouterCommand:
    """Router 解析 submit_action 后生成的指令."""
    command_type: RouterCommandType = RouterCommandType.NO_OP
    target: str | None = None
    targets: list[str] = field(default_factory=list)  # broadcast 用
    payload: dict[str, Any] = field(default_factory=dict)
    delay_seconds: float = 0.0    # 模拟时间延迟
    chain_id: str = ""
    source_action: AgentAction | None = None


# ── 行动结果（Action Agent 输出）──

@dataclass
class ActionResult:
    """ActionAgent 完成一次行动模拟后返回的结果."""
    action_type: str = ""
    executor_id: str = ""
    target_location: str = ""
    result_description: str = ""            # 行动结果文本（会送回发起者）
    duration_days: float = 1.0              # 行动耗时（模拟天数）
    changes_made: list[dict[str, Any]] = field(default_factory=list)  # 世界状态变更记录
    success: bool = True
    error: str | None = None


# ── 轮次结果（推送给前端）──

@dataclass
class TickResult:
    """一次 tick 的结果，推送给前端."""
    sim_time: float = 0.0
    new_memorials: list[dict[str, Any]] = field(default_factory=list)
    in_transit: list[dict[str, Any]] = field(default_factory=list)
    chain_entries: list[ChainLogEntry] = field(default_factory=list)
    agent_errors: list[dict[str, Any]] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


# ── 皇帝操作 ──

class PlayerActionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    HOLD = "hold"
    ISSUE_DECREE = "issue_decree"


@dataclass
class PlayerAction:
    """皇帝的批复/下旨操作."""
    action_type: PlayerActionType = PlayerActionType.HOLD
    memorial_id: str = ""
    content: str = ""             # 批注或旨意内容
    note: str = ""
