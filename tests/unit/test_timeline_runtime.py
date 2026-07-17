from __future__ import annotations

import copy

from multiagent_framework.timeline_runtime import (
    DeferredTimelineError,
    DeferredTimelineRuntime,
)
from multiagent_framework.types import EventObject


def _row(kind: str, when: float, rank: int, event_id: str):
    runtime = DeferredTimelineRuntime()
    event = EventObject(
        event_id=event_id, event_type="signal", severity=2,
        target_agents=["agent-a"], initial_payload={"value": event_id},
        generated_at=when,
    )
    return {
        "kind": kind,
        "occurrence_time": when,
        "source_rank": rank,
        "payload": runtime.event_payload(event),
        "metadata": {"source": kind},
    }


def test_deferred_timeline_orders_stably_and_round_trips_events():
    runtime = DeferredTimelineRuntime(max_entries=8)
    runtime.enqueue_many((
        _row("random", 20, 2, "late"),
        _row("temporal", 10, 0, "first"),
        _row("random", 10, 2, "second"),
    ))

    snapshot = copy.deepcopy(runtime.snapshot())
    restored = DeferredTimelineRuntime(max_entries=8)
    assert restored.restore(snapshot) is True
    assert restored.snapshot() == snapshot

    entries = [restored.pop(), restored.pop(), restored.pop()]
    assert [(item.occurrence_time, item.source_rank, item.sequence) for item in entries] == [
        (10, 0, 1), (10, 2, 2), (20, 2, 0),
    ]
    assert [restored.decode_event(item.payload).event_id for item in entries] == [
        "first", "second", "late",
    ]


def test_deferred_timeline_replace_head_preserves_stable_key():
    runtime = DeferredTimelineRuntime(max_entries=4)
    runtime.enqueue_many(({
        "kind": "lifecycle_marker", "occurrence_time": 5, "source_rank": 1,
        "payload": {"trigger": "day_start", "sim_time": 5},
        "metadata": {"start_transition_index": 0},
    },))
    before = runtime.peek()

    after = runtime.replace_head(metadata={"start_transition_index": 256})

    assert after.key == before.key
    assert runtime.peek().metadata["start_transition_index"] == 256


def test_deferred_timeline_malformed_or_oversized_snapshot_fails_closed():
    runtime = DeferredTimelineRuntime(max_entries=1)
    runtime.enqueue_many((_row("temporal", 1, 0, "ok"),))

    assert runtime.restore({"entries": [{"sequence": 0, "kind": "unknown"}]}) is False
    assert runtime.pending_count == 0
    assert runtime.last_restore_error == "deferred_timeline.kind_invalid"

    oversized = {"entries": [{}, {}]}
    assert runtime.restore(oversized) is False
    assert runtime.pending_count == 0
    assert runtime.last_restore_error == "deferred_timeline.snapshot_oversized"


def test_deferred_timeline_public_status_tracks_backpressure_and_restore_failures():
    runtime = DeferredTimelineRuntime(max_entries=1)
    runtime.enqueue_many((_row("temporal", 10, 0, "private-event"),))

    try:
        runtime.enqueue_many((_row("random", 20, 2, "blocked-secret"),))
    except DeferredTimelineError as exc:
        assert str(exc) == "deferred_timeline.capacity_exceeded"
    else:
        raise AssertionError("capacity overflow must fail closed")

    status = runtime.public_status(sim_time=25)
    assert status == {
        "pending_count": 1,
        "capacity": 1,
        "utilization": 1.0,
        "oldest_occurrence_time": 10.0,
        "oldest_age_seconds": 15.0,
        "archive_count": 0,
        "archive_capacity": 64,
        "backpressure_count": 1,
        "restore_failure_count": 0,
        "last_restore_error": "",
        "last_snapshot": {
            "entry_count": 0, "build_ms": 0.0,
            "serialization_ms": 0.0, "serialized_bytes": 0,
        },
    }
    assert "private-event" not in repr(status)

    assert runtime.restore({"entries": [{"sequence": 0, "kind": "unknown"}]}) is False
    failed = runtime.public_status(sim_time=25)
    assert failed["pending_count"] == 0
    assert failed["backpressure_count"] == 1
    assert failed["restore_failure_count"] == 1
    assert failed["last_restore_error"] == "deferred_timeline.kind_invalid"

    restored = DeferredTimelineRuntime(max_entries=1)
    assert restored.restore(runtime.snapshot()) is True
    restored_status = restored.public_status(sim_time=25)
    assert restored_status["backpressure_count"] == 1
    assert restored_status["restore_failure_count"] == 1


def test_deferred_timeline_20k_snapshot_measurement_and_restore_stress():
    entry_count = 20_000
    runtime = DeferredTimelineRuntime(max_entries=entry_count)
    rows = tuple(
        {
            "kind": "lifecycle_marker",
            "occurrence_time": float(index % 5000),
            "source_rank": 1,
            "payload": {"trigger": "stress", "sim_time": float(index % 5000)},
            "metadata": {"start_transition_index": index % 256},
        }
        for index in range(entry_count)
    )
    runtime.enqueue_many(rows)

    measurement = runtime.measure_snapshot()
    assert measurement["entry_count"] == entry_count
    assert measurement["archive_count"] == 0
    assert 2_000_000 < measurement["serialized_bytes"] < 32_000_000
    assert measurement["build_ms"] >= 0
    assert measurement["serialization_ms"] >= 0

    status = runtime.public_status(sim_time=6000)
    assert status["pending_count"] == entry_count
    assert status["oldest_occurrence_time"] == 0.0
    assert status["oldest_age_seconds"] == 6000.0
    assert status["last_snapshot"] == {
        "entry_count": entry_count,
        "build_ms": measurement["build_ms"],
        "serialization_ms": measurement["serialization_ms"],
        "serialized_bytes": measurement["serialized_bytes"],
    }

    snapshot = runtime.snapshot()
    restored = DeferredTimelineRuntime(max_entries=entry_count)
    assert restored.restore(snapshot) is True
    assert restored.pending_count == entry_count
    first = restored.pop()
    last = None
    while restored.pending_count:
        last = restored.pop()
    assert first.key == (0.0, 1, 0)
    assert last is not None
    assert last.occurrence_time == 4999.0
