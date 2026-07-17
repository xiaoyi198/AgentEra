"""Persistent gameplay overlays produced by generic causal effects."""
from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any

from .knowledge_contract import empty_knowledge_state, normalize_knowledge_state
from .knowledge_runtime import KNOWLEDGE_COMMAND_TYPES, KnowledgeRuntime, reduce_knowledge_state
from .world_commands import WorldCommandError

_SCHEMA_VERSION = "3.0"
_MAX_BUCKET = 10_000
_MAX_LIFECYCLE_FEEDBACK = 500


def empty_gameplay_effect_state() -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "agents": {},
        "actors": {},
        "issues": {},
        "issue_order": [],
        "knowledge": {},  # legacy v1 player-knowledge mirror, read only during migration
        "knowledge_runtime": empty_knowledge_state(),
        "objectives": {},
        "action_unlocks": {},
        "phase": {},
        "lifecycle": {
            "dynamic_actors": {},
            "dynamic_agents": {},
            "positions": {},
            "receipts": {},
            "recent_feedback": [],
        },
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
    lifecycle = value.get("lifecycle", {})
    if isinstance(lifecycle, Mapping):
        for key in ("dynamic_actors", "dynamic_agents", "positions", "receipts"):
            raw = lifecycle.get(key, {})
            if isinstance(raw, Mapping):
                state["lifecycle"][key] = copy.deepcopy(dict(list(raw.items())[-_MAX_BUCKET:]))
        feedback = lifecycle.get("recent_feedback", [])
        if isinstance(feedback, list):
            state["lifecycle"]["recent_feedback"] = copy.deepcopy(
                feedback[-_MAX_LIFECYCLE_FEEDBACK:]
            )
    state["knowledge_runtime"] = normalize_knowledge_state(
        value.get("knowledge_runtime"), legacy_knowledge=value.get("knowledge", {}),
    )
    # New writes only target knowledge_runtime. Keeping this empty avoids exposing or
    # persisting a second authoritative player-knowledge bucket.
    state["knowledge"] = {}
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
                "rank": data.get("rank"),
                "primary": bool(data.get("primary", False)),
                "description": str(data.get("description", "")),
                "metadata": copy.deepcopy(dict(data.get("metadata", {}))),
                "updated_at": float(sim_time),
                "command_id": str(command_id),
            }
        actor = _versioned(actor, {"actor_id": actor_id, "memberships": memberships}, sim_time=sim_time, command_id=command_id)
        next_state["actors"][actor_id] = actor
        return next_state, {"actor_id": actor_id, "organization_id": organization_id, "operation": operation, "version": actor["version"]}

    if command_type == "apply_lifecycle_transition":
        transition_id = str(data.get("transition_id", "")).strip()
        receipt_id = str(data.get("receipt_id", "")).strip()
        if not transition_id or not receipt_id:
            raise WorldCommandError(
                "lifecycle_transition_invalid",
                "transition_id and receipt_id are required",
            )
        lifecycle = next_state["lifecycle"]
        existing_receipt = lifecycle["receipts"].get(receipt_id)
        if isinstance(existing_receipt, Mapping):
            return next_state, {
                "transition_id": transition_id,
                "receipt_id": receipt_id,
                "already_applied": True,
                "receipt": copy.deepcopy(dict(existing_receipt)),
            }

        def records(name: str, *, limit: int = 1024) -> list[Mapping[str, Any]]:
            raw = data.get(name, [])
            if not isinstance(raw, list) or len(raw) > limit or any(not isinstance(item, Mapping) for item in raw):
                raise WorldCommandError(
                    "lifecycle_transition_invalid", f"{name} must be a list of objects"
                )
            return list(raw)

        counts = {
            "dynamic_actors_upserted": 0,
            "dynamic_actors_retired": 0,
            "dynamic_agents_upserted": 0,
            "dynamic_agents_retired": 0,
            "positions_updated": 0,
            "feedback_added": 0,
        }
        for raw in records("dynamic_actor_upserts"):
            actor_id = str(raw.get("actor_id", "")).strip()
            if not actor_id:
                raise WorldCommandError("lifecycle_actor_invalid", "actor_id is required")
            row = _versioned(
                lifecycle["dynamic_actors"].get(actor_id),
                {**copy.deepcopy(dict(raw)), "actor_id": actor_id, "active": True, "retired": False},
                sim_time=sim_time, command_id=command_id,
            )
            lifecycle["dynamic_actors"][actor_id] = row
            counts["dynamic_actors_upserted"] += 1
        for raw in records("dynamic_actor_retirements"):
            actor_id = str(raw.get("actor_id", "")).strip()
            if not actor_id:
                raise WorldCommandError("lifecycle_actor_invalid", "actor_id is required")
            previous = lifecycle["dynamic_actors"].get(actor_id, {"actor_id": actor_id})
            row = _versioned(
                previous,
                {
                    "actor_id": actor_id, "active": False, "retired": True,
                    "retired_reason": str(raw.get("reason", "")),
                    "retired_by_transition_id": transition_id,
                },
                sim_time=sim_time, command_id=command_id,
            )
            lifecycle["dynamic_actors"][actor_id] = row
            counts["dynamic_actors_retired"] += 1
        for raw in records("dynamic_agent_upserts"):
            agent_id = str(raw.get("agent_id", "")).strip()
            if not agent_id:
                raise WorldCommandError("lifecycle_agent_invalid", "agent_id is required")
            row = _versioned(
                lifecycle["dynamic_agents"].get(agent_id),
                {**copy.deepcopy(dict(raw)), "agent_id": agent_id, "enabled": bool(raw.get("enabled", True)), "retired": False},
                sim_time=sim_time, command_id=command_id,
            )
            lifecycle["dynamic_agents"][agent_id] = row
            counts["dynamic_agents_upserted"] += 1
        for raw in records("dynamic_agent_retirements"):
            agent_id = str(raw.get("agent_id", "")).strip()
            if not agent_id:
                raise WorldCommandError("lifecycle_agent_invalid", "agent_id is required")
            previous = lifecycle["dynamic_agents"].get(agent_id, {"agent_id": agent_id})
            row = _versioned(
                previous,
                {
                    "agent_id": agent_id, "enabled": False, "retired": True,
                    "retired_reason": str(raw.get("reason", "")),
                    "retired_by_transition_id": transition_id,
                },
                sim_time=sim_time, command_id=command_id,
            )
            lifecycle["dynamic_agents"][agent_id] = row
            counts["dynamic_agents_retired"] += 1
        for raw in records("position_updates"):
            position_id = str(raw.get("position_id", "")).strip()
            if not position_id:
                raise WorldCommandError("lifecycle_position_invalid", "position_id is required")
            incumbents_raw = raw.get("incumbent_actor_ids", [])
            if not isinstance(incumbents_raw, list) or len(incumbents_raw) > 128:
                raise WorldCommandError("lifecycle_position_invalid", "incumbent_actor_ids is invalid")
            incumbents = []
            for item in incumbents_raw:
                actor_id = str(item).strip()
                if actor_id and actor_id not in incumbents:
                    incumbents.append(actor_id)
            try:
                capacity = int(raw.get("capacity", 1))
            except (TypeError, ValueError, OverflowError) as exc:
                raise WorldCommandError("lifecycle_position_invalid", "capacity is invalid") from exc
            if capacity < 1 or capacity > 128 or len(incumbents) > capacity:
                raise WorldCommandError("lifecycle_position_invalid", "position capacity is invalid")
            row = _versioned(
                lifecycle["positions"].get(position_id),
                {
                    **copy.deepcopy(dict(raw)),
                    "position_id": position_id,
                    "capacity": capacity,
                    "incumbent_actor_ids": incumbents,
                    "vacant": len(incumbents) < capacity,
                },
                sim_time=sim_time, command_id=command_id,
            )
            lifecycle["positions"][position_id] = row
            counts["positions_updated"] += 1

        receipt_data = data.get("receipt", {})
        if not isinstance(receipt_data, Mapping):
            raise WorldCommandError("lifecycle_receipt_invalid", "receipt must be an object")
        receipt = _versioned(
            None,
            {
                **copy.deepcopy(dict(receipt_data)),
                "receipt_id": receipt_id,
                "transition_id": transition_id,
                "kind": str(data.get("kind", receipt_data.get("kind", "custom"))),
                "applied": True,
            },
            sim_time=sim_time, command_id=command_id,
        )
        lifecycle["receipts"][receipt_id] = receipt

        feedback_raw = data.get("feedback", [])
        if isinstance(feedback_raw, Mapping):
            feedback_items = [feedback_raw]
        elif isinstance(feedback_raw, list) and len(feedback_raw) <= 64 and all(isinstance(item, Mapping) for item in feedback_raw):
            feedback_items = feedback_raw
        else:
            raise WorldCommandError("lifecycle_feedback_invalid", "feedback must be an object or list of objects")
        for raw in feedback_items:
            lifecycle["recent_feedback"].append({
                **copy.deepcopy(dict(raw)),
                "transition_id": transition_id,
                "kind": str(raw.get("kind", data.get("kind", "custom"))),
                "sim_time": float(sim_time),
                "command_id": str(command_id),
            })
        try:
            feedback_limit = int(data.get("feedback_limit", _MAX_LIFECYCLE_FEEDBACK))
        except (TypeError, ValueError, OverflowError) as exc:
            raise WorldCommandError("lifecycle_feedback_invalid", "feedback_limit is invalid") from exc
        feedback_limit = max(1, min(_MAX_LIFECYCLE_FEEDBACK, feedback_limit))
        lifecycle["recent_feedback"] = lifecycle["recent_feedback"][-feedback_limit:]
        counts["feedback_added"] = len(feedback_items)
        return next_state, {
            "transition_id": transition_id,
            "receipt_id": receipt_id,
            "already_applied": False,
            "receipt": copy.deepcopy(receipt),
            **counts,
        }

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

    if command_type in KNOWLEDGE_COMMAND_TYPES:
        knowledge_state, result = reduce_knowledge_state(
            next_state.get("knowledge_runtime"),
            command_type=command_type, payload=data,
            sim_time=sim_time, command_id=command_id,
        )
        next_state["knowledge_runtime"] = knowledge_state
        return next_state, result

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
        self.knowledge = KnowledgeRuntime(self._state["knowledge_runtime"])

    def refresh(self, world_state: Any) -> dict[str, Any]:
        getter = getattr(world_state, "get_gameplay_effect_state", None)
        self._state = normalize_gameplay_effect_state(getter() if callable(getter) else None)
        self.knowledge.refresh(self._state.get("knowledge_runtime"))
        return copy.deepcopy(self._state)

    def sync_managers(
        self, *, actor_manager: Any, issue_manager: Any, objective_manager: Any,
        action_catalog: Any, sim_time: float, project_issues: bool = True,
    ) -> None:
        lifecycle_setter = getattr(actor_manager, "set_lifecycle_definitions", None)
        if callable(lifecycle_setter):
            lifecycle = self._state.get("lifecycle", {})
            lifecycle_setter(
                lifecycle.get("dynamic_actors", {}) if isinstance(lifecycle, Mapping) else {}
            )
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

    def get_player_knowledge(
        self, entity_id: str, name: str, *, field: str = "",
        sim_time: float = 0.0, holder_id: str = "player",
    ) -> Any:
        return self.knowledge.get_player_knowledge(
            entity_id, name, field=field, sim_time=sim_time, holder_id=holder_id,
        )

    def get_actor_belief(
        self, holder_id: str, entity_id: str, name: str, *, field: str = "",
        sim_time: float = 0.0,
    ) -> Any:
        return self.knowledge.get_actor_belief(
            holder_id, entity_id, name, field=field, sim_time=sim_time,
        )

    def get_world_truth(
        self, entity_id: str, name: str, *, field: str = "", sim_time: float = 0.0,
    ) -> Any:
        return self.knowledge.get_world_truth(
            entity_id, name, field=field, sim_time=sim_time,
        )

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
        return self.knowledge.resolve_condition(condition, context)

    def actor_knowledge_context(
        self, holder_ids: list[str] | tuple[str, ...], *, sim_time: float,
        max_records: int = 24, max_chars: int = 6000,
    ) -> str:
        return self.knowledge.format_actor_context(
            holder_ids, sim_time=sim_time, max_records=max_records, max_chars=max_chars,
        )

    def public_state(
        self, *, player_holder_id: str = "player", sim_time: float = 0.0,
    ) -> dict[str, Any]:
        player = self.knowledge.player_projection(
            holder_id=player_holder_id, sim_time=sim_time,
        )
        return {
            "schema_version": _SCHEMA_VERSION,
            "phase": copy.deepcopy(self._state.get("phase", {})),
            "agents": copy.deepcopy(self._state.get("agents", {})),
            "knowledge": player["knowledge"],
            "investigations": player["investigations"],
            "objectives": copy.deepcopy(self._state.get("objectives", {})),
            "action_unlocks": copy.deepcopy(self._state.get("action_unlocks", {})),
            "lifecycle": copy.deepcopy(self._state.get("lifecycle", {})),
            "counts": {
                "actors": len(self._state.get("actors", {})),
                "issues": len(self._state.get("issues", {})),
                "knowledge": len(player["knowledge"]),
                "investigations": len(player["investigations"]),
            },
        }
