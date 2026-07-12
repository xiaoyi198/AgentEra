"""Tests for safe LLM configuration and streaming fallback behavior."""

import asyncio
import json

from multiagent_framework.llm_client import LLMClient, LLMConfig, LLMResponse


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
            }
        ),
        encoding="utf-8",
    )

    config = LLMConfig.from_file(str(path))

    assert config.api_key == "secret-from-env"
    assert config.api_key_env == "TEST_WORLD_SIM_KEY"
    assert config.timeout_s == 1
    assert config.max_concurrent == 1


def test_save_persists_reference_but_not_secret(tmp_path):
    path = tmp_path / "llm.json"
    config = LLMConfig(
        api_key="do-not-write-this",
        api_key_env="TEST_WORLD_SIM_KEY",
        model="test-model",
    )

    config.save_to_file(str(path))

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "do-not-write-this" not in raw
    assert data["api_key"] == "${TEST_WORLD_SIM_KEY}"


def test_plain_in_memory_key_is_never_persisted(tmp_path):
    path = tmp_path / "llm.json"
    config = LLMConfig(api_key="ephemeral-secret", model="test-model")

    config.save_to_file(str(path))

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert "ephemeral-secret" not in raw
    assert data["api_key"] == ""


def test_direct_config_clamps_limits():
    config = LLMConfig(timeout_s=0, max_concurrent=-3)

    assert config.timeout_s == 1
    assert config.max_concurrent == 1


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
