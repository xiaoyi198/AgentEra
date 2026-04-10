"""Agent 运行时 — 管理 Agent 的生命周期和 LLM 调用."""

from __future__ import annotations

import json
import logging
import uuid
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
        self.memory: deque[dict[str, Any]] = deque(maxlen=config.max_memory)

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

    def serialize_memory(self) -> list[dict[str, Any]]:
        """序列化记忆为可持久化的列表."""
        return list(self.memory)

    def deserialize_memory(self, data: list[dict[str, Any]]) -> None:
        """从持久化列表恢复记忆."""
        self.memory.clear()
        for msg in data:
            self.memory.append(msg)

    def get_messages(self) -> list[dict[str, Any]]:
        """构建 LLM messages（system prompt + memory）."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.config.system_prompt}]
        messages.extend(self.memory)
        return messages

    def _record_response_to_memory(self, response: LLMResponse) -> None:
        """将 LLM 响应以 OpenAI 协议正确格式写入记忆.

        有 tool_calls → assistant(tool_calls) + tool(result)
        纯文本     → assistant(text) + user(纠正提示)
        """
        if response.tool_calls:
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or None,
                "tool_calls": [
                    {
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": tc.get("function", {}),
                    }
                    for tc in response.tool_calls
                ],
            }
            self.memory.append(assistant_msg)
            for tc in assistant_msg["tool_calls"]:
                self.memory.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps({"status": "已执行"}, ensure_ascii=False),
                })
        else:
            self.memory.append({"role": "assistant", "content": response.content or ""})
            self.memory.append({
                "role": "user",
                "content": "[系统] 你没有调用任何工具。请务必使用 submit_action 或 request_action 表达行动。",
            })


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
        self._record_response_to_memory(response)
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
        self._record_response_to_memory(response)
        return action
