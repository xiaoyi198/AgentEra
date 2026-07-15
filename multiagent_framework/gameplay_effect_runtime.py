"""Persistent gameplay overlays produced by generic causal effects."""
from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any

from .world_commands import WorldCommandError

_SCHEMA_VERSION = "1.0"
_MAX_BUCKET = 10_000


def empty_gameplay_effect_state() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "agents": {},
        "actors": {},
        "issues": {},
        "issue_order": [],
        "knowledge": {},
        "objectives": {},
        "action_unlocks": {},
        "phase": {},
    }


def normalize_gameplay_effect_state(value: Any) -> dict[str, Any]:
    state = empty_gameplay_effect_state()
    if not isinstance(value, Mapping):
        return state
    for key in ("agents", "actors", "issues", "knowledge", "objectives", "action_unlocks"):
        raw = value.get(key, {})
        if isinstance(raw, Mapping):
            state[key] = copy.deepcopy(dict(list(raw.items())[-_MAX_BUCKET:]))
    order = value.get("issue_order", [])
    if isinstance(order, list):
        state["issue_order"] = [str(item) for item in order[-_MAX_BUCKET:] if str(item) in state["issues"]]
    phase = value.get("phase", {})
    if isinstance(phase, Mapping):
        state["phase"] = copy.deepcopy(dict(phase))
    return state


def _versioned(previous: Any, values: Mapping[str, Any], *, sim_time: float, command_id: str) -> dict[str, Any]:
    old = previous if isinstance(previous, Mapping) else {}
    return {
        **copy.deepcopy(dict(old)),
        **copy.deepcopy(dict(values)),
        "version": max(0, int(old.get("version", 0))) + 1,
        "updated_at": float(sim_time),
        "command_id": str(command_id),
    }


def reduce_gameplay_effect_state(
    state: Mapping[str, Any] | None, *, command_type: str, payload: Mapping[str, Any],
    sim_time: float, command_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure reducer used inside the WorldState SQLite transaction and dry-runs."""
    next_state = normalize_gameplay_effect_state(state)
    command_type = str(command_type)
    data = dict(payload)

    if command_type == "set_agent_enabled":
        agent_id = str(data.get("agent_id", "")).strip()
        if not agent_id:
            raise WorldCommandError("agent_id_required", "agent_id is required")
        row = _versioned(next_state["agents"].get(agent_id), {
            "agent_id": agent_id,
            "enabled": bool(data.get("enabled", True)),
            "spawned": bool(data.get("spawned", data.get("enabled", True))),
            "actor_id": str(data.get("actor_id", "")),
            "metadata": copy.deepcopy(dict(data.get("metadata", {}))),
            "reason": str(data.get("reason", "")),
        }, sim_time=sim_time, command_id=command_id)
        next_state["agents"][agent_id] = row
        return next_state, copy.deepcopy(row)

    if command_type == "modify_membership":
        actor_id = str(data.get("actor_id", "")).strip()
        organization_id = str(data.get("organization_id", "")).strip()
        operation = str(data.get("operation", "upsert"))
        if not actor_id or not organization_id or operation not in {"upsert", "remove"}:
            raise WorldCommandError("membership_invalid", "membership command is invalid")
        actor = copy.deepcopy(next_state["actors"].get(actor_id, {"actor_id": actor_id, "memberships": {}}))
        memberships = actor.get("memberships", {})
        if not isinstance(memberships, Mapping):
            memberships = {}
        memberships = copy.deepcopy(dict(memberships))
        if operation == "remove":
            memberships[organization_id] = {
                "organization_id": organization_id,
                "removed": True,
                "updated_at": float(sim_time),
                "command_id": str(command_id),
            }
        else:
            memberships[organization_id] = {
                "organization_id": organization_id,
                "role": str(data.get("role", "member")),
                "status": str(data.get("status", "active")),
                "metadata": copy.deepcopy(dict(data.get("metadata", {}))),
                "updated_at": float(sim_time),
                "command_id": str(command_id),
            }
        actor = _versioned(actor, {"actor_id": actor_id, "memberships": memberships}, sim_time=sim_time, command_id=command_id)
        next_state["actors"][actor_id] = actor
        return next_state, {"actor_id": actor_id, "organization_id": organization_id, "operation": operation, "version": actor["version"]}

    if command_type == "create_issue":
        issue_id = str(data.get("issue_id", "")).strip()
        if not issue_id:
            raise WorldCommandError("issue_id_required", "issue_id is required")
        previous = next_state["issues"].get(issue_id)
        if previous is not None and previous != data:
            raise WorldCommandError("issue_id_conflict", "issue_id already exists", {"issue_id": issue_id})
        if previous is None:
            next_state["issues"][issue_id] = copy.deepcopy(data)
            next_state["issue_order"].append(issue_id)
            next_state["issue_order"] = next_state["issue_order"][-_MAX_BUCKET:]
        return next_state, {"issue_id": issue_id, "queued": previous is None}

    if command_type == "reveal_information":
        knowledge_id = str(data.get("knowledge_id", "")).strip()
        entity_id = str(data.get("entity_id", "")).strip()
        if not knowledge_id:
            raise WorldCommandError("knowledge_id_required", "knowledge_id is required")
        key = f"{entity_id}:{knowledge_id}"
        row = _versioned(next_state["knowledge"].get(key), {
            "entity_id": entity_id,
            "knowledge_id": knowledge_id,
            "value": copy.deepcopy(data.get("value")),
            "source": str(data.get("source", "causal_event")),
            "confidence": float(data.get("confidence", 1.0)),
            "revealed_at": float(data.get("revealed_at", sim_time)),
            "metadata": copy.deepcopy(dict(data.get("metadata", {}))),
            "reason": str(data.get("reason", "")),
        }, sim_time=sim_time, command_id=command_id)
        next_state["knowledge"][key] = row
        return next_state, copy.deepcopy(row)

    if command_type == "advance_objective":
        objective_id = str(data.get("objective_id", "")).strip()
        mode = str(data.get("mode", "adjust"))
        if not objective_id or mode not in {"adjust", "set"}:
            raise WorldCommandError("objective_effect_invalid", "objective effect is invalid")
        old = next_state["objectives"].get(objective_id, {})
        old_progress = float(old.get("progress", 0.0)) if isinstance(old, Mapping) else 0.0
        progress = float(data.get("amount", 0.0)) + old_progress if mode == "adjust" else float(data.get("progress", 0.0))
        progress = max(0.0, min(1.0, progress))
        values = {"objective_id": objective_id, "progress": progress, "reason": str(data.get("reason", ""))}
        if data.get("status") is not None:
            values["status"] = str(data["status"])
        row = _versioned(old, values, sim_time=sim_time, command_id=command_id)
        next_state["objectives"][objective_id] = row
        return next_state, copy.deepcopy(row)

    if command_type == "unlock_action":
        action_id = str(data.get("action_id", "")).strip()
        scope_id = str(data.get("scope_id", "")).strip()
        if not action_id:
            raise WorldCommandError("action_id_required", "action_id is required")
        key = f"{action_id}:{scope_id}"
        row = _versioned(next_state["action_unlocks"].get(key), {
            "action_id": action_id, "scope_id": scope_id,
            "unlocked": bool(data.get("unlocked", True)),
            "reason": str(data.get("reason", "")),
        }, sim_time=sim_time, command_id=command_id)
        next_state["action_unlocks"][key] = row
        return next_state, copy.deepcopy(row)

    if command_type == "switch_phase":
        phase_id = str(data.get("phase_id", "")).strip()
        if not phase_id:
            raise WorldCommandError("phase_id_required", "phase_id is required")
        row = _versioned(next_state.get("phase"), {
            "phase_id": phase_id,
            "metadata": copy.deepcopy(dict(data.get("metadata", {}))),
            "reason": str(data.get("reason", "")),
        }, sim_time=sim_time, command_id=command_id)
        next_state["phase"] = row
        return next_state, copy.deepcopy(row)

    raise WorldCommandError("unsupported_gameplay_effect", f"unsupported gameplay effect: {command_type}")


class GameplayEffectRuntime:
    """Read model and manager projector for the SQLite-authoritative effect state."""

    def __init__(self) -> None:
        self._state = empty_gameplay_effect_state()

    def refresh(self, world_state: Any) -> dict[str, Any]:
        getter = getattr(world_state, "get_gameplay_effect_state", None)
        self._state = normalize_gameplay_effect_state(getter() if callable(getter) else None)
        return copy.deepcopy(self._state)

    def sync_managers(
        self, *, actor_manager: Any, issue_manager: Any, objective_manager: Any,
        action_catalog: Any, sim_time: float, project_issues: bool = True,
    ) -> None:
        setter = getattr(actor_manager, "set_effect_overrides", None)
        if callable(setter):
            setter(self._state.get("actors", {}))
        setter = getattr(objective_manager, "set_effect_overrides", None)
        if callable(setter):
            setter(self._state.get("objectives", {}))
        setter = getattr(action_catalog, "set_effect_unlocks", None)
        if callable(setter):
            setter(self._state.get("action_unlocks", {}))
        projector = getattr(issue_manager, "project_effect_issues", None)
        if project_issues and callable(projector):
            projector(
                [self._state["issues"][issue_id] for issue_id in self._state.get("issue_order", []) if issue_id in self._state["issues"]],
                sim_time=float(sim_time),
            )

    def agent_enabled(self, agent_id: str) -> bool:
        row = self._state.get("agents", {}).get(str(agent_id))
        return bool(row.get("enabled", True)) if isinstance(row, Mapping) else True

    def get_player_knowledge(self, entity_id: str, name: str, *, field: str = "", sim_time: float = 0.0) -> Any:
        _ = float(sim_time)
        row = self._state.get("knowledge", {}).get(f"{str(entity_id)}:{str(name)}")
        if not isinstance(row, Mapping):
            return None
        if field:
            return copy.deepcopy(row.get(field))
        return copy.deepcopy(row.get("value"))

    def resolve_condition(self, condition: Any, context: Any) -> Any:
        if getattr(condition, "kind", "") == "phase":
            phase = self._state.get("phase", {})
            if not isinstance(phase, Mapping) or not phase:
                return None
            field = str(getattr(condition, "field", "") or "phase_id")
            if field.startswith("metadata."):
                metadata = phase.get("metadata", {})
                if not isinstance(metadata, Mapping):
                    return None
                current: Any = metadata
                for part in field.split(".")[1:]:
                    if not isinstance(current, Mapping):
                        return None
                    current = current.get(part)
                return copy.deepcopy(current)
            return copy.deepcopy(phase.get(field))
        return self.get_player_knowledge(
            str(getattr(condition, "entity_id", "")), str(getattr(condition, "name", "")),
            field=str(getattr(condition, "field", "")), sim_time=float(getattr(context, "sim_time", 0.0)),
        )

    def public_state(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "phase": copy.deepcopy(self._state.get("phase", {})),
            "agents": copy.deepcopy(self._state.get("agents", {})),
            "knowledge": copy.deepcopy(list(self._state.get("knowledge", {}).values())),
            "objectives": copy.deepcopy(self._state.get("objectives", {})),
            "action_unlocks": copy.deepcopy(self._state.get("action_unlocks", {})),
            "counts": {
                "actors": len(self._state.get("actors", {})),
                "issues": len(self._state.get("issues", {})),
                "knowledge": len(self._state.get("knowledge", {})),
            },
        }
