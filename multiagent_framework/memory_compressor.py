"""Agent 记忆压缩 — 可插拔的上下文压缩策略.

当 Agent 记忆超过阈值时，将旧消息用 LLM 压缩为一条摘要，
保留最近 N 条原文不动。

使用方式：
    from .memory_compressor import LLMMemoryCompressor
    compressor = LLMMemoryCompressor(llm_client)
    agent.set_compressor(compressor)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

SUMMARY_META_KEY = "_meta"
SUMMARY_TYPE_VALUE = "memory_summary"

# ── 摘要提示词 ──────────────────────────────────────────────────────────────────

DEFAULT_SUMMARY_PROMPT = """\
你是记忆压缩助手。请将以下对话历史浓缩为一段简洁的摘要。

当前角色：{agent_context}

{existing_summary_section}

需要压缩的对话记录：
{messages_text}

要求：
1. 保留关键事件及其结果
2. 保留重要人名、地名、数字
3. 保留未完成的承诺和待办事项
4. 保留该角色的立场和情感倾向
5. 用第三人称叙述
6. 控制在300字以内
"""


# ── 工具函数 ─────────────────────────────────────────────────────────────────────

def is_summary_message(msg: dict[str, Any]) -> bool:
    """检测消息是否为压缩摘要."""
    meta = msg.get(SUMMARY_META_KEY)
    return isinstance(meta, dict) and meta.get("type") == SUMMARY_TYPE_VALUE


def find_safe_split_point(messages: list[dict[str, Any]], target: int) -> int:
    """找到不切断 assistant(tool_calls)+tool 消息组的安全分割点.

    从 target 位置向前回退，确保不把 tool 消息和对应的 assistant 消息分到两边。
    返回值 split 表示：messages[:split] 是待压缩部分，messages[split:] 保留原文。
    """
    if target <= 0:
        return 0
    if target >= len(messages):
        return len(messages)

    i = target
    while i > 0 and messages[i].get("role") == "tool":
        i -= 1
    # i 现在指向 assistant 消息（或已退出 tool 区域）
    if i < target and messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
        return i
    return target


def format_messages_for_compression(messages: list[dict[str, Any]]) -> str:
    """将消息列表格式化为可读文本，供 LLM 摘要."""
    lines: list[str] = []
    for msg in messages:
        if is_summary_message(msg):
            continue
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "assistant" and msg.get("tool_calls"):
            # 展示工具调用概要
            tc_names = []
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                tc_names.append(fn.get("name", "unknown"))
            lines.append(f"[助手-工具调用] {', '.join(tc_names)}")
            if content:
                lines.append(f"  附言：{content}")
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                # 只保留关键参数
                brief = {k: (v[:100] if isinstance(v, str) and len(v) > 100 else v) for k, v in args.items()}
                lines.append(f"  → {fn.get('name', '?')}: {json.dumps(brief, ensure_ascii=False)}")
        elif role == "tool":
            try:
                result = json.loads(content) if content else {}
            except (json.JSONDecodeError, TypeError):
                result = content
            lines.append(f"[工具结果] {json.dumps(result, ensure_ascii=False)[:200]}")
        else:
            role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
            lines.append(f"[{role_label}] {content[:300]}")

    return "\n".join(lines)


# ── Protocol ─────────────────────────────────────────────────────────────────────

@runtime_checkable
class MemoryCompressor(Protocol):
    """记忆压缩器协议 — 实现此协议即可插入任意压缩策略."""

    async def compress(
        self,
        messages: list[dict[str, Any]],
        existing_summary: dict[str, Any] | None,
        agent_context: str,
    ) -> dict[str, Any]:
        """将旧消息压缩为一条摘要消息.

        Args:
            messages: 待压缩的旧消息列表.
            existing_summary: 上一次压缩产生的摘要（需合并），或 None.
            agent_context: Agent 身份描述（如 "况钟，苏州知府"）.

        Returns:
            摘要消息 dict，包含 role="user" 和 _meta 标记.
        """
        ...


# ── 默认实现 ─────────────────────────────────────────────────────────────────────

class LLMMemoryCompressor:
    """基于 LLM 的记忆压缩器."""

    def __init__(
        self,
        llm_client: Any,  # LLMClient
        prompt_template: str = DEFAULT_SUMMARY_PROMPT,
        temperature: float = 0.3,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template
        self._temperature = temperature

    async def compress(
        self,
        messages: list[dict[str, Any]],
        existing_summary: dict[str, Any] | None,
        agent_context: str,
    ) -> dict[str, Any]:
        # 构建已有摘要段落
        if existing_summary and existing_summary.get("content"):
            existing_text = existing_summary["content"]
            existing_section = f"此前已有的历史摘要（请将其与新内容合并）：\n{existing_text}\n"
        else:
            existing_section = ""

        messages_text = format_messages_for_compression(messages)

        prompt = self._prompt_template.format(
            agent_context=agent_context,
            existing_summary_section=existing_section,
            messages_text=messages_text,
        )

        response = await self._llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            temperature=self._temperature,
        )

        summary_text = response.content.strip()
        source_count = len(messages)
        if existing_summary:
            prev_count = existing_summary.get(SUMMARY_META_KEY, {}).get("source_count", 0)
            source_count += prev_count

        return {
            "role": "user",
            "content": f"[历史摘要] {summary_text}",
            SUMMARY_META_KEY: {
                "type": SUMMARY_TYPE_VALUE,
                "compressed_at": time.time(),
                "source_count": source_count,
            },
        }
