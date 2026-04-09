"""Agent 运行时 — 管理 Agent 的生命周期和 LLM 调用."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .llm_client import LLMClient, LLMResponse
from .types import AgentAction

logger = logging.getLogger(__name__)

# request_action 的 function calling 定义 —— 触发世界模拟
REQUEST_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "request_action",
        "description": (
            "执行一个需要时间和资源的实际行动（调查、赈灾、抓捕、建设、巡查等）。"
            "行动会由世界模拟器推演结果，完成后把结果送回给你。"
            "注意：如果你只是想把信息传递给别人（汇报/私信/传话），请用 submit_action。"
            "只有要实际做事时才用本工具。本轮只能调用 submit_action 或 request_action 其中之一。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "行动类型：investigate/relief/construction/arrest/patrol/tax_collection 等",
                },
                "target_location": {
                    "type": "string",
                    "description": "行动目标地点 ID（如 吴县、苏州、北京）",
                },
                "description": {
                    "type": "string",
                    "description": "具体行动描述：你想做什么、为什么、如何做",
                },
                "resources": {
                    "type": "string",
                    "description": "投入的资源描述（可选，如 粮食1000石、官兵50人）",
                },
            },
            "required": ["action_type", "target_location", "description"],
        },
    },
}


# submit_action 的 function calling 定义
SUBMIT_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_action",
        "description": "表达你这一轮的行动。必须三选一：do_nothing（什么也不做）、report_message（传递信息）、do_something（触发实际行动，可同时附带通知）。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["do_nothing", "report_message", "do_something"],
                    "description": (
                        "do_nothing：本轮不行动，其余字段会被忽略。\n"
                        "report_message：向他人传递信息（汇报/通报/私信/传令等），世界法则决定路径。\n"
                        "do_something：触发需要时间和资源的实际行动（调查/赈灾/建设/抓捕/巡查等）；"
                        "如需同时通知他人，在 content 中一并说明。"
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "实际传递给目标的内容（别人会看到这个）",
                },
                "original_intent": {
                    "type": "string",
                    "description": "你内心的真实想法（不会被传递，仅用于记录）",
                },
                "is_distorting": {
                    "type": "boolean",
                    "description": "你是否在故意歪曲信息（true=是，false=否）",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["critical", "urgent", "normal"],
                    "description": "紧急程度",
                },
                "metadata": {
                    "type": "object",
                    "description": "附加信息，如 target_hint 指定越级目标",
                },
            },
            "required": ["action", "content", "original_intent", "is_distorting"],
        },
    },
}


@dataclass
class AgentConfig:
    """单个 Agent 的配置."""
    agent_id: str = ""
    agent_type: str = "official"      # official / institution / router / world_sim / event_gen / time_sim
    display_name: str = ""
    system_prompt: str = ""
    personality_seed: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_memory: int = 20


class BaseAgent(ABC):
    """Agent 基类 — 框架层抽象."""

    def __init__(self, config: AgentConfig, llm_client: LLMClient) -> None:
        self.config = config
        self.llm = llm_client
        self.memory: deque[dict[str, str]] = deque(maxlen=config.max_memory)

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def agent_type(self) -> str:
        return self.config.agent_type

    @abstractmethod
    async def process(self, payload: dict[str, Any]) -> AgentAction | dict[str, Any]:
        """处理输入，返回行动."""
        ...

    def add_memory(self, role: str, content: str) -> None:
        self.memory.append({"role": role, "content": content})

    def serialize_memory(self) -> list[dict[str, str]]:
        """序列化记忆为可持久化的列表."""
        return list(self.memory)

    def deserialize_memory(self, data: list[dict[str, str]]) -> None:
        """从持久化列表恢复记忆."""
        self.memory.clear()
        for msg in data:
            self.memory.append(msg)

    def get_messages(self) -> list[dict[str, str]]:
        """构建 LLM messages（system prompt + memory）."""
        messages = [{"role": "system", "content": self.config.system_prompt}]
        messages.extend(self.memory)
        return messages


def _parse_dual_fc_response(
    response: LLMResponse,
    agent_id: str,
) -> AgentAction:
    """解析 LLM 响应 — 支持 submit_action / request_action 双 FC.

    规则：一次只接受一个 FC（取第一个识别到的）。
    - submit_action → 普通 AgentAction（走 Router 分发）
    - request_action → AgentAction(action="request_action"),
      metadata["fc_type"]="request_action" + 所有 payload 字段，
      Orchestrator 据此绕过 Router 直接分发给 ActionAgent。
    """
    if response.tool_calls:
        for tc in response.tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}") or "{}")
            except json.JSONDecodeError:
                logger.warning("Agent %s: invalid FC JSON for %s", agent_id, name)
                continue

            if name == "submit_action":
                return AgentAction(
                    agent_id=agent_id,
                    action=args.get("action", "do_nothing"),
                    content=args.get("content", ""),
                    original_intent=args.get("original_intent", ""),
                    is_distorting=args.get("is_distorting", False),
                    urgency=args.get("urgency", "normal"),
                    metadata=args.get("metadata", {}),
                )

            if name == "request_action":
                return AgentAction(
                    agent_id=agent_id,
                    action="request_action",
                    content=args.get("description", ""),
                    original_intent=args.get("description", ""),
                    is_distorting=False,
                    urgency="normal",
                    metadata={
                        "fc_type": "request_action",
                        "action_type": args.get("action_type", "unknown"),
                        "target_location": args.get("target_location", ""),
                        "description": args.get("description", ""),
                        "resources": args.get("resources", ""),
                    },
                )

    # 回退：没有合法 FC 调用
    return AgentAction(
        agent_id=agent_id,
        action="do_nothing",
        content=response.content,
        original_intent="未使用FC",
        is_distorting=False,
    )


class OfficialAgent(BaseAgent):
    """官员 Agent — 支持 submit_action + request_action 双 FC（一次只能选一个）."""

    async def process(self, payload: dict[str, Any]) -> AgentAction:
        # 将事件/消息加入记忆
        event_desc = payload.get("description", "")
        if event_desc:
            self.add_memory("user", event_desc)

        incoming_msg = payload.get("message", "")
        if incoming_msg:
            sender = payload.get("from", "未知")
            self.add_memory("user", f"来自{sender}的消息：{incoming_msg}")

        messages = self.get_messages()
        tools = [SUBMIT_ACTION_TOOL, REQUEST_ACTION_TOOL]

        try:
            response = await self.llm.chat(messages=messages, tools=tools)
        except Exception as e:
            logger.error("Agent %s LLM call failed: %s", self.agent_id, e)
            return AgentAction(
                agent_id=self.agent_id,
                action="do_nothing",
                content="",
                original_intent=f"LLM调用失败: {e}",
                is_distorting=False,
            )

        action = _parse_dual_fc_response(response, self.agent_id)
        # 记录自己的行动到记忆
        self.add_memory("assistant", f"我决定：{action.action} - {action.content}")
        return action


class InstitutionAgent(BaseAgent):
    """政府机构 Agent — 模拟机构中除一把手外的所有员工，同样支持双 FC."""

    async def process(self, payload: dict[str, Any]) -> AgentAction:
        incoming = payload.get("message", "")
        if incoming:
            self.add_memory("user", f"机构收到指令：{incoming}")

        messages = self.get_messages()
        tools = [SUBMIT_ACTION_TOOL, REQUEST_ACTION_TOOL]

        try:
            response = await self.llm.chat(messages=messages, tools=tools)
        except Exception as e:
            logger.error("Institution %s LLM failed: %s", self.agent_id, e)
            return AgentAction(
                agent_id=self.agent_id,
                action="do_nothing",
                content="",
                original_intent=f"LLM调用失败: {e}",
                is_distorting=False,
            )

        action = _parse_dual_fc_response(response, self.agent_id)
        self.add_memory("assistant", f"机构决定：{action.action}")
        return action
