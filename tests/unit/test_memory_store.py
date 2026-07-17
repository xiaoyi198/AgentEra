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
        content="The northern granary keeps emergency grain reserves",
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


def test_topic_focus_excludes_unrelated_noise_and_bounds_commitment_fallback():
    store = StructuredMemoryStore(max_records=50)
    store.remember(
        kind="semantic",
        content="The harbor pressure seal must be inspected before dawn",
        entities=("harbor",),
        importance=0.55,
    )
    relevant_commitment = store.add_commitment(
        "Inspect the harbor pressure seal",
        intent_id="harbor-check",
        entities=("harbor",),
    )
    store.remember(
        kind="semantic",
        content="The royal wedding banquet has one hundred guests",
        entities=("capital",),
        importance=1.0,
    )
    unrelated_commitments = [
        store.add_commitment("Pay the desert caravan", intent_id="desert"),
        store.add_commitment("Repair the mountain tower", intent_id="mountain"),
    ]

    results = store.retrieve(
        "harbor pressure seal",
        entities=("harbor",),
        limit=4,
        unrelated_commitment_limit=1,
    )
    ids = {item.record.memory_id for item in results}

    assert relevant_commitment.memory_id in ids
    assert not any("royal wedding" in item.record.content for item in results)
    assert sum(item.memory_id in ids for item in unrelated_commitments) == 1


def test_blank_query_omits_closed_history_but_related_query_can_recall_it():
    store = StructuredMemoryStore()
    closed = store.add_commitment("Close the lunar breach", intent_id="lunar")
    store.resolve_commitment("lunar", success=True, resolution="Breach sealed")
    active = store.remember(kind="semantic", content="Wheat harvest starts tomorrow")

    blank_results = store.retrieve("", limit=8)
    related_results = store.retrieve("lunar breach", limit=8)

    assert [item.record.memory_id for item in blank_results] == [active.memory_id]
    assert closed.memory_id in {item.record.memory_id for item in related_results}
    context = store.format_context("lunar breach")
    assert "resolution: Breach sealed" in context


def test_retrieval_suppresses_same_intent_and_near_duplicate_content():
    store = StructuredMemoryStore()
    commitment = store.add_commitment(
        "Inspect the northern harbor seal", intent_id="inspect-harbor", entities=("harbor",)
    )
    store.remember(
        kind="episodic",
        content="Inspection of the northern harbor seal is queued",
        entities=("harbor",),
        importance=0.8,
        metadata={"intent_id": "inspect-harbor"},
    )
    store.remember(
        kind="semantic",
        content="Northern harbor pressure seal requires inspection",
        entities=("harbor",),
        importance=0.7,
    )
    store.remember(
        kind="semantic",
        content="Northern harbor pressure seal requires immediate inspection",
        entities=("harbor",),
        importance=0.69,
    )

    results = store.retrieve(
        "northern harbor seal inspection",
        entities=("harbor",),
        limit=8,
        duplicate_overlap_threshold=0.75,
        unrelated_commitment_limit=0,
    )

    assert sum(
        item.record.metadata.get("intent_id") == "inspect-harbor" for item in results
    ) == 1
    assert commitment.memory_id in {item.record.memory_id for item in results}
    assert sum(item.record.kind == "semantic" for item in results) == 1


def test_kind_diversity_bonus_preserves_relevant_memory_types():
    store = StructuredMemoryStore(recency_half_life_s=1000)
    store.remember(
        kind="episodic", content="Harbor pressure alarm sounded",
        entities=("harbor",), importance=0.9, created_at=100, memory_id="event-a",
    )
    store.remember(
        kind="episodic", content="Harbor pressure alarm sounded again",
        entities=("harbor",), importance=0.86, created_at=100, memory_id="event-b",
    )
    store.remember(
        kind="semantic", content="Harbor pressure limit is eight",
        entities=("harbor",), importance=0.7, created_at=100, memory_id="fact-a",
    )

    results = store.retrieve(
        "harbor pressure", entities=("harbor",), now=100, limit=2,
        kind_diversity_bonus=0.6, duplicate_overlap_threshold=1.0,
        unrelated_commitment_limit=0,
    )

    assert {item.record.kind for item in results} == {"episodic", "semantic"}


def test_chinese_topic_retrieval_and_restore_are_consistent():
    store = StructuredMemoryStore()
    relevant = store.remember(
        kind="semantic", content="北境粮仓仍有可用于赈灾的储粮",
        entities=("北境",), importance=0.6, memory_id="north-fact",
    )
    store.remember(
        kind="semantic", content="南海盐务宴会需要重新排席",
        entities=("南海",), importance=1.0, memory_id="south-noise",
    )
    restored = StructuredMemoryStore.deserialize({"records": store.serialize()})

    before = store.retrieve("北境粮仓赈灾", entities=("北境",), limit=4)
    after = restored.retrieve("北境粮仓赈灾", entities=("北境",), limit=4)

    assert [item.record.memory_id for item in before] == [relevant.memory_id]
    assert [item.record.memory_id for item in after] == [relevant.memory_id]
