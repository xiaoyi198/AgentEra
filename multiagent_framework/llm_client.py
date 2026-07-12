"""OpenAI-compatible LLM 客户端封装."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM API configuration with environment-backed secret loading."""
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str = ""
    model: str = "gpt-4o-mini"
    timeout_s: int = 30
    max_concurrent: int = 4

    def __post_init__(self) -> None:
        self.timeout_s = max(1, int(self.timeout_s))
        self.max_concurrent = max(1, int(self.max_concurrent))

    @classmethod
    def from_file(cls, path: str = "config/llm.json") -> LLMConfig:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)

        raw_api_key = str(data.get("api_key", "") or "")
        api_key_env = str(data.get("api_key_env", "") or "")
        if raw_api_key.startswith("${") and raw_api_key.endswith("}"):
            api_key_env = raw_api_key[2:-1].strip()
            raw_api_key = ""

        if api_key_env:
            api_key = os.environ.get(api_key_env, "")
            if not api_key:
                logger.warning("LLM API key environment variable %s is not set", api_key_env)
        else:
            api_key = raw_api_key
            if api_key:
                logger.warning(
                    "Plaintext LLM API key loaded from %s; use ${ENV_VAR} instead",
                    path,
                )

        return cls(
            base_url=data.get("base_url", cls.base_url),
            api_key=api_key,
            api_key_env=api_key_env,
            model=data.get("model", cls.model),
            timeout_s=max(1, int(data.get("timeout_s", cls.timeout_s))),
            max_concurrent=max(1, int(data.get("max_concurrent", cls.max_concurrent))),
        )

    def save_to_file(self, path: str = "config/llm.json") -> None:
        """Persist non-secret configuration and an optional environment reference."""
        key_reference = f"${{{self.api_key_env}}}" if self.api_key_env else ""
        if self.api_key and not self.api_key_env:
            logger.warning(
                "LLM API key is active in memory but will not be persisted; configure api_key_env"
            )
        data = {
            "base_url": self.base_url,
            "api_key": key_reference,
            "model": self.model,
            "timeout_s": max(1, int(self.timeout_s)),
            "max_concurrent": max(1, int(self.max_concurrent)),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def to_display_dict(self) -> dict[str, Any]:
        """Return configuration metadata without exposing any key material."""
        return {
            "base_url": self.base_url,
            "api_key_masked": "configured" if self.api_key else "",
            "api_key_env": self.api_key_env,
            "api_key_configured": bool(self.api_key),
            "model": self.model,
            "timeout_s": self.timeout_s,
            "max_concurrent": self.max_concurrent,
        }


@dataclass
class LLMResponse:
    """LLM 调用结果."""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenCounter:
    """累计 token 用量统计."""
    total_input: int = 0
    total_output: int = 0
    call_count: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input += input_tokens
        self.total_output += output_tokens
        self.call_count += 1

    def to_dict(self) -> dict[str, int]:
        return {
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_tokens": self.total_input + self.total_output,
            "call_count": self.call_count,
        }


class LLMClient:
    """异步 LLM 客户端，支持 function calling."""

    def __init__(
        self,
        config: LLMConfig,
        debug_log_path: str | None = "debug_llm.jsonl",
    ) -> None:
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._client: httpx.AsyncClient | None = None
        self._debug_path = Path(debug_log_path) if debug_log_path else None
        self.token_counter = TokenCounter()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}
            if self._config.api_key:
                headers["Authorization"] = f"Bearer {self._config.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self._config.base_url,
                headers=headers,
                timeout=self._config.timeout_s,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_retries: int = 2,
        trace_context: Any = None,
    ) -> LLMResponse:
        """Run a chat completion while respecting the shared concurrency limit."""
        async with self._semaphore:
            return await self._chat_unlocked(messages, tools, temperature, max_retries)

    async def _chat_unlocked(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_retries: int = 2,
    ) -> LLMResponse:
        """Run a chat completion without acquiring the semaphore again."""
        start = time.monotonic()
        client = await self._get_client()

        body: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        for attempt in range(1 + max_retries):
            try:
                resp = await client.post("/chat/completions", json=body)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM call failed (attempt %d/%d), retry in %ds: %s",
                        attempt + 1,
                        1 + max_retries,
                        wait,
                        e,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("LLM call failed after %d attempts: %s", 1 + max_retries, e)
                    self._log_debug(body, {"error": str(e)}, time.monotonic() - start)
                    raise

        latency = (time.monotonic() - start) * 1000
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        result = LLMResponse(
            content=message.get("content", "") or "",
            tool_calls=[
                {
                    "id": tc.get("id", ""),
                    "function": tc.get("function", {}),
                }
                for tc in message.get("tool_calls", [])
            ],
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency,
            raw=data,
        )

        self.token_counter.add(result.input_tokens, result.output_tokens)
        self._log_debug(body, data, latency)
        return result

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        trace_context: Any = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式 chat completion，逐 chunk yield.

        每个 chunk 是一个 dict:
          - {"type": "delta", "content": "..."} — 文本增量
          - {"type": "tool_call_delta", "index": int, "id": str, "name": str, "arguments_delta": str}
          - {"type": "done", "response": LLMResponse} — 流结束，附带完整结果

        注意：如果底层 API 不支持 streaming 或出错，退化为一次性返回。
        """
        async with self._semaphore:
            start = time.monotonic()
            client = await self._get_client()

            body: dict[str, Any] = {
                "model": self._config.model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"

            try:
                # 用 httpx stream 接收 SSE（连接超时用默认，读取超时放宽到 120s）
                stream_timeout = httpx.Timeout(connect=self._config.timeout_s, read=120.0,
                                               write=self._config.timeout_s, pool=self._config.timeout_s)
                async with client.stream("POST", "/chat/completions", json=body, timeout=stream_timeout) as resp:
                    resp.raise_for_status()

                    full_content = ""
                    # tool_calls 按 index 累积
                    tc_accum: dict[int, dict[str, Any]] = {}
                    input_tokens = 0
                    output_tokens = 0

                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        # usage（部分模型在最后一个 chunk 带 usage）
                        if "usage" in chunk:
                            input_tokens = chunk["usage"].get("prompt_tokens", input_tokens)
                            output_tokens = chunk["usage"].get("completion_tokens", output_tokens)

                        delta = chunk.get("choices", [{}])[0].get("delta", {})

                        # 文本增量
                        if delta.get("content"):
                            full_content += delta["content"]
                            yield {"type": "delta", "content": delta["content"]}

                        # tool_call 增量
                        for tc_delta in delta.get("tool_calls", []):
                            idx = tc_delta.get("index", 0)
                            if idx not in tc_accum:
                                tc_accum[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "name": tc_delta.get("function", {}).get("name", ""),
                                    "arguments": "",
                                }
                            if tc_delta.get("id"):
                                tc_accum[idx]["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                tc_accum[idx]["name"] = fn["name"]
                            if fn.get("arguments"):
                                tc_accum[idx]["arguments"] += fn["arguments"]
                                yield {
                                    "type": "tool_call_delta",
                                    "index": idx,
                                    "id": tc_accum[idx]["id"],
                                    "name": tc_accum[idx]["name"],
                                    "arguments_delta": fn["arguments"],
                                }

                    latency = (time.monotonic() - start) * 1000

                    # 组装完整 tool_calls
                    tool_calls = []
                    for idx in sorted(tc_accum):
                        tc = tc_accum[idx]
                        tool_calls.append({
                            "id": tc["id"],
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["arguments"],
                            },
                        })

                    result = LLMResponse(
                        content=full_content,
                        tool_calls=tool_calls,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=latency,
                    )
                    self.token_counter.add(input_tokens, output_tokens)
                    self._log_debug(body, {"stream": True, "content_len": len(full_content),
                                           "tool_calls_count": len(tool_calls)}, latency)
                    yield {"type": "done", "response": result}

            except Exception as e:
                logger.warning("chat_stream failed, falling back to non-stream: %s", e)
                # 退化：用普通 chat 一次性返回
                body.pop("stream", None)
                result = await self._chat_unlocked(messages, tools, temperature)
                if result.content:
                    yield {"type": "delta", "content": result.content}
                yield {"type": "done", "response": result}

    def _log_debug(self, request: dict, response: dict, latency_ms: float) -> None:
        """记录 LLM 调试日志."""
        msg = response.get("choices", [{}])[0].get("message", {})
        entry = {
            "timestamp": time.time(),
            "model": self._config.model,
            "latency_ms": round(latency_ms, 1),
            "request_messages_count": len(request.get("messages", [])),
            "response_content": msg.get("content", ""),
            "tool_calls": [
                {"name": tc.get("function", {}).get("name"), "arguments": tc.get("function", {}).get("arguments")}
                for tc in msg.get("tool_calls", [])
            ],
            "input_tokens": response.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": response.get("usage", {}).get("completion_tokens", 0),
        }
        if self._debug_path is None:
            return
        try:
            with open(self._debug_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def update_config(self, new_config: LLMConfig) -> None:
        """热更新配置，关闭旧连接."""
        await self.close()
        self._config = new_config
        self._semaphore = asyncio.Semaphore(new_config.max_concurrent)
        logger.info("LLM config updated: model=%s, base_url=%s", new_config.model, new_config.base_url)

    @property
    def config(self) -> LLMConfig:
        return self._config

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
