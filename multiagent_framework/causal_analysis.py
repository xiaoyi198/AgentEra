"""Static diagnostics and deterministic dry-runs for DLC causal event rules.

The helpers in this module never mutate the live orchestrator or world database.
They operate on an isolated rule engine and a seed-world projection built from a
DLC content document, making them safe for draft authoring and CI validation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
import copy
import hashlib
import json
import math
import re
from typing import Any

from .action_contract import ActionCatalog
from .actor_contract import ActorManager
from .causal_events import (
    CAUSAL_METADATA_FIELDS,
    CausalConditionContext,
    CausalEventEngine,
    CausalEventError,
    CausalEventRule,
)
from .causal_templates import expand_causal_rule_document
from .event_generator import GAME_SECONDS_PER_DAY
from .gameplay_effect_runtime import (
    GameplayEffectRuntime, empty_gameplay_effect_state, reduce_gameplay_effect_state,
)
from .issue_contract import IssueManager
from .objective_contract import ObjectiveManager
from .relationship_contract import RelationshipCatalog
from .types import EventObject
from .world_state import Entity, EntityValue, InventoryItem, OngoingEvent, ResourceDefinition

_MAX_DRY_RUN_EVENTS = 256
_MAX_HORIZON_DAYS = 3650.0
CAUSAL_REPLAY_FIXTURE_FORMAT = "agentera-causal-replay-v1"
_CAUSAL_REPLAY_SCHEMA_VERSION = "1.0"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class _SeedWorldFacts:
    """Read-only world-state projection containing only facts used by conditions."""

    def __init__(self, content: Mapping[str, Any]) -> None:
        self._entities: dict[str, Entity] = {}
        for raw in _list_field(content, "entities"):
            if not isinstance(raw, Mapping):
                continue
            entity_id = str(raw.get("id", "")).strip()
            if not entity_id:
                continue
            metadata = raw.get("metadata", {})
            self._entities[entity_id] = Entity(
                id=entity_id,
                type=str(raw.get("type", "")),
                name=str(raw.get("name", entity_id)),
                description=str(raw.get("description", "")),
                current_status=str(raw.get("current_status", "")),
                updated_at=float(raw.get("updated_at", 0.0) or 0.0),
                metadata=copy.deepcopy(dict(metadata)) if isinstance(metadata, Mapping) else {},
                version=int(raw.get("version", 0) or 0),
            )
        self._entity_ids = set(self._entities)
        self.relationship_catalog = RelationshipCatalog(_list_field(content, "relationship_types"))
        self._relations: list[dict[str, Any]] = []
        for index, raw in enumerate(_list_field(content, "relations")):
            if not isinstance(raw, Mapping):
                continue
            try:
                self._relations.append(self.relationship_catalog.normalize_relation(raw, f"relations[{index}]"))
            except Exception:
                continue
        self._ongoing: list[OngoingEvent] = []
        for raw in _list_field(content, "ongoing_events"):
            if not isinstance(raw, Mapping):
                continue
            try:
                self._ongoing.append(OngoingEvent(
                    event_id=str(raw.get("event_id", "")),
                    entity_id=str(raw.get("entity_id", "")),
                    event_type=str(raw.get("event_type", "state_change")),
                    description=str(raw.get("description", "")),
                    start_time=float(raw.get("start_time", 0.0) or 0.0),
                    end_time=float(raw.get("end_time", 0.0) or 0.0),
                    expected_result=copy.deepcopy(dict(raw.get("expected_result", {}))),
                    source_action_id=str(raw.get("source_action_id", "")),
                ))
            except (TypeError, ValueError, OverflowError):
                continue
        resources = _list_field(content, "resources")
        inventory = _list_field(content, "inventory")
        values = _list_field(content, "entity_values")
        anchor_rates: dict[str, float] = {}
        for raw in resources:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name", "")).strip()
            if not name:
                continue
            try:
                rate = float(raw.get("anchor_rate", 1.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(rate) and rate > 0:
                anchor_rates[name] = rate

        self._resources: dict[str, ResourceDefinition] = {}
        for raw in resources:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name", "")).strip()
            kind = str(raw.get("kind", "asset")).strip()
            if not name:
                continue
            def optional_number(field_name: str) -> float | None:
                raw_value = raw.get(field_name)
                if raw_value is None:
                    return None
                try:
                    parsed = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    return None
                return parsed if math.isfinite(parsed) else None
            self._resources[name] = ResourceDefinition(
                name=name, kind=kind, unit=str(raw.get("unit", "")),
                default_quantity=float(raw.get("default_quantity", 0.0) or 0.0),
                min_quantity=optional_number("min_quantity"),
                max_quantity=optional_number("max_quantity"),
                anchor_rate=anchor_rates.get(name) if kind == "asset" else None,
                metadata={},
            )

        self._inventory: dict[str, list[InventoryItem]] = defaultdict(list)
        for raw in inventory:
            if not isinstance(raw, Mapping):
                continue
            entity_id = str(raw.get("entity_id", "")).strip()
            item_name = str(raw.get("item_name", raw.get("resource_name", ""))).strip()
            try:
                quantity = float(raw.get("quantity", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not entity_id or not item_name or not math.isfinite(quantity):
                continue
            self._inventory[entity_id].append(InventoryItem(
                entity_id=entity_id,
                category=str(raw.get("category", "")),
                item_name=item_name,
                quantity=quantity,
                description=str(raw.get("description", "")),
                anchor_rate=anchor_rates.get(item_name, 1.0),
            ))

        self._gameplay_effect_state = empty_gameplay_effect_state()

        self._values: dict[str, list[EntityValue]] = defaultdict(list)
        for raw in values:
            if not isinstance(raw, Mapping):
                continue
            entity_id = str(raw.get("entity_id", "")).strip()
            name = str(raw.get("name", "")).strip()
            kind = str(raw.get("kind", "metric")).strip()
            try:
                value = float(raw.get("value", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not entity_id or not name or kind not in {"metric", "parameter"} or not math.isfinite(value):
                continue
            self._values[entity_id].append(EntityValue(
                entity_id=entity_id,
                name=name,
                kind=kind,
                value=value,
                description=str(raw.get("description", "")),
            ))

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def get_all_entities(self) -> list[Entity]:
        return list(self._entities.values())

    def get_all_relations(self, *, sim_time: float = 0.0) -> list[dict[str, Any]]:
        return [
            {**copy.deepcopy(item), "active": self.relationship_catalog.is_active(item, sim_time)}
            for item in self._relations
        ]

    def list_ongoing_events(self, entity_id: str | None = None) -> list[OngoingEvent]:
        return [
            copy.deepcopy(item) for item in self._ongoing
            if entity_id is None or item.entity_id == entity_id
        ]

    def get_inventory(self, entity_id: str) -> list[InventoryItem]:
        return list(self._inventory.get(entity_id, ()))

    def get_resource_definition(self, name: str) -> ResourceDefinition | None:
        return self._resources.get(name)

    def get_entity_values(self, entity_id: str, kind: str | None = None) -> list[EntityValue]:
        values = self._values.get(entity_id, ())
        return [item for item in values if kind is None or item.kind == kind]

    def get_gameplay_effect_state(self) -> dict[str, Any]:
        return copy.deepcopy(self._gameplay_effect_state)

    def snapshot(self) -> dict[str, Any]:
        return {
            "entities": {key: Entity(**asdict(item)) for key, item in self._entities.items()},
            "entity_ids": set(self._entity_ids),
            "inventory": {
                key: [InventoryItem(**asdict(item)) for item in values]
                for key, values in self._inventory.items()
            },
            "values": {
                key: [EntityValue(**asdict(item)) for item in values]
                for key, values in self._values.items()
            },
            "relations": copy.deepcopy(self._relations),
            "ongoing": [OngoingEvent(**asdict(item)) for item in self._ongoing],
            "gameplay_effect_state": copy.deepcopy(self._gameplay_effect_state),
        }

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self._entities = {
            key: Entity(**asdict(item)) if isinstance(item, Entity) else Entity(**dict(item))
            for key, item in snapshot["entities"].items()
        }
        self._entity_ids = set(snapshot["entity_ids"])
        self._inventory = defaultdict(list, {
            key: [InventoryItem(**asdict(item)) if isinstance(item, InventoryItem) else InventoryItem(**dict(item)) for item in values]
            for key, values in snapshot["inventory"].items()
        })
        self._values = defaultdict(list, {
            key: [EntityValue(**asdict(item)) if isinstance(item, EntityValue) else EntityValue(**dict(item)) for item in values]
            for key, values in snapshot["values"].items()
        })
        self._relations = copy.deepcopy(snapshot["relations"])
        self._ongoing = [
            OngoingEvent(**asdict(item)) if isinstance(item, OngoingEvent) else OngoingEvent(**dict(item))
            for item in snapshot["ongoing"]
        ]
        self._gameplay_effect_state = copy.deepcopy(snapshot["gameplay_effect_state"])

    @staticmethod
    def _finite_number(value: Any, code: str) -> float:
        if isinstance(value, bool):
            raise CausalEventError(code)
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CausalEventError(code) from exc
        if not math.isfinite(number):
            raise CausalEventError(code)
        return number

    def _init_default_resources(self, entity_id: str) -> None:
        for definition in self._resources.values():
            if definition.kind == "asset":
                self._inventory[entity_id].append(InventoryItem(
                    entity_id=entity_id,
                    category=definition.name,
                    item_name=definition.name,
                    quantity=float(definition.default_quantity),
                    description=definition.unit,
                    anchor_rate=float(definition.anchor_rate or 1.0),
                ))
            else:
                self._values[entity_id].append(EntityValue(
                    entity_id=entity_id,
                    name=definition.name,
                    kind=definition.kind,
                    value=float(definition.default_quantity),
                    description=definition.unit,
                ))

    def _apply_relationship(self, payload: Mapping[str, Any]) -> None:
        operation = str(payload.get("operation", "upsert"))
        parent_id = str(payload.get("parent_id", ""))
        child_id = str(payload.get("child_id", ""))
        rel_type = str(payload.get("rel_type", ""))
        if parent_id not in self._entity_ids or child_id not in self._entity_ids:
            raise CausalEventError("entity_not_found")
        definition = self.relationship_catalog.get(rel_type)
        if definition is None:
            raise CausalEventError("relationship_type_not_found")
        if definition.direction == "undirected" and child_id < parent_id:
            parent_id, child_id = child_id, parent_id
        if operation == "remove":
            self._relations = [
                item for item in self._relations
                if not (
                    item.get("parent_id") == parent_id
                    and item.get("child_id") == child_id
                    and item.get("rel_type") == rel_type
                )
            ]
            return
        if operation != "upsert":
            raise CausalEventError("relationship_operation_invalid")
        relation_payload = dict(payload)
        relation_payload.pop("operation", None)
        relation_payload["parent_id"] = parent_id
        relation_payload["child_id"] = child_id
        relation = self.relationship_catalog.normalize_relation(relation_payload, "command.payload")
        self._relations = [
            item for item in self._relations
            if not (
                item.get("parent_id") == relation["parent_id"]
                and item.get("child_id") == relation["child_id"]
                and item.get("rel_type") == relation["rel_type"]
            )
        ]
        self._relations.append(relation)

    def apply_world_commands(
        self, commands: Sequence[Mapping[str, Any]], *, sim_time: float = 0.0,
        event_id: str = "dry-run",
    ) -> tuple[bool, str]:
        """Apply one immutable causal batch to the isolated projection atomically."""

        before = self.snapshot()
        try:
            for index, command in enumerate(commands):
                command_type = str(command.get("command_type", ""))
                entity_id = str(command.get("entity_id", ""))
                payload = command.get("payload", {})
                if not isinstance(payload, Mapping):
                    raise CausalEventError("causal_plan_invalid")

                if command_type == "create_entity":
                    if not entity_id or entity_id in self._entity_ids:
                        raise CausalEventError("entity_exists" if entity_id else "entity_invalid")
                    entity_type = str(payload.get("entity_type", "")).strip()
                    name = str(payload.get("name", "")).strip()
                    metadata = payload.get("metadata", {})
                    if not entity_type or not name or not isinstance(metadata, Mapping):
                        raise CausalEventError("entity_invalid")
                    self._entities[entity_id] = Entity(
                        id=entity_id,
                        type=entity_type,
                        name=name,
                        description=str(payload.get("description", "")),
                        current_status=str(payload.get("current_status", "")),
                        updated_at=float(sim_time),
                        metadata=copy.deepcopy(dict(metadata)),
                        version=0,
                    )
                    self._entity_ids.add(entity_id)
                    self._init_default_resources(entity_id)
                    continue

                if command_type in {
                    "set_agent_enabled", "modify_membership", "create_issue",
                    "set_world_truth", "set_actor_belief", "reveal_information",
                    "propagate_knowledge", "start_investigation", "advance_investigation",
                    "advance_objective", "unlock_action", "switch_phase",
                }:
                    self._gameplay_effect_state, _ = reduce_gameplay_effect_state(
                        self._gameplay_effect_state,
                        command_type=command_type,
                        payload=payload,
                        sim_time=float(sim_time),
                        command_id=f"causal:{event_id}:{index}",
                    )
                    continue

                if command_type == "modify_relationship":
                    self._apply_relationship(payload)
                    continue

                if entity_id not in self._entity_ids:
                    raise CausalEventError("entity_not_found")

                if command_type == "move_entity":
                    metadata = copy.deepcopy(self._entities[entity_id].metadata)
                    changed = False
                    for key in ("x", "y"):
                        if key in payload:
                            metadata[key] = self._finite_number(payload[key], "location_invalid")
                            changed = True
                    for key in ("region", "location_id"):
                        if key in payload:
                            metadata[key] = str(payload[key])
                            changed = True
                    if not changed:
                        raise CausalEventError("location_invalid")
                    entity = self._entities[entity_id]
                    entity.metadata = metadata
                    entity.updated_at = float(sim_time)
                    entity.version += 1
                    continue

                if command_type == "transform_entity":
                    entity = self._entities[entity_id]
                    metadata_patch = payload.get("metadata_patch", {})
                    if metadata_patch is not None and not isinstance(metadata_patch, Mapping):
                        raise CausalEventError("metadata_invalid")
                    next_type = str(payload.get("new_type", entity.type))
                    next_name = str(payload.get("new_name", entity.name))
                    if not next_type or not next_name:
                        raise CausalEventError("entity_invalid")
                    entity.type = next_type
                    entity.name = next_name
                    entity.description = str(payload.get("new_description", entity.description))
                    entity.current_status = str(payload.get("new_status", entity.current_status))
                    entity.metadata.update(copy.deepcopy(dict(metadata_patch or {})))
                    entity.updated_at = float(sim_time)
                    entity.version += 1
                    continue

                if command_type == "remove_entity":
                    del self._entities[entity_id]
                    self._entity_ids.remove(entity_id)
                    self._inventory.pop(entity_id, None)
                    self._values.pop(entity_id, None)
                    self._relations = [
                        item for item in self._relations
                        if item.get("parent_id") != entity_id and item.get("child_id") != entity_id
                    ]
                    self._ongoing = [item for item in self._ongoing if item.entity_id != entity_id]
                    continue

                if command_type == "update_status":
                    entity = self._entities[entity_id]
                    entity.current_status = str(payload.get("new_status", ""))
                    entity.updated_at = float(sim_time)
                    entity.version += 1
                    continue

                if command_type == "create_event":
                    self._ongoing.append(OngoingEvent(
                        event_id=str(payload.get("event_id", "")),
                        entity_id=entity_id,
                        event_type=str(payload.get("event_type", "state_change")),
                        description=str(payload.get("description", "")),
                        start_time=float(payload.get("start_time", 0.0)),
                        end_time=float(payload.get("end_time", 0.0)),
                        expected_result=copy.deepcopy(dict(payload.get("expected_result", {}))),
                        source_action_id=str(payload.get("source_action_id", "")),
                    ))
                    continue

                if command_type not in {"adjust_resource", "set_resource"}:
                    raise CausalEventError("unsupported_command")
                resource_name = str(payload.get("resource_name", ""))
                definition = self._resources.get(resource_name)
                if definition is None:
                    raise CausalEventError("resource_not_found")
                key = "quantity" if command_type == "set_resource" else "quantity_delta"
                number = self._finite_number(payload.get(key), "resource_quantity_invalid")
                if definition.kind == "asset":
                    items = self._inventory[entity_id]
                    item = next((candidate for candidate in items if candidate.item_name == resource_name), None)
                    old_value = item.quantity if item else definition.default_quantity
                    next_value = number if command_type == "set_resource" else old_value + number
                    if definition.min_quantity is not None and next_value < definition.min_quantity:
                        raise CausalEventError("insufficient_balance")
                    if definition.max_quantity is not None and next_value > definition.max_quantity:
                        raise CausalEventError("resource_out_of_range")
                    if item is None:
                        items.append(InventoryItem(
                            entity_id=entity_id, category=resource_name, item_name=resource_name,
                            quantity=next_value, anchor_rate=float(definition.anchor_rate or 1.0),
                        ))
                    else:
                        item.quantity = next_value
                else:
                    items = self._values[entity_id]
                    item = next((candidate for candidate in items if candidate.name == resource_name), None)
                    old_value = item.value if item else definition.default_quantity
                    next_value = number if command_type == "set_resource" else old_value + number
                    if definition.min_quantity is not None:
                        next_value = max(0.0 if definition.min_quantity == -1 else definition.min_quantity, next_value)
                    if definition.max_quantity is not None:
                        next_value = min(definition.max_quantity, next_value)
                    if item is None:
                        items.append(EntityValue(entity_id=entity_id, name=resource_name, kind=definition.kind, value=next_value))
                    else:
                        item.value = next_value
        except Exception as exc:
            self.restore(before)
            return False, str(exc) or "causal_effect_rejected"
        return True, ""


def _list_field(content: Mapping[str, Any], name: str) -> list[Any]:
    value = content.get(name, [])
    if not isinstance(value, list):
        raise CausalEventError(f"causal_analysis_{name}_invalid")
    return value


def _rules(content: Mapping[str, Any]) -> list[CausalEventRule]:
    if not isinstance(content, Mapping):
        raise CausalEventError("causal_analysis_content_invalid")
    raw_rules = expand_causal_rule_document(content)
    rules = [CausalEventRule.from_mapping(item) for item in raw_rules]
    ids = [item.rule_id for item in rules]
    if len(ids) != len(set(ids)):
        raise CausalEventError("causal_rule_duplicate")
    return sorted(rules, key=lambda item: (-item.priority, item.rule_id))


def _rule_payload(rule: CausalEventRule) -> dict[str, Any]:
    return {
        "rule_id": rule.rule_id,
        "source_event_types": list(rule.source_event_types),
        "child_event_type": rule.child_event_type,
        "enabled": rule.enabled,
        "priority": rule.priority,
        "delay_days": rule.delay_days,
        "probability": rule.probability,
        "condition_count": len(rule.conditions),
        "world_effect_count": len(rule.world_effects),
        "world_command_count": sum(2 if item.get("effect_type") == "transfer_anchored_value" else 1 for item in rule.world_effects),
        "target_mode": "override" if rule.target_agents else "inherit",
    }


def _cycle_components(edges: Sequence[tuple[str, str]]) -> list[list[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for source, target in edges:
        if source == "*":
            continue
        graph[source].add(target)
        nodes.update((source, target))

    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for child in sorted(graph.get(node, ())):
            if child not in indexes:
                visit(child)
                lowlinks[node] = min(lowlinks[node], lowlinks[child])
            elif child in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[child])
        if lowlinks[node] != indexes[node]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        component.sort()
        if len(component) > 1 or (component and component[0] in graph.get(component[0], ())):
            components.append(component)

    for node in sorted(nodes):
        if node not in indexes:
            visit(node)
    return sorted(components)


def analyze_causal_rules(content: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable, secret-free graph and authoring diagnostics."""

    rules = _rules(content)
    enabled = [rule for rule in rules if rule.enabled]
    edges: list[dict[str, Any]] = []
    edge_pairs: list[tuple[str, str]] = []
    zero_delay_pairs: list[tuple[str, str]] = []
    event_types: set[str] = set()
    source_counts: dict[str, int] = defaultdict(int)
    produced_by: dict[str, list[str]] = defaultdict(list)

    authoring_index: dict[str, dict[str, Any]] = {}
    for raw_rule in _list_field(content, "causal_event_rules"):
        if not isinstance(raw_rule, Mapping):
            continue
        rule_id = str(raw_rule.get("rule_id", "")).strip()
        if not rule_id or rule_id in authoring_index:
            continue
        group_ids = raw_rule.get("condition_group_ids", [])
        authoring_index[rule_id] = {
            "template_id": str(raw_rule.get("template_id", "")).strip(),
            "condition_group_ids": [str(item).strip() for item in group_ids] if isinstance(group_ids, list) else [],
            "inline_condition_count": len(raw_rule.get("conditions", [])) if isinstance(raw_rule.get("conditions", []), list) else 0,
        }

    for rule in rules:
        provenance = authoring_index.get(rule.rule_id, {
            "template_id": "", "condition_group_ids": [], "inline_condition_count": len(rule.conditions),
        })
        for source in rule.source_event_types:
            edge = {**_rule_payload(rule), **provenance, "source_event_type": source}
            edge["edge_id"] = hashlib.sha256(
                f"{rule.rule_id}\0{source}\0{rule.child_event_type}".encode("utf-8")
            ).hexdigest()[:20]
            edges.append(edge)
            event_types.update((source, rule.child_event_type))
            if rule.enabled:
                edge_pairs.append((source, rule.child_event_type))
                source_counts[source] += 1
                produced_by[rule.child_event_type].append(rule.rule_id)
                if rule.delay_days == 0:
                    zero_delay_pairs.append((source, rule.child_event_type))

    seed_types: set[str] = set()
    origins: dict[str, set[str]] = defaultdict(set)
    for section, key, origin in (
        ("initial_events", "event_type", "initial"),
        ("random_events", "event_type", "random"),
    ):
        for item in _list_field(content, section):
            if isinstance(item, Mapping):
                event_type = str(item.get(key, "")).strip()
                if event_type:
                    seed_types.add(event_type)
                    event_types.add(event_type)
                    origins[event_type].add(origin)
    for event_type, rule_ids in produced_by.items():
        if rule_ids:
            origins[event_type].add("causal")

    issues: list[dict[str, Any]] = []
    entity_ids = {
        str(item.get("id", "")).strip()
        for item in _list_field(content, "entities") if isinstance(item, Mapping)
    }
    agent_ids = {
        str(item.get("id", "")).strip()
        for item in _list_field(content, "agents") if isinstance(item, Mapping)
    }
    resource_map = {
        str(item.get("name", "")).strip(): item
        for item in _list_field(content, "resources") if isinstance(item, Mapping)
    }

    def issue(severity: str, code: str, message: str, *, rule_ids: Sequence[str] = (), types: Sequence[str] = ()) -> None:
        issues.append({
            "severity": severity,
            "code": code,
            "message": message,
            "rule_ids": sorted(set(rule_ids)),
            "event_types": sorted(set(types)),
        })

    for rule in rules:
        for target in rule.target_agents:
            if target not in agent_ids:
                issue("error", "causal_analysis.missing_agent", f"Rule {rule.rule_id!r} targets unknown Agent {target!r}.", rule_ids=[rule.rule_id])
        for condition in rule.conditions:
            entity_required_kinds = {
                "anchored_inventory", "metric", "parameter",
                "entity_field", "entity_tag", "entity_state",
            }
            entity_optional_kinds = {
                "ongoing_event", "world_truth", "actor_belief", "player_knowledge",
                "knowledge_confidence", "knowledge_freshness", "knowledge_secrecy",
            }
            if condition.kind in entity_required_kinds and condition.entity_id not in entity_ids:
                issue("error", "causal_analysis.missing_entity", f"Rule {rule.rule_id!r} references unknown entity {condition.entity_id!r}.", rule_ids=[rule.rule_id])
            elif condition.kind in entity_optional_kinds and condition.entity_id and condition.entity_id not in entity_ids:
                issue("error", "causal_analysis.missing_entity", f"Rule {rule.rule_id!r} references unknown entity {condition.entity_id!r}.", rule_ids=[rule.rule_id])
            if condition.kind == "relationship":
                for endpoint in (condition.entity_id, condition.target_entity_id):
                    if endpoint not in entity_ids:
                        issue("error", "causal_analysis.missing_entity", f"Rule {rule.rule_id!r} references unknown relationship endpoint {endpoint!r}.", rule_ids=[rule.rule_id])
            if condition.kind in {"anchored_inventory", "metric", "parameter"}:
                resource = resource_map.get(condition.name)
                expected_kind = "asset" if condition.kind == "anchored_inventory" else condition.kind
                if resource is None:
                    issue("error", "causal_analysis.missing_resource", f"Rule {rule.rule_id!r} references unknown resource {condition.name!r}.", rule_ids=[rule.rule_id])
                elif str(resource.get("kind", "")) != expected_kind:
                    issue("error", "causal_analysis.resource_kind", f"Rule {rule.rule_id!r} requires {condition.name!r} to have kind {expected_kind!r}.", rule_ids=[rule.rule_id])
        available_effect_entities = set(entity_ids)
        for effect in rule.world_effects:
            effect_type = str(effect.get("effect_type", ""))
            entity_fields: tuple[str, ...] = ()
            optional_entity_fields: tuple[str, ...] = ()
            if effect_type == "transfer_anchored_value":
                entity_fields = ("from_entity_id", "to_entity_id")
            elif effect_type in {
                "adjust_resource", "set_resource", "update_status", "create_ongoing_event",
                "move_entity", "transform_entity", "remove_entity",
            }:
                entity_fields = ("entity_id",)
            elif effect_type == "modify_relationship":
                entity_fields = ("parent_id", "child_id")
            elif effect_type == "modify_membership":
                entity_fields = ("organization_id",)
            elif effect_type in {
                "declare_world_truth", "set_actor_belief", "reveal_information",
                "propagate_knowledge", "start_investigation",
            }:
                optional_entity_fields = ("entity_id",)
            for field_name in (*entity_fields, *optional_entity_fields):
                entity_id = str(effect.get(field_name, ""))
                if field_name in optional_entity_fields and not entity_id:
                    continue
                if entity_id not in available_effect_entities:
                    issue("error", "causal_analysis.effect_missing_entity", f"Rule {rule.rule_id!r} world effect references unknown entity {entity_id!r}.", rule_ids=[rule.rule_id])
            if effect_type == "create_entity":
                created_id = str(effect.get("entity_id", ""))
                if created_id:
                    available_effect_entities.add(created_id)
            elif effect_type == "remove_entity":
                available_effect_entities.discard(str(effect.get("entity_id", "")))
            resource_fields: tuple[str, ...] = ()
            if effect_type in {"adjust_resource", "set_resource"}:
                resource_fields = ("resource_name",)
            elif effect_type == "transfer_anchored_value":
                resource_fields = ("from_resource", "to_resource")
            for field_name in resource_fields:
                resource_name = str(effect.get(field_name, ""))
                resource = resource_map.get(resource_name)
                if resource is None:
                    issue("error", "causal_analysis.effect_missing_resource", f"Rule {rule.rule_id!r} world effect references unknown resource {resource_name!r}.", rule_ids=[rule.rule_id])
                elif effect_type == "transfer_anchored_value" and str(resource.get("kind", "")) != "asset":
                    issue("error", "causal_analysis.effect_resource_kind", f"Rule {rule.rule_id!r} anchored transfer requires asset resource {resource_name!r}.", rule_ids=[rule.rule_id])
            if effect_type == "create_ongoing_event":
                for delta in effect.get("expected_result", {}).get("inventory_deltas", []):
                    resource_name = str(delta.get("resource", ""))
                    resource = resource_map.get(resource_name)
                    if resource is None:
                        issue("error", "causal_analysis.effect_missing_resource", f"Rule {rule.rule_id!r} ongoing result references unknown resource {resource_name!r}.", rule_ids=[rule.rule_id])
                    elif str(resource.get("kind", "")) != "asset":
                        issue("error", "causal_analysis.effect_resource_kind", f"Rule {rule.rule_id!r} ongoing inventory delta requires asset resource {resource_name!r}.", rule_ids=[rule.rule_id])

    semantic_groups: dict[str, list[str]] = defaultdict(list)
    for rule in enabled:
        signature = json.dumps({
            "source_event_types": rule.source_event_types,
            "child_event_type": rule.child_event_type,
            "target_agents": rule.target_agents,
            "delay_days": rule.delay_days,
            "probability": rule.probability,
            "severity_delta": rule.severity_delta,
            "initial_payload": rule.initial_payload,
            "inherit_payload_keys": rule.inherit_payload_keys,
            "conditions": [asdict(item) for item in rule.conditions],
            "world_effects": rule.world_effects,
            "max_depth": rule.max_depth,
            "max_fires_per_chain": rule.max_fires_per_chain,
            "priority": rule.priority,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        semantic_groups[signature].append(rule.rule_id)
    for ids in semantic_groups.values():
        if len(ids) > 1:
            issue("warning", "causal_analysis.duplicate_semantics", "Enabled rules have identical trigger semantics and will emit duplicate children.", rule_ids=ids)

    all_cycles = _cycle_components(edge_pairs)
    zero_cycles = _cycle_components(zero_delay_pairs)
    for component in all_cycles:
        component_rules = [
            rule.rule_id for rule in enabled
            if any(source in component and rule.child_event_type in component for source in rule.source_event_types)
        ]
        issue("warning", "causal_analysis.cycle", "Causal event types form a bounded cycle.", rule_ids=component_rules, types=component)
    for component in zero_cycles:
        component_rules = [
            rule.rule_id for rule in enabled
            if rule.delay_days == 0 and any(source in component and rule.child_event_type in component for source in rule.source_event_types)
        ]
        issue("warning", "causal_analysis.zero_delay_cycle", "A zero-delay cycle can consume the per-tick event budget; depth/fire limits remain mandatory.", rule_ids=component_rules, types=component)

    wildcard_immediate = [rule.rule_id for rule in enabled if "*" in rule.source_event_types and rule.delay_days == 0]
    if wildcard_immediate:
        issue("warning", "causal_analysis.immediate_wildcard_loop", "Zero-delay wildcard rules can trigger themselves through every emitted child.", rule_ids=wildcard_immediate)

    for source, count in sorted(source_counts.items()):
        if count > 8:
            ids = [rule.rule_id for rule in enabled if source in rule.source_event_types]
            issue("warning", "causal_analysis.high_fanout", f"Event type {source!r} fans out to {count} enabled rule edges.", rule_ids=ids, types=[source])

    produced_types = set(produced_by)
    for source in sorted({item for rule in enabled for item in rule.source_event_types if item != "*"}):
        if source not in seed_types and source not in produced_types:
            ids = [rule.rule_id for rule in enabled if source in rule.source_event_types]
            issue("info", "causal_analysis.external_source", f"Event type {source!r} has no authored producer and must be injected externally.", rule_ids=ids, types=[source])

    issue_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (issue_order[item["severity"]], item["code"], item["rule_ids"], item["event_types"]))
    graph_nodes = []
    for event_type in sorted(event_types):
        if event_type == "*":
            continue
        outgoing_rules = sorted(
            rule.rule_id for rule in enabled
            if event_type in rule.source_event_types or "*" in rule.source_event_types
        )
        graph_nodes.append({
            "event_type": event_type,
            "origins": sorted(origins.get(event_type, ())),
            "incoming_rules": sorted(produced_by.get(event_type, ())),
            "outgoing_rules": outgoing_rules,
            "outgoing_count": len(outgoing_rules),
        })

    fingerprint_source = json.dumps([rule.fingerprint() for rule in rules], separators=(",", ":"))
    return {
        "schema_version": "1.0",
        "fingerprint": hashlib.sha256(fingerprint_source.encode("ascii")).hexdigest(),
        "summary": {
            "rule_count": len(rules),
            "enabled_count": len(enabled),
            "event_type_count": len(graph_nodes),
            "edge_count": len(edges),
            "cycle_count": len(all_cycles),
            "world_effect_count": sum(len(rule.world_effects) for rule in rules),
            "anchored_transfer_count": sum(item.get("effect_type") == "transfer_anchored_value" for rule in rules for item in rule.world_effects),
            "ongoing_event_effect_count": sum(item.get("effect_type") == "create_ongoing_event" for rule in rules for item in rule.world_effects),
            "error_count": sum(item["severity"] == "error" for item in issues),
            "warning_count": sum(item["severity"] == "warning" for item in issues),
            "info_count": sum(item["severity"] == "info" for item in issues),
        },
        "nodes": graph_nodes,
        "edges": sorted(edges, key=lambda item: (item["source_event_type"], item["child_event_type"], -item["priority"], item["rule_id"])),
        "issues": issues,
    }


def _world_command_preview(command: Mapping[str, Any]) -> dict[str, Any]:
    payload = command.get("payload", {}) if isinstance(command.get("payload"), Mapping) else {}
    preview = {
        "command_type": str(command.get("command_type", "")),
        "entity_id": str(command.get("entity_id", "")),
    }
    for key in (
        "resource_name", "quantity", "quantity_delta", "new_status",
        "event_id", "event_type", "anchored_base_value", "anchor_rate",
        "transfer_side",
    ):
        if key in payload:
            preview[key] = payload[key]
    if "end_time" in payload:
        preview["end_day"] = float(payload["end_time"]) / GAME_SECONDS_PER_DAY
    return preview


def _event_payload(event: EventObject) -> dict[str, Any]:
    payload = dict(event.initial_payload) if isinstance(event.initial_payload, Mapping) else {}
    commands = event.world_commands if isinstance(event.world_commands, list) else []
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "target_agents": list(event.target_agents),
        "initial_payload": payload,
        "generated_at": event.generated_at,
        "day": event.generated_at / GAME_SECONDS_PER_DAY,
        "causal_depth": payload.get("causal_depth", 0),
        "world_command_count": len(commands),
        "world_commands": [_world_command_preview(item) for item in commands],
    }


def dry_run_causal_rules(
    content: Mapping[str, Any],
    source_event: Mapping[str, Any],
    *,
    horizon_days: float = 365.0,
    max_events: int = 128,
) -> dict[str, Any]:
    """Run an isolated deterministic event chain against DLC seed facts."""

    rules = _rules(content)
    if not isinstance(source_event, Mapping):
        raise CausalEventError("causal_dry_run_source_invalid")
    if isinstance(max_events, bool) or not isinstance(max_events, int) or not 1 <= max_events <= _MAX_DRY_RUN_EVENTS:
        raise CausalEventError("causal_dry_run_max_events_invalid")
    if isinstance(horizon_days, bool):
        raise CausalEventError("causal_dry_run_horizon_invalid")
    raw_severity = source_event.get("severity", 5)
    raw_generated_at = source_event.get("generated_at", 0.0)
    if isinstance(raw_severity, bool) or not isinstance(raw_severity, int) or isinstance(raw_generated_at, bool):
        raise CausalEventError("causal_dry_run_source_invalid")
    try:
        horizon = float(horizon_days)
        severity = raw_severity
        generated_at = float(raw_generated_at)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CausalEventError("causal_dry_run_source_invalid") from exc
    if not math.isfinite(horizon) or not 0 <= horizon <= _MAX_HORIZON_DAYS:
        raise CausalEventError("causal_dry_run_horizon_invalid")
    targets = source_event.get("target_agents", [])
    payload = source_event.get("initial_payload", {})
    if (
        not 1 <= severity <= 10
        or not math.isfinite(generated_at)
        or generated_at < 0
        or not isinstance(targets, list)
        or len(targets) > 256
        or any(not isinstance(item, str) or not item.strip() or len(item) > 128 for item in targets)
        or not isinstance(payload, Mapping)
        or len(payload) > 256
        or CAUSAL_METADATA_FIELDS.intersection(payload)
    ):
        raise CausalEventError("causal_dry_run_source_invalid")
    source = EventObject(
        event_id=str(source_event.get("event_id", "dry_run_source")).strip(),
        event_type=str(source_event.get("event_type", "")).strip(),
        severity=severity,
        target_agents=[str(item).strip() for item in targets],
        initial_payload=dict(payload),
        generated_at=generated_at,
    )
    if not source.event_id or len(source.event_id) > 128 or not source.event_type or len(source.event_type) > 128 or any(not item for item in source.target_agents):
        raise CausalEventError("causal_dry_run_source_invalid")

    engine = CausalEventEngine([asdict(rule) for rule in rules])
    facts = _SeedWorldFacts(content)
    action_catalog = ActionCatalog()
    action_catalog.configure(_list_field(content, "actions"), generation=0)
    actor_manager = ActorManager()
    actor_manager.configure(_list_field(content, "actors"), generation=0, preserve_state=False)
    issue_manager = IssueManager()
    issue_manager.configure(
        _list_field(content, "issue_types"), generation=0,
        action_ids=[item.get("action_id", "") for item in _list_field(content, "actions") if isinstance(item, Mapping)],
    )
    objective_manager = ObjectiveManager()
    objective_manager.configure(
        _list_field(content, "objectives"), _list_field(content, "endings"), generation=0,
    )
    gameplay_runtime = GameplayEffectRuntime()
    gameplay_runtime.refresh(facts)
    gameplay_runtime.sync_managers(
        actor_manager=actor_manager,
        issue_manager=issue_manager,
        objective_manager=objective_manager,
        action_catalog=action_catalog,
        sim_time=generated_at,
    )
    seed_history = tuple({
        "event_id": str(item.get("event_id", f"seed-{index}")),
        "event_type": str(item.get("event_type", "")),
        "generated_at": float(item.get("generated_at", 0.0) or 0.0),
        "target_agents": [str(target) for target in item.get("target_agents", [])],
    } for index, item in enumerate(_list_field(content, "initial_events")) if isinstance(item, Mapping))

    def condition_context(at: float) -> CausalConditionContext:
        return CausalConditionContext(
            world_state=facts,
            actor_manager=actor_manager,
            objective_manager=objective_manager,
            event_history=seed_history,
            sim_time=at,
            knowledge_provider=gameplay_runtime,
            issue_manager=issue_manager,
            action_catalog=action_catalog,
        )

    def apply_effect_batch(event: EventObject, at: float) -> tuple[bool, str]:
        facts_before = facts.snapshot()
        actor_before = actor_manager.snapshot()
        issue_before = issue_manager.snapshot()
        objective_before = objective_manager.snapshot()
        action_before = action_catalog.snapshot()
        ok, error = facts.apply_world_commands(
            event.world_commands, sim_time=at, event_id=event.event_id,
        )
        if not ok:
            return ok, error
        try:
            gameplay_runtime.refresh(facts)
            gameplay_runtime.sync_managers(
                actor_manager=actor_manager,
                issue_manager=issue_manager,
                objective_manager=objective_manager,
                action_catalog=action_catalog,
                sim_time=at,
            )
        except Exception as exc:
            facts.restore(facts_before)
            actor_manager.restore(actor_before)
            issue_manager.restore(issue_before, sim_time=at)
            objective_manager.restore(objective_before, sim_time=at)
            action_catalog.restore(action_before, sim_time=at)
            gameplay_runtime.refresh(facts)
            gameplay_runtime.sync_managers(
                actor_manager=actor_manager,
                issue_manager=issue_manager,
                objective_manager=objective_manager,
                action_catalog=action_catalog,
                sim_time=at,
            )
            return False, str(exc) or "causal_effect_projection_rejected"
        return True, ""

    nodes = [_event_payload(source)]
    edges: list[dict[str, Any]] = []
    node_ids = {source.event_id}
    engine.observe(source, sim_time=generated_at, condition_context=condition_context(generated_at))
    horizon_at = generated_at + horizon * GAME_SECONDS_PER_DAY

    while len(nodes) < max_events:
        snapshot = engine.snapshot()
        pending = snapshot["pending"]
        if not pending:
            break
        next_due = float(pending[0]["due_at"])
        if next_due > horizon_at:
            break
        result = engine.poll(next_due, max_events=max_events - len(nodes))
        if not result.events:
            break
        for event in result.events:
            payload_value = event.initial_payload if isinstance(event.initial_payload, Mapping) else {}
            parent_id = str(payload_value.get("causal_parent_event_id", ""))
            rule_id = str(payload_value.get("causal_rule_id", ""))
            effect_ok, effect_error = apply_effect_batch(event, next_due)
            if event.event_id not in node_ids:
                node = _event_payload(event)
                node["world_effect_status"] = "applied" if event.world_commands and effect_ok else "none" if not event.world_commands else "rejected"
                if effect_error:
                    node["world_effect_error_code"] = effect_error
                nodes.append(node)
                node_ids.add(event.event_id)
            edges.append({
                "source_event_id": parent_id,
                "target_event_id": event.event_id,
                "rule_id": rule_id,
                "delay_days": max(0.0, (event.generated_at - next((item["generated_at"] for item in nodes if item["event_id"] == parent_id), event.generated_at)) / GAME_SECONDS_PER_DAY),
            })
            if effect_ok:
                engine.observe(event, sim_time=next_due, condition_context=condition_context(next_due))

    remaining = engine.snapshot()["pending"]
    pending_due = sum(float(item["due_at"]) <= horizon_at for item in remaining)
    report = {
        "schema_version": "1.0",
        "analysis": analyze_causal_rules(content),
        "source_event": _event_payload(source),
        "horizon_days": horizon,
        "max_events": max_events,
        "summary": {
            "event_count": len(nodes),
            "generated_count": max(0, len(nodes) - 1),
            "edge_count": len(edges),
            "pending_count": len(remaining),
            "pending_within_horizon": pending_due,
            "truncated": len(nodes) >= max_events and pending_due > 0,
        },
        "nodes": nodes,
        "edges": edges,
        "pending": [
            {
                "event_id": item["event"]["event_id"],
                "event_type": item["event"]["event_type"],
                "due_day": float(item["due_at"]) / GAME_SECONDS_PER_DAY,
                "within_horizon": float(item["due_at"]) <= horizon_at,
            }
            for item in remaining[:_MAX_DRY_RUN_EVENTS]
        ],
    }
    report["replay_fixture"] = build_causal_replay_fixture(report)
    return report


def _causal_replay_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    analysis = report.get("analysis", {})
    if not isinstance(analysis, Mapping):
        raise CausalEventError("causal_replay_report_invalid")
    return {
        "analysis_fingerprint": str(analysis.get("fingerprint", "")),
        "analysis_summary": copy.deepcopy(analysis.get("summary", {})),
        "summary": copy.deepcopy(report.get("summary", {})),
        "nodes": copy.deepcopy(report.get("nodes", [])),
        "edges": copy.deepcopy(report.get("edges", [])),
        "pending": copy.deepcopy(report.get("pending", [])),
    }


def _causal_replay_digest(report: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _causal_replay_projection(report), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_causal_replay_fixture(report: Mapping[str, Any]) -> dict[str, Any]:
    """Build a deterministic, secret-free replay fixture from a dry-run report."""

    source = report.get("source_event", {})
    analysis = report.get("analysis", {})
    summary = report.get("summary", {})
    if not isinstance(source, Mapping) or not isinstance(analysis, Mapping) or not isinstance(summary, Mapping):
        raise CausalEventError("causal_replay_report_invalid")
    source_event = {
        "event_id": str(source.get("event_id", "")),
        "event_type": str(source.get("event_type", "")),
        "severity": source.get("severity", 5),
        "target_agents": copy.deepcopy(source.get("target_agents", [])),
        "initial_payload": copy.deepcopy(source.get("initial_payload", {})),
        "generated_at": source.get("generated_at", 0.0),
    }
    return {
        "format": CAUSAL_REPLAY_FIXTURE_FORMAT,
        "schema_version": _CAUSAL_REPLAY_SCHEMA_VERSION,
        "input": {
            "source_event": source_event,
            "horizon_days": report.get("horizon_days", 365.0),
            "max_events": report.get("max_events", 128),
        },
        "expected": {
            "analysis_fingerprint": str(analysis.get("fingerprint", "")),
            "result_digest": _causal_replay_digest(report),
            "summary": copy.deepcopy(dict(summary)),
        },
    }


def _normalize_causal_replay_fixture(fixture: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(fixture, Mapping):
        raise CausalEventError("causal_replay_fixture_invalid")
    if fixture.get("format") != CAUSAL_REPLAY_FIXTURE_FORMAT:
        raise CausalEventError("causal_replay_format_invalid")
    if fixture.get("schema_version") != _CAUSAL_REPLAY_SCHEMA_VERSION:
        raise CausalEventError("causal_replay_schema_invalid")
    replay_input = fixture.get("input")
    expected = fixture.get("expected")
    if not isinstance(replay_input, Mapping) or not isinstance(expected, Mapping):
        raise CausalEventError("causal_replay_fixture_invalid")
    source_event = replay_input.get("source_event")
    if not isinstance(source_event, Mapping):
        raise CausalEventError("causal_replay_fixture_invalid")
    result_digest = str(expected.get("result_digest", "")).lower()
    analysis_fingerprint = str(expected.get("analysis_fingerprint", "")).lower()
    if not _SHA256_HEX.fullmatch(result_digest) or not _SHA256_HEX.fullmatch(analysis_fingerprint):
        raise CausalEventError("causal_replay_digest_invalid")
    expected_summary = expected.get("summary")
    if not isinstance(expected_summary, Mapping):
        raise CausalEventError("causal_replay_fixture_invalid")
    return {
        "format": CAUSAL_REPLAY_FIXTURE_FORMAT,
        "schema_version": _CAUSAL_REPLAY_SCHEMA_VERSION,
        "input": {
            "source_event": copy.deepcopy(dict(source_event)),
            "horizon_days": replay_input.get("horizon_days", 365.0),
            "max_events": replay_input.get("max_events", 128),
        },
        "expected": {
            "analysis_fingerprint": analysis_fingerprint,
            "result_digest": result_digest,
            "summary": copy.deepcopy(dict(expected_summary)),
        },
    }


def replay_causal_fixture(content: Mapping[str, Any], fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Replay an exported fixture against current content and report deterministic drift."""

    normalized = _normalize_causal_replay_fixture(fixture)
    replay_input = normalized["input"]
    report = dry_run_causal_rules(
        content,
        replay_input["source_event"],
        horizon_days=replay_input["horizon_days"],
        max_events=replay_input["max_events"],
    )
    actual_digest = _causal_replay_digest(report)
    actual_analysis = str(report["analysis"]["fingerprint"])
    expected = normalized["expected"]
    drift_fields: list[str] = []
    if actual_analysis != expected["analysis_fingerprint"]:
        drift_fields.append("analysis_fingerprint")
    if actual_digest != expected["result_digest"]:
        drift_fields.append("result_digest")
    actual_summary = report["summary"]
    for key in sorted(set(expected["summary"]) | set(actual_summary)):
        if expected["summary"].get(key) != actual_summary.get(key):
            drift_fields.append(f"summary.{key}")
    return {
        "schema_version": _CAUSAL_REPLAY_SCHEMA_VERSION,
        "comparison": {
            "matches": not drift_fields,
            "analysis_matches": actual_analysis == expected["analysis_fingerprint"],
            "result_matches": actual_digest == expected["result_digest"],
            "expected_analysis_fingerprint": expected["analysis_fingerprint"],
            "actual_analysis_fingerprint": actual_analysis,
            "expected_result_digest": expected["result_digest"],
            "actual_result_digest": actual_digest,
            "drift_fields": drift_fields,
        },
        "report": report,
    }
