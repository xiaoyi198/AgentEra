"""Opt-in smoke tests for an OpenAI-compatible external API.

The tests are skipped unless both AGENTERA_TEST_BASE_URL and
AGENTERA_TEST_API_KEY are present. Secrets are read only from the process
environment and are never persisted by this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest


_REQUIRED_REPLY = "AGENTERA_API_OK"
_NON_CHAT_MODEL_MARKERS = (
    "embedding",
    "reranker",
    "image",
    "tts",
    "asr",
    "voiceclone",
    "voicedesign",
)
_PREFERRED_CHAT_MODELS = (
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "claude-3-5-haiku",
    "claude-sonnet-4-6",
    "gpt-5.4-mini",
)
_CHAT_PREFIXES = (
    "gpt-",
    "claude-",
    "gemini-",
    "deepseek-",
    "qwen",
    "glm-",
    "kimi-",
    "minimax-",
)


@dataclass(frozen=True)
class ExternalAPIConfig:
    base_url: str
    api_key: str
    model: str
    timeout_s: float
    model_attempts: int


def _normalized_v1_url(raw_url: str) -> str:
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        pytest.fail("AGENTERA_TEST_BASE_URL must be an absolute HTTP(S) URL")
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@pytest.fixture(scope="module")
def external_api_config() -> ExternalAPIConfig:
    raw_url = os.getenv("AGENTERA_TEST_BASE_URL", "").strip()
    api_key = os.getenv("AGENTERA_TEST_API_KEY", "").strip()
    if not raw_url or not api_key:
        pytest.skip(
            "external API smoke test requires AGENTERA_TEST_BASE_URL and "
            "AGENTERA_TEST_API_KEY"
        )

    timeout_s = max(5.0, float(os.getenv("AGENTERA_TEST_TIMEOUT_S", "90")))
    model_attempts = max(1, int(os.getenv("AGENTERA_TEST_MODEL_ATTEMPTS", "3")))
    return ExternalAPIConfig(
        base_url=_normalized_v1_url(raw_url),
        api_key=api_key,
        model=os.getenv("AGENTERA_TEST_MODEL", "").strip(),
        timeout_s=timeout_s,
        model_attempts=model_attempts,
    )


@pytest.fixture(scope="module")
def external_models(external_api_config: ExternalAPIConfig) -> list[str]:
    with httpx.Client(
        base_url=external_api_config.base_url,
        headers={"Authorization": f"Bearer {external_api_config.api_key}"},
        timeout=external_api_config.timeout_s,
    ) as client:
        response = client.get("/models")

    assert response.status_code == 200, (
        "external /models request failed with HTTP "
        f"{response.status_code}; response body is intentionally hidden"
    )
    payload = response.json()
    assert isinstance(payload, dict)
    entries = payload.get("data")
    assert isinstance(entries, list) and entries, "external API returned no models"
    model_ids = [
        str(item.get("id", "")).strip()
        for item in entries
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]
    assert model_ids, "external API returned no usable model IDs"
    return model_ids


def _chat_candidates(model_ids: list[str], requested_model: str) -> list[str]:
    if requested_model:
        assert requested_model in model_ids, (
            "AGENTERA_TEST_MODEL is not present in the provider model catalog"
        )
        return [requested_model]

    unique_ids = list(dict.fromkeys(model_ids))
    candidates: list[str] = []
    for preferred in _PREFERRED_CHAT_MODELS:
        if preferred in unique_ids:
            candidates.append(preferred)

    for model_id in unique_ids:
        lowered = model_id.lower()
        if any(marker in lowered for marker in _NON_CHAT_MODEL_MARKERS):
            continue
        if lowered.startswith(_CHAT_PREFIXES) and model_id not in candidates:
            candidates.append(model_id)

    for model_id in unique_ids:
        lowered = model_id.lower()
        if any(marker in lowered for marker in _NON_CHAT_MODEL_MARKERS):
            continue
        if model_id not in candidates:
            candidates.append(model_id)
    return candidates


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                chunks.append(block["text"])
        return "".join(chunks).strip()
    return ""


def test_external_provider_lists_models(external_models: list[str]) -> None:
    assert len(external_models) == len(set(external_models)), (
        "external API returned duplicate model IDs"
    )


def test_external_provider_chat_completion(
    external_api_config: ExternalAPIConfig,
    external_models: list[str],
) -> None:
    candidates = _chat_candidates(external_models, external_api_config.model)
    assert candidates, "external API catalog contains no likely chat models"

    failures: list[str] = []
    with httpx.Client(
        base_url=external_api_config.base_url,
        headers={
            "Authorization": f"Bearer {external_api_config.api_key}",
            "Content-Type": "application/json",
        },
        timeout=external_api_config.timeout_s,
    ) as client:
        for model_id in candidates[: external_api_config.model_attempts]:
            try:
                response = client.post(
                    "/chat/completions",
                    json={
                        "model": model_id,
                        "messages": [
                            {
                                "role": "user",
                                "content": f"Reply with exactly: {_REQUIRED_REPLY}",
                            }
                        ],
                        "temperature": 0,
                        "max_tokens": 256,
                    },
                )
            except httpx.HTTPError as exc:
                failures.append(f"{model_id}: {type(exc).__name__}")
                continue

            if response.status_code != 200:
                failures.append(f"{model_id}: HTTP {response.status_code}")
                continue

            try:
                payload = response.json()
            except ValueError:
                failures.append(f"{model_id}: invalid JSON")
                continue
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices:
                failures.append(f"{model_id}: missing choices")
                continue
            message = choices[0].get("message", {})
            content = _message_text(message if isinstance(message, dict) else {})
            if not content:
                failures.append(f"{model_id}: empty content")
                continue

            assert _REQUIRED_REPLY in content, (
                f"external chat model {model_id!r} returned text but did not follow "
                "the deterministic smoke-test instruction"
            )
            return

    pytest.fail(
        "no auto-selected external chat model completed the smoke test; "
        + "; ".join(failures)
    )
