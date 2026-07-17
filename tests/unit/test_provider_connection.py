from __future__ import annotations

import asyncio

import httpx
import pytest

from routers.settings import ProviderTestRequest, _probe_provider


def _request(**overrides):
    data = {
        "name": "primary",
        "base_url": "https://provider.example/v1",
        "api_key": "test-secret-key",
        "timeout_s": 3,
        "model": "wanted-model",
    }
    data.update(overrides)
    return ProviderTestRequest(**data)


def _run(handler, **overrides):
    transport = httpx.MockTransport(handler)
    return asyncio.run(_probe_provider(_request(**overrides), transport=transport))


def test_probe_reports_connected_and_model_available():
    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer test-secret-key"
        return httpx.Response(200, json={"data": [{"id": "wanted-model"}, {"id": "other"}]})

    result = _run(handler)

    assert result["ok"] is True
    assert result["code"] == "connected"
    assert result["model_count"] == 2
    assert result["model_available"] is True


def test_probe_reports_model_not_found_after_successful_connection():
    result = _run(lambda _request: httpx.Response(200, json={"data": [{"id": "other"}]}))

    assert result["ok"] is False
    assert result["code"] == "model_not_found"
    assert result["model_available"] is False


@pytest.mark.parametrize(
    ("status", "code", "message_fragment"),
    [
        (401, "unauthorized", "API Key"),
        (403, "forbidden", "没有权限"),
        (404, "not_found", "Base URL"),
        (429, "rate_limited", "限流"),
        (503, "provider_error", "服务商故障"),
    ],
)
def test_probe_classifies_http_failures_and_redacts_key(status, code, message_fragment):
    result = _run(
        lambda _request: httpx.Response(status, text="failure test-secret-key"),
    )

    assert result["ok"] is False
    assert result["code"] == code
    assert message_fragment in result["message"]
    assert "test-secret-key" not in str(result)
    assert "[REDACTED]" in result["detail"]


def test_probe_classifies_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    result = _run(handler)

    assert result["ok"] is False
    assert result["code"] == "timeout"
    assert "超时" in result["message"]


def test_probe_classifies_connection_error_and_redacts_key():
    def handler(request):
        raise httpx.ConnectError("DNS failed test-secret-key", request=request)

    result = _run(handler)

    assert result["ok"] is False
    assert result["code"] == "connect_error"
    assert "DNS" in result["message"]
    assert "test-secret-key" not in result["message"]
