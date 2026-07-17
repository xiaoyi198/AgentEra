from __future__ import annotations

from dataclasses import dataclass, field
import pytest

from multiagent_framework.derived_metrics import (
    DerivedMetricCatalog,
    DerivedMetricContractError,
    DerivedMetricEvaluationContext,
    normalize_derived_metric_document,
    validate_derived_metric_definitions,
)


@dataclass
class Entity:
    id: str
    type: str = "station"
    current_status: str = "active"
    metadata: dict = field(default_factory=dict)


@dataclass
class Item:
    item_name: str
    base_value: float


@dataclass
class Value:
    name: str
    value: float


class World:
    def __init__(self):
        self.entities = [
            Entity("north", "outpost", metadata={"tags": ["frontier"]}),
            Entity("south", "outpost", metadata={"tags": ["frontier"]}),
            Entity("capital", "city"),
        ]
        self.inventory = {
            "north": [Item("supply", 20)],
            "south": [Item("supply", 40)],
        }
        self.values = {
            ("north", "metric"): [Value("security", 60)],
            ("south", "metric"): [Value("security", 80)],
            ("north", "parameter"): [Value("multiplier", 1.5)],
        }
        self.relations = [
            {"parent_id": "north", "child_id": "capital", "rel_type": "route",
             "strength": 50, "active": True, "values": {"trust": 30}},
            {"parent_id": "south", "child_id": "capital", "rel_type": "route",
             "strength": 70, "active": True, "values": {"trust": 50}},
        ]
        self.events = [{"entity_id": "north", "event_type": "repair"}]
        self.metric_reads = 0

    def get_all_entities(self):
        return list(self.entities)

    def get_inventory(self, entity_id):
        return list(self.inventory.get(entity_id, []))

    def get_entity_values(self, entity_id, kind=None):
        self.metric_reads += 1
        return list(self.values.get((entity_id, kind), []))

    def get_all_relations(self, sim_time=0):
        return list(self.relations)

    def list_ongoing_events(self, entity_id=None):
        return [item for item in self.events if not entity_id or item["entity_id"] == entity_id]


class Actors:
    def __init__(self):
        self.actors = {
            "guard_a": {"actor_id": "guard_a", "actor_type": "guard", "runtime": {"attributes": {"readiness": 40}}},
            "guard_b": {"actor_id": "guard_b", "actor_type": "guard", "runtime": {"attributes": {"readiness": 80}}},
            "merchant": {"actor_id": "merchant", "actor_type": "civilian", "runtime": {"attributes": {"readiness": 10}}},
        }

    def get(self, actor_id):
        return self.actors.get(actor_id)

    def list(self):
        return list(self.actors.values())


def context():
    return DerivedMetricEvaluationContext(World(), Actors(), 2.5 * 86400)


def definition(metric_id, formula, **overrides):
    value = {
        "metric_id": metric_id,
        "display_name": metric_id.replace("_", " ").title(),
        "description": "safe description",
        "unit": "%",
        "precision": 2,
        "audiences": ["player", "ui", "agent", "rule", "objective"],
        "missing_policy": "fail",
        "formula": formula,
    }
    value.update(overrides)
    return value


def test_direct_sources_and_final_clamp_precision():
    catalog = DerivedMetricCatalog([
        definition("readiness", {
            "op": "add",
            "values": [
                {"source": "metric", "entity_id": "north", "name": "security"},
                {"source": "parameter", "entity_id": "north", "name": "multiplier"},
                {"source": "anchored_inventory", "entity_id": "north", "name": "supply"},
                {"source": "actor_attribute", "entity_id": "guard_a", "name": "readiness"},
                {"source": "sim_day"},
            ],
        }, minimum=0, maximum=100, precision=1),
    ], generation=7)
    result = catalog.evaluate("readiness", context(), audience="rule")
    assert result["status"] == "ok"
    assert result["value"] == 100.0
    assert result["generation"] == 7
    assert result["evaluated_at"] == 2.5 * 86400


def test_aggregate_sources_filters_and_count_zero():
    catalog = DerivedMetricCatalog([
        definition("aggregate", {
            "op": "sum",
            "values": [
                {"source": "metric", "aggregate": "avg", "name": "security", "filters": {"entity_type": "outpost"}},
                {"source": "anchored_inventory", "aggregate": "sum", "name": "supply", "filters": {"tag": "frontier"}},
                {"source": "actor_attribute", "aggregate": "avg", "name": "readiness", "filters": {"actor_type": "guard"}},
                {"source": "relation", "aggregate": "avg", "name": "route", "filters": {"value_field": "values.trust", "active": True}},
                {"source": "ongoing_event", "aggregate": "count", "name": "missing"},
            ],
        }),
    ])
    # 70 security + 60 supply + 60 readiness + 40 trust + 0 events
    assert catalog.value("aggregate", context()) == 230.0

    relation_catalog = DerivedMetricCatalog([
        definition("north_route_trust", {
            "source": "relation", "aggregate": "avg", "name": "route",
            "entity_id": "north", "target_entity_id": "capital",
            "filters": {"value_field": "values.trust", "active": True},
        }),
    ])
    assert relation_catalog.value("north_route_trust", context()) == 30.0


def test_nested_arithmetic_weighted_sum_round_clamp_and_coalesce():
    catalog = DerivedMetricCatalog([
        definition("math", {
            "op": "round", "precision": 2, "value": {
                "op": "clamp", "min": 0, "max": 100, "value": {
                    "op": "weighted_sum", "terms": [
                        {"weight": 0.5, "value": {"constant": 120}},
                        {"weight": 0.5, "value": {
                            "op": "coalesce", "values": [
                                {"source": "metric", "entity_id": "capital", "name": "missing"},
                                {"op": "divide", "left": {"constant": 90}, "right": {"constant": 3}},
                            ],
                        }},
                    ],
                },
            },
        }),
    ])
    assert catalog.value("math", context()) == 75.0


def test_dependency_graph_topology_and_batch_cache():
    ctx = context()
    catalog = DerivedMetricCatalog([
        definition("raw", {"source": "metric", "entity_id": "north", "name": "security"}),
        definition("double", {"op": "add", "values": [
            {"derived_metric": "raw"}, {"derived_metric": "raw"},
        ]}),
    ])
    values = {item["metric_id"]: item["value"] for item in catalog.evaluate_all(ctx)}
    assert values == {"raw": 60.0, "double": 120.0}
    assert ctx.world_state.metric_reads == 1
    assert catalog.get("double").dependencies == ("raw",)


def test_cycle_and_missing_dependency_are_rejected_before_publish():
    with pytest.raises(DerivedMetricContractError) as cycle:
        DerivedMetricCatalog([
            definition("a", {"derived_metric": "b"}),
            definition("b", {"derived_metric": "a"}),
        ])
    assert cycle.value.code == "derived_metric.cycle"
    with pytest.raises(DerivedMetricContractError) as missing:
        DerivedMetricCatalog([definition("a", {"derived_metric": "unknown"})])
    assert missing.value.code == "derived_metric.missing_dependency"


def test_divide_by_zero_is_structured_and_never_leaks_exception_text():
    catalog = DerivedMetricCatalog([
        definition("bad", {"op": "divide", "left": {"constant": 1}, "right": {"constant": 0}}),
    ])
    result = catalog.evaluate("bad", context())
    assert result["status"] == "error"
    assert result["error_code"] == "derived_metric.divide_by_zero"
    assert "formula" not in result
    assert "metadata" not in result


def test_missing_policy_fail_zero_and_formula_coalesce():
    missing = {"source": "metric", "entity_id": "capital", "name": "unknown"}
    catalog = DerivedMetricCatalog([
        definition("fails", missing),
        definition("zero", missing, missing_policy="zero"),
        definition("fallback", {"op": "coalesce", "values": [missing, {"constant": 9}]}),
    ])
    assert catalog.evaluate("fails", context())["status"] == "missing"
    assert catalog.value("zero", context()) == 0.0
    assert catalog.value("fallback", context()) == 9.0


def test_audience_projection_and_agent_budget_are_fail_closed():
    catalog = DerivedMetricCatalog([
        definition("public", {"constant": 10}, audiences=["player", "agent"]),
        definition("internal", {"constant": 99}, audiences=["rule"], metadata={"secret": "do not expose"}),
    ], generation=3)
    public = catalog.public_state(context())
    assert [item["metric_id"] for item in public["metrics"]] == ["public"]
    assert public["metric_count"] == 1
    assert "metadata" not in public["metrics"][0]
    prompt = catalog.agent_context(context(), max_records=8, max_chars=500)
    assert "只读派生指标摘要" in prompt
    assert "public" in prompt
    assert "internal" not in prompt
    assert "do not expose" not in prompt
    assert catalog.evaluate("internal", context(), audience="player")["status"] == "hidden"


def test_disabled_not_found_and_empty_catalog_have_structured_state():
    catalog = DerivedMetricCatalog([definition("off", {"constant": 1}, enabled=False)])
    assert catalog.evaluate("off", context())["status"] == "disabled"
    assert catalog.evaluate("absent", context())["error_code"] == "derived_metric.not_found"
    assert DerivedMetricCatalog().public_state(context()) == {
        "configured": False,
        "generation": 0,
        "evaluated_at": 2.5 * 86400,
        "metric_count": 0,
        "metrics": [],
    }


def test_reference_projection_supports_registry_cross_validation():
    catalog = DerivedMetricCatalog([
        definition("refs", {"op": "sum", "values": [
            {"source": "metric", "entity_id": "north", "name": "security"},
            {"source": "relation", "aggregate": "avg", "name": "route", "field": "strength"},
        ]}),
    ])
    assert catalog.get("refs").references == (
        {"source": "metric", "entity_id": "north", "name": "security"},
        {"source": "relation", "name": "route", "field": "strength"},
    )


def test_validation_limits_and_document_normalization():
    assert normalize_derived_metric_document(None) == []
    with pytest.raises(TypeError):
        normalize_derived_metric_document({})
    issues = validate_derived_metric_definitions([definition("bad id", {"constant": 1})])
    assert issues[0].code == "derived_metric.invalid_id"
    deep = {"constant": 1}
    for _ in range(14):
        deep = {"op": "add", "values": [deep]}
    with pytest.raises(DerivedMetricContractError) as exc:
        DerivedMetricCatalog([definition("deep", deep)])
    assert exc.value.code == "derived_metric.formula.too_deep"


def test_source_schema_rejects_non_finite_numbers_and_invalid_references():
    with pytest.raises(DerivedMetricContractError) as bad_number:
        DerivedMetricCatalog([definition("nan", {"constant": float("nan")})])
    assert bad_number.value.code == "derived_metric.formula.invalid_number"
    with pytest.raises(DerivedMetricContractError) as missing_entity:
        DerivedMetricCatalog([definition("metric", {"source": "metric", "name": "security"})])
    assert missing_entity.value.code == "derived_metric.formula.missing_entity"


def test_entity_count_and_sim_day_are_deterministic():
    catalog = DerivedMetricCatalog([
        definition("count", {"source": "entity_count", "aggregate": "count", "filters": {"entity_type": "outpost"}}),
        definition("day", {"source": "sim_day"}, precision=3),
    ])
    assert catalog.value("count", context()) == 2.0
    assert catalog.value("day", context()) == 2.5
