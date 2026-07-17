"""Persistent chronological queue for materialized simulation work.

The queue owns only committed producer output.  Authoring runtimes keep their own
cursors; once an event or marker is materialized it is serialized here so per-tick
budgets, save/load, rollback, and process restarts cannot reorder or discard it.
"""
from __future__ import annotations

import copy
import heapq
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .types import EventObject

MAX_DEFERRED_TIMELINE_ENTRIES = 32768
MAX_DEFERRED_TIMELINE_ARCHIVES = 64
_ALLOWED_KINDS = {"temporal", "lifecycle_marker", "lifecycle_condition", "random"}


class DeferredTimelineError(ValueError):
    """Raised when newly enqueued timeline work violates the queue contract."""


@dataclass(frozen=True)
class DeferredTimelineEntry:
    occurrence_time: float
    source_rank: int
    sequence: int
    kind: str
    payload: dict[str, Any]
    metadata: dict[str, Any]

    @property
    def key(self) -> tuple[float, int, int]:
        return (self.occurrence_time, self.source_rank, self.sequence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_time": self.occurrence_time,
            "source_rank": self.source_rank,
            "sequence": self.sequence,
            "kind": self.kind,
            "payload": copy.deepcopy(self.payload),
            "metadata": copy.deepcopy(self.metadata),
        }


class DeferredTimelineRuntime:
    """Bounded, deterministic and save-safe heap of materialized tick work."""

    def __init__(self, *, max_entries: int = MAX_DEFERRED_TIMELINE_ENTRIES) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries <= 0:
            raise DeferredTimelineError("deferred_timeline.max_entries_invalid")
        self.max_entries = max_entries
        self._entries: list[tuple[float, int, int, DeferredTimelineEntry]] = []
        self._next_sequence = 0
        self._archives: dict[str, dict[str, Any]] = {}
        self.last_restore_error = ""

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def pending_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()
        self._next_sequence = 0
        self._archives.clear()

    def register_archive(self, archive_id: str, value: Mapping[str, Any]) -> None:
        key = str(archive_id or "").strip()
        if not key or len(key) > 256:
            raise DeferredTimelineError("deferred_timeline.archive_id_invalid")
        payload = self._normalize_mapping(value, "deferred_timeline.archive_invalid")
        if key not in self._archives and len(self._archives) >= MAX_DEFERRED_TIMELINE_ARCHIVES:
            raise DeferredTimelineError("deferred_timeline.archive_capacity_exceeded")
        self._archives[key] = payload

    def archive(self, archive_id: str) -> dict[str, Any] | None:
        value = self._archives.get(str(archive_id))
        return copy.deepcopy(value) if value is not None else None

    def prune_archives(self) -> None:
        referenced = {
            str(item[3].metadata.get("archive_id", ""))
            for item in self._entries
            if item[3].metadata.get("archive_id")
        }
        self._archives = {key: value for key, value in self._archives.items() if key in referenced}

    @staticmethod
    def event_payload(event: EventObject) -> dict[str, Any]:
        if not isinstance(event, EventObject):
            raise DeferredTimelineError("deferred_timeline.event_invalid")
        return {"event": asdict(event)}

    @staticmethod
    def decode_event(payload: Mapping[str, Any]) -> EventObject:
        raw = payload.get("event") if isinstance(payload, Mapping) else None
        if not isinstance(raw, Mapping):
            raise DeferredTimelineError("deferred_timeline.event_invalid")
        try:
            event = EventObject(
                event_id=str(raw.get("event_id", "")),
                event_type=str(raw.get("event_type", "")),
                severity=int(raw.get("severity", 5)),
                target_agents=[str(item) for item in raw.get("target_agents", [])],
                initial_payload=copy.deepcopy(dict(raw.get("initial_payload", {}))),
                generated_at=float(raw.get("generated_at", 0.0)),
                world_commands=[copy.deepcopy(dict(item)) for item in raw.get("world_commands", [])],
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise DeferredTimelineError("deferred_timeline.event_invalid") from exc
        if not event.event_id or not event.event_type or not math.isfinite(event.generated_at) or event.generated_at < 0:
            raise DeferredTimelineError("deferred_timeline.event_invalid")
        return event

    @staticmethod
    def _normalize_time(value: Any) -> float:
        if isinstance(value, bool):
            raise DeferredTimelineError("deferred_timeline.occurrence_time_invalid")
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DeferredTimelineError("deferred_timeline.occurrence_time_invalid") from exc
        if not math.isfinite(result) or result < 0:
            raise DeferredTimelineError("deferred_timeline.occurrence_time_invalid")
        return result

    @staticmethod
    def _normalize_rank(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 255:
            raise DeferredTimelineError("deferred_timeline.source_rank_invalid")
        return value

    @staticmethod
    def _normalize_mapping(value: Any, code: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise DeferredTimelineError(code)
        try:
            return copy.deepcopy(dict(value))
        except Exception as exc:
            raise DeferredTimelineError(code) from exc

    def enqueue_many(self, rows: Sequence[Mapping[str, Any]]) -> tuple[DeferredTimelineEntry, ...]:
        if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
            raise DeferredTimelineError("deferred_timeline.batch_invalid")
        if len(self._entries) + len(rows) > self.max_entries:
            raise DeferredTimelineError("deferred_timeline.capacity_exceeded")
        normalized: list[tuple[float, int, str, dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise DeferredTimelineError("deferred_timeline.entry_invalid")
            kind = str(row.get("kind", ""))
            if kind not in _ALLOWED_KINDS:
                raise DeferredTimelineError("deferred_timeline.kind_invalid")
            normalized.append((
                self._normalize_time(row.get("occurrence_time")),
                self._normalize_rank(row.get("source_rank")),
                kind,
                self._normalize_mapping(row.get("payload", {}), "deferred_timeline.payload_invalid"),
                self._normalize_mapping(row.get("metadata", {}), "deferred_timeline.metadata_invalid"),
            ))
        created: list[DeferredTimelineEntry] = []
        for occurrence_time, source_rank, kind, payload, metadata in normalized:
            entry = DeferredTimelineEntry(
                occurrence_time=occurrence_time,
                source_rank=source_rank,
                sequence=self._next_sequence,
                kind=kind,
                payload=payload,
                metadata=metadata,
            )
            self._next_sequence += 1
            heapq.heappush(self._entries, (*entry.key, entry))
            created.append(entry)
        return tuple(created)

    def peek(self) -> DeferredTimelineEntry | None:
        return self._entries[0][3] if self._entries else None

    def pop(self) -> DeferredTimelineEntry | None:
        return heapq.heappop(self._entries)[3] if self._entries else None

    def replace_head(self, *, payload: Mapping[str, Any] | None = None, metadata: Mapping[str, Any] | None = None) -> DeferredTimelineEntry:
        current = self.pop()
        if current is None:
            raise DeferredTimelineError("deferred_timeline.empty")
        replacement = DeferredTimelineEntry(
            occurrence_time=current.occurrence_time,
            source_rank=current.source_rank,
            sequence=current.sequence,
            kind=current.kind,
            payload=self._normalize_mapping(payload if payload is not None else current.payload, "deferred_timeline.payload_invalid"),
            metadata=self._normalize_mapping(metadata if metadata is not None else current.metadata, "deferred_timeline.metadata_invalid"),
        )
        heapq.heappush(self._entries, (*replacement.key, replacement))
        return replacement

    def snapshot(self) -> dict[str, Any]:
        ordered = [item[3].to_dict() for item in sorted(self._entries)]
        return {
            "schema_version": "1.0",
            "max_entries": self.max_entries,
            "next_sequence": self._next_sequence,
            "archives": {key: copy.deepcopy(self._archives[key]) for key in sorted(self._archives)},
            "entries": ordered,
        }

    def restore(self, value: Any) -> bool:
        """Restore atomically; malformed/oversized state is rejected and cleared."""
        self.last_restore_error = ""
        if value in (None, {}):
            self.clear()
            return True
        try:
            if not isinstance(value, Mapping):
                raise DeferredTimelineError("deferred_timeline.snapshot_invalid")
            raw_entries = value.get("entries", [])
            if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes, bytearray)):
                raise DeferredTimelineError("deferred_timeline.snapshot_invalid")
            if len(raw_entries) > self.max_entries:
                raise DeferredTimelineError("deferred_timeline.snapshot_oversized")
            raw_archives = value.get("archives", {})
            if not isinstance(raw_archives, Mapping) or len(raw_archives) > MAX_DEFERRED_TIMELINE_ARCHIVES:
                raise DeferredTimelineError("deferred_timeline.archives_invalid")
            archives: dict[str, dict[str, Any]] = {}
            for archive_id, archive_value in raw_archives.items():
                key = str(archive_id or "").strip()
                if not key or len(key) > 256:
                    raise DeferredTimelineError("deferred_timeline.archive_id_invalid")
                archives[key] = self._normalize_mapping(archive_value, "deferred_timeline.archive_invalid")
            restored: list[tuple[float, int, int, DeferredTimelineEntry]] = []
            seen: set[int] = set()
            highest = -1
            for raw in raw_entries:
                if not isinstance(raw, Mapping):
                    raise DeferredTimelineError("deferred_timeline.snapshot_invalid")
                sequence = raw.get("sequence")
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or sequence in seen:
                    raise DeferredTimelineError("deferred_timeline.sequence_invalid")
                seen.add(sequence)
                highest = max(highest, sequence)
                kind = str(raw.get("kind", ""))
                if kind not in _ALLOWED_KINDS:
                    raise DeferredTimelineError("deferred_timeline.kind_invalid")
                entry = DeferredTimelineEntry(
                    occurrence_time=self._normalize_time(raw.get("occurrence_time")),
                    source_rank=self._normalize_rank(raw.get("source_rank")),
                    sequence=sequence,
                    kind=kind,
                    payload=self._normalize_mapping(raw.get("payload", {}), "deferred_timeline.payload_invalid"),
                    metadata=self._normalize_mapping(raw.get("metadata", {}), "deferred_timeline.metadata_invalid"),
                )
                if kind in {"temporal", "random"}:
                    self.decode_event(entry.payload)
                restored.append((*entry.key, entry))
            next_sequence = value.get("next_sequence", highest + 1)
            if isinstance(next_sequence, bool) or not isinstance(next_sequence, int) or next_sequence < highest + 1:
                raise DeferredTimelineError("deferred_timeline.next_sequence_invalid")
        except DeferredTimelineError as exc:
            self.clear()
            self.last_restore_error = str(exc)
            return False
        self._entries = restored
        heapq.heapify(self._entries)
        self._next_sequence = next_sequence
        self._archives = archives
        self.prune_archives()
        return True
