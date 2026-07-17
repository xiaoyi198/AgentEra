"""OpenAI-compatible LLM 客户端封装."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

import httpx

from .api_request_queue import APIRequestDeadlineExceeded, APIRequestQueue

logger = logging.getLogger(__name__)


class LLMDeadlineExceeded(TimeoutError):
    """Raised when a model call cannot finish inside its caller-owned deadline."""


@dataclass
class LLMConfig:
    """LLM API configuration with local plaintext persistence and legacy env migration."""
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_env: str = ""
    model: str = "gpt-4o-mini"
    timeout_s: int = 30
    max_concurrent: int = 4
    retry_delay_s: float = 5.0
    queue_interval_s: float = 1.0

    def __post_init__(self) -> None:
        self.timeout_s = max(1, int(self.timeout_s))
        self.max_concurrent = max(1, int(self.max_concurrent))
        self.retry_delay_s = max(0.0, float(self.retry_delay_s))
        self.queue_interval_s = max(0.0, float(self.queue_interval_s))

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

        return cls(
            base_url=data.get("base_url", cls.base_url),
            api_key=api_key,
            api_key_env=api_key_env,
            model=data.get("model", cls.model),
            timeout_s=max(1, int(data.get("timeout_s", cls.timeout_s))),
            max_concurrent=max(1, int(data.get("max_concurrent", cls.max_concurrent))),
            retry_delay_s=max(0.0, float(data.get("retry_delay_s", cls.retry_delay_s))),
            queue_interval_s=max(0.0, float(data.get("queue_interval_s", cls.queue_interval_s))),
        )

    def save_to_file(self, path: str = "config/llm.json") -> None:
        """Persist the complete local configuration, including the plaintext API key."""
        data = {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "timeout_s": max(1, int(self.timeout_s)),
            "max_concurrent": max(1, int(self.max_concurrent)),
            "retry_delay_s": max(0.0, float(self.retry_delay_s)),
            "queue_interval_s": max(0.0, float(self.queue_interval_s)),
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
            "retry_delay_s": self.retry_delay_s,
            "queue_interval_s": self.queue_interval_s,
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
        request_queue: APIRequestQueue | None = None,
    ) -> None:
        self._config = config
        self._semaphore = asyncio.Semaphore(config.max_concurrent)
        self._client: httpx.AsyncClient | None = None
        self._debug_path = Path(debug_log_path) if debug_log_path else None
        self._request_queue = request_queue
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

    @staticmethod
    def _coerce_deadline(
        explicit: float | None,
        trace_context: Any,
    ) -> float | None:
        if explicit is not None:
            try:
                return float(explicit)
            except (TypeError, ValueError):
                return None
        if isinstance(trace_context, Mapping):
            raw = trace_context.get("_llm_deadline_monotonic")
            if raw is not None:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _remaining(deadline_monotonic: float | None, phase: str) -> float | None:
        if deadline_monotonic is None:
            return None
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise LLMDeadlineExceeded(f"LLM deadline exceeded while {phase}")
        return remaining

    def _describe_exception(self, exc: BaseException) -> str:
        text = str(exc).strip()
        if text:
            return text
        if isinstance(exc, httpx.TimeoutException):
            return (
                f"{type(exc).__name__}: provider request exceeded the HTTP timeout "
                f"({self._config.timeout_s}s) without returning a response"
            )
        return type(exc).__name__

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_retries: int = 2,
        trace_context: Any = None,
        queue_context: Mapping[str, Any] | None = None,
        deadline_monotonic: float | None = None,
    ) -> LLMResponse:
        """Run a chat completion while respecting profile, provider, and turn limits."""
        deadline = self._coerce_deadline(deadline_monotonic, trace_context)
        acquired = False
        try:
            semaphore_timeout = self._remaining(
                deadline, "waiting for the model profile concurrency slot"
            )
            try:
                if semaphore_timeout is None:
                    await self._semaphore.acquire()
                else:
                    await asyncio.wait_for(
                        self._semaphore.acquire(), timeout=semaphore_timeout
                    )
            except asyncio.TimeoutError as exc:
                raise LLMDeadlineExceeded(
                    "LLM deadline exceeded while waiting for the model profile "
                    "concurrency slot"
                ) from exc
            acquired = True
            return await self._chat_unlocked(
                messages,
                tools,
                temperature,
                max_retries,
                queue_context,
                deadline_monotonic=deadline,
            )
        finally:
            if acquired:
                self._semaphore.release()

    @asynccontextmanager
    async def _request_context(
        self,
        metadata: Mapping[str, Any] | None = None,
        *,
        deadline_monotonic: float | None = None,
    ) -> AsyncIterator[dict[str, Any] | None]:
        if self._request_queue is None:
            yield None
            return
        async with self._request_queue.request(
            metadata, deadline_monotonic=deadline_monotonic
        ) as queued:
            yield queued

    @staticmethod
    def _normalize_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
        """Normalize OpenAI-compatible tool calls, including explicit JSON null."""
        if raw_tool_calls is None:
            return []
        if not isinstance(raw_tool_calls, list):
            raise ValueError("LLM response message.tool_calls must be a list or null")

        normalized: list[dict[str, Any]] = []
        for index, raw_call in enumerate(raw_tool_calls):
            if not isinstance(raw_call, dict):
                raise ValueError(f"LLM response tool_calls[{index}] must be an object")
            raw_function = raw_call.get("function") or {}
            if not isinstance(raw_function, dict):
                raise ValueError(
                    f"LLM response tool_calls[{index}].function must be an object"
                )
            name = str(raw_function.get("name", "") or "").strip()
            if not name:
                raise ValueError(
                    f"LLM response tool_calls[{index}].function.name is empty"
                )
            arguments = raw_function.get("arguments", "{}")
            if arguments is None:
                arguments = "{}"
            elif not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            normalized.append(
                {
                    "id": str(raw_call.get("id") or f"call_{uuid.uuid4().hex}"),
                    "function": {"name": name, "arguments": arguments},
                }
            )
        return normalized

    @staticmethod
    def _request_diagnostics(body: dict[str, Any]) -> dict[str, Any]:
        """Describe request shape without logging prompt or tool-result contents."""
        messages = body.get("messages")
        tools = body.get("tools")
        message_list = messages if isinstance(messages, list) else []
        tool_list = tools if isinstance(tools, list) else []
        assistant_tool_ids: list[str] = []
        empty_assistant_tool_ids = 0
        tool_result_ids: list[str] = []
        empty_tool_result_ids = 0
        roles: list[str] = []

        for message in message_list:
            if not isinstance(message, dict):
                roles.append(type(message).__name__)
                continue
            role = str(message.get("role", "") or "")
            roles.append(role)
            if role == "assistant":
                raw_calls = message.get("tool_calls") or []
                if isinstance(raw_calls, list):
                    for call in raw_calls:
                        call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
                        if call_id:
                            assistant_tool_ids.append(call_id)
                        else:
                            empty_assistant_tool_ids += 1
            elif role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                if call_id:
                    tool_result_ids.append(call_id)
                else:
                    empty_tool_result_ids += 1

        known_ids = set(assistant_tool_ids)
        tool_names = []
        for tool in tool_list:
            function = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(function, dict) and function.get("name"):
                tool_names.append(str(function["name"]))
        try:
            request_bytes = len(
                json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        except (TypeError, ValueError):
            request_bytes = -1

        return {
            "model": str(body.get("model", "") or ""),
            "message_count": len(message_list),
            "message_roles": roles,
            "tool_count": len(tool_list),
            "tool_names": tool_names,
            "request_bytes": request_bytes,
            "assistant_tool_call_count": len(assistant_tool_ids),
            "tool_result_count": len(tool_result_ids),
            "empty_assistant_tool_call_ids": empty_assistant_tool_ids,
            "empty_tool_result_ids": empty_tool_result_ids,
            "unmatched_tool_result_ids": sorted(
                {call_id for call_id in tool_result_ids if call_id not in known_ids}
            ),
        }

    def _http_error(self, response: httpx.Response, body: dict[str, Any]) -> RuntimeError:
        """Build a redacted provider error that includes request-shape diagnostics."""
        detail = ""
        try:
            payload = response.json()
            error = payload.get("error", payload) if isinstance(payload, dict) else payload
            if isinstance(error, dict):
                selected = {
                    key: error.get(key)
                    for key in ("message", "type", "code", "param")
                    if error.get(key) not in (None, "")
                }
                detail = json.dumps(selected or error, ensure_ascii=False)
            else:
                detail = str(error)
        except Exception:
            detail = response.text
        if self._config.api_key:
            detail = detail.replace(self._config.api_key, "[REDACTED]")
        detail = " ".join(detail.split())
        if len(detail) > 1000:
            detail = detail[:1000] + "\u2026"
        diagnostics = json.dumps(
            self._request_diagnostics(body), ensure_ascii=False, separators=(",", ":")
        )
        message = (
            f"Provider returned HTTP {response.status_code} for {response.request.url}; "
            f"response={detail or '<empty>'}; request={diagnostics}"
        )
        return RuntimeError(message)

    async def _chat_unlocked(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_retries: int = 2,
        queue_context: Mapping[str, Any] | None = None,
        *,
        deadline_monotonic: float | None = None,
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
                remaining = self._remaining(deadline_monotonic, "starting an HTTP attempt")
                attempt_timeout = (
                    float(self._config.timeout_s)
                    if remaining is None
                    else max(0.001, min(float(self._config.timeout_s), remaining))
                )
                request_context = dict(queue_context or {})
                request_context["attempt"] = attempt + 1
                async with self._request_context(
                    request_context, deadline_monotonic=deadline_monotonic
                ):
                    remaining = self._remaining(
                        deadline_monotonic, "waiting for the provider response"
                    )
                    if remaining is not None:
                        attempt_timeout = max(
                            0.001, min(float(self._config.timeout_s), remaining)
                        )
                    resp = await client.post(
                        "/chat/completions", json=body, timeout=attempt_timeout
                    )
                    if resp.is_error:
                        raise self._http_error(resp, body)
                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("LLM response body must be a JSON object")
                choices = data.get("choices") or []
                if not isinstance(choices, list) or not choices:
                    raise ValueError("LLM response choices must be a non-empty list")
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise ValueError("LLM response choices[0] must be an object")
                message = choice.get("message") or {}
                if not isinstance(message, dict):
                    raise ValueError("LLM response choices[0].message must be an object")
                usage = data.get("usage") or {}
                if not isinstance(usage, dict):
                    usage = {}
                latency = (time.monotonic() - start) * 1000
                result = LLMResponse(
                    content=message.get("content", "") or "",
                    tool_calls=self._normalize_tool_calls(message.get("tool_calls")),
                    input_tokens=usage.get("prompt_tokens", 0) or 0,
                    output_tokens=usage.get("completion_tokens", 0) or 0,
                    latency_ms=latency,
                    raw=data,
                )
                self.token_counter.add(result.input_tokens, result.output_tokens)
                self._log_debug(body, data, latency)
                return result
            except (LLMDeadlineExceeded, APIRequestDeadlineExceeded) as exc:
                message = self._describe_exception(exc)
                logger.error("LLM call stopped by deadline: %s", message)
                self._log_debug(
                    body,
                    {"error": message, "request_diagnostics": self._request_diagnostics(body)},
                    (time.monotonic() - start) * 1000,
                )
                if isinstance(exc, LLMDeadlineExceeded):
                    raise
                raise LLMDeadlineExceeded(message) from exc
            except Exception as exc:
                message = self._describe_exception(exc)
                if attempt < max_retries:
                    wait = self._config.retry_delay_s
                    remaining = (
                        None
                        if deadline_monotonic is None
                        else deadline_monotonic - time.monotonic()
                    )
                    if remaining is not None and (
                        remaining <= 0 or wait + 0.05 >= remaining
                    ):
                        deadline_error = LLMDeadlineExceeded(
                            "LLM deadline leaves insufficient time for another provider retry; "
                            f"last error: {message}"
                        )
                        self._log_debug(
                            body,
                            {
                                "error": str(deadline_error),
                                "request_diagnostics": self._request_diagnostics(body),
                            },
                            (time.monotonic() - start) * 1000,
                        )
                        raise deadline_error from exc
                    logger.warning(
                        "LLM call failed (attempt %d/%d), retry in %.1fs: %s",
                        attempt + 1,
                        1 + max_retries,
                        wait,
                        message,
                    )
                    if wait > 0:
                        await asyncio.sleep(wait)
                else:
                    logger.error(
                        "LLM call failed after %d attempts: %s", 1 + max_retries, message
                    )
                    self._log_debug(
                        body,
                        {"error": message, "request_diagnostics": self._request_diagnostics(body)},
                        (time.monotonic() - start) * 1000,
                    )
                    if str(exc).strip():
                        raise
                    raise RuntimeError(message) from exc
        raise RuntimeError("LLM retry loop exited unexpectedly")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        trace_context: Any = None,
        queue_context: Mapping[str, Any] | None = None,
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
                async with self._request_context(queue_context):
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
                            for tc_delta in delta.get("tool_calls") or []:
                                if not isinstance(tc_delta, dict):
                                    continue
                                idx = tc_delta.get("index", 0)
                                if idx not in tc_accum:
                                    tc_accum[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "name": (tc_delta.get("function") or {}).get("name", ""),
                                        "arguments": "",
                                    }
                                if tc_delta.get("id"):
                                    tc_accum[idx]["id"] = tc_delta["id"]
                                fn = tc_delta.get("function") or {}
                                if not isinstance(fn, dict):
                                    fn = {}
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
                            tool_calls=self._normalize_tool_calls(tool_calls),
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
                fallback_kwargs = (
                    {"queue_context": queue_context} if queue_context is not None else {}
                )
                result = await self._chat_unlocked(
                    messages, tools, temperature, **fallback_kwargs
                )
                if result.content:
                    yield {"type": "delta", "content": result.content}
                yield {"type": "done", "response": result}

    def _log_debug(self, request: dict, response: dict, latency_ms: float) -> None:
        """记录 LLM 调试日志."""
        choices = response.get("choices") or [{}]
        first_choice = choices[0] if isinstance(choices, list) and choices else {}
        msg = first_choice.get("message", {}) if isinstance(first_choice, dict) else {}
        if not isinstance(msg, dict):
            msg = {}
        usage = response.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        entry = {
            "timestamp": time.time(),
            "model": self._config.model,
            "latency_ms": round(latency_ms, 1),
            "request_messages_count": len(request.get("messages", [])),
            "response_content": msg.get("content", ""),
            "tool_calls": [
                {
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments": (tc.get("function") or {}).get("arguments"),
                }
                for tc in (msg.get("tool_calls") or [])
                if isinstance(tc, dict) and isinstance(tc.get("function") or {}, dict)
            ],
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
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
