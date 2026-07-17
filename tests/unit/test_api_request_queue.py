"""Tests for Provider-level shared outbound API queues."""

import asyncio
import json
import time

import httpx

from multiagent_framework.api_request_queue import APIRequestDeadlineExceeded, APIRequestQueue
from multiagent_framework.llm_client import LLMClient, LLMConfig


def test_queue_enforces_shared_concurrency_limit():
    async def run():
        queue = APIRequestQueue("primary", max_concurrent=1, queue_interval_s=0)
        release = asyncio.Event()
        first_started = asyncio.Event()
        active = 0
        max_active = 0

        async def worker(first=False):
            nonlocal active, max_active
            async with queue.request({"profile": "fast" if first else "deep"}):
                active += 1
                max_active = max(max_active, active)
                if first:
                    first_started.set()
                    await release.wait()
                active -= 1

        first = asyncio.create_task(worker(True))
        await first_started.wait()
        second = asyncio.create_task(worker(False))
        await asyncio.sleep(0.02)
        snapshot = queue.snapshot()
        assert snapshot["active"] == 1
        assert snapshot["waiting"] == 1
        release.set()
        await asyncio.gather(first, second)
        return max_active, queue.snapshot()

    max_active, snapshot = asyncio.run(run())
    assert max_active == 1
    assert snapshot["totals"]["completed"] == 2


def test_queue_applies_minimum_dispatch_interval():
    async def run():
        queue = APIRequestQueue("primary", max_concurrent=2, queue_interval_s=0.05)
        starts = []

        async def worker():
            async with queue.request():
                starts.append(time.monotonic())

        await asyncio.gather(worker(), worker())
        return starts

    starts = asyncio.run(run())
    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.04


def test_cancelled_waiter_is_removed_from_snapshot():
    async def run():
        queue = APIRequestQueue("primary", max_concurrent=1, queue_interval_s=0)
        release = asyncio.Event()
        started = asyncio.Event()

        async def holder():
            async with queue.request():
                started.set()
                await release.wait()

        async def waiter():
            async with queue.request():
                return None

        first = asyncio.create_task(holder())
        await started.wait()
        second = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)
        second.cancel()
        try:
            await second
        except asyncio.CancelledError:
            pass
        snapshot = queue.snapshot()
        release.set()
        await first
        return snapshot

    snapshot = asyncio.run(run())
    assert snapshot["waiting"] == 0
    assert snapshot["totals"]["cancelled"] == 1


def test_queue_snapshot_only_exposes_allowlisted_metadata():
    async def run():
        queue = APIRequestQueue("primary", max_concurrent=1, queue_interval_s=0)
        async with queue.request({
            "task_type": "action.simulation",
            "profile": "deep",
            "api_key": "super-secret-key",
            "messages": [{"role": "user", "content": "private prompt"}],
        }):
            return queue.snapshot()

    snapshot = asyncio.run(run())
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "action.simulation" in serialized
    assert "super-secret-key" not in serialized
    assert "private prompt" not in serialized
    assert "messages" not in serialized


def test_llm_retries_rejoin_queue_for_each_http_attempt():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok", "tool_calls": None}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        })

    async def run():
        queue = APIRequestQueue("primary", max_concurrent=1, queue_interval_s=0)
        client = LLMClient(
            LLMConfig(base_url="https://provider.invalid/v1", retry_delay_s=0),
            debug_log_path=None,
            request_queue=queue,
        )
        client._client = httpx.AsyncClient(
            base_url="https://provider.invalid/v1",
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await client.chat(
                [{"role": "user", "content": "hello"}],
                max_retries=1,
                queue_context={"task_type": "action.simulation", "profile": "deep"},
            )
            return response, queue.snapshot()
        finally:
            await client.close()

    response, snapshot = asyncio.run(run())
    assert response.content == "ok"
    assert snapshot["totals"]["enqueued"] == 2
    assert snapshot["totals"]["started"] == 2
    assert snapshot["totals"]["failed"] == 1


def test_queue_wait_respects_caller_deadline_and_removes_expired_request():
    async def run():
        queue = APIRequestQueue("primary", max_concurrent=1, queue_interval_s=0)
        release = asyncio.Event()
        started = asyncio.Event()

        async def holder():
            async with queue.request():
                started.set()
                await release.wait()

        first = asyncio.create_task(holder())
        await started.wait()
        deadline = time.monotonic() + 0.03
        try:
            async with queue.request(deadline_monotonic=deadline):
                raise AssertionError("expired request must not enter the active section")
        except APIRequestDeadlineExceeded as exc:
            message = str(exc)
        snapshot = queue.snapshot()
        release.set()
        await first
        return message, snapshot

    message, snapshot = asyncio.run(run())
    assert "concurrency slot" in message
    assert snapshot["waiting"] == 0
    assert snapshot["totals"]["expired"] == 1
