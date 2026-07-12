from __future__ import annotations

from multiagent_framework.memory_store import StructuredMemoryStore


def test_retrieval_combines_query_entities_importance_recency_and_commitment_boost():
    store = StructuredMemoryStore(max_records=50, recency_half_life_s=1000)
    store.remember(
        kind="episodic",
        content="Flood damaged the northern granary",
        entities=("north",),
        importance=0.7,
        created_at=900,
    )
    store.remember(
        kind="semantic",
        content="The southern port exports salt",
        entities=("south",),
        importance=0.9,
        created_at=990,
    )
    commitment = store.add_commitment(
        "Send grain to the northern granary",
        intent_id="intent-1",
        entities=("north",),
    )
    commitment.created_at = 800

    results = store.retrieve("grain granary", entities=("north",), now=1000, limit=3)

    assert results[0].record.memory_id == commitment.memory_id
    assert {item.record.kind for item in results} == {"commitment", "episodic", "semantic"}
    assert all(item.record.last_accessed_at == 1000 for item in results)


def test_commitment_lifecycle_is_idempotent_and_records_resolution():
    store = StructuredMemoryStore()
    first = store.add_commitment("Report the flood", intent_id="intent-1")
    duplicate = store.add_commitment("Report the flood", intent_id="intent-1")

    assert first.memory_id == duplicate.memory_id
    resolved = store.resolve_commitment(
        "intent-1", success=False, resolution="dispatch rejected", resolved_at=123
    )

    assert resolved is first
    assert resolved.status == "failed"
    assert resolved.metadata["resolution"] == "dispatch rejected"
    assert resolved.metadata["resolved_at"] == 123
    assert store.resolve_commitment("intent-1", success=True) is None


def test_ingest_payload_builds_all_memory_kinds_and_closes_action_result():
    store = StructuredMemoryStore()
    store.add_commitment("Open the granary", intent_id="intent-2", entities=("city",))
    store.ingest_payload({
        "description": "A flood reached the city",
        "message": "Please send aid",
        "from": "governor",
        "entity_id": "city",
        "chain_id": "chain-1",
        "memory_facts": ["The city has one granary"],
    }, agent_id="mayor")
    store.ingest_payload({
        "type": "action_result",
        "intent_id": "intent-2",
        "action_type": "relief",
        "target_location": "city",
        "success": True,
        "message": "Granary opened",
    }, agent_id="mayor")

    kinds = {record.kind for record in store.records}
    assert kinds == {"episodic", "semantic", "relationship", "commitment"}
    commitment = next(record for record in store.records if record.kind == "commitment")
    assert commitment.status == "resolved"
    assert commitment.metadata["success"] is True


def test_serialization_round_trip_and_pruning_preserve_unresolved_commitments():
    store = StructuredMemoryStore(max_records=10)
    commitment = store.add_commitment("Must remain", intent_id="keep")
    for index in range(15):
        store.remember(
            kind="episodic",
            content=f"minor event {index}",
            importance=0.1,
            created_at=100 + index,
        )

    payload = {"records": store.serialize()}
    restored = StructuredMemoryStore.deserialize(payload, max_records=10)

    assert len(restored) == 10
    assert any(record.memory_id == commitment.memory_id for record in restored.records)
    assert any(
        record.kind == "commitment" and record.status == "unresolved"
        for record in restored.records
    )


def test_context_budget_and_security_label_are_deterministic():
    store = StructuredMemoryStore()
    store.remember(kind="semantic", content="Taxes are capped at ten percent", importance=0.8)

    first = store.format_context("tax", now=1000, max_chars=200)
    second = store.format_context("tax", now=1000, max_chars=200)

    assert first == second
    assert first.startswith("## Relevant long-term memory")
    assert "Taxes are capped" in first
