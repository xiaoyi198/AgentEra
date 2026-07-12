"""Static diagnostics and deterministic dry-runs for DLC causal event rules.

The helpers in this module never mutate the live orchestrator or world database.
They operate on an isolated rule engine and a seed-world projection built from a
DLC content document, making them safe for draft authoring and CI validation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
import hashlib
import json
import math
from typing import Any

from .causal_events import (
    CAUSAL_METADATA_FIELDS,
    CausalEventEngine,
    CausalEventError,
    CausalEventRule,
)
from .event_generator import GAME_SECONDS_PER_DAY
from .types import EventObject
from .world_state import EntityValue, InventoryItem

_MAX_DRY_RUN_EVENTS = 256
_MAX_HORIZON_DAYS = 3650.0


class _SeedWorldFacts:
    """Read-only world-state projection containing only facts used by conditions."""

    def __init__(self, content: Mapping[str, Any]) -> None:
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

    def get_inventory(self, entity_id: str) -> list[InventoryItem]:
        return list(self._inventory.get(entity_id, ()))

    def get_entity_values(self, entity_id: str, kind: str | None = None) -> list[EntityValue]:
        values = self._values.get(entity_id, ())
        return [item for item in values if kind is None or item.kind == kind]


def _list_field(content: Mapping[str, Any], name: str) -> list[Any]:
    value = content.get(name, [])
    if not isinstance(value, list):
        raise CausalEventError(f"causal_analysis_{name}_invalid")
    return value


def _rules(content: Mapping[str, Any]) -> list[CausalEventRule]:
    if not isinstance(content, Mapping):
        raise CausalEventError("causal_analysis_content_invalid")
    raw_rules = _list_field(content, "causal_event_rules")
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

    for rule in rules:
        for source in rule.source_event_types:
            edge = {**_rule_payload(rule), "source_event_type": source}
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
            if condition.entity_id not in entity_ids:
                issue("error", "causal_analysis.missing_entity", f"Rule {rule.rule_id!r} references unknown entity {condition.entity_id!r}.", rule_ids=[rule.rule_id])
            resource = resource_map.get(condition.name)
            expected_kind = "asset" if condition.kind == "anchored_inventory" else condition.kind
            if resource is None:
                issue("error", "causal_analysis.missing_resource", f"Rule {rule.rule_id!r} references unknown resource {condition.name!r}.", rule_ids=[rule.rule_id])
            elif str(resource.get("kind", "")) != expected_kind:
                issue("error", "causal_analysis.resource_kind", f"Rule {rule.rule_id!r} requires {condition.name!r} to have kind {expected_kind!r}.", rule_ids=[rule.rule_id])

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
        graph_nodes.append({
            "event_type": event_type,
            "origins": sorted(origins.get(event_type, ())),
            "incoming_rules": sorted(produced_by.get(event_type, ())),
            "outgoing_count": source_counts.get(event_type, 0) + source_counts.get("*", 0),
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
            "error_count": sum(item["severity"] == "error" for item in issues),
            "warning_count": sum(item["severity"] == "warning" for item in issues),
            "info_count": sum(item["severity"] == "info" for item in issues),
        },
        "nodes": graph_nodes,
        "edges": sorted(edges, key=lambda item: (item["source_event_type"], item["child_event_type"], -item["priority"], item["rule_id"])),
        "issues": issues,
    }


def _event_payload(event: EventObject) -> dict[str, Any]:
    payload = dict(event.initial_payload) if isinstance(event.initial_payload, Mapping) else {}
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "severity": event.severity,
        "target_agents": list(event.target_agents),
        "initial_payload": payload,
        "generated_at": event.generated_at,
        "day": event.generated_at / GAME_SECONDS_PER_DAY,
        "causal_depth": payload.get("causal_depth", 0),
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
    nodes = [_event_payload(source)]
    edges: list[dict[str, Any]] = []
    node_ids = {source.event_id}
    engine.observe(source, sim_time=generated_at, world_state=facts)
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
            if event.event_id not in node_ids:
                nodes.append(_event_payload(event))
                node_ids.add(event.event_id)
            edges.append({
                "source_event_id": parent_id,
                "target_event_id": event.event_id,
                "rule_id": rule_id,
                "delay_days": max(0.0, (event.generated_at - next((item["generated_at"] for item in nodes if item["event_id"] == parent_id), event.generated_at)) / GAME_SECONDS_PER_DAY),
            })
            engine.observe(event, sim_time=next_due, world_state=facts)

    remaining = engine.snapshot()["pending"]
    pending_due = sum(float(item["due_at"]) <= horizon_at for item in remaining)
    return {
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
