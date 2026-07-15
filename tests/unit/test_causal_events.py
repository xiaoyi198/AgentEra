from __future__ import annotations

from dataclasses import asdict, dataclass
import json

import pytest

from multiagent_framework.causal_events import (
    CausalCondition,
    CausalConditionContext,
    CausalEventEngine,
    CausalEventError,
    CausalEventRule,
    compare_condition_value,
    resolve_condition_value,
)
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.types import EventObject


@dataclass
class Inventory:
    item_name: str
    quantity: float
    anchor_rate: float

    @property
    def base_value(self) -> float:
        return self.quantity * self.anchor_rate


@dataclass
class Value:
    name: str
    kind: str
    value: float


class FakeWorld:
    def __init__(self) -> None:
        self.inventory = {"city": [Inventory("grain", 4, 25), Inventory("coin", 2, 1)]}
        self.values = {
            "city": [Value("morale", "metric", 40), Value("tax", "parameter", 0.1)]
        }

    def get_inventory(self, entity_id):
        return self.inventory.get(entity_id, [])

    def get_entity_values(self, entity_id, kind=None):
        values = self.values.get(entity_id, [])
        return [item for item in values if kind is None or item.kind == kind]


def rule(**overrides):
    value = {
        "rule_id": "drought_to_famine",
        "source_event_types": ["drought"],
        "child_event_type": "famine",
        "target_agents": [],
        "delay_days": 2,
        "probability": 1,
        "severity_delta": 2,
        "initial_payload": {"phase": "warning"},
        "inherit_payload_keys": ["region"],
        "conditions": [],
        "max_depth": 4,
        "max_fires_per_chain": 1,
        "priority": 0,
        "enabled": True,
    }
    value.update(overrides)
    return value


def source(event_id="evt-1", *, event_type="drought", payload=None, severity=9, targets=None):
    return EventObject(
        event_id=event_id,
        event_type=event_type,
        severity=severity,
        target_agents=list(targets or ["mayor"]),
        initial_payload=dict(payload or {"region": "north", "secret": "do-not-copy"}),
        generated_at=10,
    )


def poll_one(engine: CausalEventEngine, now: float):
    result = engine.poll(now, max_events=128)
    assert len(result.events) == 1
    return result.events[0]


def test_materializes_deterministic_child_with_delay_inheritance_and_severity_clamp():
    first = CausalEventEngine([rule()])
    second = CausalEventEngine([rule()])
    assert first.observe(source(), sim_time=100) == 1
    assert second.observe(source(), sim_time=100) == 1
    assert not first.poll(100 + 2 * GAME_SECONDS_PER_DAY - 0.01).events

    child = poll_one(first, 100 + 2 * GAME_SECONDS_PER_DAY)
    other = poll_one(second, 100 + 2 * GAME_SECONDS_PER_DAY)
    assert child.event_id == other.event_id
    assert child.event_type == "famine"
    assert child.severity == 10
    assert child.target_agents == ["mayor"]
    assert child.generated_at == 100 + 2 * GAME_SECONDS_PER_DAY
    assert child.initial_payload["phase"] == "warning"
    assert child.initial_payload["region"] == "north"
    assert "secret" not in child.initial_payload
    assert child.initial_payload["causal_parent_event_id"] == "evt-1"
    assert child.initial_payload["causal_rule_id"] == "drought_to_famine"
    assert child.initial_payload["causal_depth"] == 1


def test_explicit_targets_override_source_and_poll_is_chronological():
    engine = CausalEventEngine([
        rule(rule_id="late", delay_days=2, target_agents=["cabinet"]),
        rule(rule_id="early", delay_days=1, child_event_type="shortage"),
    ])
    assert engine.observe(source(), sim_time=0) == 2
    early = poll_one(engine, GAME_SECONDS_PER_DAY)
    assert early.event_type == "shortage"
    late = poll_one(engine, 2 * GAME_SECONDS_PER_DAY)
    assert late.target_agents == ["cabinet"]


def test_duplicate_source_is_idempotent_and_rule_fire_count_is_reserved_at_schedule_time():
    engine = CausalEventEngine([rule(delay_days=10)])
    event = source()
    assert engine.observe(event, sim_time=0) == 1
    assert engine.observe(event, sim_time=0) == 0
    snapshot = engine.snapshot()
    chain_id = next(iter(snapshot["chain_counts"]))
    assert snapshot["chain_counts"][chain_id]["drought_to_famine"] == 1


def test_depth_and_per_chain_limits_bound_zero_delay_cycles():
    engine = CausalEventEngine([
        rule(
            rule_id="cycle",
            source_event_types=["*"],
            child_event_type="drought",
            delay_days=0,
            max_depth=3,
            max_fires_per_chain=3,
        )
    ])
    event = source()
    assert engine.observe(event, sim_time=0) == 1
    emitted = []
    while True:
        batch = engine.poll(0, max_events=128).events
        if not batch:
            break
        emitted.extend(batch)
        for child in batch:
            engine.observe(child, sim_time=0)
    assert len(emitted) == 3
    assert emitted[-1].initial_payload["causal_depth"] == 3


def test_conditions_use_anchored_base_value_and_typed_world_values():
    world = FakeWorld()
    passing = rule(conditions=[
        {"kind": "anchored_inventory", "entity_id": "city", "name": "grain", "operator": "eq", "value": 100},
        {"kind": "metric", "entity_id": "city", "name": "morale", "operator": "lt", "value": 50},
        {"kind": "parameter", "entity_id": "city", "name": "tax", "operator": "lte", "value": 0.1},
    ])
    engine = CausalEventEngine([passing])
    assert engine.observe(source(), sim_time=0, world_state=world) == 1

    raw_quantity_would_pass = rule(
        rule_id="raw-quantity-is-wrong",
        conditions=[{"kind": "anchored_inventory", "entity_id": "city", "name": "grain", "operator": "eq", "value": 4}],
    )
    engine = CausalEventEngine([raw_quantity_would_pass])
    assert engine.observe(source("evt-2"), sim_time=0, world_state=world) == 0


def test_missing_fact_or_world_state_does_not_trigger():
    missing = rule(conditions=[
        {"kind": "metric", "entity_id": "city", "name": "missing", "operator": "gt", "value": 0}
    ])
    assert CausalEventEngine([missing]).observe(source(), sim_time=0, world_state=FakeWorld()) == 0
    assert CausalEventEngine([missing]).observe(source("evt-2"), sim_time=0) == 0


def test_snapshot_restore_prevents_duplicates_and_preserves_committed_child_across_rule_reload():
    engine = CausalEventEngine([rule(delay_days=3, child_event_type="committed")])
    event = source()
    assert engine.observe(event, sim_time=5) == 1
    snapshot = engine.snapshot()

    restored = CausalEventEngine([rule(delay_days=0, child_event_type="changed")])
    restored.restore(snapshot, sim_time=5)
    assert restored.observe(event, sim_time=5) == 0
    child = poll_one(restored, 5 + 3 * GAME_SECONDS_PER_DAY)
    assert child.event_type == "committed"


def test_empty_legacy_snapshot_is_accepted():
    engine = CausalEventEngine([rule()])
    engine.restore({}, sim_time=0)
    assert engine.public_status()["pending_count"] == 0


@pytest.mark.parametrize("patch", [
    {"rule_id": "bad/id"},
    {"source_event_types": []},
    {"target_agents": "mayor"},
    {"probability": 1.1},
    {"delay_days": -1},
    {"max_depth": 0},
    {"max_fires_per_chain": 65},
    {"initial_payload": {"causal_depth": 99}},
    {"inherit_payload_keys": ["causal_chain_id"]},
    {"conditions": [{"kind": "unknown", "entity_id": "city", "name": "x", "operator": "eq", "value": 1}]},
])
def test_invalid_rule_schema_is_rejected(patch):
    with pytest.raises(CausalEventError):
        CausalEventRule.from_mapping(rule(**patch))


@pytest.mark.parametrize("snapshot", [
    {"observed_event_ids": "bad"},
    {"pending": [{"event": {"event_id": "x", "event_type": "x", "severity": 0, "target_agents": [], "initial_payload": {}}}]},
    {"pending": [{"event": {"event_id": "x", "event_type": "x", "severity": 5, "target_agents": "bad", "initial_payload": {}}}]},
    {"chain_counts": {"chain": {"rule": -1}}, "chain_order": ["chain"]},
    {"chain_counts": {"chain": {"rule": 65}}, "chain_order": ["chain"]},
    {"chain_counts": {}, "chain_order": ["missing"]},
    {"dropped_count": -1},
])
def test_corrupt_snapshot_is_rejected(snapshot):
    with pytest.raises(CausalEventError, match="causal_snapshot_invalid"):
        CausalEventEngine().restore(snapshot, sim_time=0)


def test_oversized_snapshot_is_rejected_before_materialization():
    snapshot = {"observed_event_ids": [f"event-{index}" for index in range(10_001)]}
    with pytest.raises(CausalEventError, match="causal_snapshot_invalid"):
        CausalEventEngine().restore(snapshot, sim_time=0)


def test_public_status_only_contains_safe_aggregates():
    engine = CausalEventEngine([rule(conditions=[
        {"kind": "metric", "entity_id": "secret-city", "name": "secret-metric", "operator": "gt", "value": 1}
    ], target_agents=["secret-agent"], initial_payload={"secret": "payload"})])
    status = engine.public_status()
    assert set(status) == {
        "rule_count", "enabled_count", "pending_count", "observed_count",
        "dropped_count", "next_due", "fingerprint",
        "pending_world_command_count", "effect_rule_count",
    }
    rendered = str(status)
    assert "secret" not in rendered



def test_malformed_causal_metadata_fails_closed_without_crashing():
    engine = CausalEventEngine([rule(source_event_types=["*"])])
    malformed = source("forged", payload={"causal_depth": "not-an-integer"})
    assert engine.observe(malformed, sim_time=0) == 0
    assert engine.public_status()["pending_count"] == 0



@pytest.mark.parametrize("event", [
    EventObject(event_id="", event_type="drought", severity=5, target_agents=[], initial_payload={}),
    EventObject(event_id="x", event_type="drought", severity=0, target_agents=[], initial_payload={}),
    EventObject(event_id="x", event_type="drought", severity=5, target_agents="bad", initial_payload={}),
])
def test_invalid_source_event_fails_closed_without_partial_schedule(event):
    engine = CausalEventEngine([rule()])
    assert engine.observe(event, sim_time=0) == 0
    assert engine.public_status()["pending_count"] == 0


class GenericWorld(FakeWorld):
    def __init__(self) -> None:
        super().__init__()
        self.entities = {
            "city": {
                "id": "city", "type": "settlement", "name": "Harbor",
                "current_status": "alert", "metadata": {"tags": ["coastal", "trade"], "tier": "capital"},
            },
            "fort": {
                "id": "fort", "type": "settlement", "name": "North Fort",
                "current_status": "ready", "metadata": {"tags": ["military"]},
            },
        }
        self.inventory["fort"] = [Inventory("grain", 2, 25)]
        self.values["fort"] = [Value("morale", "metric", 60)]
        self.relations = [{
            "parent_id": "city", "child_id": "fort", "rel_type": "allied",
            "strength": 72.0, "visibility": "public", "active": True,
            "values": {"trust": 0.8}, "metadata": {},
        }]
        self.ongoing = [{
            "event_id": "ongoing-1", "entity_id": "city", "event_type": "construction",
            "start_time": 0.0, "end_time": 3 * GAME_SECONDS_PER_DAY,
        }]

        class Definition:
            direction = "undirected"

        class Catalog:
            @staticmethod
            def get(rel_type):
                return Definition() if rel_type == "allied" else None

        self.relationship_catalog = Catalog()

    def get_entity(self, entity_id):
        return self.entities.get(entity_id)

    def get_all_entities(self):
        return list(self.entities.values())

    def get_all_relations(self, *, sim_time=0.0):
        return list(self.relations)

    def list_ongoing_events(self, entity_id=None):
        return [item for item in self.ongoing if entity_id is None or item["entity_id"] == entity_id]


class GenericActors:
    actors = {
        "mayor": {
            "actor_id": "mayor", "actor_type": "person",
            "traits": [{"trait_id": "careful"}],
            "memberships": [{"organization_id": "council", "role": "leader", "status": "active", "rank": 2}],
            "runtime": {"attributes": {"influence": 75.0}, "states": {"mood": "focused"}},
        },
        "captain": {
            "actor_id": "captain", "actor_type": "person", "traits": [], "memberships": [],
            "runtime": {"attributes": {"influence": 25.0}, "states": {"mood": "alert"}},
        },
    }

    def get(self, actor_id):
        return self.actors.get(actor_id)

    def list(self):
        return list(self.actors.values())


class GenericObjectives:
    def public_state(self, **_kwargs):
        return {"objectives": [{"objective_id": "secure_frontier", "status": "active", "progress": 0.75}]}


def test_generic_condition_context_resolves_world_actor_relationship_time_objective_and_aggregates():
    world = GenericWorld()
    context = CausalConditionContext(
        world_state=world,
        actor_manager=GenericActors(),
        objective_manager=GenericObjectives(),
        sim_time=2 * GAME_SECONDS_PER_DAY,
    )
    conditions = [
        {"kind": "entity_field", "entity_id": "city", "field": "metadata.tier", "operator": "eq", "value": "capital"},
        {"kind": "entity_tag", "entity_id": "city", "name": "coastal", "operator": "eq", "value": True},
        {"kind": "entity_state", "entity_id": "city", "operator": "in", "value": ["alert", "ready"]},
        {"kind": "actor_attribute", "entity_id": "mayor", "name": "influence", "operator": "gte", "value": 70},
        {"kind": "actor_state", "entity_id": "mayor", "name": "mood", "operator": "eq", "value": "focused"},
        {"kind": "actor_trait", "entity_id": "mayor", "name": "careful", "operator": "eq", "value": True},
        {"kind": "actor_membership", "entity_id": "mayor", "name": "council", "field": "role", "operator": "eq", "value": "leader"},
        {"kind": "relationship", "entity_id": "fort", "target_entity_id": "city", "name": "allied", "field": "strength", "operator": "gte", "value": 70},
        {"kind": "relationship", "entity_id": "city", "target_entity_id": "fort", "name": "allied", "field": "values.trust", "operator": "gt", "value": 0.5},
        {"kind": "ongoing_event", "entity_id": "city", "name": "construction", "operator": "gte", "value": 1},
        {"kind": "time", "name": "sim_day", "operator": "eq", "value": 2},
        {"kind": "objective_progress", "entity_id": "secure_frontier", "field": "progress", "operator": "gte", "value": 0.7},
        {"kind": "aggregate_metric", "field": "metric", "name": "morale", "aggregate": "avg", "operator": "eq", "value": 50},
        {"kind": "aggregate_metric", "field": "anchored_inventory", "name": "grain", "aggregate": "sum", "operator": "eq", "value": 150},
        {"kind": "aggregate_metric", "field": "entity_count", "aggregate": "count", "filters": {"entity_type": "settlement"}, "operator": "eq", "value": 2},
        {"kind": "aggregate_metric", "field": "actor_attribute", "name": "influence", "aggregate": "avg", "operator": "eq", "value": 50},
    ]
    parsed = [CausalCondition.from_mapping(item) for item in conditions]
    assert all(compare_condition_value(resolve_condition_value(item, context), item.operator, item.value) for item in parsed)

    engine = CausalEventEngine([rule(conditions=conditions)])
    assert engine.observe(source(), sim_time=context.sim_time, condition_context=context) == 1


def test_event_history_counts_current_event_and_survives_restart_without_payload_storage():
    current_rule = rule(conditions=[{
        "kind": "event_history", "name": "drought", "field": "count",
        "operator": "gte", "value": 1, "window_days": 7,
    }])
    engine = CausalEventEngine([current_rule])
    event = source(payload={"secret": "must-not-enter-history"})
    assert engine.observe(event, sim_time=GAME_SECONDS_PER_DAY) == 1
    snapshot = engine.snapshot()
    assert snapshot["schema_version"] == "2.0"
    assert snapshot["event_history"] == [{
        "event_id": "evt-1", "event_type": "drought",
        "generated_at": 10.0, "target_agents": ["mayor"],
    }]
    assert "secret" not in str(snapshot["event_history"])

    restored = CausalEventEngine([rule(source_event_types=["rain"], conditions=[{
        "kind": "event_history", "name": "drought", "operator": "gte", "value": 1,
        "window_days": 7,
    }])])
    restored.restore(snapshot, sim_time=2 * GAME_SECONDS_PER_DAY)
    assert restored.observe(source("evt-rain", event_type="rain"), sim_time=2 * GAME_SECONDS_PER_DAY) == 1


def test_player_knowledge_fails_closed_without_provider_and_uses_only_provider_value():
    condition = {
        "kind": "player_knowledge", "entity_id": "city", "name": "hidden_route",
        "operator": "eq", "value": "known",
    }
    without_provider = CausalEventEngine([rule(conditions=[condition])])
    assert without_provider.observe(source(), sim_time=0, world_state=GenericWorld()) == 0

    class Knowledge:
        @staticmethod
        def resolve_condition(_condition, _context):
            return "known"

    context = CausalConditionContext(world_state=GenericWorld(), knowledge_provider=Knowledge())
    with_provider = CausalEventEngine([rule(conditions=[condition])])
    assert with_provider.observe(source("evt-known"), sim_time=0, condition_context=context) == 1


def test_generic_comparison_operators_and_missing_semantics_are_stable():
    assert compare_condition_value(["a", "b"], "contains", "a") is True
    assert compare_condition_value("a", "in", ("a", "b")) is True
    assert compare_condition_value("a", "not_in", ("b", "c")) is True
    assert compare_condition_value(True, "ne", False) is True
    missing = CausalCondition.from_mapping({
        "kind": "entity_field", "entity_id": "missing", "field": "name", "operator": "not_exists",
    })
    context = CausalConditionContext(world_state=GenericWorld())
    assert compare_condition_value(resolve_condition_value(missing, context), missing.operator, missing.value) is True



def test_condition_sequence_values_have_stable_json_round_trip_and_rule_fingerprint():
    raw = {
        "rule_id": "sequence_condition",
        "source_event_types": ["seed"],
        "child_event_type": "follow_up",
        "conditions": [{
            "kind": "entity_state",
            "entity_id": "city",
            "operator": "in",
            "value": ["alert", "ready"],
            "filters": {"allowed_states": ["alert", "ready"]},
        }],
    }

    first = CausalEventRule.from_mapping(raw)
    encoded = json.dumps(asdict(first), ensure_ascii=False, sort_keys=True)
    second = CausalEventRule.from_mapping(json.loads(encoded))

    assert first.conditions[0].value == ("alert", "ready")
    assert second.conditions[0].filters["allowed_states"] == ("alert", "ready")
    assert first.fingerprint() == second.fingerprint()
