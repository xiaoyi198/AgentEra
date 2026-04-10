"""Agent 运行时 — 管理 Agent 的生命周期和 LLM 调用."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .fc_handlers import parse_agent_fc_response
from .fc_tools import AGENT_TOOLS
from .llm_client import LLMClient, LLMResponse
from .types import AgentAction

logger = logging.getLogger(__name__)


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
        tools = AGENT_TOOLS

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

        action = parse_agent_fc_response(response, self.agent_id)
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
        tools = AGENT_TOOLS

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

        action = parse_agent_fc_response(response, self.agent_id)
        self.add_memory("assistant", f"机构决定：{action.action}")
        return action
