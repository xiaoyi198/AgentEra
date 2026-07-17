"""Crash-resilient Agent wakeup outbox with at-least-once delivery semantics."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterator, Mapping
from typing import Any, Callable

WAKEUP_OUTBOX_SCHEMA_VERSION = "1.0"
DEFAULT_WAKEUP_CAPACITY = 32_768
MAX_WAKEUP_PAYLOAD_BYTES = 256 * 1024
MAX_WAKEUP_OUTBOX_BYTES = 64 * 1024 * 1024
MAX_WAKEUP_DEPTH = 16
MAX_WAKEUP_NODES = 20_000
MAX_WAKEUP_ID_LENGTH = 256


class WakeupOutboxError(ValueError):
    """Raised when a wakeup cannot be accepted safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def stable_chain_id(delivery_id: str) -> str:
    """Derive a stable chain id from a durable delivery id."""
    digest = hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()[:16]
    return f"chain_{digest}"


def event_delivery_id(event_id: str, target: str) -> str:
    """Build the deterministic delivery id used by event replay."""
    digest = hashlib.sha256(
        f"{event_id}\0{target}".encode("utf-8")
    ).hexdigest()[:20]
    return f"event:{digest}"


def chain_delivery_id(chain_id: str, target: str, payload: Mapping[str, Any]) -> str:
    """Build a replay-stable id for a wakeup emitted by one routed chain."""
    canonical_payload = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    digest = hashlib.sha256(
        f"{chain_id}\0{target}\0{canonical_payload}".encode("utf-8")
    ).hexdigest()[:20]
    return f"chain:{digest}"


def _json_safe_copy(value: Any) -> Any:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_WAKEUP_NODES:
            raise WakeupOutboxError("payload_too_complex", "wakeup payload has too many values")
        if depth > MAX_WAKEUP_DEPTH:
            raise WakeupOutboxError("payload_too_deep", "wakeup payload is too deeply nested")
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise WakeupOutboxError("payload_not_json_safe", "wakeup payload contains a non-finite number")
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise WakeupOutboxError("payload_not_json_safe", "wakeup payload keys must be strings")
                visit(child, depth + 1)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child, depth + 1)
            return
        raise WakeupOutboxError(
            "payload_not_json_safe",
            f"wakeup payload contains unsupported type {type(item).__name__}",
        )

    visit(value, 0)
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WakeupOutboxError("payload_not_json_safe", "wakeup payload is not JSON-safe") from exc
    if len(encoded) > MAX_WAKEUP_PAYLOAD_BYTES:
        raise WakeupOutboxError("payload_too_large", "wakeup payload exceeds the size limit")
    return json.loads(encoded.decode("utf-8"))


def _clean_id(value: Any, *, field: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise WakeupOutboxError(f"{field}_required", f"{field} is required")
    if len(text) > MAX_WAKEUP_ID_LENGTH:
        raise WakeupOutboxError(f"{field}_too_long", f"{field} exceeds the length limit")
    return text


class WakeupOutboxRuntime:
    """Bounded persistent outbox.

    Entries move through ``pending -> inflight -> acked/removed``. A restored
    ``inflight`` entry is deliberately returned to ``pending`` so a crash can
    cause a duplicate delivery but cannot silently lose the wakeup.

    The object intentionally offers the small list-like surface used by legacy
    function-call handlers: iteration, indexing, equality, ``append`` and
    ``clear``. New orchestration code should use ``enqueue``/``claim_pending``/
    ``ack``/``retry`` directly.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_WAKEUP_CAPACITY,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = int(capacity)
        self._on_change = on_change
        self._next_sequence = 1
        self._entries: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._total_payload_bytes = 0
        self.last_restore_error = ""

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _pending(self) -> list[dict[str, Any]]:
        return [entry for entry in self._entries if entry["status"] == "pending"]

    def __len__(self) -> int:
        return sum(entry["status"] == "pending" for entry in self._entries)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._pending())

    def __getitem__(self, index: int | slice) -> Any:
        return self._pending()[index]

    def __bool__(self) -> bool:
        return any(entry["status"] == "pending" for entry in self._entries)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, WakeupOutboxRuntime):
            return self._pending() == other._pending()
        if isinstance(other, list):
            return self._pending() == other
        return NotImplemented

    def append(self, wakeup: Mapping[str, Any]) -> None:
        self.enqueue(wakeup)

    def clear(self) -> None:
        if self._entries:
            self._entries.clear()
            self._by_id.clear()
            self._total_payload_bytes = 0
            self._changed()

    @property
    def total_count(self) -> int:
        return len(self._entries)

    @property
    def pending_count(self) -> int:
        return len(self)

    @property
    def inflight_count(self) -> int:
        return sum(entry["status"] == "inflight" for entry in self._entries)

    @property
    def payload_bytes(self) -> int:
        return self._total_payload_bytes

    def get(self, delivery_id: str) -> dict[str, Any] | None:
        return self._by_id.get(str(delivery_id))

    def enqueue(
        self,
        wakeup: Mapping[str, Any],
        *,
        delivery_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(wakeup, Mapping):
            raise WakeupOutboxError("entry_invalid", "wakeup must be an object")
        target = _clean_id(wakeup.get("target"), field="target", required=True)
        payload_value = wakeup.get("payload", {})
        if not isinstance(payload_value, Mapping):
            raise WakeupOutboxError("payload_invalid", "wakeup payload must be an object")
        payload = _json_safe_copy(dict(payload_value))
        payload_bytes = len(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))
        timer_id = _clean_id(wakeup.get("timer_id"), field="timer_id")
        event_id = _clean_id(
            wakeup.get("event_id", payload.get("event_id", "")), field="event_id",
        )

        provided_chain_id = _clean_id(
            wakeup.get("chain_id", payload.get("chain_id", "")), field="chain_id",
        )
        chosen_id = _clean_id(
            delivery_id or wakeup.get("delivery_id"), field="delivery_id",
        )
        sequence = self._next_sequence
        if not chosen_id:
            if timer_id:
                chosen_id = f"timer:{timer_id}"
            elif event_id:
                chosen_id = event_delivery_id(event_id, target)
            elif provided_chain_id:
                chosen_id = chain_delivery_id(provided_chain_id, target, payload)
            else:
                chosen_id = f"wakeup:{sequence}"
        chosen_id = _clean_id(chosen_id, field="delivery_id", required=True)
        existing = self._by_id.get(chosen_id)
        if existing is not None:
            return existing
        if len(self._entries) >= self.capacity:
            raise WakeupOutboxError("capacity_exceeded", "wakeup outbox capacity exceeded")
        if self._total_payload_bytes + payload_bytes > MAX_WAKEUP_OUTBOX_BYTES:
            raise WakeupOutboxError(
                "payload_capacity_exceeded", "wakeup outbox payload capacity exceeded"
            )

        chain_id = provided_chain_id or stable_chain_id(chosen_id)
        raw_chain_ids = wakeup.get("chain_ids", [])
        chain_ids: list[str] = []
        if raw_chain_ids:
            if not isinstance(raw_chain_ids, (list, tuple)):
                raise WakeupOutboxError("chain_ids_invalid", "chain_ids must be an array")
            for raw_chain_id in raw_chain_ids:
                normalized = _clean_id(raw_chain_id, field="chain_id")
                if normalized and normalized not in chain_ids:
                    chain_ids.append(normalized)
        if chain_id not in chain_ids:
            chain_ids.insert(0, chain_id)

        entry = {
            "delivery_id": chosen_id,
            "sequence": sequence,
            "status": "pending",
            "attempts": 0,
            "payload_bytes": payload_bytes,
            "target": target,
            "payload": payload,
            "chain_id": chain_id,
            "chain_ids": chain_ids,
            "event_id": event_id,
            "timer_id": timer_id,
        }
        self._entries.append(entry)
        self._by_id[chosen_id] = entry
        self._total_payload_bytes += payload_bytes
        self._next_sequence += 1
        self._changed()
        return entry

    def claim_pending(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        for entry in self._entries:
            if entry["status"] != "pending":
                continue
            entry["status"] = "inflight"
            entry["attempts"] += 1
            claimed.append(entry)
            if limit is not None and len(claimed) >= limit:
                break
        if claimed:
            self._changed()
        return claimed

    def ack(self, delivery_ids: list[str] | tuple[str, ...] | set[str]) -> int:
        wanted = {str(item) for item in delivery_ids if str(item)}
        if not wanted:
            return 0
        before = len(self._entries)
        self._entries = [entry for entry in self._entries if entry["delivery_id"] not in wanted]
        removed = before - len(self._entries)
        if removed:
            self._by_id = {entry["delivery_id"]: entry for entry in self._entries}
            self._total_payload_bytes = sum(
                int(entry.get("payload_bytes", 0)) for entry in self._entries
            )
            self._changed()
        return removed

    def retry(self, delivery_ids: list[str] | tuple[str, ...] | set[str]) -> int:
        wanted = {str(item) for item in delivery_ids if str(item)}
        changed = 0
        for entry in self._entries:
            if entry["delivery_id"] in wanted and entry["status"] == "inflight":
                entry["status"] = "pending"
                changed += 1
        if changed:
            self._changed()
        return changed

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": WAKEUP_OUTBOX_SCHEMA_VERSION,
            "capacity": self.capacity,
            "next_sequence": self._next_sequence,
            "entries": copy.deepcopy(self._entries),
        }

    def restore(self, state: Any) -> bool:
        if state in (None, {}):
            self._entries.clear()
            self._by_id.clear()
            self._total_payload_bytes = 0
            self._next_sequence = 1
            self.last_restore_error = ""
            return True
        if not isinstance(state, Mapping):
            return self._reject_restore("snapshot_invalid")
        raw_entries = state.get("entries", [])
        if not isinstance(raw_entries, list) or len(raw_entries) > self.capacity:
            return self._reject_restore("entries_invalid")

        restored: list[dict[str, Any]] = []
        restored_ids: set[str] = set()
        restored_payload_bytes = 0
        max_sequence = 0
        try:
            for raw in raw_entries:
                if not isinstance(raw, Mapping):
                    raise WakeupOutboxError("entry_invalid", "snapshot entry must be an object")
                sequence = int(raw.get("sequence", 0))
                if sequence <= 0:
                    raise WakeupOutboxError("sequence_invalid", "snapshot sequence must be positive")
                delivery_id = _clean_id(raw.get("delivery_id"), field="delivery_id", required=True)
                if delivery_id in restored_ids:
                    raise WakeupOutboxError("delivery_id_duplicate", "snapshot contains a duplicate delivery id")
                normalized = WakeupOutboxRuntime(capacity=self.capacity)
                normalized._next_sequence = sequence
                entry = normalized.enqueue(raw, delivery_id=delivery_id)
                entry["sequence"] = sequence
                entry["attempts"] = max(0, int(raw.get("attempts", 0)))
                status = str(raw.get("status", "pending"))
                if status not in {"pending", "inflight"}:
                    raise WakeupOutboxError("status_invalid", "snapshot entry has invalid status")
                entry["status"] = "pending"  # inflight is replayed after restart
                restored_payload_bytes += int(entry.get("payload_bytes", 0))
                if restored_payload_bytes > MAX_WAKEUP_OUTBOX_BYTES:
                    raise WakeupOutboxError(
                        "payload_capacity_exceeded",
                        "wakeup outbox payload capacity exceeded",
                    )
                restored.append(entry)
                restored_ids.add(delivery_id)
                max_sequence = max(max_sequence, sequence)
            requested_next = int(state.get("next_sequence", max_sequence + 1))
            next_sequence = max(max_sequence + 1, requested_next, 1)
        except (TypeError, ValueError, WakeupOutboxError) as exc:
            code = exc.code if isinstance(exc, WakeupOutboxError) else "snapshot_invalid"
            return self._reject_restore(code)

        self._entries = restored
        self._by_id = {entry["delivery_id"]: entry for entry in restored}
        self._total_payload_bytes = restored_payload_bytes
        self._next_sequence = next_sequence
        self.last_restore_error = ""
        return True

    def _reject_restore(self, code: str) -> bool:
        self._entries.clear()
        self._by_id.clear()
        self._total_payload_bytes = 0
        self._next_sequence = 1
        self.last_restore_error = str(code)
        self._changed()
        return False
