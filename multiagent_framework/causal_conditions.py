"""Generic, deterministic causal-condition schema and value resolver."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field as dataclass_field
import math
from typing import Any

from .event_generator import GAME_SECONDS_PER_DAY

MAX_REFERENCE_LENGTH = 128
OPERATORS = {
    "lt", "lte", "eq", "ne", "gte", "gt",
    "contains", "not_contains", "in", "not_in", "exists", "not_exists",
}
FACT_KINDS = {
    "anchored_inventory", "metric", "parameter",
    "entity_field", "entity_tag", "entity_state",
    "actor_attribute", "actor_state", "actor_trait", "actor_membership",
    "relationship", "ongoing_event", "event_history", "time",
    "objective_progress", "aggregate_metric", "player_knowledge",
}
AGGREGATES = {"sum", "avg", "min", "max", "count"}
AGGREGATE_SOURCES = {
    "entity_count", "anchored_inventory", "metric", "parameter",
    "actor_attribute", "relation", "ongoing_event",
}
_MISSING = object()


class CausalEventError(ValueError):
    """Raised when causal rules, conditions, or scheduler state are invalid."""


def _text(value: Any, *, limit: int = MAX_REFERENCE_LENGTH) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise CausalEventError("causal_condition_reference_invalid")
    return text


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > 512:
            raise CausalEventError("causal_condition_value_invalid")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise CausalEventError("causal_condition_value_invalid")
        return number
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 64:
            raise CausalEventError("causal_condition_value_invalid")
        return tuple(_scalar(item) for item in value)
    raise CausalEventError("causal_condition_value_invalid")


@dataclass(frozen=True)
class CausalCondition:
    kind: str
    entity_id: str = ""
    name: str = ""
    operator: str = "eq"
    value: Any = None
    target_entity_id: str = ""
    field: str = ""
    aggregate: str = ""
    window_days: float | None = None
    filters: dict[str, Any] = dataclass_field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CausalCondition":
        if not isinstance(value, Mapping):
            raise CausalEventError("causal_condition_invalid")
        operator = _text(value.get("operator", "eq"), limit=32)
        raw_filters = value.get("filters", {})
        if not isinstance(raw_filters, Mapping) or len(raw_filters) > 16:
            raise CausalEventError("causal_condition_filters_invalid")
        filters = {_text(key, limit=64): _scalar(item) for key, item in raw_filters.items()}
        raw_window = value.get("window_days")
        window_days: float | None = None
        if raw_window is not None:
            if isinstance(raw_window, bool):
                raise CausalEventError("causal_condition_window_invalid")
            try:
                window_days = float(raw_window)
            except (TypeError, ValueError, OverflowError) as exc:
                raise CausalEventError("causal_condition_window_invalid") from exc
            if not math.isfinite(window_days) or not 0 <= window_days <= 36500:
                raise CausalEventError("causal_condition_window_invalid")
        condition = cls(
            kind=_text(value.get("kind", ""), limit=64),
            entity_id=_text(value.get("entity_id", "")),
            name=_text(value.get("name", "")),
            operator=operator,
            value=None if operator in {"exists", "not_exists"} else _scalar(value.get("value")),
            target_entity_id=_text(value.get("target_entity_id", "")),
            field=_text(value.get("field", ""), limit=128),
            aggregate=_text(value.get("aggregate", ""), limit=16),
            window_days=window_days,
            filters=filters,
        )
        condition.validate()
        return condition

    def validate(self) -> None:
        if self.kind not in FACT_KINDS:
            raise CausalEventError("causal_condition_kind_invalid")
        if self.operator not in OPERATORS:
            raise CausalEventError("causal_condition_operator_invalid")
        if self.operator not in {"exists", "not_exists"} and self.value is None:
            raise CausalEventError("causal_condition_value_invalid")
        if self.kind in {"anchored_inventory", "metric", "parameter"} and (not self.entity_id or not self.name):
            raise CausalEventError("causal_condition_reference_invalid")
        if self.kind in {"entity_field", "entity_tag", "entity_state"} and not self.entity_id:
            raise CausalEventError("causal_condition_reference_invalid")
        if self.kind == "entity_field" and not (self.field or self.name):
            raise CausalEventError("causal_condition_reference_invalid")
        if self.kind in {"actor_attribute", "actor_state", "actor_trait", "actor_membership"} and (not self.entity_id or not self.name):
            raise CausalEventError("causal_condition_reference_invalid")
        if self.kind == "relationship" and (not self.entity_id or not self.target_entity_id or not self.name):
            raise CausalEventError("causal_condition_reference_invalid")
        if self.kind == "objective_progress" and not (self.entity_id or self.name):
            raise CausalEventError("causal_condition_reference_invalid")
        if self.kind == "aggregate_metric":
            if self.aggregate and self.aggregate not in AGGREGATES:
                raise CausalEventError("causal_condition_aggregate_invalid")
            if self.field not in AGGREGATE_SOURCES:
                raise CausalEventError("causal_condition_aggregate_source_invalid")


@dataclass(frozen=True)
class CausalConditionContext:
    world_state: Any = None
    actor_manager: Any = None
    objective_manager: Any = None
    event_history: Sequence[Mapping[str, Any]] = ()
    current_event: Any = None
    sim_time: float = 0.0
    knowledge_provider: Any = None
    issue_manager: Any = None
    action_catalog: Any = None


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _entity_matches_filters(entity: Any, filters: Mapping[str, Any]) -> bool:
    if filters.get("entity_type") and _field(entity, "type", "") != filters["entity_type"]:
        return False
    if filters.get("status") and _field(entity, "current_status", "") != filters["status"]:
        return False
    tag = filters.get("tag")
    if tag:
        metadata = _field(entity, "metadata", {})
        tags = metadata.get("tags", []) if isinstance(metadata, Mapping) else []
        if tag not in tags:
            return False
    return True


def _relationship_value(condition: CausalCondition, context: CausalConditionContext) -> Any:
    world_state = context.world_state
    getter = getattr(world_state, "get_all_relations", None)
    if not callable(getter):
        return _MISSING
    try:
        relations = getter(sim_time=context.sim_time)
    except TypeError:
        relations = getter()
    direction = "directed"
    catalog = getattr(world_state, "relationship_catalog", None)
    definition = catalog.get(condition.name) if catalog is not None and hasattr(catalog, "get") else None
    if definition is not None:
        direction = str(_field(definition, "direction", "directed"))
    match = None
    for relation in relations:
        if str(_field(relation, "rel_type", "")) != condition.name:
            continue
        parent = str(_field(relation, "parent_id", ""))
        child = str(_field(relation, "child_id", ""))
        endpoints_match = parent == condition.entity_id and child == condition.target_entity_id
        if direction == "undirected":
            endpoints_match = endpoints_match or (parent == condition.target_entity_id and child == condition.entity_id)
        if endpoints_match:
            match = relation
            break
    if match is None:
        return _MISSING
    if not condition.field or condition.field == "exists":
        return True
    if condition.field.startswith("values."):
        values = _field(match, "values", {})
        return values.get(condition.field.split(".", 1)[1], _MISSING) if isinstance(values, Mapping) else _MISSING
    if condition.field.startswith("metadata."):
        metadata = _field(match, "metadata", {})
        return metadata.get(condition.field.split(".", 1)[1], _MISSING) if isinstance(metadata, Mapping) else _MISSING
    return _field(match, condition.field, _MISSING)


def _aggregate_value(condition: CausalCondition, context: CausalConditionContext) -> Any:
    source = condition.field
    aggregate = condition.aggregate or "sum"
    values: list[float] = []
    world_state = context.world_state
    if source in {"entity_count", "anchored_inventory", "metric", "parameter"}:
        getter = getattr(world_state, "get_all_entities", None)
        if not callable(getter):
            return _MISSING
        entities = [item for item in getter() if _entity_matches_filters(item, condition.filters)]
        if condition.entity_id:
            entities = [item for item in entities if str(_field(item, "id", "")) == condition.entity_id]
        if source == "entity_count":
            values = [1.0 for _ in entities]
        elif source == "anchored_inventory":
            inventory_getter = getattr(world_state, "get_inventory", None)
            if not callable(inventory_getter):
                return _MISSING
            for entity in entities:
                matches = [
                    float(_field(item, "base_value", 0.0))
                    for item in inventory_getter(str(_field(entity, "id", "")))
                    if str(_field(item, "item_name", "")) == condition.name
                ]
                if matches:
                    values.append(sum(matches))
        else:
            value_getter = getattr(world_state, "get_entity_values", None)
            if not callable(value_getter):
                return _MISSING
            for entity in entities:
                item = next((
                    item for item in value_getter(str(_field(entity, "id", "")), source)
                    if str(_field(item, "name", "")) == condition.name
                ), None)
                if item is not None:
                    values.append(float(_field(item, "value", 0.0)))
    elif source == "actor_attribute":
        getter = getattr(context.actor_manager, "list", None)
        if not callable(getter):
            return _MISSING
        for actor in getter():
            if condition.entity_id and str(actor.get("actor_id", "")) != condition.entity_id:
                continue
            if condition.filters.get("actor_type") and actor.get("actor_type") != condition.filters["actor_type"]:
                continue
            actual = actor.get("runtime", {}).get("attributes", {}).get(condition.name, _MISSING)
            if isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(float(actual)):
                values.append(float(actual))
    elif source == "relation":
        getter = getattr(world_state, "get_all_relations", None)
        if not callable(getter):
            return _MISSING
        try:
            relations = getter(sim_time=context.sim_time)
        except TypeError:
            relations = getter()
        value_field = str(condition.filters.get("value_field", "strength"))
        for relation in relations:
            if condition.name and str(_field(relation, "rel_type", "")) != condition.name:
                continue
            if condition.filters.get("active") is not None and bool(_field(relation, "active", False)) != bool(condition.filters["active"]):
                continue
            actual = _field(relation, value_field, _MISSING)
            if isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(float(actual)):
                values.append(float(actual))
            elif aggregate == "count":
                values.append(1.0)
    elif source == "ongoing_event":
        getter = getattr(world_state, "list_ongoing_events", None)
        if not callable(getter):
            return _MISSING
        events = getter(condition.entity_id or None)
        values = [1.0 for item in events if not condition.name or str(_field(item, "event_type", "")) == condition.name]
    if aggregate == "count":
        return float(len(values))
    if not values:
        return _MISSING
    if aggregate == "sum":
        return sum(values)
    if aggregate == "avg":
        return sum(values) / len(values)
    if aggregate == "min":
        return min(values)
    return max(values)


def resolve_condition_value(condition: CausalCondition, context: CausalConditionContext) -> Any:
    """Resolve one bounded condition fact without mutating runtime state."""
    world_state = context.world_state
    if condition.kind == "anchored_inventory":
        getter = getattr(world_state, "get_inventory", None)
        if not callable(getter):
            return _MISSING
        matches = [
            float(_field(item, "base_value", 0.0))
            for item in getter(condition.entity_id)
            if str(_field(item, "item_name", "")) == condition.name
        ]
        return sum(matches) if matches else _MISSING
    if condition.kind in {"metric", "parameter"}:
        getter = getattr(world_state, "get_entity_values", None)
        if not callable(getter):
            return _MISSING
        item = next((
            item for item in getter(condition.entity_id, condition.kind)
            if str(_field(item, "name", "")) == condition.name
        ), None)
        return _field(item, "value", _MISSING) if item is not None else _MISSING
    if condition.kind in {"entity_field", "entity_tag", "entity_state"}:
        getter = getattr(world_state, "get_entity", None)
        entity = getter(condition.entity_id) if callable(getter) else None
        if entity is None:
            return _MISSING
        if condition.kind == "entity_state":
            return _field(entity, "current_status", _MISSING)
        metadata = _field(entity, "metadata", {})
        if condition.kind == "entity_tag":
            tags = metadata.get("tags", []) if isinstance(metadata, Mapping) else []
            return condition.name in tags
        field_name = condition.field or condition.name
        if field_name.startswith("metadata."):
            return metadata.get(field_name.split(".", 1)[1], _MISSING) if isinstance(metadata, Mapping) else _MISSING
        return _field(entity, field_name, _MISSING)
    if condition.kind.startswith("actor_"):
        getter = getattr(context.actor_manager, "get", None)
        actor = getter(condition.entity_id) if callable(getter) else None
        if not isinstance(actor, Mapping):
            return _MISSING
        runtime = actor.get("runtime", {})
        if condition.kind == "actor_attribute":
            return runtime.get("attributes", {}).get(condition.name, _MISSING)
        if condition.kind == "actor_state":
            return runtime.get("states", {}).get(condition.name, _MISSING)
        if condition.kind == "actor_trait":
            return any(item.get("trait_id") == condition.name for item in actor.get("traits", []))
        membership = next((item for item in actor.get("memberships", []) if item.get("organization_id") == condition.name), None)
        if membership is None:
            return _MISSING
        return membership.get(condition.field, _MISSING) if condition.field else True
    if condition.kind == "relationship":
        return _relationship_value(condition, context)
    if condition.kind == "ongoing_event":
        getter = getattr(world_state, "list_ongoing_events", None)
        if not callable(getter):
            return _MISSING
        events = [
            item for item in getter(condition.entity_id or None)
            if not condition.name or str(_field(item, "event_type", "")) == condition.name
        ]
        if condition.field in {"exists", "active"}:
            return bool(events)
        if condition.field == "remaining_days":
            return min((
                max(0.0, float(_field(item, "end_time", context.sim_time)) - context.sim_time) / GAME_SECONDS_PER_DAY
                for item in events
            ), default=_MISSING)
        return float(len(events))
    if condition.kind == "event_history":
        threshold = context.sim_time - (
            condition.window_days * GAME_SECONDS_PER_DAY
            if condition.window_days is not None else math.inf
        )
        events = [
            item for item in context.event_history
            if (not condition.name or str(item.get("event_type", "")) == condition.name)
            and (not condition.entity_id or condition.entity_id in item.get("target_agents", []))
            and float(item.get("generated_at", 0.0)) >= threshold
        ]
        if condition.field == "last_generated_at":
            return max((float(item.get("generated_at", 0.0)) for item in events), default=_MISSING)
        if condition.field in {"exists", "seen"}:
            return bool(events)
        return float(len(events))
    if condition.kind == "time":
        if condition.name in {"sim_time", "seconds"}:
            return float(context.sim_time)
        return float(context.sim_time) / GAME_SECONDS_PER_DAY
    if condition.kind == "objective_progress":
        getter = getattr(context.objective_manager, "public_state", None)
        if not callable(getter):
            return _MISSING
        try:
            state = getter(
                world_state=world_state,
                sim_time=context.sim_time,
                issue_manager=context.issue_manager,
                action_catalog=context.action_catalog,
            )
        except Exception:
            return _MISSING
        objective_id = condition.entity_id or condition.name
        objective = next((
            item for item in state.get("objectives", [])
            if item.get("objective_id") == objective_id
        ), None)
        return objective.get(condition.field or "progress", _MISSING) if objective is not None else _MISSING
    if condition.kind == "aggregate_metric":
        return _aggregate_value(condition, context)
    provider = context.knowledge_provider
    if provider is None:
        return _MISSING
    resolver = getattr(provider, "resolve_condition", None)
    if callable(resolver):
        return resolver(condition, context)
    if callable(provider):
        return provider(condition, context)
    getter = getattr(provider, "get_player_knowledge", None)
    if callable(getter):
        return getter(
            condition.entity_id,
            condition.name,
            field=condition.field,
            sim_time=context.sim_time,
        )
    return _MISSING


def compare_condition_value(actual: Any, operator: str, expected: Any) -> bool:
    """Apply stable, fail-closed comparison semantics to a resolved fact."""
    exists = actual is not _MISSING and actual is not None
    if operator == "exists":
        return exists
    if operator == "not_exists":
        return not exists
    if not exists:
        return False
    if operator in {"contains", "not_contains"}:
        try:
            result = expected in actual
        except (TypeError, ValueError):
            result = False
        return result if operator == "contains" else not result
    if operator in {"in", "not_in"}:
        try:
            result = actual in expected
        except (TypeError, ValueError):
            result = False
        return result if operator == "in" else not result
    both_numbers = (
        isinstance(actual, (int, float)) and not isinstance(actual, bool)
        and isinstance(expected, (int, float)) and not isinstance(expected, bool)
    )
    if both_numbers:
        left, right = float(actual), float(expected)
        if not math.isfinite(left) or not math.isfinite(right):
            return False
        if operator == "eq":
            return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
        if operator == "ne":
            return not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
        return {
            "lt": left < right,
            "lte": left <= right,
            "gte": left >= right,
            "gt": left > right,
        }.get(operator, False)
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    try:
        return {
            "lt": actual < expected,
            "lte": actual <= expected,
            "gte": actual >= expected,
            "gt": actual > expected,
        }.get(operator, False)
    except TypeError:
        return False
