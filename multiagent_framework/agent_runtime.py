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
from .memory_compressor import MemoryCompressor, find_safe_split_point, is_summary_message
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
    compress_threshold: int = 40      # 记忆条数超过此值时触发压缩
    keep_recent: int = 20             # 压缩时保留最近 N 条原文


class BaseAgent(ABC):
    """Agent 基类 — 框架层抽象."""

    def __init__(self, config: AgentConfig, llm_client: LLMClient) -> None:
        self.config = config
        self.llm = llm_client
        self.memory: deque[dict[str, Any]] = deque()  # 无 maxlen，由压缩机制管理
        self._summary: dict[str, Any] | None = None
        self._compressor: MemoryCompressor | None = None
        self._compress_failures: int = 0

    @property
    def agent_id(self) -> str:
        return self.config.agent_id

    @property
    def agent_type(self) -> str:
        return self.config.agent_type

    def set_compressor(self, compressor: MemoryCompressor) -> None:
        """注入记忆压缩器."""
        self._compressor = compressor

    @abstractmethod
    async def process(self, payload: dict[str, Any]) -> list[AgentAction] | dict[str, Any]:
        """处理输入，返回行动列表（支持同时 submit_action + request_action）."""
        ...

    def add_memory(self, role: str, content: str) -> None:
        self.memory.append({"role": role, "content": content})

    def serialize_memory(self) -> dict[str, Any]:
        """序列化记忆为可持久化的字典（v2 格式）."""
        return {
            "version": 2,
            "summary": self._summary,
            "messages": list(self.memory),
        }

    def deserialize_memory(self, data: list[dict[str, Any]] | dict[str, Any]) -> None:
        """从持久化数据恢复记忆，兼容 v1（list）和 v2（dict）格式."""
        self.memory.clear()
        self._summary = None
        if isinstance(data, list):
            # v1 遗留格式
            for msg in data:
                self.memory.append(msg)
        elif isinstance(data, dict) and data.get("version") == 2:
            self._summary = data.get("summary")
            for msg in data.get("messages", []):
                self.memory.append(msg)
        else:
            logger.warning("Agent %s: unknown memory format, treating as empty", self.agent_id)

    def get_messages(self) -> list[dict[str, Any]]:
        """构建 LLM messages（system prompt + [摘要] + memory）."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.config.system_prompt}]
        if self._summary is not None:
            # 发给 LLM 时剥离 _meta 字段
            summary_for_llm = {k: v for k, v in self._summary.items() if k != "_meta"}
            messages.append(summary_for_llm)
        messages.extend(self.memory)
        return messages

    async def _maybe_compress(self) -> None:
        """检查记忆是否超过阈值，超过则压缩旧消息为摘要."""
        if self._compressor is None:
            return
        if len(self.memory) <= self.config.compress_threshold:
            return

        mem_list = list(self.memory)
        target_split = len(mem_list) - self.config.keep_recent
        split = find_safe_split_point(mem_list, target_split)
        if split <= 0:
            return

        older = mem_list[:split]
        agent_context = f"{self.config.display_name} ({self.config.agent_id})"

        try:
            new_summary = await self._compressor.compress(
                older, self._summary, agent_context,
            )
            # 压缩成功：替换旧消息
            self._summary = new_summary
            self.memory.clear()
            for msg in mem_list[split:]:
                self.memory.append(msg)
            self._compress_failures = 0
            logger.info(
                "Agent %s: compressed %d messages into summary, %d remaining",
                self.agent_id, len(older), len(self.memory),
            )
        except Exception as e:
            self._compress_failures += 1
            logger.warning(
                "Agent %s: compression failed (%d/3): %s",
                self.agent_id, self._compress_failures, e,
            )
            if self._compress_failures >= 3:
                # 退化截断：保留 keep_recent 条 + 已有摘要
                keep = mem_list[-self.config.keep_recent:]
                self.memory.clear()
                for msg in keep:
                    self.memory.append(msg)
                logger.warning(
                    "Agent %s: fallback truncation, kept %d messages",
                    self.agent_id, len(self.memory),
                )

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
    """官员 Agent — 支持 submit_action + request_action 双 FC（可同时调用）."""

    async def process(self, payload: dict[str, Any]) -> list[AgentAction]:
        # 将事件/消息加入记忆
        event_desc = payload.get("description", "")
        if event_desc:
            self.add_memory("user", event_desc)

        incoming_msg = payload.get("message", "")
        if incoming_msg:
            sender = payload.get("from", "未知")
            self.add_memory("user", f"来自{sender}的消息：{incoming_msg}")

        await self._maybe_compress()
        messages = self.get_messages()
        tools = AGENT_TOOLS

        try:
            response = await self.llm.chat(messages=messages, tools=tools)
        except Exception as e:
            logger.error("Agent %s LLM call failed: %s", self.agent_id, e)
            return [AgentAction(
                agent_id=self.agent_id,
                action="do_nothing",
                content="",
                original_intent=f"LLM调用失败: {e}",
                is_distorting=False,
            )]

        actions = parse_agent_fc_response(response, self.agent_id)
        self._record_response_to_memory(response)
        return actions


class InstitutionAgent(BaseAgent):
    """政府机构 Agent — 模拟机构中除一把手外的所有员工，同样支持双 FC."""

    async def process(self, payload: dict[str, Any]) -> list[AgentAction]:
        incoming = payload.get("message", "")
        if incoming:
            self.add_memory("user", f"机构收到指令：{incoming}")

        await self._maybe_compress()
        messages = self.get_messages()
        tools = AGENT_TOOLS

        try:
            response = await self.llm.chat(messages=messages, tools=tools)
        except Exception as e:
            logger.error("Institution %s LLM failed: %s", self.agent_id, e)
            return [AgentAction(
                agent_id=self.agent_id,
                action="do_nothing",
                content="",
                original_intent=f"LLM调用失败: {e}",
                is_distorting=False,
            )]

        actions = parse_agent_fc_response(response, self.agent_id)
        self._record_response_to_memory(response)
        return actions
