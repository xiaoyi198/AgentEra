"""DLC-authored dynamic actor, agent, position, and organization lifecycles.

The contract is declarative and generation-bound.  It describes deterministic
transitions; authoritative mutation is performed later by ``LifecycleRuntime``
through the World Command bus.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import string
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .causal_conditions import CausalCondition, CausalEventError


MAX_ACTOR_TEMPLATES = 256
MAX_POSITIONS = 512
MAX_TRANSITIONS = 1024
MAX_OPERATIONS = 64
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REFERENCE_ID = re.compile(r"^[^\s/\\\x00-\x1f]{1,128}$")
LIFECYCLE_KINDS = {
    "birth", "death", "promotion", "succession", "vacancy",
    "organization_split", "organization_merge", "entity_transform", "custom",
}
TRIGGERS = {
    "condition", "manual", "day_start", "week_start", "month_start", "year_start",
    "season_start", "season_end", "cycle_start", "cycle_end",
}
OPERATION_KINDS = {
    "spawn_actor", "retire_actor", "appoint_position", "vacate_position",
    "transform_entity", "split_organization", "merge_organizations",
}
SCORE_SOURCES = {"attribute", "state", "membership_rank", "actor_id"}
_PATTERN_FIELDS = {"sequence", "transition_id", "template_id", "actor_id"}


class LifecycleContractError(ValueError):
    def __init__(self, code: str, *, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(code)


def _mapping(value: Any, code: str, path: str, *, limit: int = 256) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or len(value) > limit:
        raise LifecycleContractError(code, path=path)
    return value


def _sequence(value: Any, code: str, path: str, *, limit: int = 1024) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) > limit:
        raise LifecycleContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 1000) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise LifecycleContractError(code, path=path)
    if any(ord(char) < 32 and char not in "\n\t" for char in result):
        raise LifecycleContractError(code, path=path)
    return result


def _identifier(value: Any, code: str, path: str, *, required: bool = True) -> str:
    result = _text(value, code, path, required=required, limit=128)
    if result and not _SAFE_ID.fullmatch(result):
        raise LifecycleContractError(code, path=path)
    return result


def _reference(value: Any, code: str, path: str, *, required: bool = True) -> str:
    result = _text(value, code, path, required=required, limit=128)
    if result and not _REFERENCE_ID.fullmatch(result):
        raise LifecycleContractError(code, path=path)
    return result


def _integer(value: Any, code: str, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise LifecycleContractError(code, path=path)
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LifecycleContractError(code, path=path) from exc
    if result < minimum or result > maximum:
        raise LifecycleContractError(code, path=path)
    return result


def _number(value: Any, code: str, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise LifecycleContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LifecycleContractError(code, path=path) from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise LifecycleContractError(code, path=path)
    return result


def _safe_json(value: Any, code: str, path: str, depth: int = 0) -> Any:
    if depth > 5:
        raise LifecycleContractError(code, path=path)
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > 4000:
            raise LifecycleContractError(code, path=path)
        return copy.deepcopy(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise LifecycleContractError(code, path=path)
        return copy.deepcopy(value)
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise LifecycleContractError(code, path=path)
        return {
            _text(key, code, f"{path}.{key}", limit=128): _safe_json(item, code, f"{path}.{key}", depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 256:
            raise LifecycleContractError(code, path=path)
        return [_safe_json(item, code, f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]
    raise LifecycleContractError(code, path=path)


def _pattern(value: Any, code: str, path: str, *, default: str = "") -> str:
    result = _text(value if value is not None else default, code, path, required=True, limit=256)
    try:
        fields = [name for _, name, _, _ in string.Formatter().parse(result) if name]
    except ValueError as exc:
        raise LifecycleContractError(code, path=path) from exc
    if any(name not in _PATTERN_FIELDS for name in fields):
        raise LifecycleContractError(code, path=path)
    return result


def normalize_lifecycle_document(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("DLC content field 'lifecycle' must be an object")
    return copy.deepcopy(dict(value))


@dataclass(frozen=True)
class LifecycleActorTemplate:
    template_id: str
    display_name: str
    actor: dict[str, Any]
    entity: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "LifecycleActorTemplate":
        raw = _mapping(value, "lifecycle.actor_template.invalid", path)
        template_id = _identifier(raw.get("template_id"), "lifecycle.actor_template.invalid_id", f"{path}.template_id")
        actor = dict(_mapping(raw.get("actor", {}), "lifecycle.actor_template.invalid_actor", f"{path}.actor"))
        actor.pop("actor_id", None)
        if not actor.get("display_name"):
            actor["display_name"] = str(raw.get("display_name") or template_id)
        entity = dict(_mapping(raw.get("entity", {}), "lifecycle.actor_template.invalid_entity", f"{path}.entity"))
        agent = dict(_mapping(raw.get("agent", {}), "lifecycle.actor_template.invalid_agent", f"{path}.agent"))
        if agent:
            agent["source_agent_id"] = _reference(agent.get("source_agent_id"), "lifecycle.actor_template.invalid_source_agent", f"{path}.agent.source_agent_id")
            if "system_prompt" in agent:
                agent["system_prompt"] = _text(agent.get("system_prompt"), "lifecycle.actor_template.invalid_prompt", f"{path}.agent.system_prompt", required=False, limit=12000)
        return cls(
            template_id=template_id,
            display_name=_text(raw.get("display_name", template_id), "lifecycle.actor_template.invalid_name", f"{path}.display_name", limit=128),
            actor=_safe_json(actor, "lifecycle.actor_template.invalid_actor", f"{path}.actor"),
            entity=_safe_json(entity, "lifecycle.actor_template.invalid_entity", f"{path}.entity"),
            agent=_safe_json(agent, "lifecycle.actor_template.invalid_agent", f"{path}.agent"),
            metadata=_safe_json(raw.get("metadata", {}), "lifecycle.actor_template.invalid_metadata", f"{path}.metadata"),
        )


@dataclass(frozen=True)
class LifecycleScoreField:
    source: str
    name: str = ""
    weight: float = 1.0
    direction: str = "desc"

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "LifecycleScoreField":
        raw = _mapping(value, "lifecycle.position.invalid_score", path)
        source = _identifier(raw.get("source", "attribute"), "lifecycle.position.invalid_score_source", f"{path}.source")
        if source not in SCORE_SOURCES:
            raise LifecycleContractError("lifecycle.position.invalid_score_source", path=f"{path}.source")
        direction = _identifier(raw.get("direction", "desc"), "lifecycle.position.invalid_score_direction", f"{path}.direction")
        if direction not in {"asc", "desc"}:
            raise LifecycleContractError("lifecycle.position.invalid_score_direction", path=f"{path}.direction")
        name = _identifier(raw.get("name", ""), "lifecycle.position.invalid_score_name", f"{path}.name", required=source == "attribute")
        return cls(source=source, name=name, weight=_number(raw.get("weight", 1), "lifecycle.position.invalid_score_weight", f"{path}.weight", -1000, 1000), direction=direction)


@dataclass(frozen=True)
class LifecyclePosition:
    position_id: str
    display_name: str
    organization_id: str
    role: str
    rank: float | None = None
    capacity: int = 1
    incumbent_actor_ids: tuple[str, ...] = ()
    candidate_actor_ids: tuple[str, ...] = ()
    scores: tuple[LifecycleScoreField, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "LifecyclePosition":
        raw = _mapping(value, "lifecycle.position.invalid", path)
        incumbents = tuple(_reference(item, "lifecycle.position.invalid_incumbent", f"{path}.incumbent_actor_ids[{index}]") for index, item in enumerate(_sequence(raw.get("incumbent_actor_ids", []), "lifecycle.position.invalid_incumbents", f"{path}.incumbent_actor_ids", limit=128)))
        candidates = tuple(_reference(item, "lifecycle.position.invalid_candidate", f"{path}.candidate_actor_ids[{index}]") for index, item in enumerate(_sequence(raw.get("candidate_actor_ids", []), "lifecycle.position.invalid_candidates", f"{path}.candidate_actor_ids", limit=512)))
        if len(incumbents) != len(set(incumbents)) or len(candidates) != len(set(candidates)):
            raise LifecycleContractError("lifecycle.position.duplicate_actor", path=path)
        capacity = _integer(raw.get("capacity", 1), "lifecycle.position.invalid_capacity", f"{path}.capacity", 1, 128)
        if len(incumbents) > capacity:
            raise LifecycleContractError("lifecycle.position.capacity_exceeded", path=f"{path}.incumbent_actor_ids")
        scores = tuple(LifecycleScoreField.from_mapping(item, f"{path}.scores[{index}]") for index, item in enumerate(_sequence(raw.get("scores", []), "lifecycle.position.invalid_scores", f"{path}.scores", limit=16)))
        rank = raw.get("rank")
        return cls(
            position_id=_identifier(raw.get("position_id"), "lifecycle.position.invalid_id", f"{path}.position_id"),
            display_name=_text(raw.get("display_name", raw.get("position_id", "")), "lifecycle.position.invalid_name", f"{path}.display_name", limit=128),
            organization_id=_reference(raw.get("organization_id"), "lifecycle.position.invalid_organization", f"{path}.organization_id"),
            role=_identifier(raw.get("role", "member"), "lifecycle.position.invalid_role", f"{path}.role"),
            rank=None if rank is None else _number(rank, "lifecycle.position.invalid_rank", f"{path}.rank", -1e9, 1e9),
            capacity=capacity,
            incumbent_actor_ids=incumbents,
            candidate_actor_ids=candidates,
            scores=scores,
            metadata=_safe_json(raw.get("metadata", {}), "lifecycle.position.invalid_metadata", f"{path}.metadata"),
        )


@dataclass(frozen=True)
class LifecycleOperation:
    operation: str
    data: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "LifecycleOperation":
        raw = dict(_mapping(value, "lifecycle.operation.invalid", path))
        operation = _identifier(raw.pop("operation", ""), "lifecycle.operation.invalid_type", f"{path}.operation")
        if operation not in OPERATION_KINDS:
            raise LifecycleContractError("lifecycle.operation.invalid_type", path=f"{path}.operation")
        data = _safe_json(raw, "lifecycle.operation.invalid_payload", path)
        required: dict[str, tuple[str, ...]] = {
            "spawn_actor": ("template_id",),
            "retire_actor": (),
            "appoint_position": ("position_id",),
            "vacate_position": ("position_id",),
            "transform_entity": ("entity_id",),
            "split_organization": ("source_organization_id", "destinations"),
            "merge_organizations": ("source_organization_ids", "target_organization_id"),
        }
        if operation == "retire_actor" and not (data.get("actor_id") or data.get("position_id")):
            raise LifecycleContractError("lifecycle.operation.target_required", path=path)
        for name in required[operation]:
            if not data.get(name):
                raise LifecycleContractError("lifecycle.operation.field_required", path=f"{path}.{name}")
        if operation == "spawn_actor":
            data["template_id"] = _identifier(data.get("template_id"), "lifecycle.operation.invalid_template", f"{path}.template_id")
            data["actor_id_pattern"] = _pattern(data.get("actor_id_pattern"), "lifecycle.operation.invalid_pattern", f"{path}.actor_id_pattern", default="{template_id}_{sequence}")
            if data.get("entity_id_pattern"):
                data["entity_id_pattern"] = _pattern(data.get("entity_id_pattern"), "lifecycle.operation.invalid_pattern", f"{path}.entity_id_pattern")
            if data.get("agent_id_pattern"):
                data["agent_id_pattern"] = _pattern(data.get("agent_id_pattern"), "lifecycle.operation.invalid_pattern", f"{path}.agent_id_pattern")
        return cls(operation=operation, data=data)

    def to_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, **copy.deepcopy(self.data)}


@dataclass(frozen=True)
class LifecycleTransition:
    transition_id: str
    display_name: str
    kind: str
    trigger: str
    cycle_id: str = ""
    season_id: str = ""
    month_id: str = ""
    day: int | None = None
    mode: str = "all"
    conditions: tuple[CausalCondition, ...] = ()
    once: bool = False
    cooldown_days: float = 0.0
    edge_triggered: bool = True
    enabled: bool = True
    operations: tuple[LifecycleOperation, ...] = ()
    feedback: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "LifecycleTransition":
        raw = _mapping(value, "lifecycle.transition.invalid", path)
        kind = _identifier(raw.get("kind", "custom"), "lifecycle.transition.invalid_kind", f"{path}.kind")
        trigger = _identifier(raw.get("trigger", "condition"), "lifecycle.transition.invalid_trigger", f"{path}.trigger")
        if kind not in LIFECYCLE_KINDS:
            raise LifecycleContractError("lifecycle.transition.invalid_kind", path=f"{path}.kind")
        if trigger not in TRIGGERS:
            raise LifecycleContractError("lifecycle.transition.invalid_trigger", path=f"{path}.trigger")
        mode = _identifier(raw.get("mode", "all"), "lifecycle.transition.invalid_mode", f"{path}.mode")
        if mode not in {"all", "any"}:
            raise LifecycleContractError("lifecycle.transition.invalid_mode", path=f"{path}.mode")
        try:
            conditions = tuple(CausalCondition.from_mapping(item) for item in _sequence(raw.get("conditions", []), "lifecycle.transition.invalid_conditions", f"{path}.conditions", limit=64))
        except CausalEventError as exc:
            raise LifecycleContractError(str(exc), path=f"{path}.conditions") from exc
        operations = tuple(LifecycleOperation.from_mapping(item, f"{path}.operations[{index}]") for index, item in enumerate(_sequence(raw.get("operations", []), "lifecycle.transition.invalid_operations", f"{path}.operations", limit=MAX_OPERATIONS)))
        if not operations:
            raise LifecycleContractError("lifecycle.transition.operations_required", path=f"{path}.operations")
        for flag in ("once", "edge_triggered", "enabled"):
            if flag in raw and not isinstance(raw[flag], bool):
                raise LifecycleContractError("lifecycle.transition.invalid_flag", path=f"{path}.{flag}")
        day = raw.get("day")
        return cls(
            transition_id=_identifier(raw.get("transition_id"), "lifecycle.transition.invalid_id", f"{path}.transition_id"),
            display_name=_text(raw.get("display_name", raw.get("transition_id", "")), "lifecycle.transition.invalid_name", f"{path}.display_name", limit=128),
            kind=kind,
            trigger=trigger,
            cycle_id=_identifier(raw.get("cycle_id", ""), "lifecycle.transition.invalid_cycle", f"{path}.cycle_id", required=False),
            season_id=_identifier(raw.get("season_id", ""), "lifecycle.transition.invalid_season", f"{path}.season_id", required=False),
            month_id=_identifier(raw.get("month_id", ""), "lifecycle.transition.invalid_month", f"{path}.month_id", required=False),
            day=None if day is None else _integer(day, "lifecycle.transition.invalid_day", f"{path}.day", 1, 100000),
            mode=mode,
            conditions=conditions,
            once=bool(raw.get("once", False)),
            cooldown_days=_number(raw.get("cooldown_days", 0), "lifecycle.transition.invalid_cooldown", f"{path}.cooldown_days", 0, 365000),
            edge_triggered=bool(raw.get("edge_triggered", True)),
            enabled=bool(raw.get("enabled", True)),
            operations=operations,
            feedback=_safe_json(raw.get("feedback", {}), "lifecycle.transition.invalid_feedback", f"{path}.feedback"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id, "display_name": self.display_name,
            "kind": self.kind, "trigger": self.trigger, "cycle_id": self.cycle_id,
            "season_id": self.season_id, "month_id": self.month_id, "day": self.day,
            "mode": self.mode, "conditions": [asdict(item) for item in self.conditions],
            "once": self.once, "cooldown_days": self.cooldown_days,
            "edge_triggered": self.edge_triggered, "enabled": self.enabled,
            "operations": [item.to_dict() for item in self.operations],
            "feedback": copy.deepcopy(self.feedback),
        }


@dataclass(frozen=True)
class LifecycleContract:
    enabled: bool = False
    actor_templates: tuple[LifecycleActorTemplate, ...] = ()
    positions: tuple[LifecyclePosition, ...] = ()
    transitions: tuple[LifecycleTransition, ...] = ()
    recent_feedback_limit: int = 50

    @classmethod
    def from_mapping(cls, value: Any) -> "LifecycleContract":
        raw = _mapping(value or {}, "lifecycle.invalid", "lifecycle")
        enabled = raw.get("enabled", bool(raw.get("actor_templates") or raw.get("positions") or raw.get("transitions")))
        if not isinstance(enabled, bool):
            raise LifecycleContractError("lifecycle.invalid_enabled", path="lifecycle.enabled")
        templates = tuple(LifecycleActorTemplate.from_mapping(item, f"lifecycle.actor_templates[{index}]") for index, item in enumerate(_sequence(raw.get("actor_templates", []), "lifecycle.invalid_actor_templates", "lifecycle.actor_templates", limit=MAX_ACTOR_TEMPLATES)))
        positions = tuple(LifecyclePosition.from_mapping(item, f"lifecycle.positions[{index}]") for index, item in enumerate(_sequence(raw.get("positions", []), "lifecycle.invalid_positions", "lifecycle.positions", limit=MAX_POSITIONS)))
        transitions = tuple(LifecycleTransition.from_mapping(item, f"lifecycle.transitions[{index}]") for index, item in enumerate(_sequence(raw.get("transitions", []), "lifecycle.invalid_transitions", "lifecycle.transitions", limit=MAX_TRANSITIONS)))
        for values, attr, code in ((templates, "template_id", "lifecycle.duplicate_actor_template"), (positions, "position_id", "lifecycle.duplicate_position"), (transitions, "transition_id", "lifecycle.duplicate_transition")):
            ids = [getattr(item, attr) for item in values]
            if len(ids) != len(set(ids)):
                raise LifecycleContractError(code, path="lifecycle")
        template_ids = {item.template_id for item in templates}
        position_ids = {item.position_id for item in positions}
        for t_index, transition in enumerate(transitions):
            for o_index, operation in enumerate(transition.operations):
                if operation.operation == "spawn_actor" and operation.data.get("template_id") not in template_ids:
                    raise LifecycleContractError("lifecycle.operation.missing_template", path=f"lifecycle.transitions[{t_index}].operations[{o_index}].template_id")
                if operation.operation in {"appoint_position", "vacate_position"} and operation.data.get("position_id") not in position_ids:
                    raise LifecycleContractError("lifecycle.operation.missing_position", path=f"lifecycle.transitions[{t_index}].operations[{o_index}].position_id")
                if operation.operation == "retire_actor" and operation.data.get("position_id") and operation.data.get("position_id") not in position_ids:
                    raise LifecycleContractError("lifecycle.operation.missing_position", path=f"lifecycle.transitions[{t_index}].operations[{o_index}].position_id")
        return cls(enabled=enabled, actor_templates=templates, positions=positions, transitions=transitions, recent_feedback_limit=_integer(raw.get("recent_feedback_limit", 50), "lifecycle.invalid_feedback_limit", "lifecycle.recent_feedback_limit", 1, 500))

    @property
    def configured(self) -> bool:
        return bool(self.enabled and (self.actor_templates or self.positions or self.transitions))

    @property
    def template_map(self) -> dict[str, LifecycleActorTemplate]:
        return {item.template_id: item for item in self.actor_templates}

    @property
    def position_map(self) -> dict[str, LifecyclePosition]:
        return {item.position_id: item for item in self.positions}

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "actor_templates": [asdict(item) for item in self.actor_templates],
            "positions": [asdict(item) for item in self.positions],
            "transitions": [item.to_dict() for item in self.transitions],
            "recent_feedback_limit": self.recent_feedback_limit,
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def public_state(self, *, generation: int = 0, runtime_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        runtime = runtime_state if isinstance(runtime_state, Mapping) else {}
        positions = copy.deepcopy(dict(runtime.get("positions", {}))) if isinstance(runtime.get("positions", {}), Mapping) else {}
        for definition in self.positions:
            positions.setdefault(definition.position_id, {
                "position_id": definition.position_id,
                "display_name": definition.display_name,
                "organization_id": definition.organization_id,
                "role": definition.role,
                "rank": definition.rank,
                "capacity": definition.capacity,
                "incumbent_actor_ids": list(definition.incumbent_actor_ids),
                "vacant": len(definition.incumbent_actor_ids) < definition.capacity,
                "version": 0,
            })
        dynamic_actors = runtime.get("dynamic_actors", {}) if isinstance(runtime.get("dynamic_actors", {}), Mapping) else {}
        dynamic_agents = runtime.get("dynamic_agents", {}) if isinstance(runtime.get("dynamic_agents", {}), Mapping) else {}
        feedback = runtime.get("recent_feedback", []) if isinstance(runtime.get("recent_feedback", []), list) else []
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "generation": int(generation),
            "fingerprint": self.fingerprint(),
            "counts": {
                "actor_templates": len(self.actor_templates), "positions": len(self.positions),
                "transitions": len(self.transitions), "dynamic_actors": len(dynamic_actors),
                "dynamic_agents": len(dynamic_agents),
            },
            "positions": [positions[key] for key in sorted(positions)],
            "recent_feedback": copy.deepcopy(feedback[-self.recent_feedback_limit:]),
        }


def validate_lifecycle_document(value: Any) -> None:
    LifecycleContract.from_mapping(value)
