"""Authoritative DLC objective, milestone, and ending contracts.

The objective runtime evaluates deterministic facts from world state and durable
framework receipts. LLM output and clients can inspect progress, but cannot write
objective completion or endings directly.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
import re
import threading
from typing import Any

from .gameplay_contract import GameplayContract

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NUMERIC_OPERATORS = {"lt", "lte", "eq", "ne", "gte", "gt"}
_TEXT_OPERATORS = {"eq", "ne"}
_FACT_KINDS = {
    "anchored_inventory", "metric", "parameter", "entity_status", "sim_day",
    "issue_count", "case_count", "decision_count", "action_count",
}
_GROUP_OPERATORS = {"all", "any", "not"}
_OBJECTIVE_CATEGORIES = {"primary", "optional", "failure"}
_MILESTONE_MODES = {"ordered", "independent"}
_ENDING_OUTCOMES = {"victory", "defeat", "neutral"}
_ENDING_OPERATORS = {"all", "any"}
_MAX_OBJECTIVES = 128
_MAX_MILESTONES = 64
_MAX_CONDITION_DEPTH = 8
_MAX_CONDITION_CHILDREN = 32
_MAX_TRANSITIONS = 2000


class ObjectiveContractError(ValueError):
    def __init__(self, code: str, *, path: str = "", details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.path = path
        self.details = copy.deepcopy(dict(details or {}))
        super().__init__(code)


@dataclass(frozen=True)
class ObjectiveValidationIssue:
    code: str
    message: str
    path: str = ""
    severity: str = "error"


def _mapping(value: Any, code: str, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ObjectiveContractError(code, path=path)
    return value


def _sequence(value: Any, code: str, path: str, limit: int) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) > limit:
        raise ObjectiveContractError(code, path=path)
    return list(value)


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 1000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ObjectiveContractError(code, path=path)
    result = value.strip()
    if (required and not result) or len(result) > limit:
        raise ObjectiveContractError(code, path=path)
    return result


def _identifier(value: Any, code: str, path: str) -> str:
    result = _text(value, code, path, limit=64)
    if not _SAFE_ID.fullmatch(result):
        raise ObjectiveContractError(code, path=path)
    return result


def _number(value: Any, code: str, path: str) -> float:
    if isinstance(value, bool):
        raise ObjectiveContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ObjectiveContractError(code, path=path) from exc
    if not math.isfinite(result):
        raise ObjectiveContractError(code, path=path)
    return result


def _integer(value: Any, code: str, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ObjectiveContractError(code, path=path)
    return value


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_condition(value: Any, path: str, *, depth: int = 0) -> dict[str, Any]:
    if depth > _MAX_CONDITION_DEPTH:
        raise ObjectiveContractError("objective.condition.too_deep", path=path)
    raw = _mapping(value, "objective.condition.invalid", path)
    op = str(raw.get("op", "")).strip()
    if op:
        if op not in _GROUP_OPERATORS:
            raise ObjectiveContractError("objective.condition.invalid_operator", path=path + ".op")
        children = _sequence(raw.get("conditions", []), "objective.condition.invalid_children", path + ".conditions", _MAX_CONDITION_CHILDREN)
        if (op == "not" and len(children) != 1) or (op != "not" and not children):
            raise ObjectiveContractError("objective.condition.invalid_children", path=path + ".conditions")
        result = {
            "op": op,
            "conditions": [
                _parse_condition(child, f"{path}.conditions[{index}]", depth=depth + 1)
                for index, child in enumerate(children)
            ],
        }
        label = _text(raw.get("label", ""), "objective.condition.invalid_label", path + ".label", required=False, limit=128)
        if label:
            result["label"] = label
        return result

    kind = str(raw.get("kind", "")).strip()
    if kind not in _FACT_KINDS:
        raise ObjectiveContractError("objective.condition.invalid_kind", path=path + ".kind")
    result: dict[str, Any] = {"kind": kind}
    label = _text(raw.get("label", ""), "objective.condition.invalid_label", path + ".label", required=False, limit=128)
    if label:
        result["label"] = label

    if kind == "entity_status":
        result.update({
            "entity_id": _text(raw.get("entity_id"), "objective.condition.invalid_entity", path + ".entity_id", limit=128),
            "operator": str(raw.get("operator", "eq")),
            "value": _text(raw.get("value"), "objective.condition.invalid_value", path + ".value", limit=512),
        })
        if result["operator"] not in _TEXT_OPERATORS:
            raise ObjectiveContractError("objective.condition.invalid_operator", path=path + ".operator")
        return result

    if kind in {"anchored_inventory", "metric", "parameter"}:
        result.update({
            "entity_id": _text(raw.get("entity_id"), "objective.condition.invalid_entity", path + ".entity_id", limit=128),
            "name": _text(raw.get("name"), "objective.condition.invalid_name", path + ".name", limit=128),
            "operator": str(raw.get("operator", "gte")),
            "value": _number(raw.get("base_value") if kind == "anchored_inventory" and "base_value" in raw else raw.get("value"), "objective.condition.invalid_value", path + ".value"),
        })
    elif kind == "sim_day":
        result.update({"operator": str(raw.get("operator", "gte")), "value": _number(raw.get("value"), "objective.condition.invalid_value", path + ".value")})
    elif kind in {"issue_count", "case_count", "decision_count"}:
        result.update({"operator": str(raw.get("operator", "gte")), "value": _number(raw.get("value", 1), "objective.condition.invalid_value", path + ".value")})
        for key in ("issue_type_id", "status"):
            text = _text(raw.get(key, ""), f"objective.condition.invalid_{key}", path + f".{key}", required=False, limit=128)
            if text:
                result[key] = text
    else:
        result.update({
            "action_id": _identifier(raw.get("action_id"), "objective.condition.invalid_action", path + ".action_id"),
            "operator": str(raw.get("operator", "gte")),
            "value": _number(raw.get("value", 1), "objective.condition.invalid_value", path + ".value"),
        })
        for key in ("actor_id", "control_object_id", "target_id"):
            text = _text(raw.get(key, ""), f"objective.condition.invalid_{key}", path + f".{key}", required=False, limit=128)
            if text:
                result[key] = text
    if result["operator"] not in _NUMERIC_OPERATORS:
        raise ObjectiveContractError("objective.condition.invalid_operator", path=path + ".operator")
    return result


@dataclass(frozen=True)
class ObjectiveMilestone:
    milestone_id: str
    display_name: str
    description: str
    condition: dict[str, Any]

    @classmethod
    def parse(cls, value: Any, path: str) -> "ObjectiveMilestone":
        raw = _mapping(value, "objective.milestone.invalid", path)
        return cls(
            milestone_id=_identifier(raw.get("milestone_id"), "objective.milestone.invalid_id", path + ".milestone_id"),
            display_name=_text(raw.get("display_name", raw.get("milestone_id", "")), "objective.milestone.invalid_display_name", path + ".display_name", limit=128),
            description=_text(raw.get("description", ""), "objective.milestone.invalid_description", path + ".description", required=False),
            condition=_parse_condition(raw.get("condition"), path + ".condition"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObjectiveDefinition:
    objective_id: str
    display_name: str
    description: str
    category: str
    required: bool
    milestone_mode: str
    milestones: tuple[ObjectiveMilestone, ...]
    completion: dict[str, Any] | None
    enabled: bool
    order: int

    @classmethod
    def parse(cls, value: Any, path: str) -> "ObjectiveDefinition":
        raw = _mapping(value, "objective.invalid", path)
        category = str(raw.get("category", "primary")).strip()
        if category not in _OBJECTIVE_CATEGORIES:
            raise ObjectiveContractError("objective.invalid_category", path=path + ".category")
        required = raw.get("required", category == "primary")
        enabled = raw.get("enabled", True)
        if not isinstance(required, bool):
            raise ObjectiveContractError("objective.invalid_required", path=path + ".required")
        if not isinstance(enabled, bool):
            raise ObjectiveContractError("objective.invalid_enabled", path=path + ".enabled")
        mode = str(raw.get("milestone_mode", "ordered"))
        if mode not in _MILESTONE_MODES:
            raise ObjectiveContractError("objective.invalid_milestone_mode", path=path + ".milestone_mode")
        milestones = tuple(
            ObjectiveMilestone.parse(item, f"{path}.milestones[{index}]")
            for index, item in enumerate(_sequence(raw.get("milestones", []), "objective.invalid_milestones", path + ".milestones", _MAX_MILESTONES))
        )
        ids = [item.milestone_id for item in milestones]
        if len(ids) != len(set(ids)):
            raise ObjectiveContractError("objective.milestone.duplicate", path=path + ".milestones")
        completion_raw = raw.get("completion", raw.get("condition"))
        completion = None if completion_raw in (None, {}) else _parse_condition(completion_raw, path + ".completion")
        if not milestones and completion is None:
            raise ObjectiveContractError("objective.missing_completion", path=path)
        return cls(
            objective_id=_identifier(raw.get("objective_id"), "objective.invalid_id", path + ".objective_id"),
            display_name=_text(raw.get("display_name", raw.get("objective_id", "")), "objective.invalid_display_name", path + ".display_name", limit=128),
            description=_text(raw.get("description", ""), "objective.invalid_description", path + ".description", required=False),
            category=category,
            required=required,
            milestone_mode=mode,
            milestones=milestones,
            completion=completion,
            enabled=enabled,
            order=_integer(raw.get("order", 0), "objective.invalid_order", path + ".order"),
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["milestones"] = [item.to_dict() for item in self.milestones]
        return result


@dataclass(frozen=True)
class EndingDefinition:
    ending_id: str
    display_name: str
    description: str
    outcome: str
    objective_ids: tuple[str, ...]
    operator: str
    priority: int
    enabled: bool

    @classmethod
    def parse(cls, value: Any, path: str) -> "EndingDefinition":
        raw = _mapping(value, "ending.invalid", path)
        outcome = str(raw.get("outcome", "neutral")).strip()
        if outcome not in _ENDING_OUTCOMES:
            raise ObjectiveContractError("ending.invalid_outcome", path=path + ".outcome")
        trigger = _mapping(raw.get("trigger", {}), "ending.invalid_trigger", path + ".trigger")
        objective_ids = tuple(
            _identifier(item, "ending.invalid_objective", f"{path}.trigger.objective_ids[{index}]")
            for index, item in enumerate(_sequence(trigger.get("objective_ids", []), "ending.invalid_objectives", path + ".trigger.objective_ids", _MAX_OBJECTIVES))
        )
        if not objective_ids or len(objective_ids) != len(set(objective_ids)):
            raise ObjectiveContractError("ending.invalid_objectives", path=path + ".trigger.objective_ids")
        operator = str(trigger.get("operator", "all"))
        if operator not in _ENDING_OPERATORS:
            raise ObjectiveContractError("ending.invalid_operator", path=path + ".trigger.operator")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ObjectiveContractError("ending.invalid_enabled", path=path + ".enabled")
        return cls(
            ending_id=_identifier(raw.get("ending_id"), "ending.invalid_id", path + ".ending_id"),
            display_name=_text(raw.get("display_name", raw.get("ending_id", "")), "ending.invalid_display_name", path + ".display_name", limit=128),
            description=_text(raw.get("description", ""), "ending.invalid_description", path + ".description", required=False),
            outcome=outcome,
            objective_ids=objective_ids,
            operator=operator,
            priority=_integer(raw.get("priority", 0), "ending.invalid_priority", path + ".priority"),
            enabled=enabled,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ending_id": self.ending_id,
            "display_name": self.display_name,
            "description": self.description,
            "outcome": self.outcome,
            "trigger": {"objective_ids": list(self.objective_ids), "operator": self.operator},
            "priority": self.priority,
            "enabled": self.enabled,
        }


def normalize_objective_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("objectives must be a list")
    return [ObjectiveDefinition.parse(item, f"objectives[{index}]").to_dict() for index, item in enumerate(value)]


def normalize_ending_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("endings must be a list")
    return [EndingDefinition.parse(item, f"endings[{index}]").to_dict() for index, item in enumerate(value)]


def _walk_conditions(
    condition: Mapping[str, Any], path: str
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    yield path, condition
    for index, child in enumerate(condition.get("conditions", [])):
        yield from _walk_conditions(child, f"{path}.conditions[{index}]")


def validate_objective_definitions(
    objectives: Any,
    endings: Any,
    *,
    entity_ids: Iterable[str] = (),
    resources: Mapping[str, Mapping[str, Any]] | None = None,
    action_ids: Iterable[str] = (),
    issue_type_ids: Iterable[str] = (),
    gameplay: GameplayContract | None = None,
) -> list[ObjectiveValidationIssue]:
    issues: list[ObjectiveValidationIssue] = []
    try:
        objective_values = _sequence(objectives, "objectives.invalid", "objectives", _MAX_OBJECTIVES)
        parsed_objectives = [ObjectiveDefinition.parse(item, f"objectives[{index}]") for index, item in enumerate(objective_values)]
    except ObjectiveContractError as exc:
        return [ObjectiveValidationIssue(exc.code, exc.code, exc.path)]
    try:
        ending_values = _sequence(endings, "endings.invalid", "endings", _MAX_OBJECTIVES)
        parsed_endings = [EndingDefinition.parse(item, f"endings[{index}]") for index, item in enumerate(ending_values)]
    except ObjectiveContractError as exc:
        return [ObjectiveValidationIssue(exc.code, exc.code, exc.path)]

    objective_ids = {item.objective_id for item in parsed_objectives}
    if len(objective_ids) != len(parsed_objectives):
        issues.append(ObjectiveValidationIssue("objectives.duplicate", "Duplicate objective_id", "objectives"))
    ending_ids = {item.ending_id for item in parsed_endings}
    if len(ending_ids) != len(parsed_endings):
        issues.append(ObjectiveValidationIssue("endings.duplicate", "Duplicate ending_id", "endings"))
    known_entities = set(entity_ids)
    known_actions = set(action_ids)
    known_issues = set(issue_type_ids)
    known_resources = dict(resources or {})

    for objective_index, definition in enumerate(parsed_objectives):
        conditions: list[tuple[str, Mapping[str, Any]]] = []
        if definition.completion is not None:
            conditions.append((f"objectives[{objective_index}].completion", definition.completion))
        conditions.extend(
            (f"objectives[{objective_index}].milestones[{milestone_index}].condition", milestone.condition)
            for milestone_index, milestone in enumerate(definition.milestones)
        )
        for base_path, root in conditions:
            for condition_path, condition in _walk_conditions(root, base_path):
                kind = condition.get("kind")
                if not kind:
                    continue
                entity_id = str(condition.get("entity_id", ""))
                if entity_id and entity_id not in known_entities:
                    issues.append(ObjectiveValidationIssue("objective.condition.unknown_entity", f"Unknown entity: {entity_id}", condition_path + ".entity_id"))
                if kind in {"anchored_inventory", "metric", "parameter"}:
                    name = str(condition.get("name", ""))
                    resource = known_resources.get(name)
                    expected_kind = "asset" if kind == "anchored_inventory" else kind
                    if resource is None:
                        issues.append(ObjectiveValidationIssue("objective.condition.unknown_resource", f"Unknown resource: {name}", condition_path + ".name"))
                    elif str(resource.get("kind", "asset")) != expected_kind:
                        issues.append(ObjectiveValidationIssue("objective.condition.resource_kind", f"Resource {name} must be {expected_kind}", condition_path + ".name"))
                if kind == "action_count" and condition.get("action_id") not in known_actions:
                    issues.append(ObjectiveValidationIssue("objective.condition.unknown_action", f"Unknown action: {condition.get('action_id')}", condition_path + ".action_id"))
                if kind in {"issue_count", "case_count", "decision_count"} and condition.get("issue_type_id") and condition.get("issue_type_id") not in known_issues:
                    issues.append(ObjectiveValidationIssue("objective.condition.unknown_issue_type", f"Unknown issue type: {condition.get('issue_type_id')}", condition_path + ".issue_type_id"))

    for index, ending in enumerate(parsed_endings):
        for objective_id in ending.objective_ids:
            if objective_id not in objective_ids:
                issues.append(ObjectiveValidationIssue("ending.unknown_objective", f"Unknown objective: {objective_id}", f"endings[{index}].trigger.objective_ids"))
    if parsed_endings and not parsed_objectives:
        issues.append(ObjectiveValidationIssue("ending.without_objectives", "Endings require at least one objective", "endings"))
    _ = gameplay
    return issues


def _get_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator in _TEXT_OPERATORS and (isinstance(actual, str) or isinstance(expected, str)):
        return actual == expected if operator == "eq" else actual != expected
    try:
        left, right = float(actual), float(expected)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(left) or not math.isfinite(right):
        return False
    if operator == "eq":
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    if operator == "ne":
        return not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    return {"lt": left < right, "lte": left <= right, "gte": left >= right, "gt": left > right}.get(operator, False)


def _numeric_progress(actual: Any, expected: Any, operator: str, passed: bool) -> float:
    if passed:
        return 1.0
    try:
        left, right = float(actual), float(expected)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(left) or not math.isfinite(right):
        return 0.0
    if operator in {"gte", "gt"} and right > 0:
        return max(0.0, min(1.0, left / right))
    if operator in {"lte", "lt"} and left > 0 and right >= 0:
        return max(0.0, min(1.0, right / left))
    if operator in {"eq", "ne"}:
        scale = max(abs(right), 1.0)
        return max(0.0, min(1.0, 1.0 - abs(left - right) / scale))
    return 0.0


class ObjectiveManager:
    """Generation-aware, monotonic objective runtime."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._definitions: dict[str, ObjectiveDefinition] = {}
        self._endings: dict[str, EndingDefinition] = {}
        self._generation = 0
        self._gameplay = GameplayContract()
        self._objective_receipts: dict[str, dict[str, Any]] = {}
        self._milestone_receipts: dict[str, dict[str, Any]] = {}
        self._ending_receipt: dict[str, Any] | None = None
        self._transition_order: deque[dict[str, Any]] = deque(maxlen=_MAX_TRANSITIONS)
        self._last_progress: dict[str, Any] = {}
        self._effect_overrides: dict[str, dict[str, Any]] = {}
        self._fingerprint = _canonical_hash({"objectives": [], "endings": []})

    def configure(
        self,
        objectives: Iterable[Mapping[str, Any] | ObjectiveDefinition],
        endings: Iterable[Mapping[str, Any] | EndingDefinition],
        *,
        generation: int = 0,
        gameplay: GameplayContract | None = None,
    ) -> None:
        parsed_objectives: dict[str, ObjectiveDefinition] = {}
        for index, raw in enumerate(objectives):
            item = raw if isinstance(raw, ObjectiveDefinition) else ObjectiveDefinition.parse(raw, f"objectives[{index}]")
            if item.objective_id in parsed_objectives:
                raise ObjectiveContractError("objectives.duplicate", path=f"objectives[{index}].objective_id")
            parsed_objectives[item.objective_id] = item
        parsed_endings: dict[str, EndingDefinition] = {}
        for index, raw in enumerate(endings):
            item = raw if isinstance(raw, EndingDefinition) else EndingDefinition.parse(raw, f"endings[{index}]")
            if item.ending_id in parsed_endings:
                raise ObjectiveContractError("endings.duplicate", path=f"endings[{index}].ending_id")
            unknown = sorted(set(item.objective_ids) - set(parsed_objectives))
            if unknown:
                raise ObjectiveContractError("ending.unknown_objective", path=f"endings[{index}].trigger.objective_ids", details={"objective_ids": unknown})
            parsed_endings[item.ending_id] = item
        canonical = {
            "objectives": [parsed_objectives[key].to_dict() for key in sorted(parsed_objectives)],
            "endings": [parsed_endings[key].to_dict() for key in sorted(parsed_endings)],
        }
        with self._lock:
            self._definitions = parsed_objectives
            self._endings = parsed_endings
            self._generation = max(0, int(generation))
            self._gameplay = gameplay or GameplayContract()
            self._fingerprint = _canonical_hash(canonical)

    @staticmethod
    def _count_action_receipts(action_catalog: Any, condition: Mapping[str, Any]) -> int:
        if action_catalog is None or not hasattr(action_catalog, "snapshot"):
            return 0
        snapshot = action_catalog.snapshot()
        count = 0
        for receipt in snapshot.get("receipts", {}).values():
            response = receipt.get("response", {}) if isinstance(receipt, Mapping) else {}
            invocation = response.get("invocation", {}) if isinstance(response, Mapping) else {}
            if response.get("action_id") != condition.get("action_id") or not response.get("ok", False):
                continue
            if any(condition.get(key) and invocation.get(key) != condition.get(key) for key in ("actor_id", "control_object_id", "target_id")):
                continue
            count += 1
        return count

    @staticmethod
    def _count_issue_records(issue_manager: Any, kind: str, condition: Mapping[str, Any], sim_time: float) -> int:
        if issue_manager is None:
            return 0
        method_name = {"issue_count": "list_issues", "case_count": "list_cases", "decision_count": "list_decision_requests"}[kind]
        method = getattr(issue_manager, method_name, None)
        if not callable(method):
            return 0
        records = method(sim_time=sim_time)
        issue_type_id = str(condition.get("issue_type_id", ""))
        status = str(condition.get("status", ""))
        return sum(
            1 for record in records
            if (not issue_type_id or str(record.get("issue_type_id", "")) == issue_type_id)
            and (not status or str(record.get("status", "")) == status)
        )

    def _evaluate_condition(
        self,
        condition: Mapping[str, Any],
        *,
        world_state: Any,
        sim_time: float,
        issue_manager: Any,
        action_catalog: Any,
    ) -> dict[str, Any]:
        if condition.get("op"):
            op = str(condition["op"])
            children = [
                self._evaluate_condition(child, world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
                for child in condition.get("conditions", [])
            ]
            passed = all(item["ok"] for item in children) if op == "all" else any(item["ok"] for item in children)
            if op == "not":
                passed = not children[0]["ok"]
            progress = sum(item["progress"] for item in children) / len(children) if op == "all" else max((item["progress"] for item in children), default=0.0)
            if op == "not":
                progress = 1.0 - children[0]["progress"]
            return {"op": op, "label": condition.get("label", ""), "ok": passed, "progress": round(max(0.0, min(1.0, progress)), 4), "conditions": children}

        kind = str(condition.get("kind", ""))
        actual: Any = None
        error = ""
        if kind == "sim_day":
            actual = sim_time / 86400.0
        elif kind == "entity_status":
            entity = world_state.get_entity(str(condition.get("entity_id", ""))) if world_state is not None else None
            actual = _get_field(entity, "current_status", None) if entity is not None else None
            if entity is None:
                error = "entity_not_found"
        elif kind == "anchored_inventory":
            entity_id, name = str(condition.get("entity_id", "")), str(condition.get("name", ""))
            if world_state is None or world_state.get_entity(entity_id) is None:
                error = "entity_not_found"
            else:
                actual = sum(float(_get_field(item, "base_value", 0.0)) for item in world_state.get_inventory(entity_id) if _get_field(item, "item_name", "") == name)
        elif kind in {"metric", "parameter"}:
            entity_id, name = str(condition.get("entity_id", "")), str(condition.get("name", ""))
            if world_state is None or world_state.get_entity(entity_id) is None:
                error = "entity_not_found"
            else:
                item = next((item for item in world_state.get_entity_values(entity_id, kind) if _get_field(item, "name", "") == name), None)
                actual = _get_field(item, "value", None) if item is not None else None
                if item is None:
                    error = "value_not_found"
        elif kind in {"issue_count", "case_count", "decision_count"}:
            actual = self._count_issue_records(issue_manager, kind, condition, sim_time)
        elif kind == "action_count":
            actual = self._count_action_receipts(action_catalog, condition)
        expected = condition.get("value")
        passed = not error and actual is not None and _compare(actual, str(condition.get("operator", "eq")), expected)
        return {
            "kind": kind,
            "label": condition.get("label", ""),
            "entity_id": condition.get("entity_id", ""),
            "name": condition.get("name", condition.get("action_id", "")),
            "operator": condition.get("operator", "eq"),
            "expected": expected,
            "actual": actual,
            "ok": passed,
            "progress": round(_numeric_progress(actual, expected, str(condition.get("operator", "eq")), passed), 4) if kind != "entity_status" else (1.0 if passed else 0.0),
            "error": error or ("" if passed else "condition_failed"),
        }

    def _remember_transition(self, transition: dict[str, Any]) -> None:
        self._transition_order.append(copy.deepcopy(transition))

    def set_effect_overrides(self, overrides: Mapping[str, Any] | None) -> None:
        """Replace objective progress/status projected from gameplay effects."""
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(overrides, Mapping):
            for raw_id, raw in overrides.items():
                objective_id = str(raw_id)
                if objective_id not in self._definitions or not isinstance(raw, Mapping):
                    continue
                item = copy.deepcopy(dict(raw))
                try:
                    item["progress"] = max(0.0, min(1.0, float(item.get("progress", 0.0))))
                except (TypeError, ValueError, OverflowError):
                    continue
                normalized[objective_id] = item
        with self._lock:
            self._effect_overrides = normalized

    def _state_locked(
        self,
        *,
        world_state: Any,
        sim_time: float,
        issue_manager: Any,
        action_catalog: Any,
    ) -> dict[str, Any]:
        objective_states: list[dict[str, Any]] = []
        for definition in sorted(self._definitions.values(), key=lambda item: (item.order, item.objective_id)):
            completion_receipt = self._objective_receipts.get(definition.objective_id)
            effect_override = self._effect_overrides.get(definition.objective_id, {})
            milestones: list[dict[str, Any]] = []
            milestone_progress: list[float] = []
            locked = False
            for milestone in definition.milestones:
                key = f"{definition.objective_id}:{milestone.milestone_id}"
                receipt = self._milestone_receipts.get(key)
                evaluation = self._last_progress.get(key)
                if evaluation is None:
                    evaluation = self._evaluate_condition(milestone.condition, world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
                status = "completed" if receipt else ("locked" if locked else "pending")
                if definition.milestone_mode == "ordered" and not receipt:
                    locked = True
                progress = 1.0 if receipt else (0.0 if status == "locked" else float(evaluation.get("progress", 0.0)))
                milestone_progress.append(progress)
                milestones.append({**milestone.to_dict(), "status": status, "completed_at": receipt.get("completed_at") if receipt else None, "progress": progress, "evaluation": evaluation if status != "locked" else None})
            completion_evaluation = self._last_progress.get(definition.objective_id)
            if completion_evaluation is None and definition.completion is not None:
                completion_evaluation = self._evaluate_condition(definition.completion, world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
            condition_progress = float(completion_evaluation.get("progress", 0.0)) if completion_evaluation else 1.0
            if milestones:
                progress = (sum(milestone_progress) / len(milestone_progress) + condition_progress) / (2 if definition.completion is not None else 1)
            else:
                progress = condition_progress
            objective_states.append({
                **definition.to_dict(),
                "status": (
                    "completed" if completion_receipt
                    else str(effect_override.get("status")) if effect_override.get("status")
                    else "active" if definition.enabled
                    else "disabled"
                ),
                "completed_at": completion_receipt.get("completed_at") if completion_receipt else None,
                "progress": round(
                    1.0 if completion_receipt
                    else float(effect_override["progress"]) if "progress" in effect_override
                    else max(0.0, min(1.0, progress)),
                    4,
                ),
                "effect_override": copy.deepcopy(effect_override) if effect_override else None,
                "completion_evaluation": completion_evaluation,
                "milestones": milestones,
            })
        ending_states = []
        for definition in sorted(self._endings.values(), key=lambda item: (-item.priority, item.ending_id)):
            completed = [objective_id in self._objective_receipts for objective_id in definition.objective_ids]
            eligible = definition.enabled and (
                all(completed) if definition.operator == "all" else any(completed)
            )
            ending_states.append({**definition.to_dict(), "eligible": eligible, "reached": bool(self._ending_receipt and self._ending_receipt.get("ending_id") == definition.ending_id)})
        sandbox = not any(item.enabled for item in self._definitions.values()) and not any(item.enabled for item in self._endings.values())
        return {
            "configured": bool(self._definitions or self._endings),
            "sandbox": sandbox,
            "generation": self._generation,
            "fingerprint": self._fingerprint,
            "status": "ended" if self._ending_receipt else ("sandbox" if sandbox else "in_progress"),
            "objectives": objective_states,
            "endings": ending_states,
            "current_ending": copy.deepcopy(self._ending_receipt),
            "recent_transitions": list(copy.deepcopy(self._transition_order))[-50:],
            "archived_objective_receipt_count": len(set(self._objective_receipts) - set(self._definitions)),
            "archived_milestone_receipt_count": sum(1 for key in self._milestone_receipts if key.split(":", 1)[0] not in self._definitions),
        }


    def evaluate(
        self,
        *,
        world_state: Any,
        sim_time: float,
        issue_manager: Any = None,
        action_catalog: Any = None,
    ) -> dict[str, Any]:
        with self._lock:
            transitions: list[dict[str, Any]] = []
            if self._ending_receipt is None:
                for definition in sorted(self._definitions.values(), key=lambda item: (item.order, item.objective_id)):
                    override = self._effect_overrides.get(definition.objective_id, {})
                    override_completed = (
                        str(override.get("status", "")) == "completed"
                        or float(override.get("progress", 0.0)) >= 1.0
                    )
                    if (
                        definition.enabled
                        and override_completed
                        and definition.objective_id not in self._objective_receipts
                    ):
                        receipt = {
                            "objective_id": definition.objective_id,
                            "category": definition.category,
                            "completed_at": float(override.get("updated_at", sim_time)),
                            "generation": self._generation,
                            "source": "gameplay_effect",
                            "command_id": str(override.get("command_id", "")),
                        }
                        self._objective_receipts[definition.objective_id] = receipt
                        transition = {"type": "objective_completed", **receipt}
                        transitions.append(transition)
                        self._remember_transition(transition)
                for definition in sorted(self._definitions.values(), key=lambda item: (item.order, item.objective_id)):
                    if not definition.enabled or definition.objective_id in self._objective_receipts:
                        continue
                    if definition.milestone_mode == "ordered":
                        for milestone in definition.milestones:
                            key = f"{definition.objective_id}:{milestone.milestone_id}"
                            if key in self._milestone_receipts:
                                continue
                            evaluated = self._evaluate_condition(milestone.condition, world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
                            self._last_progress[key] = evaluated
                            if evaluated["ok"]:
                                receipt = {"objective_id": definition.objective_id, "milestone_id": milestone.milestone_id, "completed_at": float(sim_time), "generation": self._generation}
                                self._milestone_receipts[key] = receipt
                                transition = {"type": "milestone_completed", **receipt}
                                transitions.append(transition)
                                self._remember_transition(transition)
                                continue
                            break
                    else:
                        for milestone in definition.milestones:
                            key = f"{definition.objective_id}:{milestone.milestone_id}"
                            if key in self._milestone_receipts:
                                continue
                            evaluated = self._evaluate_condition(milestone.condition, world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
                            self._last_progress[key] = evaluated
                            if evaluated["ok"]:
                                receipt = {"objective_id": definition.objective_id, "milestone_id": milestone.milestone_id, "completed_at": float(sim_time), "generation": self._generation}
                                self._milestone_receipts[key] = receipt
                                transition = {"type": "milestone_completed", **receipt}
                                transitions.append(transition)
                                self._remember_transition(transition)
                    milestones_complete = all(f"{definition.objective_id}:{item.milestone_id}" in self._milestone_receipts for item in definition.milestones)
                    completion_ok = True
                    if definition.completion is not None:
                        evaluated = self._evaluate_condition(definition.completion, world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
                        self._last_progress[definition.objective_id] = evaluated
                        completion_ok = bool(evaluated["ok"])
                    if milestones_complete and completion_ok:
                        receipt = {"objective_id": definition.objective_id, "category": definition.category, "completed_at": float(sim_time), "generation": self._generation}
                        self._objective_receipts[definition.objective_id] = receipt
                        transition = {"type": "objective_completed", **receipt}
                        transitions.append(transition)
                        self._remember_transition(transition)

                eligible: list[EndingDefinition] = []
                for ending in self._endings.values():
                    if not ending.enabled:
                        continue
                    completed = [objective_id in self._objective_receipts for objective_id in ending.objective_ids]
                    if all(completed) if ending.operator == "all" else any(completed):
                        eligible.append(ending)
                if eligible:
                    ending = sorted(eligible, key=lambda item: (-item.priority, item.ending_id))[0]
                    self._ending_receipt = {**ending.to_dict(), "reached_at": float(sim_time), "generation": self._generation}
                    transition = {"type": "ending_reached", "ending_id": ending.ending_id, "outcome": ending.outcome, "reached_at": float(sim_time), "generation": self._generation}
                    transitions.append(transition)
                    self._remember_transition(transition)
            state = self._state_locked(world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)
            return {"changed": bool(transitions), "transitions": copy.deepcopy(transitions), "state": state}

    def public_state(self, *, world_state: Any, sim_time: float, issue_manager: Any = None, action_catalog: Any = None) -> dict[str, Any]:
        return self.evaluate(world_state=world_state, sim_time=sim_time, issue_manager=issue_manager, action_catalog=action_catalog)["state"]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": "1.0",
                "objective_receipts": copy.deepcopy(self._objective_receipts),
                "milestone_receipts": copy.deepcopy(self._milestone_receipts),
                "ending_receipt": copy.deepcopy(self._ending_receipt),
                "transitions": list(copy.deepcopy(self._transition_order)),
            }

    def restore(self, value: Any, *, sim_time: float = 0.0) -> None:
        if value in (None, {}):
            return
        raw = _mapping(value, "objective.snapshot.invalid", "objective_runtime")
        objective_receipts = raw.get("objective_receipts", {})
        milestone_receipts = raw.get("milestone_receipts", {})
        transitions = raw.get("transitions", [])
        ending_receipt = raw.get("ending_receipt")
        if not isinstance(objective_receipts, Mapping) or not isinstance(milestone_receipts, Mapping):
            raise ObjectiveContractError("objective.snapshot.invalid")
        if not isinstance(transitions, Sequence) or isinstance(transitions, (str, bytes, bytearray)):
            raise ObjectiveContractError("objective.snapshot.invalid")
        if ending_receipt is not None and not isinstance(ending_receipt, Mapping):
            raise ObjectiveContractError("objective.snapshot.invalid")
        with self._lock:
            for objective_id, receipt in objective_receipts.items():
                if isinstance(receipt, Mapping):
                    self._objective_receipts[str(objective_id)] = copy.deepcopy(dict(receipt))
            for milestone_key, receipt in milestone_receipts.items():
                if isinstance(receipt, Mapping):
                    self._milestone_receipts[str(milestone_key)] = copy.deepcopy(dict(receipt))
            if self._ending_receipt is None and ending_receipt is not None:
                self._ending_receipt = copy.deepcopy(dict(ending_receipt))
            self._transition_order.extend(copy.deepcopy(list(transitions)[-_MAX_TRANSITIONS:]))
            _ = float(sim_time)
