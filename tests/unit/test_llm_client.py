"""Tests for safe LLM configuration and streaming fallback behavior."""

import asyncio
import json
import time

import httpx
import pytest

from multiagent_framework.llm_client import LLMClient, LLMConfig, LLMDeadlineExceeded, LLMResponse


def test_environment_placeholder_resolves(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_WORLD_SIM_KEY", "secret-from-env")
    path = tmp_path / "llm.json"
    path.write_text(
        json.dumps(
            {
                "base_url": "https://example.invalid/v1",
                "api_key": "${TEST_WORLD_SIM_KEY}",
                "model": "test-model",
                "timeout_s": 0,
                "max_concurrent": 0,
                "retry_delay_s": 7.5,
                "queue_interval_s": 0.75,
            }
        ),
        encoding="utf-8",
    )

    config = LLMConfig.from_file(str(path))

    assert config.api_key == "secret-from-env"
    assert config.api_key_env == "TEST_WORLD_SIM_KEY"
    assert config.timeout_s == 1
    assert config.max_concurrent == 1
    assert config.retry_delay_s == 7.5
    assert config.queue_interval_s == 0.75


def test_save_persists_plaintext_key_for_local_use(tmp_path):
    path = tmp_path / "llm.json"
    config = LLMConfig(
        api_key="local-plaintext-key",
        api_key_env="LEGACY_ENV_NAME",
        model="test-model",
    )

    config.save_to_file(str(path))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["api_key"] == "local-plaintext-key"
    assert "api_key_env" not in data
    assert data["retry_delay_s"] == 5.0
    assert data["queue_interval_s"] == 1.0


def test_plaintext_key_round_trips_from_local_file(tmp_path):
    path = tmp_path / "llm.json"
    LLMConfig(api_key="local-secret", model="test-model").save_to_file(str(path))

    loaded = LLMConfig.from_file(str(path))

    assert loaded.api_key == "local-secret"
    assert loaded.api_key_env == ""


def test_direct_config_clamps_limits():
    config = LLMConfig(timeout_s=0, max_concurrent=-3, retry_delay_s=-4, queue_interval_s=-2)

    assert config.timeout_s == 1
    assert config.max_concurrent == 1
    assert config.retry_delay_s == 0.0
    assert config.queue_interval_s == 0.0


def test_display_metadata_never_reveals_key_material():
    config = LLMConfig(
        api_key="secret-with-identifying-suffix",
        api_key_env="TEST_WORLD_SIM_KEY",
    )

    display = config.to_display_dict()

    assert display["api_key_configured"] is True
    assert display["api_key_env"] == "TEST_WORLD_SIM_KEY"
    assert "suffix" not in json.dumps(display)


def test_stream_fallback_does_not_reenter_semaphore():
    class FailingStreamClient:
        def stream(self, *args, **kwargs):
            raise RuntimeError("streaming unavailable")

    async def run():
        client = LLMClient(LLMConfig(max_concurrent=1))

        async def get_client():
            return FailingStreamClient()

        async def fallback(messages, tools=None, temperature=0.7, max_retries=2):
            return LLMResponse(content="fallback")

        client._get_client = get_client
        client._chat_unlocked = fallback

        chunks = []
        async for chunk in client.chat_stream([{"role": "user", "content": "hello"}]):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(asyncio.wait_for(run(), timeout=1.0))

    assert chunks[0] == {"type": "delta", "content": "fallback"}
    assert chunks[1]["type"] == "done"
    assert chunks[1]["response"].content == "fallback"


def test_chat_accepts_explicit_null_tool_calls():
    async def run():
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {"message": {"content": "正常响应", "tool_calls": None}}
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                },
            )

        client = LLMClient(LLMConfig(base_url="https://provider.invalid/v1"), debug_log_path=None)
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.chat([{"role": "user", "content": "hello"}], max_retries=0)
        finally:
            await client.close()

    response = asyncio.run(run())

    assert response.content == "正常响应"
    assert response.tool_calls == []
    assert response.input_tokens == 3
    assert response.output_tokens == 2


def test_chat_normalizes_missing_tool_call_id_and_arguments():
    async def run():
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "message": {
                            "content": None,
                            "tool_calls": [{
                                "id": None,
                                "function": {"name": "list_entities", "arguments": None},
                            }],
                        }
                    }],
                    "usage": None,
                },
            )

        client = LLMClient(LLMConfig(base_url="https://provider.invalid/v1"), debug_log_path=None)
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.chat([{"role": "user", "content": "hello"}], max_retries=0)
        finally:
            await client.close()

    response = asyncio.run(run())

    assert response.content == ""
    assert response.tool_calls[0]["id"].startswith("call_")
    assert response.tool_calls[0]["function"] == {
        "name": "list_entities",
        "arguments": "{}",
    }


def test_upstream_400_retries_after_configured_cooldown(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("multiagent_framework.llm_client.asyncio.sleep", fake_sleep)

    async def run():
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(
                    400,
                    request=request,
                    json={
                        "error": {
                            "message": "Upstream request failed",
                            "type": "Console Go",
                        }
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": "retry succeeded"}}],
                    "usage": {},
                },
            )

        client = LLMClient(
            LLMConfig(
                base_url="https://provider.invalid/v1",
                retry_delay_s=5,
            ),
            debug_log_path=None,
        )
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await client.chat(
                [{"role": "user", "content": "retry me"}],
                max_retries=1,
            )
            return calls, response
        finally:
            await client.close()

    calls, response = asyncio.run(run())

    assert calls == 2
    assert sleeps == [5.0]
    assert response.content == "retry succeeded"


def test_400_error_includes_provider_detail_and_content_free_request_diagnostics():
    async def run():
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                request=request,
                json={"error": {"message": "Upstream request failed", "type": "Console Go"}},
            )

        client = LLMClient(
            LLMConfig(base_url="https://provider.invalid/v1", retry_delay_s=0),
            debug_log_path=None,
        )
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        try:
            await client.chat(
                [{"role": "user", "content": "do-not-echo-this-prompt"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "list_entities",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }],
                max_retries=0,
            )
        finally:
            await client.close()

    with pytest.raises(RuntimeError) as captured:
        asyncio.run(run())

    text = str(captured.value)
    assert "HTTP 400" in text
    assert "Upstream request failed" in text
    assert '"message_count":1' in text
    assert '"tool_count":1' in text
    assert "do-not-echo-this-prompt" not in text


def test_deadline_stops_retry_cycle_and_reports_empty_httpx_timeout():
    calls = []

    async def run():
        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ReadTimeout("", request=request)

        client = LLMClient(
            LLMConfig(
                base_url="https://provider.invalid/v1",
                timeout_s=30,
                retry_delay_s=0.2,
            ),
            debug_log_path=None,
        )
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        try:
            deadline = time.monotonic() + 0.05
            await client.chat(
                [{"role": "user", "content": "hello"}],
                max_retries=2,
                deadline_monotonic=deadline,
            )
        finally:
            await client.close()

    with pytest.raises(LLMDeadlineExceeded) as captured:
        asyncio.run(run())

    text = str(captured.value)
    assert "insufficient time" in text
    assert "ReadTimeout" in text
    assert "30s" in text
    assert len(calls) == 1


def test_turn_deadline_caps_each_http_attempt_timeout():
    async def run():
        observed_timeout = None

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_timeout
            observed_timeout = request.extensions["timeout"]["read"]
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {},
                },
            )

        client = LLMClient(
            LLMConfig(base_url="https://provider.invalid/v1", timeout_s=30),
            debug_log_path=None,
        )
        transport = httpx.MockTransport(handler)
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1", transport=transport
        )
        try:
            response = await client.chat(
                [{"role": "user", "content": "hello"}],
                max_retries=0,
                deadline_monotonic=time.monotonic() + 0.2,
            )
            return response, observed_timeout
        finally:
            await client.close()

    response, observed_timeout = asyncio.run(run())
    assert response.content == "ok"
    assert 0 < observed_timeout <= 0.2
