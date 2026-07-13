"""Deterministic DLC-authored player action contracts.

DLCs declare action schemas while the framework enforces player control,
preconditions, anchored costs, authoritative time, cooldowns and idempotency.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
import re
import threading
from typing import Any, Callable, Iterable

from .causal_events import CausalEventError, materialize_world_commands, normalize_world_effects
from .event_generator import GAME_SECONDS_PER_DAY
from .gameplay_contract import GameplayContract
from .time_simulator import TimeSimulator
from .types import EventObject
from .world_commands import WorldCommand, WorldCommandType

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
REF = re.compile(r"^\$(?:actor|target|control|duration_days|param\.[A-Za-z0-9_.:-]{1,64})$")
PARAM_TYPES = {"string", "integer", "number", "boolean"}
TARGET_KINDS = {"none", "entity", "agent"}
CONDITION_KINDS = {"anchored_inventory", "metric", "parameter", "entity_status"}
RESULT_MODES = {"world_commands", "ongoing_event", "wake_agent", "emit_event", "composite"}
OPERATORS = {"lt", "lte", "eq", "ne", "gte", "gt"}
COOLDOWN_SCOPES = {"action", "control", "target", "control_target"}
MAX_RECEIPTS = 5000
MAX_COOLDOWNS = 5000


class ActionContractError(ValueError):
    def __init__(self, code: str, message: str | None = None, path: str = "", details: dict[str, Any] | None = None):
        super().__init__(message or code)
        self.code = code
        self.path = path
        self.details = details or {}


@dataclass(frozen=True)
class ActionValidationIssue:
    code: str
    message: str
    path: str


def _map(value: Any, code: str, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ActionContractError(code, path=path)
    return value


def _list(value: Any, code: str, path: str, limit: int = 256) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) > limit:
        raise ActionContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str, required: bool = True, limit: int = 512) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise ActionContractError(code, path=path)
    return result


def _num(value: Any, code: str, path: str, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ActionContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionContractError(code, path=path) from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ActionContractError(code, path=path)
    return result


def _numref(value: Any, code: str, path: str, minimum: float | None = None) -> float | str:
    if isinstance(value, str) and REF.fullmatch(value.strip()):
        return value.strip()
    return _num(value, code, path, minimum)


def _id(value: Any, code: str, path: str) -> str:
    result = _text(value, code, path, limit=64)
    if not SAFE_ID.fullmatch(result):
        raise ActionContractError(code, path=path)
    return result


def _strings(value: Any, code: str, path: str, allowed: set[str] | None = None) -> tuple[str, ...]:
    result: list[str] = []
    for index, item in enumerate(_list(value, code, path)):
        text = _text(item, code, f"{path}[{index}]", limit=128)
        if allowed is not None and text not in allowed:
            raise ActionContractError(code, path=f"{path}[{index}]")
        if text not in result:
            result.append(text)
    return tuple(result)


def normalize_action_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("DLC content field 'actions' must be a list")
    return [copy.deepcopy(item) if isinstance(item, dict) else item for item in value]


def _validate_template_refs(value: Any, parameter_names: set[str], path: str) -> None:
    if isinstance(value, str) and value.startswith("$"):
        if not REF.fullmatch(value):
            raise ActionContractError("action.reference.invalid", path=path)
        if value.startswith("$param.") and value[7:] not in parameter_names:
            raise ActionContractError("action.reference.unknown_parameter", path=path)
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _validate_template_refs(child, parameter_names, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _validate_template_refs(child, parameter_names, f"{path}[{index}]")


def _resolve_refs(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        if value == "$actor":
            return context.get("actor", "")
        if value == "$control":
            return context.get("control", "")
        if value == "$target":
            return context.get("target", "")
        if value == "$duration_days":
            return context.get("duration_days", 0.0)
        if value.startswith("$param."):
            return copy.deepcopy(context.get("parameters", {}).get(value[7:]))
        raise ActionContractError("action.reference.invalid")
    if isinstance(value, Mapping):
        return {str(key): _resolve_refs(child, context) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_resolve_refs(child, context) for child in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class ActionParameter:
    name: str
    type: str = "string"
    display_name: str = ""
    description: str = ""
    required: bool = False
    default: Any = None
    enum: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any], path: str) -> "ActionParameter":
        kind = str(raw.get("type", "string"))
        if kind not in PARAM_TYPES:
            raise ActionContractError("action.parameter.invalid_type", path=path + ".type")
        required = raw.get("required", False)
        if not isinstance(required, bool):
            raise ActionContractError("action.parameter.invalid_required", path=path + ".required")
        low = raw.get("minimum")
        high = raw.get("maximum")
        low = None if low is None else _num(low, "action.parameter.invalid_minimum", path + ".minimum")
        high = None if high is None else _num(high, "action.parameter.invalid_maximum", path + ".maximum")
        if low is not None and high is not None and high < low:
            raise ActionContractError("action.parameter.invalid_range", path=path)
        parameter = cls(
            name=_id(raw.get("name"), "action.parameter.invalid_name", path + ".name"),
            type=kind,
            display_name=_text(raw.get("display_name", ""), "action.parameter.invalid_display_name", path + ".display_name", False, 128),
            description=_text(raw.get("description", ""), "action.parameter.invalid_description", path + ".description", False, 1000),
            required=required,
            default=copy.deepcopy(raw.get("default")),
            enum=tuple(_list(raw.get("enum", []), "action.parameter.invalid_enum", path + ".enum", 128)),
            minimum=low,
            maximum=high,
        )
        if "default" in raw:
            _, error = parameter.normalize(raw.get("default"), True)
            if error:
                raise ActionContractError(f"action.parameter.default_{error}", path=path + ".default")
        for index, item in enumerate(parameter.enum):
            _, error = parameter.normalize(item, True)
            if error and error != "enum":
                raise ActionContractError("action.parameter.invalid_enum", path=f"{path}.enum[{index}]")
        return parameter

    def normalize(self, supplied: Any, present: bool) -> tuple[Any, str | None]:
        value = supplied if present else copy.deepcopy(self.default)
        if not present and value is None and self.required:
            return None, "required"
        if value is None:
            return None, None
        valid = (
            (self.type == "boolean" and isinstance(value, bool))
            or (self.type == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (self.type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)))
            or (self.type == "string" and isinstance(value, str))
        )
        if not valid:
            return None, "type"
        if self.enum and value not in self.enum:
            return None, "enum"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.minimum is not None and value < self.minimum:
                return None, "minimum"
            if self.maximum is not None and value > self.maximum:
                return None, "maximum"
        return (float(value) if self.type == "number" else value), None


def _parse_condition(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    kind = str(raw.get("kind", ""))
    if kind not in CONDITION_KINDS:
        raise ActionContractError("action.precondition.invalid_kind", path=path + ".kind")
    result: dict[str, Any] = {"kind": kind}
    if kind == "entity_status":
        result["entity_id"] = raw.get("entity_id", "$target")
        result["operator"] = str(raw.get("operator", "eq"))
        if result["operator"] not in {"eq", "ne"}:
            raise ActionContractError("action.precondition.invalid_operator", path=path + ".operator")
        result["value"] = _text(raw.get("value"), "action.precondition.invalid_value", path + ".value", limit=512)
        return result
    result["entity_id"] = raw.get("entity_id", "$control")
    result["name"] = _text(raw.get("name"), "action.precondition.invalid_name", path + ".name", limit=128)
    result["operator"] = str(raw.get("operator", "gte"))
    if result["operator"] not in OPERATORS:
        raise ActionContractError("action.precondition.invalid_operator", path=path + ".operator")
    result["value"] = _numref(raw.get("base_value") if kind == "anchored_inventory" else raw.get("value"), "action.precondition.invalid_value", path + ".value")
    return result


def _parse_cost(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    result = {
        "entity_id": raw.get("entity_id", "$control"),
        "resource_name": _text(raw.get("resource_name"), "action.cost.invalid_resource", path + ".resource_name", limit=128),
        "base_value": _numref(raw.get("base_value"), "action.cost.invalid_base_value", path + ".base_value", 0.0),
        "reason": _text(raw.get("reason", ""), "action.cost.invalid_reason", path + ".reason", False, 512),
    }
    if result["base_value"] == 0:
        raise ActionContractError("action.cost.invalid_base_value", path=path + ".base_value")
    return result


def _parse_duration(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action_type": _text(raw.get("action_type", "$action"), "action.duration.invalid_action_type", path + ".action_type", limit=128),
        "proposed_days": _numref(raw.get("proposed_days", 1.0), "action.duration.invalid_proposed_days", path + ".proposed_days", 0.0),
        "severity": _numref(raw.get("severity", 5), "action.duration.invalid_severity", path + ".severity", 0.0),
        "resource_value": _numref(raw.get("resource_value", 0.0), "action.duration.invalid_resource_value", path + ".resource_value", 0.0),
        "distance_days": _numref(raw.get("distance_days", 0.0), "action.duration.invalid_distance_days", path + ".distance_days", 0.0),
        "success": raw.get("success", True),
    }
    if "workload_days" in raw:
        result["workload_days"] = _numref(raw.get("workload_days"), "action.duration.invalid_workload_days", path + ".workload_days", 0.0)
    if not isinstance(result["success"], (bool, str)) or isinstance(result["success"], str) and not REF.fullmatch(result["success"]):
        raise ActionContractError("action.duration.invalid_success", path=path + ".success")
    return result


def _parse_effect_templates(value: Any, path: str) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, "action.result.invalid_effects", path, 32)):
        effect = dict(_map(item, "action.result.invalid_effect", f"{path}[{index}]"))
        effect_type = str(effect.get("effect_type", ""))
        if effect_type not in {"adjust_resource", "set_resource", "update_status", "create_ongoing_event", "transfer_anchored_value"}:
            raise ActionContractError("action.result.invalid_effect_type", path=f"{path}[{index}].effect_type")
        effects.append(copy.deepcopy(effect))
    return effects


def _parse_result(raw: Mapping[str, Any], path: str, depth: int = 0) -> dict[str, Any]:
    if depth > 4:
        raise ActionContractError("action.result.too_deep", path=path)
    mode = str(raw.get("mode", "world_commands"))
    if mode not in RESULT_MODES:
        raise ActionContractError("action.result.invalid_mode", path=path + ".mode")
    if mode == "world_commands":
        return {"mode": mode, "effects": _parse_effect_templates(raw.get("effects", []), path + ".effects")}
    if mode == "ongoing_event":
        effect = dict(raw.get("effect", {}))
        effect.setdefault("effect_type", "create_ongoing_event")
        return {"mode": mode, "effect": _parse_effect_templates([effect], path + ".effect")[0]}
    if mode == "wake_agent":
        payload = raw.get("payload", {})
        if not isinstance(payload, Mapping) or len(payload) > 64:
            raise ActionContractError("action.result.invalid_payload", path=path + ".payload")
        return {
            "mode": mode,
            "agent_id": raw.get("agent_id", "$target"),
            "payload": copy.deepcopy(dict(payload)),
        }
    if mode == "emit_event":
        event = dict(_map(raw.get("event", {}), "action.result.invalid_event", path + ".event"))
        event_type = _text(event.get("event_type"), "action.result.invalid_event_type", path + ".event.event_type", limit=128)
        severity = event.get("severity", 5)
        if not isinstance(severity, str):
            severity = int(_num(severity, "action.result.invalid_severity", path + ".event.severity", 1))
            if severity > 10:
                raise ActionContractError("action.result.invalid_severity", path=path + ".event.severity")
        targets = list(_list(event.get("target_agents", []), "action.result.invalid_targets", path + ".event.target_agents", 64))
        payload = dict(_map(event.get("initial_payload", {}), "action.result.invalid_payload", path + ".event.initial_payload"))
        return {"mode": mode, "event": {"event_type": event_type, "severity": severity, "target_agents": targets, "initial_payload": payload}}
    children = tuple(
        _parse_result(_map(item, "action.result.invalid_child", f"{path}.results[{index}]"), f"{path}.results[{index}]", depth + 1)
        for index, item in enumerate(_list(raw.get("results", []), "action.result.invalid_children", path + ".results", 16))
    )
    if not children:
        raise ActionContractError("action.result.empty_composite", path=path + ".results")
    return {"mode": mode, "results": list(children)}


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    display_name: str
    description: str
    parameters: tuple[ActionParameter, ...]
    target: dict[str, Any]
    permissions: dict[str, Any]
    preconditions: tuple[dict[str, Any], ...]
    costs: tuple[dict[str, Any], ...]
    duration: dict[str, Any]
    cooldown: dict[str, Any]
    result: dict[str, Any]
    enabled: bool = True

    @classmethod
    def parse(cls, raw: Mapping[str, Any], path: str = "action") -> "ActionDefinition":
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ActionContractError("action.invalid_enabled", path=path + ".enabled")
        parameters = tuple(
            ActionParameter.parse(_map(item, "action.parameter.invalid", f"{path}.parameters[{index}]"), f"{path}.parameters[{index}]")
            for index, item in enumerate(_list(raw.get("parameters", []), "action.invalid_parameters", path + ".parameters", 32))
        )
        names = {item.name for item in parameters}
        if len(names) != len(parameters):
            raise ActionContractError("action.parameter.duplicate", path=path + ".parameters")
        target = dict(_map(raw.get("target", {}), "action.invalid_target", path + ".target"))
        target.setdefault("kind", "none")
        target.setdefault("required", target["kind"] != "none")
        target.setdefault("allowed_ids", [])
        target.setdefault("entity_types", [])
        target.setdefault("controlled_only", False)
        if target["kind"] not in TARGET_KINDS or not isinstance(target["required"], bool) or not isinstance(target["controlled_only"], bool):
            raise ActionContractError("action.target.invalid", path=path + ".target")
        target["allowed_ids"] = list(_strings(target["allowed_ids"], "action.target.invalid_allowed_ids", path + ".target.allowed_ids"))
        target["entity_types"] = list(_strings(target["entity_types"], "action.target.invalid_entity_types", path + ".target.entity_types"))
        permissions = dict(_map(raw.get("permissions", {}), "action.invalid_permissions", path + ".permissions"))
        permissions.setdefault("control_modes", ["direct", "command"])
        permissions.setdefault("control_kinds", ["entity", "agent"])
        permissions.setdefault("control_ids", [])
        permissions.setdefault("actor_ids", [])
        permissions["control_modes"] = list(_strings(permissions["control_modes"], "action.permissions.invalid_modes", path + ".permissions.control_modes", {"direct", "command"}))
        permissions["control_kinds"] = list(_strings(permissions["control_kinds"], "action.permissions.invalid_kinds", path + ".permissions.control_kinds", {"entity", "agent"}))
        permissions["control_ids"] = list(_strings(permissions["control_ids"], "action.permissions.invalid_control_ids", path + ".permissions.control_ids"))
        permissions["actor_ids"] = list(_strings(permissions["actor_ids"], "action.permissions.invalid_actor_ids", path + ".permissions.actor_ids"))
        if not permissions["control_modes"] or not permissions["control_kinds"]:
            raise ActionContractError("action.permissions.empty", path=path + ".permissions")
        preconditions = tuple(_parse_condition(_map(item, "action.precondition.invalid", f"{path}.preconditions[{index}]"), f"{path}.preconditions[{index}]") for index, item in enumerate(_list(raw.get("preconditions", []), "action.invalid_preconditions", path + ".preconditions", 32)))
        costs = tuple(_parse_cost(_map(item, "action.cost.invalid", f"{path}.costs[{index}]"), f"{path}.costs[{index}]") for index, item in enumerate(_list(raw.get("costs", []), "action.invalid_costs", path + ".costs", 32)))
        duration = _parse_duration(_map(raw.get("duration", {}), "action.invalid_duration", path + ".duration"), path + ".duration")
        if duration["action_type"] == "$action":
            duration["action_type"] = str(raw.get("action_id", ""))
        cooldown = dict(_map(raw.get("cooldown", {}), "action.invalid_cooldown", path + ".cooldown"))
        cooldown.setdefault("days", 0)
        cooldown.setdefault("scope", "control_target")
        cooldown["days"] = _num(cooldown["days"], "action.cooldown.invalid_days", path + ".cooldown.days", 0)
        if cooldown["scope"] not in COOLDOWN_SCOPES:
            raise ActionContractError("action.cooldown.invalid_scope", path=path + ".cooldown.scope")
        result = _parse_result(_map(raw.get("result", {}), "action.invalid_result", path + ".result"), path + ".result")
        definition = cls(
            action_id=_id(raw.get("action_id"), "action.invalid_id", path + ".action_id"),
            display_name=_text(raw.get("display_name"), "action.invalid_display_name", path + ".display_name", limit=128),
            description=_text(raw.get("description", ""), "action.invalid_description", path + ".description", False, 1000),
            parameters=parameters,
            target=target,
            permissions=permissions,
            preconditions=preconditions,
            costs=costs,
            duration=duration,
            cooldown=cooldown,
            result=result,
            enabled=enabled,
        )
        _validate_template_refs({"preconditions": preconditions, "costs": costs, "duration": duration, "result": result}, names, path)
        return definition

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "display_name": self.display_name,
            "description": self.description,
            "parameters": [asdict(item) | {"enum": list(item.enum)} for item in self.parameters],
            "target": copy.deepcopy(self.target),
            "permissions": copy.deepcopy(self.permissions),
            "preconditions": copy.deepcopy(list(self.preconditions)),
            "costs": copy.deepcopy(list(self.costs)),
            "duration": copy.deepcopy(self.duration),
            "cooldown": copy.deepcopy(self.cooldown),
            "result": copy.deepcopy(self.result),
            "enabled": self.enabled,
        }


def _get_field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _walk_results(result: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield result
    if result.get("mode") == "composite":
        for child in result.get("results", []):
            yield from _walk_results(child)


def validate_action_definitions(
    values: Any,
    *,
    entity_ids: Iterable[str] = (),
    agent_ids: Iterable[str] = (),
    resources: Mapping[str, Any] | Iterable[Any] = (),
    gameplay: GameplayContract | None = None,
) -> list[ActionValidationIssue]:
    """Validate action syntax and cross-references without mutating runtime state."""
    issues: list[ActionValidationIssue] = []
    entities = {str(item) for item in entity_ids}
    agents = {str(item) for item in agent_ids}
    if isinstance(resources, Mapping):
        resource_map = {str(key): value for key, value in resources.items()}
    else:
        resource_map = {str(_get_field(item, "name", "")): item for item in resources}
    definitions: list[tuple[ActionDefinition, str]] = []
    seen: set[str] = set()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return [ActionValidationIssue("actions.invalid", "Actions must be a list", "actions")]
    for index, raw in enumerate(values):
        path = f"actions[{index}]"
        try:
            definition = ActionDefinition.parse(_map(raw, "action.invalid", path), path)
        except ActionContractError as exc:
            issues.append(ActionValidationIssue(exc.code, str(exc), exc.path or path))
            continue
        if definition.action_id in seen:
            issues.append(ActionValidationIssue("actions.duplicate", f"Duplicate action_id: {definition.action_id}", path + ".action_id"))
            continue
        seen.add(definition.action_id)
        definitions.append((definition, path))
    controlled = {(item.kind, item.id) for item in (gameplay.player.controlled_objects if gameplay and gameplay.player else ())}
    actor_id = gameplay.player.actor_id if gameplay and gameplay.player else ""
    for definition, path in definitions:
        target = definition.target
        for item in target.get("allowed_ids", []):
            available = entities if target["kind"] == "entity" else agents
            if item not in available:
                issues.append(ActionValidationIssue("action.target.unknown_id", f"Unknown target id: {item}", path + ".target.allowed_ids"))
        for item in definition.permissions.get("control_ids", []):
            if gameplay and not any(control_id == item for _, control_id in controlled):
                issues.append(ActionValidationIssue("action.permissions.unknown_control", f"Unknown controlled object: {item}", path + ".permissions.control_ids"))
        for item in definition.permissions.get("actor_ids", []):
            if gameplay and actor_id and item != actor_id:
                issues.append(ActionValidationIssue("action.permissions.unknown_actor", f"Unknown player actor: {item}", path + ".permissions.actor_ids"))
        for index, condition in enumerate(definition.preconditions):
            if condition["kind"] in {"anchored_inventory", "metric", "parameter"}:
                resource = resource_map.get(condition["name"])
                expected_kind = "asset" if condition["kind"] == "anchored_inventory" else condition["kind"]
                if resource is None:
                    issues.append(ActionValidationIssue("action.precondition.unknown_resource", f"Unknown resource: {condition['name']}", f"{path}.preconditions[{index}].name"))
                elif _get_field(resource, "kind") != expected_kind:
                    issues.append(ActionValidationIssue("action.precondition.resource_kind", f"Resource {condition['name']} must be {expected_kind}", f"{path}.preconditions[{index}].name"))
            entity_ref = condition.get("entity_id")
            if isinstance(entity_ref, str) and not entity_ref.startswith("$") and entity_ref not in entities:
                issues.append(ActionValidationIssue("action.precondition.unknown_entity", f"Unknown entity: {entity_ref}", f"{path}.preconditions[{index}].entity_id"))
        for index, cost in enumerate(definition.costs):
            resource = resource_map.get(cost["resource_name"])
            if resource is None:
                issues.append(ActionValidationIssue("action.cost.unknown_resource", f"Unknown resource: {cost['resource_name']}", f"{path}.costs[{index}].resource_name"))
            elif _get_field(resource, "kind") != "asset":
                issues.append(ActionValidationIssue("action.cost.resource_kind", f"Cost resource {cost['resource_name']} must be asset", f"{path}.costs[{index}].resource_name"))
            entity_ref = cost.get("entity_id")
            if isinstance(entity_ref, str) and not entity_ref.startswith("$") and entity_ref not in entities:
                issues.append(ActionValidationIssue("action.cost.unknown_entity", f"Unknown entity: {entity_ref}", f"{path}.costs[{index}].entity_id"))
        for result in _walk_results(definition.result):
            if result.get("mode") in {"world_commands", "ongoing_event"}:
                effects = result.get("effects", []) if result.get("mode") == "world_commands" else [result.get("effect", {})]
                for effect in effects:
                    for key in ("entity_id", "from_entity_id", "to_entity_id"):
                        reference = effect.get(key)
                        if isinstance(reference, str) and reference and not reference.startswith("$") and reference not in entities:
                            issues.append(ActionValidationIssue("action.result.unknown_entity", f"Unknown entity: {reference}", path + ".result"))
                    for key in ("resource_name", "from_resource", "to_resource"):
                        name = effect.get(key)
                        if isinstance(name, str) and name and not name.startswith("$") and name not in resource_map:
                            issues.append(ActionValidationIssue("action.result.unknown_resource", f"Unknown resource: {name}", path + ".result"))
            if result.get("mode") == "wake_agent":
                agent_id = result.get("agent_id")
                if isinstance(agent_id, str) and not agent_id.startswith("$") and agent_id not in agents:
                    issues.append(ActionValidationIssue("action.result.unknown_agent", f"Unknown agent: {agent_id}", path + ".result.agent_id"))
            if result.get("mode") == "emit_event":
                for target_agent in result.get("event", {}).get("target_agents", []):
                    if isinstance(target_agent, str) and not target_agent.startswith("$") and target_agent not in agents:
                        issues.append(ActionValidationIssue("action.result.unknown_agent", f"Unknown agent: {target_agent}", path + ".result.event.target_agents"))
    return issues


@dataclass(frozen=True)
class ActionInvocation:
    invocation_id: str
    expected_generation: int | None = None
    actor_id: str = ""
    control_object_id: str = ""
    target_id: str = ""
    parameters: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | "ActionInvocation", *, require_id: bool = False) -> "ActionInvocation":
        if isinstance(value, cls):
            invocation = value
        else:
            raw = _map(value, "action.invocation.invalid", "invocation")
            invocation_id = str(raw.get("invocation_id", "")).strip()
            if invocation_id and (len(invocation_id) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", invocation_id)):
                raise ActionContractError("action.invocation.invalid_id", path="invocation.invocation_id")
            expected = raw.get("expected_generation")
            if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
                raise ActionContractError("action.invocation.invalid_generation", path="invocation.expected_generation")
            params = raw.get("parameters", {})
            if not isinstance(params, Mapping) or len(params) > 64:
                raise ActionContractError("action.invocation.invalid_parameters", path="invocation.parameters")
            invocation = cls(
                invocation_id=invocation_id,
                expected_generation=expected,
                actor_id=str(raw.get("actor_id", "")).strip(),
                control_object_id=str(raw.get("control_object_id", "")).strip(),
                target_id=str(raw.get("target_id", "")).strip(),
                parameters=copy.deepcopy(dict(params)),
            )
        if require_id and not invocation.invocation_id:
            raise ActionContractError("action.invocation.id_required", path="invocation.invocation_id")
        return invocation

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "expected_generation": self.expected_generation,
            "actor_id": self.actor_id,
            "control_object_id": self.control_object_id,
            "target_id": self.target_id,
            "parameters": copy.deepcopy(self.parameters or {}),
        }


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError, OverflowError):
        return False
    return {"lt": a < b, "lte": a <= b, "gte": a >= b, "gt": a > b}.get(operator, False)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class ActionCatalog:
    """Published action definitions plus monotonic execution receipts/cooldowns."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._definitions: dict[str, ActionDefinition] = {}
        self._generation = 0
        self._gameplay = GameplayContract()
        self._cooldowns: dict[str, float] = {}
        self._cooldown_order: deque[str] = deque()
        self._receipts: dict[str, dict[str, Any]] = {}
        self._receipt_order: deque[str] = deque()
        self._fingerprint = _canonical_hash([])

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def fingerprint(self) -> str:
        with self._lock:
            return self._fingerprint

    def configure(self, definitions: Iterable[Mapping[str, Any] | ActionDefinition], *, generation: int = 0, gameplay: GameplayContract | None = None) -> None:
        parsed: dict[str, ActionDefinition] = {}
        for index, raw in enumerate(definitions):
            definition = raw if isinstance(raw, ActionDefinition) else ActionDefinition.parse(_map(raw, "action.invalid", f"actions[{index}]"), f"actions[{index}]")
            if definition.action_id in parsed:
                raise ActionContractError("actions.duplicate", path=f"actions[{index}].action_id")
            parsed[definition.action_id] = definition
        canonical = [parsed[key].to_dict() for key in sorted(parsed)]
        with self._lock:
            self._definitions = parsed
            self._generation = max(0, int(generation))
            self._gameplay = gameplay or GameplayContract()
            self._fingerprint = _canonical_hash(canonical)

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "configured": bool(self._definitions),
                "generation": self._generation,
                "fingerprint": self._fingerprint,
                "actions": [self._definitions[key].to_dict() for key in sorted(self._definitions) if self._definitions[key].enabled],
            }

    def _definition(self, action_id: str) -> ActionDefinition:
        definition = self._definitions.get(str(action_id))
        if definition is None:
            raise ActionContractError("action.not_found", details={"action_id": str(action_id)})
        if not definition.enabled:
            raise ActionContractError("action.disabled", details={"action_id": definition.action_id})
        return definition

    def _normalize_parameters(self, definition: ActionDefinition, supplied: Mapping[str, Any]) -> dict[str, Any]:
        known = {item.name for item in definition.parameters}
        unknown = sorted(str(key) for key in supplied if key not in known)
        if unknown:
            raise ActionContractError("action.parameter.unknown", details={"parameters": unknown})
        result: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for parameter in definition.parameters:
            value, error = parameter.normalize(supplied.get(parameter.name), parameter.name in supplied)
            if error:
                errors[parameter.name] = error
            elif value is not None:
                result[parameter.name] = value
        if errors:
            raise ActionContractError("action.parameter.invalid_value", details={"parameters": errors})
        return result

    def _authorize(self, definition: ActionDefinition, invocation: ActionInvocation) -> tuple[str, Any]:
        player = self._gameplay.player
        if player is None:
            raise ActionContractError("action.gameplay_unconfigured")
        actor_id = invocation.actor_id or player.actor_id
        if invocation.actor_id and player.actor_id and invocation.actor_id != player.actor_id:
            raise ActionContractError("action.actor_forbidden")
        allowed_actors = definition.permissions.get("actor_ids", [])
        if allowed_actors and actor_id not in allowed_actors:
            raise ActionContractError("action.actor_forbidden")
        eligible = [
            item for item in player.controlled_objects
            if item.kind in definition.permissions["control_kinds"]
            and item.mode in definition.permissions["control_modes"]
            and (not definition.permissions["control_ids"] or item.id in definition.permissions["control_ids"])
        ]
        if invocation.control_object_id:
            control = next((item for item in eligible if item.id == invocation.control_object_id), None)
            if control is None:
                raise ActionContractError("action.control_forbidden")
        elif len(eligible) == 1:
            control = eligible[0]
        elif not eligible:
            raise ActionContractError("action.control_unavailable")
        else:
            raise ActionContractError("action.control_required", details={"control_ids": [item.id for item in eligible]})
        return actor_id, control

    def _normalize_target(self, definition: ActionDefinition, target_id: str, control: Any, world_state: Any, available_agent_ids: Iterable[str]) -> str:
        target = definition.target
        kind = target["kind"]
        if kind == "none":
            return ""
        value = str(target_id or "").strip()
        if not value and target["required"]:
            raise ActionContractError("action.target.required")
        if not value:
            return ""
        if target["allowed_ids"] and value not in target["allowed_ids"]:
            raise ActionContractError("action.target.forbidden")
        if target["controlled_only"] and not any(item.kind == kind and item.id == value for item in (self._gameplay.player.controlled_objects if self._gameplay.player else ())):
            raise ActionContractError("action.target.not_controlled")
        if kind == "entity":
            entity = world_state.get_entity(value) if world_state is not None else None
            if entity is None:
                raise ActionContractError("action.target.not_found")
            if target["entity_types"] and _get_field(entity, "type") not in target["entity_types"]:
                raise ActionContractError("action.target.invalid_type")
        elif available_agent_ids and value not in set(available_agent_ids):
            raise ActionContractError("action.target.not_found")
        return value

    @staticmethod
    def _resource(world_state: Any, name: str, expected_kind: str) -> Any:
        definition = world_state.get_resource_definition(name) if world_state is not None else None
        if definition is None:
            raise ActionContractError("action.resource.not_found", details={"resource_name": name})
        if _get_field(definition, "kind") != expected_kind:
            raise ActionContractError("action.resource.invalid_kind", details={"resource_name": name, "expected_kind": expected_kind})
        return definition

    @staticmethod
    def _inventory_item(world_state: Any, entity_id: str, name: str) -> Any:
        return next((item for item in world_state.get_inventory(entity_id) if _get_field(item, "item_name") == name), None)

    @staticmethod
    def _entity_value(world_state: Any, entity_id: str, name: str, kind: str) -> Any:
        return next((item for item in world_state.get_entity_values(entity_id, kind) if _get_field(item, "name") == name), None)

    def _evaluate_preconditions(self, definition: ActionDefinition, context: Mapping[str, Any], world_state: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for index, template in enumerate(definition.preconditions):
            condition = _resolve_refs(template, context)
            kind = condition["kind"]
            entity_id = str(condition.get("entity_id", ""))
            actual: Any = None
            expected = condition.get("value")
            if not world_state.get_entity(entity_id):
                passed = False
                error = "entity_not_found"
            elif kind == "entity_status":
                actual = _get_field(world_state.get_entity(entity_id), "current_status", "")
                passed = _compare(actual, condition["operator"], expected)
                error = "" if passed else "condition_failed"
            elif kind == "anchored_inventory":
                self._resource(world_state, condition["name"], "asset")
                item = self._inventory_item(world_state, entity_id, condition["name"])
                actual = float(_get_field(item, "base_value", 0.0)) if item else 0.0
                passed = _compare(actual, condition["operator"], expected)
                error = "" if passed else "condition_failed"
            else:
                self._resource(world_state, condition["name"], kind)
                item = self._entity_value(world_state, entity_id, condition["name"], kind)
                actual = _get_field(item, "value", None)
                passed = actual is not None and _compare(actual, condition["operator"], expected)
                error = "" if passed else "condition_failed"
            results.append({"index": index, "kind": kind, "entity_id": entity_id, "name": condition.get("name", ""), "operator": condition["operator"], "expected": expected, "actual": actual, "ok": passed, "error": error})
        return results

    def _materialize_costs(self, definition: ActionDefinition, context: Mapping[str, Any], world_state: Any) -> list[dict[str, Any]]:
        costs: list[dict[str, Any]] = []
        for index, template in enumerate(definition.costs):
            cost = _resolve_refs(template, context)
            entity_id = str(cost["entity_id"])
            if not world_state.get_entity(entity_id):
                raise ActionContractError("action.cost.entity_not_found", details={"entity_id": entity_id})
            resource = self._resource(world_state, str(cost["resource_name"]), "asset")
            anchor = float(_get_field(resource, "anchor_rate", 0.0) or 0.0)
            if not math.isfinite(anchor) or anchor <= 0:
                raise ActionContractError("action.cost.invalid_anchor", details={"resource_name": cost["resource_name"]})
            base_value = float(cost["base_value"])
            quantity = base_value / anchor
            item = self._inventory_item(world_state, entity_id, str(cost["resource_name"]))
            available_quantity = float(_get_field(item, "quantity", 0.0)) if item else 0.0
            costs.append({
                "index": index,
                "entity_id": entity_id,
                "resource_name": str(cost["resource_name"]),
                "base_value": base_value,
                "anchor_rate": anchor,
                "quantity": quantity,
                "available_quantity": available_quantity,
                "available_base_value": available_quantity * anchor,
                "reason": str(cost.get("reason", "")),
                "ok": available_quantity >= quantity,
            })
        return costs


    def _estimate_duration(self, definition: ActionDefinition, context: Mapping[str, Any], time_simulator: TimeSimulator) -> dict[str, Any]:
        duration = _resolve_refs(definition.duration, context)
        estimate = time_simulator.estimate(
            action_type=str(duration.get("action_type") or definition.action_id),
            proposed_days=duration.get("proposed_days", 1.0),
            severity=duration.get("severity", 5),
            workload_days=duration.get("workload_days"),
            resource_value=duration.get("resource_value", 0.0),
            distance_days=duration.get("distance_days", 0.0),
            success=bool(duration.get("success", True)),
        )
        return estimate.public_metadata()

    @staticmethod
    def _cooldown_key(definition: ActionDefinition, control_id: str, target_id: str) -> str:
        scope = definition.cooldown["scope"]
        parts = [definition.action_id]
        if scope in {"control", "control_target"}:
            parts.append(control_id or "-")
        if scope in {"target", "control_target"}:
            parts.append(target_id or "-")
        return "|".join(parts)

    def _cooldown_state(self, definition: ActionDefinition, control_id: str, target_id: str, sim_time: float) -> dict[str, Any]:
        key = self._cooldown_key(definition, control_id, target_id)
        ready_at = float(self._cooldowns.get(key, 0.0))
        remaining_seconds = max(0.0, ready_at - float(sim_time))
        return {
            "scope": definition.cooldown["scope"],
            "days": float(definition.cooldown["days"]),
            "key": key,
            "ready": remaining_seconds <= 0,
            "ready_at": ready_at,
            "remaining_days": round(remaining_seconds / GAME_SECONDS_PER_DAY, 6),
        }

    def _compile_result(self, result: Mapping[str, Any], context: Mapping[str, Any], world_state: Any, sim_time: float, source_id: str, chain_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        mode = result["mode"]
        commands: list[dict[str, Any]] = []
        side_effects: list[dict[str, Any]] = []
        if mode in {"world_commands", "ongoing_event"}:
            templates = result.get("effects", []) if mode == "world_commands" else [result["effect"]]
            resolved = _resolve_refs(templates, context)
            try:
                effects = normalize_world_effects(resolved)
                commands.extend(materialize_world_commands(effects, world_state=world_state, due_at=sim_time, source_id=source_id, chain_id=chain_id))
            except CausalEventError as exc:
                raise ActionContractError("action.result.invalid_world_effect", str(exc)) from exc
        elif mode == "wake_agent":
            side_effects.append({"kind": "wake_agent", "agent_id": str(_resolve_refs(result["agent_id"], context)), "payload": _resolve_refs(result.get("payload", {}), context)})
        elif mode == "emit_event":
            event = _resolve_refs(result["event"], context)
            side_effects.append({"kind": "emit_event", "event": event})
        else:
            for index, child in enumerate(result.get("results", [])):
                child_commands, child_effects = self._compile_result(child, context, world_state, sim_time, f"{source_id}:{index}", chain_id)
                commands.extend(child_commands)
                side_effects.extend(child_effects)
        return commands, side_effects

    @staticmethod
    def _command_from_plan(plan: Mapping[str, Any], *, invocation_id: str, index: int, chain_id: str, sim_time: float, metadata: Mapping[str, Any]) -> WorldCommand:
        command_type = WorldCommandType(str(plan["command_type"]))
        command_id = "wcmd_action_" + hashlib.sha256(f"{invocation_id}\0{index}".encode("utf-8")).hexdigest()[:32]
        return WorldCommand(
            command_type=command_type,
            payload=copy.deepcopy(dict(plan.get("payload", {}))),
            command_id=command_id,
            entity_id=str(plan.get("entity_id", "")),
            chain_id=chain_id,
            intent_id=invocation_id,
            sim_time=float(sim_time),
            metadata=copy.deepcopy(dict(metadata)),
        )

    def preview(
        self,
        action_id: str,
        invocation: Mapping[str, Any] | ActionInvocation,
        *,
        world_state: Any,
        time_simulator: TimeSimulator,
        sim_time: float = 0.0,
        available_agent_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        with self._lock:
            return self._preview_locked(
                action_id, invocation, world_state=world_state,
                time_simulator=time_simulator, sim_time=sim_time,
                available_agent_ids=available_agent_ids,
            )

    def _preview_locked(
        self,
        action_id: str,
        invocation: Mapping[str, Any] | ActionInvocation,
        *,
        world_state: Any,
        time_simulator: TimeSimulator,
        sim_time: float = 0.0,
        available_agent_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        definition = self._definition(action_id)
        request = ActionInvocation.from_mapping(invocation)
        if request.expected_generation is not None and request.expected_generation != self._generation:
            raise ActionContractError("action.generation_conflict", details={"expected_generation": request.expected_generation, "actual_generation": self._generation})
        if world_state is None:
            raise ActionContractError("action.world_unavailable")
        actor_id, control = self._authorize(definition, request)
        parameters = self._normalize_parameters(definition, request.parameters or {})
        target_id = self._normalize_target(definition, request.target_id, control, world_state, available_agent_ids)
        normalized_invocation = ActionInvocation(
            invocation_id=request.invocation_id,
            expected_generation=request.expected_generation,
            actor_id=actor_id,
            control_object_id=control.id,
            target_id=target_id,
            parameters=parameters,
        )
        context: dict[str, Any] = {"actor": actor_id, "control": control.id, "target": target_id, "parameters": parameters, "duration_days": 0.0}
        preconditions = self._evaluate_preconditions(definition, context, world_state)
        costs = self._materialize_costs(definition, context, world_state)
        duration = self._estimate_duration(definition, context, time_simulator)
        context["duration_days"] = duration["duration_days"]
        source_id = request.invocation_id or ("preview_" + _canonical_hash({"action_id": definition.action_id, "invocation": normalized_invocation.to_dict()})[:24])
        chain_id = f"action:{source_id}"
        result_commands, side_effects = self._compile_result(definition.result, context, world_state, float(sim_time), source_id, chain_id)
        cost_commands = [
            {
                "command_type": "adjust_resource",
                "entity_id": cost["entity_id"],
                "payload": {
                    "resource_name": cost["resource_name"],
                    "quantity_delta": -cost["quantity"],
                    "reason": cost["reason"] or f"action:{definition.action_id}",
                    "anchored_base_value": cost["base_value"],
                    "anchor_rate": cost["anchor_rate"],
                },
            }
            for cost in costs
        ]
        plans = cost_commands + result_commands
        commands = [
            self._command_from_plan(plan, invocation_id=source_id, index=index, chain_id=chain_id, sim_time=sim_time, metadata={"source": "dlc_action", "action_id": definition.action_id, "generation": self._generation}).canonical_payload() | {"command_id": "wcmd_action_" + hashlib.sha256(f"{source_id}\0{index}".encode("utf-8")).hexdigest()[:32]}
            for index, plan in enumerate(plans)
        ]
        cooldown = self._cooldown_state(definition, control.id, target_id, sim_time)
        ok = all(item["ok"] for item in preconditions) and all(item["ok"] for item in costs) and cooldown["ready"]
        return {
            "ok": ok,
            "action_id": definition.action_id,
            "display_name": definition.display_name,
            "generation": self._generation,
            "invocation": normalized_invocation.to_dict(),
            "permission": {"ok": True, "actor_id": actor_id, "control": control.to_dict()},
            "preconditions": preconditions,
            "costs": costs,
            "duration": duration,
            "cooldown": cooldown,
            "world_commands": commands,
            "side_effects": copy.deepcopy(side_effects),
        }

    def _remember_cooldown(self, key: str, ready_at: float) -> None:
        if key not in self._cooldowns:
            self._cooldown_order.append(key)
        self._cooldowns[key] = max(float(self._cooldowns.get(key, 0.0)), float(ready_at))
        while len(self._cooldown_order) > MAX_COOLDOWNS:
            oldest = self._cooldown_order.popleft()
            self._cooldowns.pop(oldest, None)

    def _remember_receipt(self, invocation_id: str, receipt: dict[str, Any]) -> None:
        if invocation_id not in self._receipts:
            self._receipt_order.append(invocation_id)
        self._receipts[invocation_id] = copy.deepcopy(receipt)
        while len(self._receipt_order) > MAX_RECEIPTS:
            oldest = self._receipt_order.popleft()
            self._receipts.pop(oldest, None)


    def execute(
        self,
        action_id: str,
        invocation: Mapping[str, Any] | ActionInvocation,
        *,
        world_state: Any,
        time_simulator: TimeSimulator,
        sim_time: float = 0.0,
        available_agent_ids: Iterable[str] = (),
        wake_callback: Callable[[str, dict[str, Any], dict[str, Any]], Any] | None = None,
        emit_callback: Callable[[EventObject], Any] | None = None,
    ) -> dict[str, Any]:
        # Serialize receipt lookup, authoritative preview and commit. This makes
        # invocation_id idempotency effective even under concurrent API calls.
        with self._lock:
            return self._execute_locked(
                action_id, invocation, world_state=world_state,
                time_simulator=time_simulator, sim_time=sim_time,
                available_agent_ids=available_agent_ids,
                wake_callback=wake_callback, emit_callback=emit_callback,
            )

    def _execute_locked(
        self,
        action_id: str,
        invocation: Mapping[str, Any] | ActionInvocation,
        *,
        world_state: Any,
        time_simulator: TimeSimulator,
        sim_time: float = 0.0,
        available_agent_ids: Iterable[str] = (),
        wake_callback: Callable[[str, dict[str, Any], dict[str, Any]], Any] | None = None,
        emit_callback: Callable[[EventObject], Any] | None = None,
    ) -> dict[str, Any]:
        request = ActionInvocation.from_mapping(invocation, require_id=True)
        if request.expected_generation is not None and request.expected_generation != self._generation:
            raise ActionContractError("action.generation_conflict", details={"expected_generation": request.expected_generation, "actual_generation": self._generation})
        request_hash = _canonical_hash({"action_id": str(action_id), "invocation": request.to_dict()})
        existing = self._receipts.get(request.invocation_id)
        if existing is not None:
            if existing.get("request_hash") != request_hash:
                raise ActionContractError("action.invocation.conflict", details={"invocation_id": request.invocation_id})
            replay = copy.deepcopy(existing["response"])
            replay["replayed"] = True
            return replay
        definition = self._definition(action_id)
        preview = self.preview(
            action_id,
            request,
            world_state=world_state,
            time_simulator=time_simulator,
            sim_time=sim_time,
            available_agent_ids=available_agent_ids,
        )
        if not preview["ok"]:
            failed = {
                "preconditions": [item for item in preview["preconditions"] if not item["ok"]],
                "costs": [item for item in preview["costs"] if not item["ok"]],
                "cooldown": preview["cooldown"],
            }
            raise ActionContractError("action.not_ready", details=failed)
        chain_id = f"action:{request.invocation_id}"
        commands: list[WorldCommand] = []
        for index, item in enumerate(preview["world_commands"]):
            commands.append(WorldCommand(
                command_type=WorldCommandType(str(item["command_type"])),
                payload=copy.deepcopy(dict(item.get("payload", {}))),
                command_id=str(item["command_id"]),
                entity_id=str(item.get("entity_id", "")),
                expected_version=item.get("expected_version"),
                chain_id=chain_id,
                intent_id=request.invocation_id,
                sim_time=float(sim_time),
                metadata={"source": "dlc_action", "action_id": definition.action_id, "generation": self._generation},
            ))
        batch = world_state.command_bus.execute_batch(commands, atomic=True)
        if not batch.ok or not batch.committed:
            raise ActionContractError("action.world_command_failed", details={"batch": batch.to_dict()})
        callback_results: list[dict[str, Any]] = []
        for index, side_effect in enumerate(preview["side_effects"]):
            try:
                if side_effect["kind"] == "wake_agent":
                    agent_id = str(side_effect.get("agent_id", ""))
                    if not agent_id:
                        raise ValueError("empty agent id")
                    if available_agent_ids and agent_id not in set(available_agent_ids):
                        raise ValueError("unknown agent id")
                    if wake_callback is not None:
                        wake_callback(agent_id, copy.deepcopy(side_effect.get("payload", {})), {"chain_id": chain_id, "invocation_id": request.invocation_id, "action_id": definition.action_id})
                    callback_results.append({"index": index, "kind": "wake_agent", "ok": True, "agent_id": agent_id})
                else:
                    document = side_effect.get("event", {})
                    event_id = "evt_action_" + hashlib.sha256(f"{request.invocation_id}\0{index}".encode("utf-8")).hexdigest()[:24]
                    event = EventObject(
                        event_id=event_id,
                        event_type=str(document.get("event_type", "")),
                        severity=int(document.get("severity", 5)),
                        target_agents=[str(item) for item in document.get("target_agents", [])],
                        initial_payload=copy.deepcopy(dict(document.get("initial_payload", {}))),
                        generated_at=float(sim_time),
                    )
                    if emit_callback is not None:
                        emit_callback(event)
                    callback_results.append({"index": index, "kind": "emit_event", "ok": True, "event_id": event_id})
            except Exception as exc:  # world commands are already committed; report sidecar failure
                callback_results.append({"index": index, "kind": side_effect.get("kind", "unknown"), "ok": False, "error": str(exc)})
        cooldown_days = float(definition.cooldown["days"])
        ready_at = float(sim_time) + cooldown_days * GAME_SECONDS_PER_DAY
        self._remember_cooldown(preview["cooldown"]["key"], ready_at)
        response = {
            "ok": True,
            "status": "executed",
            "replayed": False,
            "action_id": definition.action_id,
            "generation": self._generation,
            "invocation_id": request.invocation_id,
            "invocation": preview["invocation"],
            "duration": preview["duration"],
            "costs": preview["costs"],
            "batch": batch.to_dict(),
            "side_effects": callback_results,
            "cooldown": self._cooldown_state(definition, preview["invocation"]["control_object_id"], preview["invocation"]["target_id"], sim_time),
        }
        self._remember_receipt(request.invocation_id, {"request_hash": request_hash, "response": response})
        return copy.deepcopy(response)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "cooldowns": {key: self._cooldowns[key] for key in self._cooldown_order if key in self._cooldowns},
            "cooldown_order": [key for key in self._cooldown_order if key in self._cooldowns],
            "receipts": {key: copy.deepcopy(self._receipts[key]) for key in self._receipt_order if key in self._receipts},
            "receipt_order": [key for key in self._receipt_order if key in self._receipts],
        }

    def restore(self, value: Any, *, sim_time: float = 0.0) -> None:
        with self._lock:
            self._restore_locked(value, sim_time=sim_time)

    def _restore_locked(self, value: Any, *, sim_time: float = 0.0) -> None:
        if value is None:
            return
        if not isinstance(value, Mapping):
            raise ActionContractError("action.snapshot.invalid")
        cooldowns = value.get("cooldowns", {})
        receipts = value.get("receipts", {})
        if not isinstance(cooldowns, Mapping) or not isinstance(receipts, Mapping):
            raise ActionContractError("action.snapshot.invalid")
        cooldown_order = value.get("cooldown_order", list(cooldowns))
        receipt_order = value.get("receipt_order", list(receipts))
        if not isinstance(cooldown_order, Sequence) or isinstance(cooldown_order, (str, bytes, bytearray)):
            raise ActionContractError("action.snapshot.invalid")
        if not isinstance(receipt_order, Sequence) or isinstance(receipt_order, (str, bytes, bytearray)):
            raise ActionContractError("action.snapshot.invalid")
        for raw_key in cooldown_order[-MAX_COOLDOWNS:]:
            key = str(raw_key)
            if key not in cooldowns:
                continue
            ready_at = _num(cooldowns[key], "action.snapshot.invalid", "cooldowns", 0.0)
            self._remember_cooldown(key, ready_at)
        for raw_id in receipt_order[-MAX_RECEIPTS:]:
            invocation_id = str(raw_id)
            receipt = receipts.get(invocation_id)
            if not isinstance(receipt, Mapping) or not isinstance(receipt.get("response"), Mapping) or not isinstance(receipt.get("request_hash"), str):
                raise ActionContractError("action.snapshot.invalid")
            self._remember_receipt(invocation_id, copy.deepcopy(dict(receipt)))
        # Expired cooldown entries are retained as a bounded audit key set. This
        # makes restore monotonic and prevents a generation rollback from reviving
        # an older, later ready_at value over a newer snapshot.
        _ = float(sim_time)
