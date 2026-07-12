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

from .event_generator import GAME_SECONDS_PER_DAY
from .types import EventObject

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_OPERATORS = {"lt", "lte", "eq", "gte", "gt"}
_FACT_KINDS = {"anchored_inventory", "metric", "parameter"}
CAUSAL_METADATA_FIELDS = frozenset({
    "causal_chain_id", "causal_parent_event_id", "causal_rule_id", "causal_depth"
})
_RESERVED_PAYLOAD = CAUSAL_METADATA_FIELDS
_MAX_OBSERVED_EVENTS = 10_000
_MAX_CHAIN_STATES = 2_000
_MAX_PENDING_EVENTS = 10_000
_MAX_ID_LENGTH = 128
_MAX_TARGETS = 256
_MAX_PAYLOAD_KEYS = 256


class CausalEventError(ValueError):
    """Raised when causal rules or persisted scheduler state are invalid."""


@dataclass(frozen=True)
class CausalCondition:
    kind: str
    entity_id: str
    name: str
    operator: str
    value: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CausalCondition":
        if not isinstance(value, Mapping):
            raise CausalEventError("causal_condition_invalid")
        raw_number = value.get("value")
        if isinstance(raw_number, bool):
            raise CausalEventError("causal_condition_value_invalid")
        try:
            number = float(raw_number)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CausalEventError("causal_condition_value_invalid") from exc
        condition = cls(
            kind=str(value.get("kind", "")).strip(),
            entity_id=str(value.get("entity_id", "")).strip(),
            name=str(value.get("name", "")).strip(),
            operator=str(value.get("operator", "")).strip(),
            value=number,
        )
        if condition.kind not in _FACT_KINDS:
            raise CausalEventError("causal_condition_kind_invalid")
        if not condition.entity_id or not condition.name:
            raise CausalEventError("causal_condition_reference_invalid")
        if condition.operator not in _OPERATORS:
            raise CausalEventError("causal_condition_operator_invalid")
        if not math.isfinite(condition.value):
            raise CausalEventError("causal_condition_value_invalid")
        return condition


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
        payload = value.get("initial_payload", {})
        for items, code in (
            (source_types, "causal_source_types_invalid"),
            (targets, "causal_targets_invalid"),
            (inherit_keys, "causal_inherit_keys_invalid"),
            (conditions, "causal_conditions_invalid"),
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
    def _fact(world_state: Any, condition: CausalCondition) -> float | None:
        if world_state is None:
            return None
        if condition.kind == "anchored_inventory":
            items = world_state.get_inventory(condition.entity_id)
            matches = [item.base_value for item in items if item.item_name == condition.name]
            return sum(matches) if matches else None
        values = world_state.get_entity_values(condition.entity_id, condition.kind)
        for item in values:
            if item.name == condition.name:
                return float(item.value)
        return None

    @classmethod
    def _conditions_match(cls, rule: CausalEventRule, world_state: Any) -> bool:
        for condition in rule.conditions:
            actual = cls._fact(world_state, condition)
            if actual is None or not math.isfinite(actual):
                return False
            expected = condition.value
            if not {
                "lt": actual < expected, "lte": actual <= expected,
                "eq": math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9),
                "gte": actual >= expected, "gt": actual > expected,
            }[condition.operator]:
                return False
        return True

    def _remember_observed(self, event_id: str) -> bool:
        if event_id in self._observed:
            return False
        self._observed.add(event_id)
        self._observed_order.append(event_id)
        while len(self._observed_order) > _MAX_OBSERVED_EVENTS:
            self._observed.discard(self._observed_order.popleft())
        return True

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

    def observe(self, event: EventObject, *, sim_time: float, world_state: Any = None) -> int:
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
        counts = self._counts_for(chain_id)
        scheduled = 0
        for rule in self._rules:
            if not rule.enabled or ("*" not in rule.source_event_types and event.event_type not in rule.source_event_types):
                continue
            next_depth = depth + 1
            if next_depth > rule.max_depth or counts.get(rule.rule_id, 0) >= rule.max_fires_per_chain:
                continue
            if not self._conditions_match(rule, world_state):
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
            child = EventObject(
                event_id=child_id,
                event_type=rule.child_event_type,
                severity=max(1, min(10, int(event.severity) + rule.severity_delta)),
                target_agents=list(rule.target_agents or tuple(event.target_agents)),
                initial_payload=child_payload,
                generated_at=float(sim_time) + rule.delay_days * GAME_SECONDS_PER_DAY,
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
            "schema_version": "1.0",
            "observed_event_ids": list(self._observed_order),
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
        pending = value.get("pending", [])
        counts = value.get("chain_counts", {})
        order = value.get("chain_order", [])
        dropped = value.get("dropped_count", 0)
        if (
            not isinstance(observed, list)
            or not isinstance(pending, list)
            or not isinstance(counts, Mapping)
            or not isinstance(order, list)
            or isinstance(dropped, bool)
            or not isinstance(dropped, int)
            or dropped < 0
            or len(observed) > _MAX_OBSERVED_EVENTS
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

        new_pending: dict[str, _PendingCausalEvent] = {}
        for raw in pending:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("event"), Mapping):
                raise CausalEventError("causal_snapshot_invalid")
            event_value = raw["event"]
            targets = event_value.get("target_agents", [])
            payload = event_value.get("initial_payload", {})
            if (
                not isinstance(targets, list)
                or len(targets) > _MAX_TARGETS
                or not isinstance(payload, Mapping)
                or len(payload) > _MAX_PAYLOAD_KEYS
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
            "observed_count": len(self._observed),
            "dropped_count": self._dropped_count,
            "next_due": min((item.due_at for item in self._pending.values()), default=None),
            "fingerprint": hashlib.sha256("".join(rule.fingerprint() for rule in self._rules).encode("ascii")).hexdigest(),
        }
