"""Generic DLC-authored causal effect contracts and immutable command plans.

The module is deliberately free of orchestrator/runtime dependencies so the same
normalizer and materializer can be used by causal rules, DLC actions, Registry
validation, snapshot restore, and isolated dry-runs.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
import math
import re
from typing import Any

from .causal_conditions import CausalEventError
from .event_generator import GAME_SECONDS_PER_DAY

SAFE_EFFECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UNICODE_EFFECT_ID = re.compile(r"^[^\x00-\x1f\x7f/\\]{1,128}$")
MAX_WORLD_EFFECTS = 32
MAX_WORLD_COMMANDS = 64
MAX_ID_LENGTH = 128

WORLD_EFFECT_TYPES = frozenset({
    "adjust_resource", "set_resource", "update_status",
    "create_ongoing_event", "transfer_anchored_value",
    "create_entity", "move_entity", "transform_entity", "remove_entity",
    "enable_agent", "disable_agent", "modify_relationship",
    "modify_membership", "create_issue", "reveal_information",
    "advance_objective", "unlock_action", "switch_phase",
})

MATERIALIZED_COMMAND_TYPES = frozenset({
    "adjust_resource", "set_resource", "update_status", "create_event",
    "create_entity", "move_entity", "transform_entity", "remove_entity",
    "set_agent_enabled", "modify_relationship", "modify_membership",
    "create_issue", "reveal_information", "advance_objective",
    "unlock_action", "switch_phase",
})


def _finite(value: Any, code: str) -> float:
    if isinstance(value, bool):
        raise CausalEventError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CausalEventError(code) from exc
    if not math.isfinite(number):
        raise CausalEventError(code)
    return number


def _text(value: Any, code: str, *, required: bool = True, limit: int = MAX_ID_LENGTH) -> str:
    text = str(value or "").strip()
    if (required and not text) or len(text) > limit:
        raise CausalEventError(code)
    return text


def _identifier(value: Any, code: str, *, required: bool = True) -> str:
    text = _text(value, code, required=required)
    if text and not (SAFE_EFFECT_ID.fullmatch(text) or UNICODE_EFFECT_ID.fullmatch(text)):
        raise CausalEventError(code)
    return text


def _safe_json(value: Any, code: str, *, depth: int = 0) -> Any:
    if depth > 8:
        raise CausalEventError(code)
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > 12000:
            raise CausalEventError(code)
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _finite(value, code)
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise CausalEventError(code)
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _text(raw_key, code, limit=128)
            result[key] = _safe_json(raw_value, code, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 128:
            raise CausalEventError(code)
        return [_safe_json(item, code, depth=depth + 1) for item in value]
    raise CausalEventError(code)


def _object(value: Any, code: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CausalEventError(code)
    result = _safe_json(value, code)
    assert isinstance(result, dict)
    return result


def _normalize_expected_result(value: Any) -> dict[str, Any]:
    result = _object(value, "causal_effect_expected_result_invalid")
    normalized: dict[str, Any] = {}
    if "new_status" in result:
        status = _text(result.get("new_status"), "causal_effect_expected_result_invalid", required=False, limit=512)
        if status:
            normalized["new_status"] = status
    deltas = result.get("inventory_deltas", [])
    if not isinstance(deltas, list) or len(deltas) > 32:
        raise CausalEventError("causal_effect_expected_result_invalid")
    normalized_deltas = []
    for raw in deltas:
        if not isinstance(raw, Mapping):
            raise CausalEventError("causal_effect_expected_result_invalid")
        item = {
            "resource": _identifier(raw.get("resource", raw.get("resource_name", "")), "causal_effect_resource_invalid"),
            "delta": _finite(raw.get("delta"), "causal_effect_quantity_invalid"),
        }
        reason = _text(raw.get("reason", ""), "causal_effect_reason_invalid", required=False, limit=512)
        if reason:
            item["reason"] = reason
        normalized_deltas.append(item)
    if normalized_deltas:
        normalized["inventory_deltas"] = normalized_deltas
    return normalized


def normalize_world_effect(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CausalEventError("causal_effect_invalid")
    effect_type = str(value.get("effect_type", value.get("type", ""))).strip()
    if effect_type not in WORLD_EFFECT_TYPES:
        raise CausalEventError("causal_effect_type_invalid")
    reason = _text(value.get("reason", ""), "causal_effect_reason_invalid", required=False, limit=512)

    if effect_type in {"adjust_resource", "set_resource"}:
        result = {
            "effect_type": effect_type,
            "entity_id": _identifier(value.get("entity_id"), "causal_effect_entity_invalid"),
            "resource_name": _identifier(value.get("resource_name"), "causal_effect_resource_invalid"),
        }
        key = "quantity_delta" if effect_type == "adjust_resource" else "quantity"
        result[key] = _finite(value.get(key), "causal_effect_quantity_invalid")
    elif effect_type == "update_status":
        result = {
            "effect_type": effect_type,
            "entity_id": _identifier(value.get("entity_id"), "causal_effect_entity_invalid"),
            "new_status": _text(value.get("new_status"), "causal_effect_status_invalid", limit=512),
        }
    elif effect_type == "create_ongoing_event":
        duration = _finite(value.get("duration_days", 0), "causal_effect_duration_invalid")
        if not 0 <= duration <= 3650:
            raise CausalEventError("causal_effect_duration_invalid")
        result = {
            "effect_type": effect_type,
            "entity_id": _identifier(value.get("entity_id"), "causal_effect_entity_invalid"),
            "event_type": _identifier(value.get("event_type"), "causal_effect_event_type_invalid"),
            "description": _text(value.get("description", ""), "causal_effect_description_invalid", required=False, limit=1000),
            "duration_days": duration,
            "expected_result": _normalize_expected_result(value.get("expected_result", {})),
        }
    elif effect_type == "transfer_anchored_value":
        base_value = _finite(value.get("base_value"), "causal_effect_base_value_invalid")
        if base_value <= 0:
            raise CausalEventError("causal_effect_base_value_invalid")
        result = {
            "effect_type": effect_type,
            "from_entity_id": _identifier(value.get("from_entity_id"), "causal_effect_entity_invalid"),
            "from_resource": _identifier(value.get("from_resource"), "causal_effect_resource_invalid"),
            "to_entity_id": _identifier(value.get("to_entity_id"), "causal_effect_entity_invalid"),
            "to_resource": _identifier(value.get("to_resource"), "causal_effect_resource_invalid"),
            "base_value": base_value,
        }
    elif effect_type == "create_entity":
        metadata = _object(value.get("metadata", {}), "causal_effect_metadata_invalid")
        resources = _object(value.get("resources", {}), "causal_effect_resources_invalid")
        normalized_resources = {
            _identifier(key, "causal_effect_resource_invalid"): _finite(amount, "causal_effect_quantity_invalid")
            for key, amount in resources.items()
        }
        result = {
            "effect_type": effect_type,
            "entity_id": _identifier(value.get("entity_id", value.get("id")), "causal_effect_entity_invalid"),
            "entity_type": _identifier(value.get("entity_type", value.get("type")), "causal_effect_entity_type_invalid"),
            "name": _text(value.get("name"), "causal_effect_entity_name_invalid", limit=512),
            "description": _text(value.get("description", ""), "causal_effect_description_invalid", required=False, limit=4000),
            "current_status": _text(value.get("current_status", value.get("status", "")), "causal_effect_status_invalid", required=False, limit=512),
            "metadata": metadata,
            "resources": normalized_resources,
        }
    elif effect_type == "move_entity":
        result = {"effect_type": effect_type, "entity_id": _identifier(value.get("entity_id"), "causal_effect_entity_invalid")}
        present = False
        for key in ("x", "y"):
            if value.get(key) is not None:
                result[key] = _finite(value.get(key), "causal_effect_coordinates_invalid")
                present = True
        for key, limit in (("region", 256), ("location_id", MAX_ID_LENGTH)):
            if value.get(key) is not None:
                result[key] = _text(value.get(key), "causal_effect_location_invalid", required=False, limit=limit)
                present = True
        if not present:
            raise CausalEventError("causal_effect_location_invalid")
    elif effect_type == "transform_entity":
        result = {"effect_type": effect_type, "entity_id": _identifier(value.get("entity_id"), "causal_effect_entity_invalid")}
        mapping = {
            "new_type": ("causal_effect_entity_type_invalid", MAX_ID_LENGTH),
            "new_name": ("causal_effect_entity_name_invalid", 512),
            "new_description": ("causal_effect_description_invalid", 4000),
            "new_status": ("causal_effect_status_invalid", 512),
        }
        for key, (code, limit) in mapping.items():
            if key in value:
                result[key] = _text(value.get(key), code, required=False, limit=limit)
        if "metadata_patch" in value:
            result["metadata_patch"] = _object(value.get("metadata_patch"), "causal_effect_metadata_invalid")
        if len(result) == 2:
            raise CausalEventError("causal_effect_transform_empty")
    elif effect_type == "remove_entity":
        result = {"effect_type": effect_type, "entity_id": _identifier(value.get("entity_id"), "causal_effect_entity_invalid")}
    elif effect_type in {"enable_agent", "disable_agent"}:
        result = {
            "effect_type": effect_type,
            "agent_id": _identifier(value.get("agent_id"), "causal_effect_agent_invalid"),
            "actor_id": _identifier(value.get("actor_id", ""), "causal_effect_actor_invalid", required=False),
            "metadata": _object(value.get("metadata", {}), "causal_effect_metadata_invalid"),
        }
    elif effect_type == "modify_relationship":
        operation = str(value.get("operation", "upsert")).strip().lower()
        if operation not in {"upsert", "remove"}:
            raise CausalEventError("causal_effect_relationship_operation_invalid")
        result = {
            "effect_type": effect_type, "operation": operation,
            "parent_id": _identifier(value.get("parent_id"), "causal_effect_entity_invalid"),
            "child_id": _identifier(value.get("child_id"), "causal_effect_entity_invalid"),
            "rel_type": _identifier(value.get("rel_type"), "causal_effect_relationship_type_invalid"),
        }
        if operation == "upsert":
            if value.get("strength") is not None:
                result["strength"] = _finite(value.get("strength"), "causal_effect_relationship_strength_invalid")
            if value.get("visibility") is not None:
                result["visibility"] = _text(value.get("visibility"), "causal_effect_relationship_visibility_invalid", limit=32)
            starts_in = _finite(value.get("starts_in_days", 0), "causal_effect_relationship_time_invalid")
            duration = value.get("duration_days")
            if starts_in < 0 or starts_in > 3650:
                raise CausalEventError("causal_effect_relationship_time_invalid")
            result["starts_in_days"] = starts_in
            if duration is not None:
                duration_value = _finite(duration, "causal_effect_relationship_time_invalid")
                if duration_value <= 0 or duration_value > 3650:
                    raise CausalEventError("causal_effect_relationship_time_invalid")
                result["duration_days"] = duration_value
            result["values"] = _object(value.get("values", {}), "causal_effect_relationship_values_invalid")
            result["note"] = _text(value.get("note", ""), "causal_effect_relationship_note_invalid", required=False, limit=2000)
            result["metadata"] = _object(value.get("metadata", {}), "causal_effect_metadata_invalid")
    elif effect_type == "modify_membership":
        operation = str(value.get("operation", "upsert")).strip().lower()
        if operation not in {"upsert", "remove"}:
            raise CausalEventError("causal_effect_membership_operation_invalid")
        result = {
            "effect_type": effect_type, "operation": operation,
            "actor_id": _identifier(value.get("actor_id"), "causal_effect_actor_invalid"),
            "organization_id": _identifier(value.get("organization_id"), "causal_effect_entity_invalid"),
        }
        if operation == "upsert":
            result.update({
                "role": _text(value.get("role", "member"), "causal_effect_membership_role_invalid", limit=256),
                "status": _text(value.get("status", "active"), "causal_effect_membership_status_invalid", limit=128),
                "metadata": _object(value.get("metadata", {}), "causal_effect_metadata_invalid"),
            })
    elif effect_type == "create_issue":
        source = _object(value.get("source", {}), "causal_effect_issue_source_invalid")
        if not source:
            source = {"kind": "system", "id": "causal_event"}
        deadline = value.get("deadline_days")
        result = {
            "effect_type": effect_type,
            "issue_type_id": _identifier(value.get("issue_type_id"), "causal_effect_issue_type_invalid"),
            "source": source,
            "title": _text(value.get("title", ""), "causal_effect_issue_title_invalid", required=False, limit=512),
            "summary": _text(value.get("summary", value.get("description", "")), "causal_effect_issue_summary_invalid", required=False, limit=12000),
            "details": _object(value.get("details", {}), "causal_effect_issue_details_invalid"),
            "evidence": _safe_json(value.get("evidence", []), "causal_effect_issue_evidence_invalid"),
            "urgency": _text(value.get("urgency", "normal"), "causal_effect_issue_urgency_invalid", limit=32),
            "metadata": _object(value.get("metadata", {}), "causal_effect_metadata_invalid"),
        }
        if not isinstance(result["evidence"], list):
            raise CausalEventError("causal_effect_issue_evidence_invalid")
        if deadline is not None:
            deadline_value = _finite(deadline, "causal_effect_issue_deadline_invalid")
            if deadline_value < 0 or deadline_value > 3650:
                raise CausalEventError("causal_effect_issue_deadline_invalid")
            result["deadline_days"] = deadline_value
    elif effect_type == "reveal_information":
        confidence = _finite(value.get("confidence", 1.0), "causal_effect_knowledge_confidence_invalid")
        if not 0 <= confidence <= 1:
            raise CausalEventError("causal_effect_knowledge_confidence_invalid")
        result = {
            "effect_type": effect_type,
            "entity_id": _identifier(value.get("entity_id", ""), "causal_effect_entity_invalid", required=False),
            "knowledge_id": _identifier(value.get("knowledge_id", value.get("name")), "causal_effect_knowledge_invalid"),
            "value": _safe_json(value.get("value", True), "causal_effect_knowledge_value_invalid"),
            "source": _text(value.get("source", "causal_event"), "causal_effect_knowledge_source_invalid", limit=256),
            "confidence": confidence,
            "metadata": _object(value.get("metadata", {}), "causal_effect_metadata_invalid"),
        }
    elif effect_type == "advance_objective":
        mode = str(value.get("mode", "adjust")).strip().lower()
        if mode not in {"adjust", "set"}:
            raise CausalEventError("causal_effect_objective_mode_invalid")
        amount_key = "amount" if mode == "adjust" else "progress"
        amount = _finite(value.get(amount_key), "causal_effect_objective_progress_invalid")
        result = {
            "effect_type": effect_type,
            "objective_id": _identifier(value.get("objective_id"), "causal_effect_objective_invalid"),
            "mode": mode,
            amount_key: amount,
        }
        if value.get("status") is not None:
            result["status"] = _text(value.get("status"), "causal_effect_objective_status_invalid", limit=64)
    elif effect_type == "unlock_action":
        result = {
            "effect_type": effect_type,
            "action_id": _identifier(value.get("action_id"), "causal_effect_action_invalid"),
            "scope_id": _identifier(value.get("scope_id", ""), "causal_effect_scope_invalid", required=False),
        }
    else:  # switch_phase
        result = {
            "effect_type": effect_type,
            "phase_id": _identifier(value.get("phase_id"), "causal_effect_phase_invalid"),
            "metadata": _object(value.get("metadata", {}), "causal_effect_metadata_invalid"),
        }

    if reason:
        result["reason"] = reason
    return result


def normalize_world_effects(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)) or len(values) > MAX_WORLD_EFFECTS:
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
    anchor = _finite(rate, "causal_effect_anchor_invalid")
    if anchor <= 0:
        raise CausalEventError("causal_effect_anchor_invalid")
    return anchor


def _relationship_plan(effect: Mapping[str, Any], world_state: Any, due_at: float) -> dict[str, Any]:
    catalog = getattr(world_state, "relationship_catalog", None)
    if catalog is None:
        raise CausalEventError("causal_effect_relationship_catalog_unavailable")
    raw = {
        "parent_id": effect["parent_id"], "child_id": effect["child_id"],
        "rel_type": effect["rel_type"],
    }
    if effect["operation"] == "remove":
        definition = catalog.get(effect["rel_type"])
        if definition is None:
            raise CausalEventError("causal_effect_relationship_type_invalid")
        if definition.direction == "undirected" and raw["child_id"] < raw["parent_id"]:
            raw["parent_id"], raw["child_id"] = raw["child_id"], raw["parent_id"]
        return {"command_type": "modify_relationship", "entity_id": raw["parent_id"], "payload": {**raw, "operation": "remove"}}
    starts_at = due_at + float(effect.get("starts_in_days", 0)) * GAME_SECONDS_PER_DAY
    raw.update({
        "strength": effect.get("strength"), "visibility": effect.get("visibility"),
        "starts_at": starts_at,
        "values": copy.deepcopy(effect.get("values", {})),
        "note": effect.get("note", ""), "metadata": copy.deepcopy(effect.get("metadata", {})),
    })
    if effect.get("duration_days") is not None:
        raw["expires_at"] = starts_at + float(effect["duration_days"]) * GAME_SECONDS_PER_DAY
    raw = {key: value for key, value in raw.items() if value is not None}
    try:
        normalized = catalog.normalize_relation(raw, "world_effect")
    except Exception as exc:
        raise CausalEventError(getattr(exc, "code", "causal_effect_relationship_invalid")) from exc
    return {"command_type": "modify_relationship", "entity_id": normalized["parent_id"], "payload": {"operation": "upsert", **normalized}}


def materialize_world_commands(
    effects: Sequence[Mapping[str, Any]], *, world_state: Any, due_at: float,
    source_id: str, chain_id: str,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for effect_index, effect in enumerate(effects):
        effect_type = str(effect["effect_type"])
        plans: list[dict[str, Any]] = []
        if effect_type in {"adjust_resource", "set_resource"}:
            key = "quantity_delta" if effect_type == "adjust_resource" else "quantity"
            plans.append({"command_type": effect_type, "entity_id": effect["entity_id"], "payload": {"resource_name": effect["resource_name"], key: effect[key], "reason": effect.get("reason", "")}})
        elif effect_type == "update_status":
            plans.append({"command_type": "update_status", "entity_id": effect["entity_id"], "payload": {"new_status": effect["new_status"]}})
        elif effect_type == "create_ongoing_event":
            ongoing_id = "ongoing_" + hashlib.sha256(f"{source_id}\0{effect_index}".encode()).hexdigest()[:24]
            plans.append({"command_type": "create_event", "entity_id": effect["entity_id"], "payload": {
                "event_id": ongoing_id, "event_type": effect["event_type"], "description": effect.get("description", ""),
                "start_time": due_at, "end_time": due_at + float(effect["duration_days"]) * GAME_SECONDS_PER_DAY,
                "expected_result": copy.deepcopy(effect.get("expected_result", {})), "source_action_id": chain_id,
            }})
        elif effect_type == "transfer_anchored_value":
            from_anchor = _resource_anchor(world_state, str(effect["from_resource"]))
            to_anchor = _resource_anchor(world_state, str(effect["to_resource"]))
            base_value = float(effect["base_value"])
            transfer_id = "transfer_" + hashlib.sha256(f"{source_id}\0{effect_index}".encode()).hexdigest()[:24]
            common = {"reason": effect.get("reason", ""), "anchored_base_value": base_value, "transfer_id": transfer_id}
            plans.extend([
                {"command_type": "adjust_resource", "entity_id": effect["from_entity_id"], "payload": {"resource_name": effect["from_resource"], "quantity_delta": -(base_value / from_anchor), **common, "anchor_rate": from_anchor, "transfer_side": "debit"}},
                {"command_type": "adjust_resource", "entity_id": effect["to_entity_id"], "payload": {"resource_name": effect["to_resource"], "quantity_delta": base_value / to_anchor, **common, "anchor_rate": to_anchor, "transfer_side": "credit"}},
            ])
        elif effect_type == "create_entity":
            plans.append({"command_type": "create_entity", "entity_id": effect["entity_id"], "payload": {
                "entity_type": effect["entity_type"], "name": effect["name"], "description": effect.get("description", ""),
                "current_status": effect.get("current_status", ""), "metadata": copy.deepcopy(effect.get("metadata", {})),
            }})
            for resource_name, quantity in sorted(effect.get("resources", {}).items()):
                plans.append({"command_type": "set_resource", "entity_id": effect["entity_id"], "payload": {"resource_name": resource_name, "quantity": quantity, "reason": effect.get("reason", "") or f"create_entity:{effect['entity_id']}"}})
        elif effect_type in {"move_entity", "transform_entity"}:
            payload = {key: copy.deepcopy(value) for key, value in effect.items() if key not in {"effect_type", "entity_id", "reason"}}
            if effect.get("reason"):
                payload["reason"] = effect["reason"]
            plans.append({"command_type": effect_type, "entity_id": effect["entity_id"], "payload": payload})
        elif effect_type == "remove_entity":
            plans.append({"command_type": "remove_entity", "entity_id": effect["entity_id"], "payload": {"reason": effect.get("reason", "")}})
        elif effect_type in {"enable_agent", "disable_agent"}:
            plans.append({"command_type": "set_agent_enabled", "entity_id": effect["agent_id"], "payload": {
                "agent_id": effect["agent_id"], "enabled": effect_type == "enable_agent", "spawned": effect_type == "enable_agent",
                "actor_id": effect.get("actor_id", ""), "metadata": copy.deepcopy(effect.get("metadata", {})), "reason": effect.get("reason", ""),
            }})
        elif effect_type == "modify_relationship":
            plans.append(_relationship_plan(effect, world_state, due_at))
        elif effect_type == "modify_membership":
            plans.append({"command_type": "modify_membership", "entity_id": effect["actor_id"], "payload": {
                key: copy.deepcopy(value) for key, value in effect.items() if key not in {"effect_type", "reason"}
            } | ({"reason": effect["reason"]} if effect.get("reason") else {})})
        elif effect_type == "create_issue":
            digest = hashlib.sha256(f"{source_id}\0{effect_index}".encode()).hexdigest()[:24]
            payload = {key: copy.deepcopy(value) for key, value in effect.items() if key not in {"effect_type", "deadline_days", "reason"}}
            payload.update({
                "issue_id": f"issue_{digest}", "case_id": f"case_{digest}",
                "external_id": f"causal:{source_id}:{effect_index}", "deterministic_ids": True,
                "created_at": due_at,
            })
            if effect.get("deadline_days") is not None:
                payload["deadline_at"] = due_at + float(effect["deadline_days"]) * GAME_SECONDS_PER_DAY
            if effect.get("reason"):
                payload.setdefault("metadata", {})["reason"] = effect["reason"]
            plans.append({"command_type": "create_issue", "entity_id": payload["issue_id"], "payload": payload})
        elif effect_type == "reveal_information":
            payload = {key: copy.deepcopy(value) for key, value in effect.items() if key not in {"effect_type", "reason"}}
            payload["revealed_at"] = due_at
            if effect.get("reason"):
                payload["reason"] = effect["reason"]
            plans.append({"command_type": "reveal_information", "entity_id": effect.get("entity_id", ""), "payload": payload})
        elif effect_type == "advance_objective":
            plans.append({"command_type": "advance_objective", "entity_id": effect["objective_id"], "payload": {key: value for key, value in effect.items() if key not in {"effect_type", "reason"}} | ({"reason": effect["reason"]} if effect.get("reason") else {})})
        elif effect_type == "unlock_action":
            plans.append({"command_type": "unlock_action", "entity_id": effect["action_id"], "payload": {"action_id": effect["action_id"], "scope_id": effect.get("scope_id", ""), "unlocked": True, "reason": effect.get("reason", "")}})
        else:
            plans.append({"command_type": "switch_phase", "entity_id": effect["phase_id"], "payload": {"phase_id": effect["phase_id"], "metadata": copy.deepcopy(effect.get("metadata", {})), "reason": effect.get("reason", "")}})

        for sub_index, plan in enumerate(plans):
            plan["effect_index"] = effect_index
            plan["effect_type"] = effect_type
            plan["sub_index"] = sub_index
            commands.append(plan)
    if len(commands) > MAX_WORLD_COMMANDS:
        raise CausalEventError("causal_effect_command_limit")
    return commands


def normalize_materialized_command(value: Any) -> dict[str, Any]:
    """Validate one immutable command restored from a causal snapshot.

    The four legacy command shapes keep their original strict validation so old
    transfer snapshots can still be reconciled safely. New generic gameplay
    commands are JSON-sanitized here and receive command-specific validation in
    the authoritative WorldCommand reducers.
    """
    if not isinstance(value, Mapping):
        raise CausalEventError("causal_snapshot_invalid")
    command_type = str(value.get("command_type", ""))
    if command_type not in MATERIALIZED_COMMAND_TYPES:
        raise CausalEventError("causal_snapshot_invalid")

    payload_value = value.get("payload", {})
    if not isinstance(payload_value, Mapping) or len(payload_value) > 128:
        raise CausalEventError("causal_snapshot_invalid")

    if command_type in {"adjust_resource", "set_resource"}:
        entity_id = _text(value.get("entity_id"), "causal_snapshot_invalid")
        if len(payload_value) > 32:
            raise CausalEventError("causal_snapshot_invalid")
        number_key = "quantity_delta" if command_type == "adjust_resource" else "quantity"
        normalized_payload: dict[str, Any] = {
            "resource_name": _text(payload_value.get("resource_name"), "causal_snapshot_invalid"),
            number_key: _finite(payload_value.get(number_key), "causal_snapshot_invalid"),
        }
        reason = str(payload_value.get("reason") or "")
        if len(reason) > 512:
            raise CausalEventError("causal_snapshot_invalid")
        if reason:
            normalized_payload["reason"] = reason
        for key in ("anchored_base_value", "anchor_rate"):
            if key in payload_value:
                normalized_payload[key] = _finite(payload_value[key], "causal_snapshot_invalid")
        transfer_side = payload_value.get("transfer_side")
        if transfer_side in {"debit", "credit"}:
            normalized_payload["transfer_side"] = transfer_side
            transfer_id = str(payload_value.get("transfer_id") or "")
            if transfer_id:
                if len(transfer_id) > 64:
                    raise CausalEventError("causal_snapshot_invalid")
                normalized_payload["transfer_id"] = transfer_id
        elif transfer_side is not None or payload_value.get("transfer_id"):
            raise CausalEventError("causal_snapshot_invalid")
        result: dict[str, Any] = {
            "command_type": command_type,
            "entity_id": entity_id,
            "payload": normalized_payload,
        }
    elif command_type == "update_status":
        if len(payload_value) > 32:
            raise CausalEventError("causal_snapshot_invalid")
        result = {
            "command_type": command_type,
            "entity_id": _text(value.get("entity_id"), "causal_snapshot_invalid"),
            "payload": {
                "new_status": _text(
                    payload_value.get("new_status"),
                    "causal_snapshot_invalid",
                    limit=512,
                )
            },
        }
    elif command_type == "create_event":
        if len(payload_value) > 32:
            raise CausalEventError("causal_snapshot_invalid")
        event_id = _text(payload_value.get("event_id"), "causal_snapshot_invalid")
        event_type = _text(
            payload_value.get("event_type"),
            "causal_snapshot_invalid",
            limit=64,
        )
        description = str(payload_value.get("description") or "")
        source_action_id = str(payload_value.get("source_action_id") or "")
        if len(description) > 1000 or len(source_action_id) > MAX_ID_LENGTH:
            raise CausalEventError("causal_snapshot_invalid")
        start_time = _finite(payload_value.get("start_time"), "causal_snapshot_invalid")
        end_time = _finite(payload_value.get("end_time"), "causal_snapshot_invalid")
        if start_time < 0 or end_time < start_time:
            raise CausalEventError("causal_snapshot_invalid")
        result = {
            "command_type": command_type,
            "entity_id": _text(value.get("entity_id"), "causal_snapshot_invalid"),
            "payload": {
                "event_id": event_id,
                "event_type": event_type,
                "description": description,
                "start_time": start_time,
                "end_time": end_time,
                "expected_result": _normalize_expected_result(
                    payload_value.get("expected_result", {})
                ),
                "source_action_id": source_action_id,
            },
        }
    else:
        entity_id = _text(
            value.get("entity_id", ""),
            "causal_snapshot_invalid",
            required=False,
        )
        result = {
            "command_type": command_type,
            "entity_id": entity_id,
            "payload": _object(payload_value, "causal_snapshot_invalid"),
        }

    for key in ("effect_index", "sub_index"):
        if key in value:
            raw_number = value[key]
            if isinstance(raw_number, bool):
                raise CausalEventError("causal_snapshot_invalid")
            try:
                number = int(raw_number)
            except (TypeError, ValueError, OverflowError) as exc:
                raise CausalEventError("causal_snapshot_invalid") from exc
            if number < 0 or number > MAX_WORLD_COMMANDS:
                raise CausalEventError("causal_snapshot_invalid")
            result[key] = number
    if "effect_type" in value:
        effect_type = str(value["effect_type"])
        if effect_type not in WORLD_EFFECT_TYPES:
            raise CausalEventError("causal_snapshot_invalid")
        result["effect_type"] = effect_type
    return result
