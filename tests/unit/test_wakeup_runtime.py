from __future__ import annotations

import copy

import pytest

import multiagent_framework.wakeup_runtime as wakeup_runtime
from multiagent_framework.wakeup_runtime import (
    WakeupOutboxError,
    WakeupOutboxRuntime,
    event_delivery_id,
    stable_chain_id,
)


def test_event_delivery_and_chain_ids_are_stable() -> None:
    first = WakeupOutboxRuntime()
    second = WakeupOutboxRuntime()
    wakeup = {
        "target": "agent-a",
        "event_id": "event-42",
        "payload": {"event_id": "event-42", "message": "inspect"},
    }

    left = first.enqueue(wakeup)
    right = second.enqueue(copy.deepcopy(wakeup))

    expected_delivery_id = event_delivery_id("event-42", "agent-a")
    assert left["delivery_id"] == right["delivery_id"] == expected_delivery_id
    assert left["chain_id"] == right["chain_id"] == stable_chain_id(expected_delivery_id)


def test_duplicate_delivery_id_is_idempotent() -> None:
    outbox = WakeupOutboxRuntime()
    first = outbox.enqueue({"target": "agent-a", "payload": {"n": 1}}, delivery_id="fixed")
    duplicate = outbox.enqueue({"target": "agent-a", "payload": {"n": 2}}, delivery_id="fixed")

    assert duplicate is first
    assert outbox.total_count == 1
    assert first["payload"] == {"n": 1}


def test_snapshot_restore_requeues_inflight_entries() -> None:
    outbox = WakeupOutboxRuntime()
    entry = outbox.enqueue({"target": "agent-a", "payload": {"message": "hello"}})
    claimed = outbox.claim_pending()

    assert claimed == [entry]
    assert outbox.pending_count == 0
    assert outbox.inflight_count == 1

    restored = WakeupOutboxRuntime()
    assert restored.restore(outbox.snapshot())

    replay = restored.claim_pending()
    assert [item["delivery_id"] for item in replay] == [entry["delivery_id"]]
    assert replay[0]["attempts"] == 2


def test_ack_removes_entry_and_retry_returns_it_to_pending() -> None:
    outbox = WakeupOutboxRuntime()
    first = outbox.enqueue({"target": "a", "payload": {}}, delivery_id="a")
    second = outbox.enqueue({"target": "b", "payload": {}}, delivery_id="b")
    outbox.claim_pending()

    assert outbox.retry([first["delivery_id"]]) == 1
    assert [entry["delivery_id"] for entry in outbox] == ["a"]
    assert outbox.ack([second["delivery_id"]]) == 1
    assert outbox.total_count == 1


def test_restore_fails_closed_for_malformed_snapshot() -> None:
    outbox = WakeupOutboxRuntime()
    outbox.enqueue({"target": "safe", "payload": {}}, delivery_id="safe")

    assert not outbox.restore({"entries": [{"delivery_id": "bad", "sequence": 1, "payload": []}]})
    assert outbox.total_count == 0
    assert outbox.last_restore_error == "target_required"


def test_oversized_and_non_json_payloads_are_rejected() -> None:
    outbox = WakeupOutboxRuntime()

    with pytest.raises(WakeupOutboxError) as oversized:
        outbox.enqueue({"target": "a", "payload": {"blob": "x" * (256 * 1024)}})
    assert oversized.value.code == "payload_too_large"

    with pytest.raises(WakeupOutboxError) as unsafe:
        outbox.enqueue({"target": "a", "payload": {"bad": object()}})
    assert unsafe.value.code == "payload_not_json_safe"


def test_capacity_is_bounded() -> None:
    outbox = WakeupOutboxRuntime(capacity=1)
    outbox.enqueue({"target": "a", "payload": {}})

    with pytest.raises(WakeupOutboxError) as exc:
        outbox.enqueue({"target": "b", "payload": {}})
    assert exc.value.code == "capacity_exceeded"



def test_chain_wakeup_replay_deduplicates_but_distinct_payload_does_not():
    outbox = WakeupOutboxRuntime()
    base = {
        "target": "agent-a",
        "chain_id": "chain-source-1",
        "payload": {"message": "same", "intent_id": "intent-1"},
    }

    first = outbox.enqueue(base)
    replay = outbox.enqueue(copy.deepcopy(base))
    distinct = outbox.enqueue({
        **base,
        "payload": {"message": "different", "intent_id": "intent-2"},
    })

    assert replay is first
    assert distinct["delivery_id"] != first["delivery_id"]
    assert outbox.total_count == 2

def test_restore_rejects_snapshot_over_total_payload_budget(monkeypatch) -> None:
    source = WakeupOutboxRuntime()
    source.enqueue({"target": "a", "payload": {"message": "first"}}, delivery_id="one")
    source.enqueue({"target": "b", "payload": {"message": "second"}}, delivery_id="two")
    snapshot = source.snapshot()
    first_size = snapshot["entries"][0]["payload_bytes"]
    second_size = snapshot["entries"][1]["payload_bytes"]
    monkeypatch.setattr(
        wakeup_runtime, "MAX_WAKEUP_OUTBOX_BYTES", first_size + second_size - 1,
    )

    restored = WakeupOutboxRuntime()
    assert not restored.restore(snapshot)
    assert restored.total_count == 0
    assert restored.last_restore_error == "payload_capacity_exceeded"
