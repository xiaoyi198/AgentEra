from __future__ import annotations

import copy

from multiagent_framework.timeline_runtime import DeferredTimelineRuntime
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
