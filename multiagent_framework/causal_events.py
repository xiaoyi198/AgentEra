"""Deterministic, restart-safe causal world-event chains.

DLC rules are evaluated when an event is observed.  Matching rules materialize
complete child events immediately and queue them for a later simulation time.
This freezes committed semantics across DLC reloads while keeping future source
events on the newly published rule generation.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
from typing import Any

from .causal_conditions import (
    CausalCondition, CausalConditionContext, CausalEventError,
    compare_condition_value, resolve_condition_value,
)
from .event_generator import GAME_SECONDS_PER_DAY
from .types import EventObject

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CAUSAL_METADATA_FIELDS = frozenset({
    "causal_chain_id", "causal_parent_event_id", "causal_rule_id", "causal_depth"
})
_RESERVED_PAYLOAD = CAUSAL_METADATA_FIELDS
_MAX_OBSERVED_EVENTS = 10_000
_MAX_EVENT_HISTORY = 10_000
_MAX_CHAIN_STATES = 2_000
_MAX_PENDING_EVENTS = 10_000
_MAX_ID_LENGTH = 128
_MAX_TARGETS = 256
_MAX_PAYLOAD_KEYS = 256
_MAX_WORLD_EFFECTS = 32
_MAX_WORLD_COMMANDS = 64
_WORLD_EFFECT_TYPES = {
    "adjust_resource", "set_resource", "update_status",
    "create_ongoing_event", "transfer_anchored_value",
}


def _finite_effect_number(value: Any, code: str) -> float:
    if isinstance(value, bool):
        raise CausalEventError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CausalEventError(code) from exc
    if not math.isfinite(number):
        raise CausalEventError(code)
    return number


def _effect_text(value: Any, code: str, *, limit: int = _MAX_ID_LENGTH) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise CausalEventError(code)
    return text


def _normalize_expected_result(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > 64:
        raise CausalEventError("causal_effect_expected_result_invalid")
    result: dict[str, Any] = {}
    if "new_status" in value:
        status = str(value.get("new_status") or "")
        if len(status) > 512:
            raise CausalEventError("causal_effect_expected_result_invalid")
        if status:
            result["new_status"] = status
    deltas = value.get("inventory_deltas", [])
    if not isinstance(deltas, Sequence) or isinstance(deltas, (str, bytes, bytearray)) or len(deltas) > 32:
        raise CausalEventError("causal_effect_expected_result_invalid")
    normalized_deltas: list[dict[str, Any]] = []
    for raw in deltas:
        if not isinstance(raw, Mapping):
            raise CausalEventError("causal_effect_expected_result_invalid")
        resource = _effect_text(
            raw.get("resource", raw.get("resource_name", "")),
            "causal_effect_resource_invalid",
        )
        delta = _finite_effect_number(raw.get("delta"), "causal_effect_quantity_invalid")
        reason = str(raw.get("reason") or "")
        if len(reason) > 512:
            raise CausalEventError("causal_effect_reason_invalid")
        item = {"resource": resource, "delta": delta}
        if reason:
            item["reason"] = reason
        normalized_deltas.append(item)
    if normalized_deltas:
        result["inventory_deltas"] = normalized_deltas
    return result


def _normalize_world_effect(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CausalEventError("causal_effect_invalid")
    effect_type = str(value.get("effect_type", value.get("type", ""))).strip()
    if effect_type not in _WORLD_EFFECT_TYPES:
        raise CausalEventError("causal_effect_type_invalid")
    reason = str(value.get("reason") or "")
    if len(reason) > 512:
        raise CausalEventError("causal_effect_reason_invalid")

    if effect_type in {"adjust_resource", "set_resource"}:
        result = {
            "effect_type": effect_type,
            "entity_id": _effect_text(value.get("entity_id"), "causal_effect_entity_invalid"),
            "resource_name": _effect_text(value.get("resource_name"), "causal_effect_resource_invalid"),
        }
        number_key = "quantity_delta" if effect_type == "adjust_resource" else "quantity"
        result[number_key] = _finite_effect_number(value.get(number_key), "causal_effect_quantity_invalid")
        if reason:
            result["reason"] = reason
        return result

    if effect_type == "update_status":
        status = _effect_text(value.get("new_status"), "causal_effect_status_invalid", limit=512)
        return {
            "effect_type": effect_type,
            "entity_id": _effect_text(value.get("entity_id"), "causal_effect_entity_invalid"),
            "new_status": status,
        }

    if effect_type == "create_ongoing_event":
        event_type = _effect_text(value.get("event_type"), "causal_effect_event_type_invalid", limit=64)
        if not _SAFE_ID.fullmatch(event_type):
            raise CausalEventError("causal_effect_event_type_invalid")
        description = str(value.get("description") or "")
        if len(description) > 1000:
            raise CausalEventError("causal_effect_description_invalid")
        duration = _finite_effect_number(value.get("duration_days", 0), "causal_effect_duration_invalid")
        if not 0 <= duration <= 3650:
            raise CausalEventError("causal_effect_duration_invalid")
        return {
            "effect_type": effect_type,
            "entity_id": _effect_text(value.get("entity_id"), "causal_effect_entity_invalid"),
            "event_type": event_type,
            "description": description,
            "duration_days": duration,
            "expected_result": _normalize_expected_result(value.get("expected_result", {})),
        }

    base_value = _finite_effect_number(value.get("base_value"), "causal_effect_base_value_invalid")
    if base_value <= 0:
        raise CausalEventError("causal_effect_base_value_invalid")
    result = {
        "effect_type": effect_type,
        "from_entity_id": _effect_text(value.get("from_entity_id"), "causal_effect_entity_invalid"),
        "from_resource": _effect_text(value.get("from_resource"), "causal_effect_resource_invalid"),
        "to_entity_id": _effect_text(value.get("to_entity_id"), "causal_effect_entity_invalid"),
        "to_resource": _effect_text(value.get("to_resource"), "causal_effect_resource_invalid"),
        "base_value": base_value,
    }
    if reason:
        result["reason"] = reason
    return result


def normalize_world_effect(value: Mapping[str, Any]) -> dict[str, Any]:
    """Public deterministic normalizer shared by causal and player actions."""
    return _normalize_world_effect(value)


def normalize_world_effects(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise CausalEventError("world_effects_invalid")
    if len(values) > _MAX_WORLD_EFFECTS:
        raise CausalEventError("world_effects_invalid")
    return [normalize_world_effect(value) for value in values]


def _resource_anchor(world_state: Any, name: str) -> float:
    getter = getattr(world_state, "get_resource_definition", None)
    definition = getter(name) if callable(getter) else None
    if definition is None:
        raise CausalEventError("causal_effect_resource_unavailable")
    kind = definition.get("kind") if isinstance(definition, Mapping) else getattr(definition, "kind", None)
    rate = definition.get("anchor_rate") if isinstance(definition, Mapping) else getattr(definition, "anchor_rate", None)
    if kind != "asset":
        raise CausalEventError("causal_effect_resource_kind_invalid")
    anchor = _finite_effect_number(rate, "causal_effect_anchor_invalid")
    if anchor <= 0:
        raise CausalEventError("causal_effect_anchor_invalid")
    return anchor


def _materialize_world_commands(
    effects: Sequence[Mapping[str, Any]], *, world_state: Any, due_at: float,
    child_id: str, chain_id: str,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for index, effect in enumerate(effects):
        effect_type = str(effect["effect_type"])
        if effect_type in {"adjust_resource", "set_resource"}:
            number_key = "quantity_delta" if effect_type == "adjust_resource" else "quantity"
            payload = {
                "resource_name": effect["resource_name"],
                number_key: effect[number_key],
                "reason": effect.get("reason", ""),
            }
            commands.append({"command_type": effect_type, "entity_id": effect["entity_id"], "payload": payload})
        elif effect_type == "update_status":
            commands.append({
                "command_type": "update_status", "entity_id": effect["entity_id"],
                "payload": {"new_status": effect["new_status"]},
            })
        elif effect_type == "create_ongoing_event":
            ongoing_id = "ongoing_" + hashlib.sha256(f"{child_id}\0{index}".encode("utf-8")).hexdigest()[:24]
            commands.append({
                "command_type": "create_event", "entity_id": effect["entity_id"],
                "payload": {
                    "event_id": ongoing_id,
                    "event_type": effect["event_type"],
                    "description": effect.get("description", ""),
                    "start_time": due_at,
                    "end_time": due_at + float(effect["duration_days"]) * GAME_SECONDS_PER_DAY,
                    "expected_result": dict(effect.get("expected_result", {})),
                    "source_action_id": chain_id,
                },
            })
        else:
            from_anchor = _resource_anchor(world_state, str(effect["from_resource"]))
            to_anchor = _resource_anchor(world_state, str(effect["to_resource"]))
            base_value = float(effect["base_value"])
            transfer_id = "transfer_" + hashlib.sha256(
                f"{child_id}\0{index}".encode("utf-8")
            ).hexdigest()[:24]
            audit = {
                "reason": effect.get("reason", ""),
                "anchored_base_value": base_value,
                "anchor_rate": from_anchor,
                "transfer_side": "debit",
                "transfer_id": transfer_id,
            }
            commands.append({
                "command_type": "adjust_resource", "entity_id": effect["from_entity_id"],
                "payload": {
                    "resource_name": effect["from_resource"],
                    "quantity_delta": -(base_value / from_anchor),
                    **audit,
                },
            })
            commands.append({
                "command_type": "adjust_resource", "entity_id": effect["to_entity_id"],
                "payload": {
                    "resource_name": effect["to_resource"],
                    "quantity_delta": base_value / to_anchor,
                    "reason": effect.get("reason", ""),
                    "anchored_base_value": base_value,
                    "anchor_rate": to_anchor,
                    "transfer_side": "credit",
                    "transfer_id": transfer_id,
                },
            })
    if len(commands) > _MAX_WORLD_COMMANDS:
        raise CausalEventError("causal_effect_command_limit")
    return commands


def materialize_world_commands(
    effects: Sequence[Mapping[str, Any]], *, world_state: Any, due_at: float,
    source_id: str, chain_id: str,
) -> list[dict[str, Any]]:
    """Compile normalized world effects into immutable WorldCommand plans."""
    return _materialize_world_commands(
        effects, world_state=world_state, due_at=due_at,
        child_id=source_id, chain_id=chain_id,
    )


def _normalize_materialized_command(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CausalEventError("causal_snapshot_invalid")
    command_type = str(value.get("command_type", ""))
    if command_type not in {"adjust_resource", "set_resource", "update_status", "create_event"}:
        raise CausalEventError("causal_snapshot_invalid")
    entity_id = _effect_text(value.get("entity_id"), "causal_snapshot_invalid")
    payload = value.get("payload", {})
    if not isinstance(payload, Mapping) or len(payload) > 32:
        raise CausalEventError("causal_snapshot_invalid")
    if command_type in {"adjust_resource", "set_resource"}:
        number_key = "quantity_delta" if command_type == "adjust_resource" else "quantity"
        normalized_payload = {
            "resource_name": _effect_text(payload.get("resource_name"), "causal_snapshot_invalid"),
            number_key: _finite_effect_number(payload.get(number_key), "causal_snapshot_invalid"),
        }
        reason = str(payload.get("reason") or "")
        if len(reason) > 512:
            raise CausalEventError("causal_snapshot_invalid")
        if reason:
            normalized_payload["reason"] = reason
        for key in ("anchored_base_value", "anchor_rate"):
            if key in payload:
                normalized_payload[key] = _finite_effect_number(payload[key], "causal_snapshot_invalid")
        if payload.get("transfer_side") in {"debit", "credit"}:
            normalized_payload["transfer_side"] = payload["transfer_side"]
            transfer_id = str(payload.get("transfer_id") or "")
            if transfer_id:
                if len(transfer_id) > 64:
                    raise CausalEventError("causal_snapshot_invalid")
                normalized_payload["transfer_id"] = transfer_id
        return {"command_type": command_type, "entity_id": entity_id, "payload": normalized_payload}
    if command_type == "update_status":
        return {
            "command_type": command_type, "entity_id": entity_id,
            "payload": {"new_status": _effect_text(payload.get("new_status"), "causal_snapshot_invalid", limit=512)},
        }
    event_id = _effect_text(payload.get("event_id"), "causal_snapshot_invalid")
    event_type = _effect_text(payload.get("event_type"), "causal_snapshot_invalid", limit=64)
    description = str(payload.get("description") or "")
    source_action_id = str(payload.get("source_action_id") or "")
    if len(description) > 1000 or len(source_action_id) > _MAX_ID_LENGTH:
        raise CausalEventError("causal_snapshot_invalid")
    start_time = _finite_effect_number(payload.get("start_time"), "causal_snapshot_invalid")
    end_time = _finite_effect_number(payload.get("end_time"), "causal_snapshot_invalid")
    if start_time < 0 or end_time < start_time:
        raise CausalEventError("causal_snapshot_invalid")
    return {
        "command_type": command_type, "entity_id": entity_id,
        "payload": {
            "event_id": event_id, "event_type": event_type,
            "description": description, "start_time": start_time, "end_time": end_time,
            "expected_result": _normalize_expected_result(payload.get("expected_result", {})),
            "source_action_id": source_action_id,
        },
    }


@dataclass(frozen=True)
class CausalEventRule:
    rule_id: str
    source_event_types: tuple[str, ...]
    child_event_type: str
    target_agents: tuple[str, ...] = ()
    delay_days: float = 0.0
    probability: float = 1.0
    severity_delta: int = 0
    initial_payload: dict[str, Any] = field(default_factory=dict)
    inherit_payload_keys: tuple[str, ...] = ()
    conditions: tuple[CausalCondition, ...] = ()
    world_effects: tuple[dict[str, Any], ...] = ()
    max_depth: int = 4
    max_fires_per_chain: int = 1
    priority: int = 0
    enabled: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CausalEventRule":
        if not isinstance(value, Mapping):
            raise CausalEventError("causal_rule_invalid")
        source_types = value.get("source_event_types", [])
        targets = value.get("target_agents", [])
        inherit_keys = value.get("inherit_payload_keys", [])
        conditions = value.get("conditions", [])
        world_effects = value.get("world_effects", [])
        payload = value.get("initial_payload", {})
        for items, code in (
            (source_types, "causal_source_types_invalid"),
            (targets, "causal_targets_invalid"),
            (inherit_keys, "causal_inherit_keys_invalid"),
            (conditions, "causal_conditions_invalid"),
            (world_effects, "causal_effects_invalid"),
        ):
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
                raise CausalEventError(code)
        if not isinstance(payload, Mapping):
            raise CausalEventError("causal_payload_invalid")
        enabled = value.get("enabled", True)
        numeric = (
            value.get("delay_days", 0.0), value.get("probability", 1.0),
            value.get("severity_delta", 0), value.get("max_depth", 4),
            value.get("max_fires_per_chain", 1), value.get("priority", 0),
        )
        if not isinstance(enabled, bool) or any(isinstance(item, bool) for item in numeric):
            raise CausalEventError("causal_number_invalid")
        try:
            rule = cls(
                rule_id=str(value.get("rule_id", "")).strip(),
                source_event_types=tuple(str(item).strip() for item in source_types),
                child_event_type=str(value.get("child_event_type", "")).strip(),
                target_agents=tuple(str(item).strip() for item in targets),
                delay_days=float(numeric[0]), probability=float(numeric[1]),
                severity_delta=int(numeric[2]),
                initial_payload=dict(payload),
                inherit_payload_keys=tuple(str(item).strip() for item in inherit_keys),
                conditions=tuple(CausalCondition.from_mapping(item) for item in conditions),
                world_effects=tuple(_normalize_world_effect(item) for item in world_effects),
                max_depth=int(numeric[3]), max_fires_per_chain=int(numeric[4]),
                priority=int(numeric[5]), enabled=enabled,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise CausalEventError("causal_number_invalid") from exc
        rule.validate()
        return rule

    def validate(self) -> None:
        if not _SAFE_ID.fullmatch(self.rule_id):
            raise CausalEventError("causal_rule_id_invalid")
        if not self.source_event_types or any(
            item != "*" and not _SAFE_ID.fullmatch(item) for item in self.source_event_types
        ):
            raise CausalEventError("causal_source_types_invalid")
        if not _SAFE_ID.fullmatch(self.child_event_type):
            raise CausalEventError("causal_child_type_invalid")
        if any(not _SAFE_ID.fullmatch(item) for item in self.target_agents):
            raise CausalEventError("causal_targets_invalid")
        if not math.isfinite(self.delay_days) or self.delay_days < 0:
            raise CausalEventError("causal_delay_invalid")
        if not math.isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise CausalEventError("causal_probability_invalid")
        if not -9 <= self.severity_delta <= 9:
            raise CausalEventError("causal_severity_delta_invalid")
        if not 1 <= self.max_depth <= 16:
            raise CausalEventError("causal_max_depth_invalid")
        if not 1 <= self.max_fires_per_chain <= 64:
            raise CausalEventError("causal_max_fires_invalid")
        if any(not item or item in _RESERVED_PAYLOAD for item in self.inherit_payload_keys):
            raise CausalEventError("causal_inherit_keys_invalid")
        if _RESERVED_PAYLOAD.intersection(self.initial_payload):
            raise CausalEventError("causal_payload_reserved")
        if len(self.world_effects) > _MAX_WORLD_EFFECTS:
            raise CausalEventError("causal_effect_limit")

    def fingerprint(self) -> str:
        encoded = json.dumps(
            {
                **asdict(self),
                "conditions": [asdict(item) for item in self.conditions],
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CausalEventPollResult:
    events: tuple[EventObject, ...]
    due_count: int


@dataclass
class _PendingCausalEvent:
    due_at: float
    event: EventObject


class CausalEventEngine:
    """Evaluate causal rules and queue fully materialized child events."""

    def __init__(self, rules: Sequence[Mapping[str, Any]] = ()) -> None:
        self._rules: tuple[CausalEventRule, ...] = ()
        self._pending: dict[str, _PendingCausalEvent] = {}
        self._observed_order: deque[str] = deque()
        self._observed: set[str] = set()
        self._event_history: deque[dict[str, Any]] = deque()
        self._chain_counts: dict[str, dict[str, int]] = {}
        self._chain_order: deque[str] = deque()
        self._dropped_count = 0
        self.configure(rules)

    @staticmethod
    def _unit(rule_id: str, source_event_id: str, chain_id: str) -> float:
        digest = hashlib.sha256(
            f"world-simulator/causal-event/v1\0{rule_id}\0{source_event_id}\0{chain_id}".encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)

    def configure(self, rules: Sequence[Mapping[str, Any]]) -> None:
        parsed = [CausalEventRule.from_mapping(item) for item in rules]
        ids = [item.rule_id for item in parsed]
        if len(ids) != len(set(ids)):
            raise CausalEventError("causal_rule_duplicate")
        self._rules = tuple(sorted(parsed, key=lambda item: (-item.priority, item.rule_id)))

    @staticmethod
    def _conditions_match(rule: CausalEventRule, context: CausalConditionContext) -> bool:
        return all(
            compare_condition_value(
                resolve_condition_value(condition, context),
                condition.operator,
                condition.value,
            )
            for condition in rule.conditions
        )

    def _remember_observed(self, event_id: str) -> bool:
        if event_id in self._observed:
            return False
        self._observed.add(event_id)
        self._observed_order.append(event_id)
        while len(self._observed_order) > _MAX_OBSERVED_EVENTS:
            self._observed.discard(self._observed_order.popleft())
        return True

    def _remember_history(self, event: EventObject) -> None:
        self._event_history.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "generated_at": float(event.generated_at),
            "target_agents": list(event.target_agents),
        })
        while len(self._event_history) > _MAX_EVENT_HISTORY:
            self._event_history.popleft()

    def _chain_has_pending(self, chain_id: str) -> bool:
        return any(
            pending.event.initial_payload.get("causal_chain_id") == chain_id
            for pending in self._pending.values()
        )

    def _trim_chain_states(self) -> None:
        """Evict old completed chains without orphaning active pending chains."""
        scans = len(self._chain_order)
        while len(self._chain_order) > _MAX_CHAIN_STATES and scans > 0:
            old = self._chain_order.popleft()
            scans -= 1
            if self._chain_has_pending(old):
                self._chain_order.append(old)
                continue
            self._chain_counts.pop(old, None)

    def _counts_for(self, chain_id: str) -> dict[str, int]:
        if chain_id not in self._chain_counts:
            self._chain_counts[chain_id] = {}
            self._chain_order.append(chain_id)
            self._trim_chain_states()
        return self._chain_counts[chain_id]

    def observe(
        self,
        event: EventObject,
        *,
        sim_time: float,
        world_state: Any = None,
        condition_context: CausalConditionContext | None = None,
    ) -> int:
        targets = event.target_agents
        if (
            not isinstance(event.event_id, str)
            or not event.event_id
            or len(event.event_id) > _MAX_ID_LENGTH
            or not isinstance(event.event_type, str)
            or not event.event_type
            or len(event.event_type) > _MAX_ID_LENGTH
            or isinstance(event.severity, bool)
            or not isinstance(event.severity, int)
            or not 1 <= event.severity <= 10
            or not isinstance(targets, list)
            or len(targets) > _MAX_TARGETS
            or any(not isinstance(item, str) or not item or len(item) > _MAX_ID_LENGTH for item in targets)
            or isinstance(sim_time, bool)
            or not isinstance(sim_time, (int, float))
            or not math.isfinite(sim_time)
            or sim_time < 0
            or not self._remember_observed(event.event_id)
        ):
            return 0
        payload = event.initial_payload if isinstance(event.initial_payload, Mapping) else {}
        metadata_fields = CAUSAL_METADATA_FIELDS.intersection(payload)
        if metadata_fields:
            if metadata_fields != CAUSAL_METADATA_FIELDS:
                return 0
            raw_depth = payload.get("causal_depth")
            chain_id = payload.get("causal_chain_id")
            parent_id = payload.get("causal_parent_event_id")
            rule_id = payload.get("causal_rule_id")
            if (
                isinstance(raw_depth, bool)
                or not isinstance(raw_depth, int)
                or not 1 <= raw_depth <= 16
                or not isinstance(chain_id, str)
                or not re.fullmatch(r"cause_[0-9a-f]{16,24}", chain_id)
                or not isinstance(parent_id, str)
                or not parent_id
                or len(parent_id) > _MAX_ID_LENGTH
                or not isinstance(rule_id, str)
                or not _SAFE_ID.fullmatch(rule_id)
            ):
                return 0
            depth = raw_depth
        else:
            depth = 0
            chain_id = "cause_" + hashlib.sha256(event.event_id.encode("utf-8")).hexdigest()[:16]
        self._remember_history(event)
        provided = condition_context or CausalConditionContext()
        condition_world = world_state if world_state is not None else provided.world_state
        context = CausalConditionContext(
            world_state=condition_world,
            actor_manager=provided.actor_manager,
            objective_manager=provided.objective_manager,
            event_history=tuple(provided.event_history) + tuple(self._event_history),
            current_event=event,
            sim_time=float(sim_time),
            knowledge_provider=provided.knowledge_provider,
            issue_manager=provided.issue_manager,
            action_catalog=provided.action_catalog,
        )
        counts = self._counts_for(chain_id)
        scheduled = 0
        for rule in self._rules:
            if not rule.enabled or ("*" not in rule.source_event_types and event.event_type not in rule.source_event_types):
                continue
            next_depth = depth + 1
            if next_depth > rule.max_depth or counts.get(rule.rule_id, 0) >= rule.max_fires_per_chain:
                continue
            if not self._conditions_match(rule, context):
                continue
            if self._unit(rule.rule_id, event.event_id, chain_id) >= rule.probability:
                continue
            child_id = "cause_" + hashlib.sha256(
                f"{chain_id}\0{event.event_id}\0{rule.rule_id}\0{counts.get(rule.rule_id, 0)}".encode("utf-8")
            ).hexdigest()[:24]
            if child_id in self._pending or child_id in self._observed:
                continue
            if len(self._pending) >= _MAX_PENDING_EVENTS:
                self._dropped_count += 1
                continue
            child_payload = dict(rule.initial_payload)
            for key in rule.inherit_payload_keys:
                if key in payload:
                    child_payload[key] = payload[key]
            child_payload.update({
                "causal_chain_id": chain_id,
                "causal_parent_event_id": event.event_id,
                "causal_rule_id": rule.rule_id,
                "causal_depth": next_depth,
            })
            due_at = float(sim_time) + rule.delay_days * GAME_SECONDS_PER_DAY
            try:
                world_commands = _materialize_world_commands(
                    rule.world_effects, world_state=condition_world, due_at=due_at,
                    child_id=child_id, chain_id=chain_id,
                )
            except CausalEventError:
                self._dropped_count += 1
                continue
            child = EventObject(
                event_id=child_id,
                event_type=rule.child_event_type,
                severity=max(1, min(10, int(event.severity) + rule.severity_delta)),
                target_agents=list(rule.target_agents or tuple(event.target_agents)),
                initial_payload=child_payload,
                generated_at=due_at,
                world_commands=world_commands,
            )
            self._pending[child_id] = _PendingCausalEvent(child.generated_at, child)
            counts[rule.rule_id] = counts.get(rule.rule_id, 0) + 1
            scheduled += 1
        return scheduled

    def poll(self, now: float, *, max_events: int = 128) -> CausalEventPollResult:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events <= 0:
            raise CausalEventError("causal_poll_budget_invalid")
        due_ids = [
            event_id for event_id, pending in sorted(
                self._pending.items(), key=lambda item: (item[1].due_at, item[0])
            )
            if pending.due_at <= float(now)
        ][:max_events]
        events = tuple(self._pending.pop(event_id).event for event_id in due_ids)
        if events:
            self._trim_chain_states()
        return CausalEventPollResult(events=events, due_count=len(due_ids))

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "observed_event_ids": list(self._observed_order),
            "event_history": list(self._event_history),
            "chain_counts": {key: dict(value) for key, value in self._chain_counts.items()},
            "chain_order": list(self._chain_order),
            "dropped_count": self._dropped_count,
            "pending": [
                {"due_at": pending.due_at, "event": asdict(pending.event)}
                for _, pending in sorted(self._pending.items(), key=lambda item: (item[1].due_at, item[0]))
            ],
        }

    def restore(self, value: Mapping[str, Any], *, sim_time: float) -> None:
        if not isinstance(value, Mapping):
            raise CausalEventError("causal_snapshot_invalid")
        observed = value.get("observed_event_ids", [])
        history = value.get("event_history", [])
        pending = value.get("pending", [])
        counts = value.get("chain_counts", {})
        order = value.get("chain_order", [])
        dropped = value.get("dropped_count", 0)
        if (
            not isinstance(observed, list)
            or not isinstance(history, list)
            or not isinstance(pending, list)
            or not isinstance(counts, Mapping)
            or not isinstance(order, list)
            or isinstance(dropped, bool)
            or not isinstance(dropped, int)
            or dropped < 0
            or len(observed) > _MAX_OBSERVED_EVENTS
            or len(history) > _MAX_EVENT_HISTORY
            or len(pending) > _MAX_PENDING_EVENTS
            or len(counts) > _MAX_CHAIN_STATES + _MAX_PENDING_EVENTS
            or len(order) > _MAX_CHAIN_STATES + _MAX_PENDING_EVENTS
        ):
            raise CausalEventError("causal_snapshot_invalid")

        observed_ids: list[str] = []
        for item in observed:
            if not isinstance(item, str):
                raise CausalEventError("causal_snapshot_invalid")
            event_id = item
            if not event_id or len(event_id) > _MAX_ID_LENGTH:
                raise CausalEventError("causal_snapshot_invalid")
            observed_ids.append(event_id)

        history_records: list[dict[str, Any]] = []
        history_ids: set[str] = set()
        for item in history:
            if not isinstance(item, Mapping):
                raise CausalEventError("causal_snapshot_invalid")
            event_id = str(item.get("event_id", ""))
            event_type = str(item.get("event_type", ""))
            targets = item.get("target_agents", [])
            try:
                generated_at = float(item.get("generated_at", 0.0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise CausalEventError("causal_snapshot_invalid") from exc
            if (
                not event_id or len(event_id) > _MAX_ID_LENGTH
                or event_id in history_ids
                or not event_type or len(event_type) > _MAX_ID_LENGTH
                or not isinstance(targets, list) or len(targets) > _MAX_TARGETS
                or any(not isinstance(target, str) or not target or len(target) > _MAX_ID_LENGTH for target in targets)
                or not math.isfinite(generated_at) or generated_at < 0
            ):
                raise CausalEventError("causal_snapshot_invalid")
            history_ids.add(event_id)
            history_records.append({
                "event_id": event_id,
                "event_type": event_type,
                "generated_at": generated_at,
                "target_agents": list(targets),
            })

        new_pending: dict[str, _PendingCausalEvent] = {}
        for raw in pending:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("event"), Mapping):
                raise CausalEventError("causal_snapshot_invalid")
            event_value = raw["event"]
            targets = event_value.get("target_agents", [])
            payload = event_value.get("initial_payload", {})
            world_commands = event_value.get("world_commands", [])
            if (
                not isinstance(targets, list)
                or len(targets) > _MAX_TARGETS
                or not isinstance(payload, Mapping)
                or len(payload) > _MAX_PAYLOAD_KEYS
                or not isinstance(world_commands, list)
                or len(world_commands) > _MAX_WORLD_COMMANDS
            ):
                raise CausalEventError("causal_snapshot_invalid")
            try:
                event_id = str(event_value.get("event_id", ""))
                event_type = str(event_value.get("event_type", ""))
                severity = int(event_value.get("severity", 5))
                generated_at = float(event_value.get("generated_at", raw.get("due_at", sim_time)))
                due_at = float(raw.get("due_at", generated_at))
            except (TypeError, ValueError, OverflowError) as exc:
                raise CausalEventError("causal_snapshot_invalid") from exc
            target_ids = [str(item) for item in targets]
            if (
                not event_id
                or len(event_id) > _MAX_ID_LENGTH
                or not event_type
                or len(event_type) > _MAX_ID_LENGTH
                or not 1 <= severity <= 10
                or any(not item or len(item) > _MAX_ID_LENGTH for item in target_ids)
                or not math.isfinite(generated_at)
                or generated_at < 0
                or not math.isfinite(due_at)
                or due_at < 0
                or event_id in new_pending
            ):
                raise CausalEventError("causal_snapshot_invalid")
            event = EventObject(
                event_id=event_id,
                event_type=event_type,
                severity=severity,
                target_agents=target_ids,
                initial_payload=dict(payload),
                generated_at=generated_at,
                world_commands=[_normalize_materialized_command(item) for item in world_commands],
            )
            new_pending[event.event_id] = _PendingCausalEvent(due_at, event)

        new_counts: dict[str, dict[str, int]] = {}
        for raw_chain_id, raw_counts in counts.items():
            chain_id = str(raw_chain_id)
            if (
                not chain_id
                or len(chain_id) > _MAX_ID_LENGTH
                or not isinstance(raw_counts, Mapping)
                or len(raw_counts) > 256
            ):
                raise CausalEventError("causal_snapshot_invalid")
            parsed_counts: dict[str, int] = {}
            for raw_rule_id, raw_amount in raw_counts.items():
                rule_id = str(raw_rule_id)
                if (
                    not rule_id
                    or len(rule_id) > _MAX_ID_LENGTH
                    or isinstance(raw_amount, bool)
                    or not isinstance(raw_amount, int)
                    or not 0 <= raw_amount <= 64
                ):
                    raise CausalEventError("causal_snapshot_invalid")
                parsed_counts[rule_id] = raw_amount
            new_counts[chain_id] = parsed_counts

        order_ids = [str(item) for item in order]
        if any(
            not item or len(item) > _MAX_ID_LENGTH or item not in new_counts
            for item in order_ids
        ):
            raise CausalEventError("causal_snapshot_invalid")

        self._pending = new_pending
        self._observed_order = deque(observed_ids)
        self._observed = set(observed_ids)
        self._event_history = deque(history_records)
        self._chain_counts = new_counts
        self._chain_order = deque(dict.fromkeys(order_ids))
        for chain_id in new_counts:
            if chain_id not in self._chain_order:
                self._chain_order.append(chain_id)
        self._dropped_count = dropped
        self._trim_chain_states()

    def public_status(self) -> dict[str, Any]:
        return {
            "rule_count": len(self._rules),
            "enabled_count": sum(rule.enabled for rule in self._rules),
            "pending_count": len(self._pending),
            "pending_world_command_count": sum(len(item.event.world_commands) for item in self._pending.values()),
            "effect_rule_count": sum(bool(rule.world_effects) for rule in self._rules),
            "observed_count": len(self._observed),
            "dropped_count": self._dropped_count,
            "next_due": min((item.due_at for item in self._pending.values()), default=None),
            "fingerprint": hashlib.sha256("".join(rule.fingerprint() for rule in self._rules).encode("ascii")).hexdigest(),
        }
