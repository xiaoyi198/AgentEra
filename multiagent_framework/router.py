"""世界运行法则（Router）— LLM 驱动的中枢大脑."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from .agent_runtime import BaseAgent, AgentConfig
from .court_graph import CourtGraph
from .llm_client import LLMClient, LLMResponse
from .types import AgentAction, ChainLogEntry, RouterCommand, RouterCommandType, TimerEntry

logger = logging.getLogger(__name__)

MAX_HOPS = 10

# Router 的 FC 工具集
ROUTER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "wake_agent",
            "description": "唤醒指定 Agent，直接投递消息",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标 Agent ID"},
                    "message": {"type": "string", "description": "传递的消息内容"},
                    "from_agent": {"type": "string", "description": "消息来源 Agent ID"},
                },
                "required": ["target", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_timer",
            "description": "创建延迟定时器，消息在指定天数后送达",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "目标 Agent ID"},
                    "delay_days": {"type": "number", "description": "延迟天数"},
                    "message": {"type": "string", "description": "送达时的消息内容"},
                    "from_agent": {"type": "string", "description": "消息来源 Agent ID"},
                    "urgency": {"type": "string", "description": "紧急程度"},
                },
                "required": ["target", "delay_days", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_policy",
            "description": "调用世界模拟 Agent 执行政策效果",
            "parameters": {
                "type": "object",
                "properties": {
                    "policy": {"type": "string", "description": "政策描述"},
                    "location": {"type": "string", "description": "执行地点"},
                    "executor": {"type": "string", "description": "执行者 Agent ID"},
                },
                "required": ["policy", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "relay_decree",
            "description": "传递皇帝圣旨给指定官员",
            "parameters": {
                "type": "object",
                "properties": {
                    "decree": {"type": "string", "description": "圣旨内容"},
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "接收圣旨的 Agent ID 列表",
                    },
                    "urgency": {"type": "string"},
                },
                "required": ["decree", "targets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_withheld",
            "description": "标记消息被压下不报",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "压下原因"},
                },
                "required": ["reason"],
            },
        },
    },
]


class RouterAgent:
    """世界运行法则 — 解析官员意图，执行世界规则.

    不继承 BaseAgent，因为 Router 有完全不同的工作方式：
    批量处理多个 AgentAction，返回 RouterCommand 列表。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        court_graph: CourtGraph,
        system_prompt: str = "",
        seconds_per_day: float = 600.0,
    ) -> None:
        self.llm = llm_client
        self.court = court_graph
        self.seconds_per_day = seconds_per_day
        self._system_prompt = system_prompt or self._default_prompt()
        self.last_response_text = ""

    def _default_prompt(self) -> str:
        return """你是大明帝国的世界运行法则（Router），负责解读官员的行动意图并执行世界规则。

你的职责：
1. 解析每个官员的 submit_action，理解他们想做什么
2. 查找合适的目标（上级、指定目标等）
3. 决定消息传递方式（直接唤醒 or 创建定时器延迟送达）
4. 对于政策执行类行动，调用 execute_policy
5. 对于压下不报的行动，调用 mark_withheld

延迟规则参考（你可以根据情况灵活调整）：
- 直属上下级私信：0天延迟，用 wake_agent
- 加急公文：距离天数-1
- 正式公文：按距离天数
- 越级汇报：至少2天

你会收到一批官员的行动，请为每个行动调用合适的工具。"""

    async def process_batch(
        self,
        actions: list[AgentAction],
        context: dict[str, Any] | None = None,
    ) -> list[RouterCommand]:
        """批量处理官员的 submit_action，返回路由指令."""
        if not actions:
            return []

        batch_desc = self._build_batch_description(actions, context)

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": batch_desc},
        ]

        try:
            response = await self.llm.chat(messages=messages, tools=ROUTER_TOOLS)
        except Exception as e:
            logger.error("Router LLM failed: %s", e)
            return []

        commands = self._parse_commands(response, actions)
        # 记录 Router 原始响应供调试
        self.last_response_text = response.content or ""
        return commands

    def _build_batch_description(
        self, actions: list[AgentAction], context: dict[str, Any] | None
    ) -> str:
        """构建给 Router LLM 的批量行动描述."""
        # 附上所有已知 Agent ID，让 Router 用正确的 ID
        all_officials = self.court.get_all_officials()
        id_list = ", ".join(o.id for o in all_officials)
        parts = [f"可用的官员ID：{id_list}\n\n以下官员提交了行动，请逐一处理（调用工具时 target 必须使用上面列出的官员ID）：\n"]

        for i, action in enumerate(actions, 1):
            official = self.court.get_official(action.agent_id)
            superior = self.court.get_superior(action.agent_id) if official else None

            info = f"【行动 {i}】\n"
            info += f"  官员：{action.agent_id}"
            if official:
                info += f"（{official.name}，品级{official.rank}，驻{official.location_id}）"
            info += "\n"
            if superior:
                dist_days = self.court.get_distance(action.agent_id, superior.id)
                info += f"  直属上级：{superior.id}（{superior.name}），距离约{dist_days:.1f}天\n"
            # 直属下级列表
            if official:
                subs = self.court.get_subordinates(action.agent_id)
                if subs:
                    sub_list = ", ".join(f"{s.id}（{s.name}）" for s in subs)
                    info += f"  直属下级：{sub_list}\n"
                else:
                    info += f"  直属下级：无（最基层）\n"
            info += f"  行动：{action.action}\n"
            info += f"  内容：{action.content}\n"
            info += f"  紧急程度：{action.urgency}\n"
            if action.metadata:
                info += f"  附加信息：{json.dumps(action.metadata, ensure_ascii=False)}\n"
            parts.append(info)

        if context:
            parts.append(f"\n当前世界状态：{json.dumps(context, ensure_ascii=False)}")

        return "\n".join(parts)

    def _parse_commands(
        self, response: LLMResponse, actions: list[AgentAction]
    ) -> list[RouterCommand]:
        """解析 Router LLM 的 function call 响应为 RouterCommand 列表."""
        commands: list[RouterCommand] = []

        for tc in response.tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                logger.warning("Router: invalid FC JSON for %s", name)
                continue

            cmd = self._fc_to_command(name, args, actions)
            if cmd:
                commands.append(cmd)

        # 如果 LLM 没有返回任何 FC（可能只返回了文本），记录日志
        if not commands and response.content:
            logger.info("Router returned text only: %s", response.content[:200])

        return commands

    def _fc_to_command(
        self, fc_name: str, args: dict[str, Any], actions: list[AgentAction]
    ) -> RouterCommand | None:
        """将单个 FC 调用转换为 RouterCommand."""
        # 尝试关联到原始 action
        source_action = None
        from_agent = args.get("from_agent", "")
        if from_agent:
            for a in actions:
                if a.agent_id == from_agent:
                    source_action = a
                    break

        if fc_name == "wake_agent":
            return RouterCommand(
                command_type=RouterCommandType.WAKE_AGENT,
                target=args.get("target"),
                payload={"message": args.get("message", ""), "from": from_agent},
                delay_seconds=0,
                chain_id=f"chain_{uuid.uuid4().hex[:8]}",
                source_action=source_action,
            )
        elif fc_name == "create_timer":
            delay_days = args.get("delay_days", 1)
            delay_seconds = delay_days * 86400  # 游戏秒（1天=86400）
            return RouterCommand(
                command_type=RouterCommandType.CREATE_TIMER,
                target=args.get("target"),
                payload={
                    "message": args.get("message", ""),
                    "from": from_agent,
                    "urgency": args.get("urgency", "normal"),
                },
                delay_seconds=delay_seconds,
                chain_id=f"chain_{uuid.uuid4().hex[:8]}",
                source_action=source_action,
            )
        elif fc_name == "execute_policy":
            return RouterCommand(
                command_type=RouterCommandType.EXECUTE_POLICY,
                target=args.get("executor"),
                payload={
                    "policy": args.get("policy", ""),
                    "location": args.get("location", ""),
                },
                chain_id=f"chain_{uuid.uuid4().hex[:8]}",
                source_action=source_action,
            )
        elif fc_name == "relay_decree":
            return RouterCommand(
                command_type=RouterCommandType.RELAY_DECREE,
                targets=args.get("targets", []),
                payload={
                    "decree": args.get("decree", ""),
                    "urgency": args.get("urgency", "normal"),
                },
                chain_id=f"chain_{uuid.uuid4().hex[:8]}",
                source_action=source_action,
            )
        elif fc_name == "mark_withheld":
            return RouterCommand(
                command_type=RouterCommandType.MARK_WITHHELD,
                payload={"reason": args.get("reason", "")},
                chain_id=f"chain_{uuid.uuid4().hex[:8]}",
                source_action=source_action,
            )
        return None

    def update_time_scale(self, seconds_per_day: float) -> None:
        """更新时间倍率."""
        self.seconds_per_day = seconds_per_day
