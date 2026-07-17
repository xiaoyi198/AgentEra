"""Shared FIFO-style request queues for outbound model API calls."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Mapping


class APIRequestDeadlineExceeded(TimeoutError):
    """Raised when a queued provider attempt cannot start before its deadline."""


class APIRequestQueue:
    """Bound and pace every outbound HTTP attempt for one API provider.

    A logical model call may retry several times. Each HTTP attempt joins this
    queue separately so retries cannot monopolize a provider slot and newly
    queued work remains observable.
    """

    def __init__(
        self,
        provider: str,
        *,
        max_concurrent: int = 2,
        queue_interval_s: float = 1.0,
        visible_limit: int = 50,
    ) -> None:
        self.provider = str(provider)
        self.max_concurrent = max(1, int(max_concurrent))
        self.queue_interval_s = max(0.0, float(queue_interval_s))
        self.visible_limit = max(1, int(visible_limit))
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._dispatch_lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._next_dispatch_at = 0.0
        self._waiting: dict[str, dict[str, Any]] = {}
        self._active: dict[str, dict[str, Any]] = {}
        self._totals = {
            "enqueued": 0,
            "started": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "expired": 0,
            "wait_ms_total": 0.0,
            "max_wait_ms": 0.0,
        }
        self._last_started_at = 0.0
        self._last_completed_at = 0.0

    @staticmethod
    def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, str]:
        value = dict(metadata or {})
        allowed = (
            "task_type",
            "profile",
            "model",
            "agent_id",
            "trace_id",
            "turn_id",
            "chain_id",
            "attempt",
        )
        return {
            key: str(value.get(key, "") or "")[:160]
            for key in allowed
            if value.get(key) not in (None, "")
        }

    def _set_waiting_state(self, request_id: str, state: str) -> None:
        with self._state_lock:
            item = self._waiting.get(request_id)
            if item is not None:
                item["state"] = state

    @asynccontextmanager
    async def request(
        self,
        metadata: Mapping[str, Any] | None = None,
        *,
        deadline_monotonic: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        request_id = uuid.uuid4().hex
        enqueued_at = time.time()
        enqueued_monotonic = time.monotonic()
        item = {
            "request_id": request_id,
            "state": "queued",
            "enqueued_at": enqueued_at,
            **self._safe_metadata(metadata),
        }
        with self._state_lock:
            self._waiting[request_id] = item
            self._totals["enqueued"] += 1

        acquired = False
        started_at = 0.0
        failed = False
        cancelled = False

        def remaining(phase: str) -> float | None:
            if deadline_monotonic is None:
                return None
            value = float(deadline_monotonic) - time.monotonic()
            if value <= 0:
                raise APIRequestDeadlineExceeded(
                    f"provider queue deadline exceeded while {phase} for {self.provider}"
                )
            return value

        try:
            slot_timeout = remaining("waiting for a concurrency slot")
            try:
                if slot_timeout is None:
                    await self._semaphore.acquire()
                else:
                    await asyncio.wait_for(self._semaphore.acquire(), timeout=slot_timeout)
            except asyncio.TimeoutError as exc:
                raise APIRequestDeadlineExceeded(
                    f"provider queue deadline exceeded while waiting for a concurrency slot "
                    f"for {self.provider}"
                ) from exc
            acquired = True
            self._set_waiting_state(request_id, "spacing")

            dispatch_timeout = remaining("waiting for dispatch pacing")
            dispatch_acquired = False
            try:
                if dispatch_timeout is None:
                    await self._dispatch_lock.acquire()
                else:
                    await asyncio.wait_for(
                        self._dispatch_lock.acquire(), timeout=dispatch_timeout
                    )
                dispatch_acquired = True
                delay = max(0.0, self._next_dispatch_at - time.monotonic())
                if delay > 0:
                    spacing_timeout = remaining("waiting for dispatch pacing")
                    if spacing_timeout is not None and delay >= spacing_timeout:
                        raise APIRequestDeadlineExceeded(
                            f"provider queue deadline exceeded during dispatch pacing "
                            f"for {self.provider}"
                        )
                    await asyncio.sleep(delay)
                started_monotonic = time.monotonic()
                started_at = time.time()
                self._next_dispatch_at = started_monotonic + self.queue_interval_s
            except asyncio.TimeoutError as exc:
                raise APIRequestDeadlineExceeded(
                    f"provider queue deadline exceeded while waiting for dispatch pacing "
                    f"for {self.provider}"
                ) from exc
            finally:
                if dispatch_acquired:
                    self._dispatch_lock.release()

            wait_ms = max(0.0, (time.monotonic() - enqueued_monotonic) * 1000.0)
            active_item = {
                **item,
                "state": "active",
                "started_at": started_at,
                "wait_ms": round(wait_ms, 2),
            }
            with self._state_lock:
                self._waiting.pop(request_id, None)
                self._active[request_id] = active_item
                self._totals["started"] += 1
                self._totals["wait_ms_total"] += wait_ms
                self._totals["max_wait_ms"] = max(
                    self._totals["max_wait_ms"], wait_ms
                )
                self._last_started_at = started_at

            try:
                yield {
                    "request_id": request_id,
                    "provider": self.provider,
                    "wait_ms": round(wait_ms, 2),
                    "started_at": started_at,
                }
            except asyncio.CancelledError:
                cancelled = True
                raise
            except BaseException:
                failed = True
                raise
        except asyncio.CancelledError:
            with self._state_lock:
                if request_id in self._waiting:
                    self._waiting.pop(request_id, None)
                    self._totals["cancelled"] += 1
            raise
        except APIRequestDeadlineExceeded:
            with self._state_lock:
                self._waiting.pop(request_id, None)
                self._totals["expired"] += 1
            raise
        except BaseException:
            with self._state_lock:
                self._waiting.pop(request_id, None)
            raise
        finally:
            if started_at:
                completed_at = time.time()
                with self._state_lock:
                    self._active.pop(request_id, None)
                    self._totals["completed"] += 1
                    self._totals["failed"] += int(failed)
                    self._totals["cancelled"] += int(cancelled)
                    self._last_completed_at = completed_at
            if acquired:
                self._semaphore.release()

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._state_lock:
            waiting = [dict(item) for item in self._waiting.values()]
            active = [dict(item) for item in self._active.values()]
            totals = dict(self._totals)
            last_started_at = self._last_started_at
            last_completed_at = self._last_completed_at
        waiting.sort(key=lambda item: (item.get("enqueued_at", 0.0), item["request_id"]))
        active.sort(key=lambda item: (item.get("started_at", 0.0), item["request_id"]))
        for index, item in enumerate(waiting, start=1):
            item["position"] = index
            item["wait_ms"] = round(max(0.0, now - item["enqueued_at"]) * 1000.0, 2)
        for item in active:
            item["active_ms"] = round(max(0.0, now - item["started_at"]) * 1000.0, 2)
        started = max(1, int(totals["started"]))
        totals["wait_ms_total"] = round(float(totals["wait_ms_total"]), 2)
        totals["max_wait_ms"] = round(float(totals["max_wait_ms"]), 2)
        totals["avg_wait_ms"] = round(float(totals["wait_ms_total"]) / started, 2)
        return {
            "provider": self.provider,
            "max_concurrent": self.max_concurrent,
            "queue_interval_s": self.queue_interval_s,
            "waiting": len(waiting),
            "spacing": sum(item.get("state") == "spacing" for item in waiting),
            "active": len(active),
            "available_slots": max(
                0,
                self.max_concurrent
                - len(active)
                - sum(item.get("state") == "spacing" for item in waiting),
            ),
            "last_started_at": last_started_at or None,
            "last_completed_at": last_completed_at or None,
            "totals": totals,
            "requests": (active + waiting)[: self.visible_limit],
        }
