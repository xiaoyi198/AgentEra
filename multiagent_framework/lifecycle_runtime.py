"""Deterministic execution runtime for DLC-authored lifecycle transitions.

The runtime decides when a declarative transition is eligible and expands its
operations into one atomic WorldCommand batch. SQLite gameplay state remains
authoritative for dynamic actors, agents, positions, and receipts.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any, Mapping, Sequence

from .causal_conditions import (
    CausalConditionContext,
    compare_condition_value,
    resolve_condition_value,
)
from .event_generator import GAME_SECONDS_PER_DAY
from .lifecycle_contract import (
    LifecycleContract,
    LifecyclePosition,
    LifecycleScoreField,
    LifecycleTransition,
)
from .world_commands import WorldCommand, WorldCommandBatchResult, WorldCommandType

MAX_LIFECYCLE_TRANSITIONS_PER_POLL = 256


@dataclass(frozen=True)
class LifecyclePollResult:
    """Serializable summary of lifecycle work attempted during one poll."""

    applied: tuple[dict[str, Any], ...] = ()
    skipped: tuple[dict[str, Any], ...] = ()
    failed: tuple[dict[str, Any], ...] = ()
    truncated: bool = False

    @property
    def applied_count(self) -> int:
        return len(self.applied)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_count": self.applied_count,
            "failed_count": self.failed_count,
            "applied": copy.deepcopy(list(self.applied)),
            "skipped": copy.deepcopy(list(self.skipped)),
            "failed": copy.deepcopy(list(self.failed)),
            "truncated": self.truncated,
        }


@dataclass
class _Expansion:
    commands: list[WorldCommand] = field(default_factory=list)
    dynamic_actor_upserts: list[dict[str, Any]] = field(default_factory=list)
    dynamic_actor_retirements: list[dict[str, Any]] = field(default_factory=list)
    dynamic_agent_upserts: list[dict[str, Any]] = field(default_factory=list)
    dynamic_agent_retirements: list[dict[str, Any]] = field(default_factory=list)
    position_updates: dict[str, dict[str, Any]] = field(default_factory=dict)
    variables: dict[str, str] = field(default_factory=dict)


class LifecycleRuntime:
    """Evaluate lifecycle triggers and commit deterministic atomic mutations."""

    def __init__(self, contract: LifecycleContract | None = None, *, generation: int = 0) -> None:
        self.contract = contract or LifecycleContract.from_mapping({})
        self.generation = max(0, int(generation))
        self._condition_state: dict[str, dict[str, Any]] = {}

    def configure(self, contract: LifecycleContract, *, generation: int, preserve_state: bool = True) -> None:
        if not isinstance(contract, LifecycleContract):
            raise TypeError("contract must be a LifecycleContract")
        previous = self._condition_state if preserve_state else {}
        known = {item.transition_id for item in contract.transitions}
        self.contract = contract
        self.generation = max(0, int(generation))
        self._condition_state = {
            transition_id: copy.deepcopy(raw)
            for transition_id, raw in previous.items()
            if transition_id in known and isinstance(raw, Mapping)
        }

    def public_state(self, *, world_state: Any = None) -> dict[str, Any]:
        lifecycle_state: Mapping[str, Any] = {}
        if world_state is not None:
            getter = getattr(world_state, "get_gameplay_effect_state", None)
            if callable(getter):
                state = getter()
                raw = state.get("lifecycle", {}) if isinstance(state, Mapping) else {}
                if isinstance(raw, Mapping):
                    lifecycle_state = raw
        result = self.contract.public_state(generation=self.generation, runtime_state=lifecycle_state)
        result["condition_state"] = copy.deepcopy(self._condition_state)
        return result

    def poll(
        self,
        sim_time: float,
        *,
        markers: Sequence[Mapping[str, Any]] = (),
        world_state: Any,
        condition_context: CausalConditionContext | None = None,
        actor_manager: Any = None,
        max_transitions: int = MAX_LIFECYCLE_TRANSITIONS_PER_POLL,
    ) -> LifecyclePollResult:
        now = self._sim_time(sim_time)
        if not self.contract.configured:
            return LifecyclePollResult()
        context = self._context(condition_context, now, world_state, actor_manager)
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        limit = max(1, int(max_transitions))
        attempted = 0
        truncated = False

        for transition in self.contract.transitions:
            if not transition.enabled or transition.trigger == "manual":
                continue
            if transition.trigger == "condition":
                state = self._condition_state.setdefault(
                    transition.transition_id,
                    {"latch": False, "last_evaluated_at": None},
                )
                truth = self._conditions_match(transition, context)
                prior = bool(state.get("latch", False))
                state["latch"] = truth
                state["last_evaluated_at"] = now
                if not truth or (transition.edge_triggered and prior):
                    continue
                candidates = ({
                    "trigger": "condition",
                    "sim_time": now,
                    "marker_id": self._condition_marker_id(transition, now),
                },)
            else:
                candidates = tuple(
                    marker for marker in markers
                    if isinstance(marker, Mapping) and self._matches(transition, marker)
                )

            for marker in candidates:
                if attempted >= limit:
                    truncated = True
                    break
                attempted += 1
                outcome = self._execute_if_eligible(
                    transition,
                    marker=marker,
                    sim_time=float(marker.get("sim_time", now)),
                    world_state=world_state,
                    actor_manager=actor_manager,
                    condition_context=context,
                )
                bucket = applied if outcome.get("status") == "applied" else (
                    failed if outcome.get("status") == "failed" else skipped
                )
                bucket.append(outcome)
            if truncated:
                break
        return LifecyclePollResult(tuple(applied), tuple(skipped), tuple(failed), truncated)

    def execute_manual(
        self,
        transition_id: str,
        *,
        world_state: Any,
        sim_time: float,
        request_id: str,
        actor_manager: Any = None,
        condition_context: CausalConditionContext | None = None,
        variables: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        transition = next((item for item in self.contract.transitions if item.transition_id == str(transition_id)), None)
        if transition is None:
            return {"status": "failed", "error_code": "transition_not_found", "transition_id": str(transition_id)}
        if transition.trigger != "manual":
            return {"status": "failed", "error_code": "transition_not_manual", "transition_id": transition.transition_id}
        request = str(request_id or "").strip()
        if not request:
            return {"status": "failed", "error_code": "request_id_required", "transition_id": transition.transition_id}
        now = self._sim_time(sim_time)
        context = self._context(condition_context, now, world_state, actor_manager)
        if not self._conditions_match(transition, context):
            return {"status": "skipped", "reason": "conditions_not_met", "transition_id": transition.transition_id}
        marker = {"trigger": "manual", "sim_time": now, "marker_id": f"manual:{request}"}
        return self._execute_if_eligible(
            transition,
            marker=marker,
            sim_time=now,
            world_state=world_state,
            actor_manager=actor_manager,
            condition_context=context,
            initial_variables=variables,
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "fingerprint": self.contract.fingerprint(),
            "condition_state": copy.deepcopy(self._condition_state),
        }

    def restore(self, value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        raw_state = value.get("condition_state", {})
        if not isinstance(raw_state, Mapping):
            return
        known = {item.transition_id for item in self.contract.transitions}
        restored: dict[str, dict[str, Any]] = {}
        for transition_id, raw in raw_state.items():
            if transition_id not in known or not isinstance(raw, Mapping):
                continue
            last = raw.get("last_evaluated_at")
            if last is not None:
                try:
                    last = self._sim_time(last)
                except (TypeError, ValueError):
                    last = None
            restored[str(transition_id)] = {
                "latch": bool(raw.get("latch", False)),
                "last_evaluated_at": last,
            }
        self._condition_state = restored

    @staticmethod
    def _sim_time(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError("lifecycle_sim_time_invalid")
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise ValueError("lifecycle_sim_time_invalid")
        return result

    @staticmethod
    def _context(context: CausalConditionContext | None, sim_time: float, world_state: Any, actor_manager: Any) -> CausalConditionContext:
        source = context or CausalConditionContext()
        return CausalConditionContext(
            world_state=world_state if world_state is not None else source.world_state,
            actor_manager=actor_manager if actor_manager is not None else source.actor_manager,
            objective_manager=source.objective_manager,
            event_history=source.event_history,
            current_event=source.current_event,
            sim_time=sim_time,
            knowledge_provider=source.knowledge_provider,
            issue_manager=source.issue_manager,
            action_catalog=source.action_catalog,
            derived_metric_provider=source.derived_metric_provider,
        )

    @staticmethod
    def _conditions_match(transition: LifecycleTransition, context: CausalConditionContext) -> bool:
        if not transition.conditions:
            return True
        values = [
            compare_condition_value(resolve_condition_value(condition, context), condition.operator, condition.value)
            for condition in transition.conditions
        ]
        return all(values) if transition.mode == "all" else any(values)

    @staticmethod
    def _matches(transition: LifecycleTransition, marker: Mapping[str, Any]) -> bool:
        if transition.trigger != marker.get("trigger"):
            return False
        date = marker.get("date", {})
        date = date if isinstance(date, Mapping) else {}
        return not (
            (transition.cycle_id and transition.cycle_id != marker.get("cycle_id"))
            or (transition.season_id and transition.season_id != marker.get("season_id"))
            or (transition.month_id and transition.month_id != date.get("month_id"))
            or (transition.day is not None and transition.day not in {date.get("day"), date.get("day_of_year")})
        )

    @staticmethod
    def _condition_marker_id(transition: LifecycleTransition, sim_time: float) -> str:
        return f"condition:{transition.transition_id}:{round(sim_time * 1_000_000)}"

    def _execute_if_eligible(
        self,
        transition: LifecycleTransition,
        *,
        marker: Mapping[str, Any],
        sim_time: float,
        world_state: Any,
        actor_manager: Any,
        condition_context: CausalConditionContext,
        initial_variables: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._gameplay_state(world_state)
        lifecycle = state.get("lifecycle", {})
        receipts = lifecycle.get("receipts", {}) if isinstance(lifecycle, Mapping) else {}
        receipt_id = self._receipt_id(transition, marker)
        if isinstance(receipts, Mapping) and receipt_id in receipts:
            return {"status": "skipped", "reason": "already_applied", "transition_id": transition.transition_id, "receipt_id": receipt_id}
        prior_receipts = [
            raw for raw in receipts.values()
            if isinstance(raw, Mapping) and raw.get("transition_id") == transition.transition_id
        ] if isinstance(receipts, Mapping) else []
        if transition.once and prior_receipts:
            return {"status": "skipped", "reason": "once_already_applied", "transition_id": transition.transition_id, "receipt_id": receipt_id}
        last_fired = max((float(raw.get("updated_at", raw.get("sim_time", 0.0)) or 0.0) for raw in prior_receipts), default=None)
        if last_fired is not None and sim_time - last_fired < transition.cooldown_days * GAME_SECONDS_PER_DAY - 1e-9:
            return {"status": "skipped", "reason": "cooldown", "transition_id": transition.transition_id, "receipt_id": receipt_id}
        context = self._context(condition_context, sim_time, world_state, actor_manager)
        if not self._conditions_match(transition, context):
            return {"status": "skipped", "reason": "conditions_not_met", "transition_id": transition.transition_id, "receipt_id": receipt_id}

        sequence = len(prior_receipts) + 1
        variables = {"sequence": str(sequence), "transition_id": transition.transition_id, "template_id": "", "actor_id": ""}
        if isinstance(initial_variables, Mapping):
            for key, value in initial_variables.items():
                if key in variables:
                    variables[key] = str(value)
        expansion = _Expansion(variables=variables)
        try:
            self._expand_operations(
                transition,
                expansion,
                lifecycle=lifecycle if isinstance(lifecycle, Mapping) else {},
                world_state=world_state,
                actor_manager=actor_manager,
                sim_time=sim_time,
                receipt_id=receipt_id,
            )
            final_payload = {
                "transition_id": transition.transition_id,
                "receipt_id": receipt_id,
                "kind": transition.kind,
                "dynamic_actor_upserts": expansion.dynamic_actor_upserts,
                "dynamic_actor_retirements": expansion.dynamic_actor_retirements,
                "dynamic_agent_upserts": expansion.dynamic_agent_upserts,
                "dynamic_agent_retirements": expansion.dynamic_agent_retirements,
                "position_updates": list(expansion.position_updates.values()),
                "receipt": {
                    "marker": copy.deepcopy(dict(marker)),
                    "marker_id": str(marker.get("marker_id") or ""),
                    "sequence": sequence,
                    "generation": self.generation,
                },
                "feedback": self._feedback(transition, expansion.variables),
                "feedback_limit": self.contract.recent_feedback_limit,
            }
            expansion.commands.append(WorldCommand(
                command_type=WorldCommandType.APPLY_LIFECYCLE_TRANSITION,
                command_id=self._command_id(receipt_id, "finalize"),
                sim_time=sim_time,
                chain_id=receipt_id,
                payload=final_payload,
                metadata={"source": "lifecycle", "generation": self.generation},
            ))
            batch: WorldCommandBatchResult = world_state.command_bus.execute_batch(expansion.commands, atomic=True)
        except Exception as exc:
            return {"status": "failed", "error_code": "lifecycle_expansion_failed", "error": str(exc), "transition_id": transition.transition_id, "receipt_id": receipt_id}
        if not batch.ok or not batch.committed:
            rejection = next((item for item in batch.results if not item.ok), None)
            return {
                "status": "failed",
                "error_code": rejection.error_code if rejection else "lifecycle_batch_failed",
                "error": rejection.error if rejection else "atomic lifecycle batch failed",
                "transition_id": transition.transition_id,
                "receipt_id": receipt_id,
                "batch": batch.to_dict(),
            }
        return {
            "status": "applied",
            "transition_id": transition.transition_id,
            "kind": transition.kind,
            "receipt_id": receipt_id,
            "sequence": sequence,
            "command_count": len(expansion.commands),
            "actor_id": expansion.variables.get("actor_id", ""),
            "batch": batch.to_dict(),
        }

    @staticmethod
    def _gameplay_state(world_state: Any) -> dict[str, Any]:
        getter = getattr(world_state, "get_gameplay_effect_state", None)
        if not callable(getter):
            raise TypeError("world_state must expose get_gameplay_effect_state")
        value = getter()
        return value if isinstance(value, dict) else {}

    def _receipt_id(self, transition: LifecycleTransition, marker: Mapping[str, Any]) -> str:
        marker_identity = json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(f"{self.generation}|{transition.transition_id}|{marker_identity}".encode("utf-8")).hexdigest()[:24]
        return f"lifecycle:{transition.transition_id}:{digest}"

    @staticmethod
    def _command_id(receipt_id: str, suffix: str) -> str:
        digest = hashlib.sha256(f"{receipt_id}|{suffix}".encode("utf-8")).hexdigest()[:24]
        return f"wcmd_lifecycle_{digest}"

    def _add_command(
        self,
        expansion: _Expansion,
        *,
        receipt_id: str,
        suffix: str,
        command_type: WorldCommandType,
        sim_time: float,
        entity_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        expansion.commands.append(WorldCommand(
            command_type=command_type,
            command_id=self._command_id(receipt_id, suffix),
            entity_id=entity_id,
            sim_time=sim_time,
            chain_id=receipt_id,
            payload=copy.deepcopy(dict(payload or {})),
            metadata={"source": "lifecycle", "generation": self.generation},
        ))

    def _expand_operations(self, transition: LifecycleTransition, expansion: _Expansion, **context: Any) -> None:
        for index, operation in enumerate(transition.operations):
            handler = getattr(self, f"_expand_{operation.operation}")
            handler(operation.data, expansion, operation_index=index, **context)

    def _expand_spawn_actor(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        template_id = str(data["template_id"])
        template = self.contract.template_map[template_id]
        expansion.variables["template_id"] = template_id
        actor_id = self._format(str(data["actor_id_pattern"]), expansion.variables)
        if not actor_id:
            raise ValueError("spawn_actor_id_empty")
        expansion.variables["actor_id"] = actor_id
        lifecycle = context["lifecycle"]
        dynamic_actors = lifecycle.get("dynamic_actors", {}) if isinstance(lifecycle, Mapping) else {}
        existing = dynamic_actors.get(actor_id) if isinstance(dynamic_actors, Mapping) else None
        if isinstance(existing, Mapping) and not existing.get("retired") and existing.get("active") is not False:
            raise ValueError(f"spawn_actor_exists:{actor_id}")
        actor = copy.deepcopy(template.actor)
        actor_patch = data.get("actor_patch", {})
        if isinstance(actor_patch, Mapping):
            actor.update(copy.deepcopy(dict(actor_patch)))
        actor_metadata = copy.deepcopy(dict(actor.get("metadata", {}))) if isinstance(actor.get("metadata"), Mapping) else {}
        actor.update({
            "actor_id": actor_id,
            "template_id": template_id,
            "display_name": str(data.get("display_name") or actor.get("display_name") or template.display_name),
            "metadata": {**copy.deepcopy(template.metadata), **actor_metadata},
        })

        entity = copy.deepcopy(template.entity)
        entity_patch = data.get("entity_patch", {})
        if isinstance(entity_patch, Mapping):
            entity.update(copy.deepcopy(dict(entity_patch)))
        if entity:
            pattern = str(data.get("entity_id_pattern") or "{actor_id}")
            entity_id = self._format(pattern, expansion.variables)
            actor["entity_id"] = entity_id
            if context["world_state"].get_entity(entity_id) is None:
                payload = {
                    "entity_type": str(entity.get("entity_type") or entity.get("type") or actor.get("actor_type") or "person"),
                    "name": str(entity.get("name") or actor["display_name"]),
                    "description": str(entity.get("description", "")),
                    "current_status": str(entity.get("current_status", entity.get("status", "active"))),
                    "metadata": copy.deepcopy(dict(entity.get("metadata", {}))) if isinstance(entity.get("metadata"), Mapping) else {},
                }
                for key in ("location_id", "region", "x", "y"):
                    if key in entity:
                        payload["metadata"][key] = copy.deepcopy(entity[key])
                self._add_command(
                    expansion,
                    receipt_id=context["receipt_id"],
                    suffix=f"op{context['operation_index']}:create_entity:{entity_id}",
                    command_type=WorldCommandType.CREATE_ENTITY,
                    sim_time=context["sim_time"],
                    entity_id=entity_id,
                    payload=payload,
                )
        expansion.dynamic_actor_upserts.append(actor)

        agent = copy.deepcopy(template.agent)
        agent_patch = data.get("agent_patch", {})
        if isinstance(agent_patch, Mapping):
            agent.update(copy.deepcopy(dict(agent_patch)))
        if agent:
            agent_id = self._format(str(data.get("agent_id_pattern") or "{actor_id}_agent"), expansion.variables)
            agent.update({
                "agent_id": agent_id,
                "actor_id": actor_id,
                "display_name": str(agent.get("display_name") or actor["display_name"]),
                "enabled": bool(agent.get("enabled", True)),
            })
            expansion.dynamic_agent_upserts.append(agent)

    def _expand_retire_actor(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        lifecycle = context["lifecycle"]
        actor_id = self._format(str(data.get("actor_id", "")), expansion.variables)
        position_id = str(data.get("position_id", ""))
        if not actor_id and position_id:
            position = self._position_state(position_id, lifecycle, expansion)
            incumbents = position.get("incumbent_actor_ids", [])
            actor_id = str(incumbents[0]) if incumbents else ""
        if not actor_id:
            raise ValueError("retire_actor_target_missing")
        expansion.variables["actor_id"] = actor_id
        reason = str(data.get("reason") or "lifecycle transition")
        expansion.dynamic_actor_retirements.append({"actor_id": actor_id, "reason": reason})
        dynamic_agents = lifecycle.get("dynamic_agents", {}) if isinstance(lifecycle, Mapping) else {}
        if isinstance(dynamic_agents, Mapping):
            for agent_id, raw in sorted(dynamic_agents.items()):
                if isinstance(raw, Mapping) and raw.get("actor_id") == actor_id and not raw.get("retired"):
                    expansion.dynamic_agent_retirements.append({"agent_id": str(agent_id), "reason": reason})
        for definition in self.contract.positions:
            position = self._position_state(definition.position_id, lifecycle, expansion)
            incumbents = list(position.get("incumbent_actor_ids", []))
            if actor_id not in incumbents:
                continue
            position["incumbent_actor_ids"] = [item for item in incumbents if item != actor_id]
            expansion.position_updates[definition.position_id] = position
            self._membership_command(
                expansion,
                actor_id=actor_id,
                position=definition,
                operation="remove",
                context=context,
                suffix=f"op{context['operation_index']}:retire_membership:{definition.position_id}:{actor_id}",
            )
        if bool(data.get("remove_entity", False)):
            entity_id = str(data.get("entity_id") or self._actor_entity_id(actor_id, lifecycle, context.get("actor_manager")))
            if entity_id and context["world_state"].get_entity(entity_id) is not None:
                self._add_command(
                    expansion,
                    receipt_id=context["receipt_id"],
                    suffix=f"op{context['operation_index']}:remove_entity:{entity_id}",
                    command_type=WorldCommandType.REMOVE_ENTITY,
                    sim_time=context["sim_time"],
                    entity_id=entity_id,
                )

    def _expand_appoint_position(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        position_id = str(data["position_id"])
        definition = self.contract.position_map[position_id]
        position = self._position_state(position_id, context["lifecycle"], expansion)
        incumbents = list(position.get("incumbent_actor_ids", []))
        actor_id = self._format(str(data.get("actor_id", "")), expansion.variables)
        if not actor_id:
            actor_id = self._select_candidate(
                definition,
                incumbents=incumbents,
                lifecycle=context["lifecycle"],
                actor_manager=context.get("actor_manager"),
            )
        if not actor_id:
            raise ValueError(f"position_candidate_missing:{position_id}")
        if actor_id not in incumbents:
            if len(incumbents) >= definition.capacity:
                if not bool(data.get("replace", False)):
                    raise ValueError(f"position_full:{position_id}")
                displaced = incumbents.pop(-1)
                self._membership_command(
                    expansion,
                    actor_id=displaced,
                    position=definition,
                    operation="remove",
                    context=context,
                    suffix=f"op{context['operation_index']}:displace:{position_id}:{displaced}",
                )
            incumbents.append(actor_id)
        position["incumbent_actor_ids"] = incumbents
        expansion.position_updates[position_id] = position
        expansion.variables["actor_id"] = actor_id
        self._membership_command(
            expansion,
            actor_id=actor_id,
            position=definition,
            operation="upsert",
            context=context,
            suffix=f"op{context['operation_index']}:appoint:{position_id}:{actor_id}",
            overrides=data,
        )

    def _expand_vacate_position(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        position_id = str(data["position_id"])
        definition = self.contract.position_map[position_id]
        position = self._position_state(position_id, context["lifecycle"], expansion)
        incumbents = list(position.get("incumbent_actor_ids", []))
        requested = self._format(str(data.get("actor_id", "")), expansion.variables)
        targets = [requested] if requested else list(incumbents)
        for actor_id in targets:
            if actor_id not in incumbents:
                continue
            incumbents.remove(actor_id)
            self._membership_command(
                expansion,
                actor_id=actor_id,
                position=definition,
                operation="remove",
                context=context,
                suffix=f"op{context['operation_index']}:vacate:{position_id}:{actor_id}",
            )
        position["incumbent_actor_ids"] = incumbents
        expansion.position_updates[position_id] = position

    def _expand_transform_entity(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        entity_id = self._format(str(data["entity_id"]), expansion.variables)
        payload = {
            key: copy.deepcopy(value)
            for key, value in data.items()
            if key in {"new_type", "new_name", "new_description", "new_status", "metadata_patch"}
        }
        self._add_command(
            expansion,
            receipt_id=context["receipt_id"],
            suffix=f"op{context['operation_index']}:transform:{entity_id}",
            command_type=WorldCommandType.TRANSFORM_ENTITY,
            sim_time=context["sim_time"],
            entity_id=entity_id,
            payload=payload,
        )

    def _expand_split_organization(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        source_id = str(data["source_organization_id"])
        destinations = data.get("destinations", [])
        if not isinstance(destinations, Sequence) or isinstance(destinations, (str, bytes, bytearray)):
            raise ValueError("split_destinations_invalid")
        if not destinations:
            raise ValueError("split_destinations_empty")
        moved: set[str] = set()
        for destination_index, raw in enumerate(destinations):
            if not isinstance(raw, Mapping):
                raise ValueError("split_destination_invalid")
            destination_id = str(raw.get("organization_id") or raw.get("entity_id") or raw.get("id") or "").strip()
            if not destination_id:
                raise ValueError("split_destination_id_missing")
            self._ensure_organization_entity(
                destination_id,
                raw,
                expansion,
                context,
                suffix=f"op{context['operation_index']}:destination:{destination_index}",
            )
            actor_ids = raw.get("actor_ids", [])
            if not isinstance(actor_ids, Sequence) or isinstance(actor_ids, (str, bytes, bytearray)):
                raise ValueError("split_actor_ids_invalid")
            for actor_id_raw in actor_ids:
                actor_id = str(actor_id_raw)
                if not actor_id or actor_id in moved:
                    continue
                moved.add(actor_id)
                self._generic_membership_command(
                    expansion,
                    actor_id,
                    source_id,
                    "remove",
                    context,
                    suffix=f"op{context['operation_index']}:split_remove:{actor_id}",
                )
                self._generic_membership_command(
                    expansion,
                    actor_id,
                    destination_id,
                    "upsert",
                    context,
                    suffix=f"op{context['operation_index']}:split_add:{destination_id}:{actor_id}",
                    role=str(raw.get("role", "member")),
                    rank=raw.get("rank"),
                    metadata={"lifecycle_split_from": source_id},
                )
        self._retire_or_mark_organization(source_id, data, expansion, context, status="split")

    def _expand_merge_organizations(self, data: Mapping[str, Any], expansion: _Expansion, **context: Any) -> None:
        sources = data.get("source_organization_ids", [])
        if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes, bytearray)) or not sources:
            raise ValueError("merge_sources_invalid")
        source_ids = [str(item) for item in sources if str(item)]
        target_id = str(data["target_organization_id"])
        target_raw = data.get("target", {})
        target_definition = target_raw if isinstance(target_raw, Mapping) else {}
        self._ensure_organization_entity(
            target_id,
            target_definition,
            expansion,
            context,
            suffix=f"op{context['operation_index']}:target",
        )
        actor_ids_raw = data.get("actor_ids")
        if actor_ids_raw is None:
            actor_ids = self._actors_in_organizations(source_ids, context["lifecycle"], context.get("actor_manager"))
        elif isinstance(actor_ids_raw, Sequence) and not isinstance(actor_ids_raw, (str, bytes, bytearray)):
            actor_ids = sorted({str(item) for item in actor_ids_raw if str(item)})
        else:
            raise ValueError("merge_actor_ids_invalid")
        for actor_id in actor_ids:
            for source_id in source_ids:
                self._generic_membership_command(
                    expansion,
                    actor_id,
                    source_id,
                    "remove",
                    context,
                    suffix=f"op{context['operation_index']}:merge_remove:{source_id}:{actor_id}",
                )
            self._generic_membership_command(
                expansion,
                actor_id,
                target_id,
                "upsert",
                context,
                suffix=f"op{context['operation_index']}:merge_add:{target_id}:{actor_id}",
                role=str(data.get("role", "member")),
                rank=data.get("rank"),
                metadata={"lifecycle_merged_from": source_ids},
            )
        for source_id in source_ids:
            self._retire_or_mark_organization(source_id, data, expansion, context, status="merged")

    def _ensure_organization_entity(
        self,
        organization_id: str,
        raw: Mapping[str, Any],
        expansion: _Expansion,
        context: Mapping[str, Any],
        *,
        suffix: str,
    ) -> None:
        if context["world_state"].get_entity(organization_id) is not None:
            return
        payload = {
            "entity_type": str(raw.get("entity_type") or raw.get("type") or "organization"),
            "name": str(raw.get("name") or raw.get("display_name") or organization_id),
            "description": str(raw.get("description", "")),
            "current_status": str(raw.get("current_status", "active")),
            "metadata": copy.deepcopy(dict(raw.get("metadata", {}))) if isinstance(raw.get("metadata"), Mapping) else {},
        }
        self._add_command(
            expansion,
            receipt_id=context["receipt_id"],
            suffix=suffix,
            command_type=WorldCommandType.CREATE_ENTITY,
            sim_time=context["sim_time"],
            entity_id=organization_id,
            payload=payload,
        )

    def _retire_or_mark_organization(
        self,
        organization_id: str,
        data: Mapping[str, Any],
        expansion: _Expansion,
        context: Mapping[str, Any],
        *,
        status: str,
    ) -> None:
        if context["world_state"].get_entity(organization_id) is None:
            return
        if bool(data.get("remove_source", data.get("remove_sources", False))):
            command_type = WorldCommandType.REMOVE_ENTITY
            payload: dict[str, Any] = {}
        else:
            command_type = WorldCommandType.TRANSFORM_ENTITY
            payload = {"new_status": status, "metadata_patch": {"lifecycle_status": status}}
        self._add_command(
            expansion,
            receipt_id=context["receipt_id"],
            suffix=f"op{context['operation_index']}:{status}:{organization_id}",
            command_type=command_type,
            sim_time=context["sim_time"],
            entity_id=organization_id,
            payload=payload,
        )

    def _position_state(self, position_id: str, lifecycle: Mapping[str, Any], expansion: _Expansion) -> dict[str, Any]:
        if position_id in expansion.position_updates:
            return copy.deepcopy(expansion.position_updates[position_id])
        definition = self.contract.position_map[position_id]
        positions = lifecycle.get("positions", {}) if isinstance(lifecycle, Mapping) else {}
        raw = positions.get(position_id) if isinstance(positions, Mapping) else None
        if isinstance(raw, Mapping):
            result = copy.deepcopy(dict(raw))
        else:
            result = {
                "position_id": definition.position_id,
                "display_name": definition.display_name,
                "organization_id": definition.organization_id,
                "role": definition.role,
                "rank": definition.rank,
                "capacity": definition.capacity,
                "incumbent_actor_ids": list(definition.incumbent_actor_ids),
                "metadata": copy.deepcopy(definition.metadata),
            }
        result["capacity"] = definition.capacity
        result["incumbent_actor_ids"] = list(result.get("incumbent_actor_ids", []))
        return result

    def _membership_command(
        self,
        expansion: _Expansion,
        *,
        actor_id: str,
        position: LifecyclePosition,
        operation: str,
        context: Mapping[str, Any],
        suffix: str,
        overrides: Mapping[str, Any] | None = None,
    ) -> None:
        values = overrides if isinstance(overrides, Mapping) else {}
        self._generic_membership_command(
            expansion,
            actor_id,
            position.organization_id,
            operation,
            context,
            suffix=suffix,
            role=str(values.get("role") or position.role),
            rank=values.get("rank", position.rank),
            primary=bool(values.get("primary", False)),
            description=str(values.get("description") or position.display_name),
            metadata={"position_id": position.position_id, **copy.deepcopy(position.metadata)},
        )

    def _generic_membership_command(
        self,
        expansion: _Expansion,
        actor_id: str,
        organization_id: str,
        operation: str,
        context: Mapping[str, Any],
        *,
        suffix: str,
        role: str = "member",
        rank: Any = None,
        primary: bool = False,
        description: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        payload = {"actor_id": actor_id, "organization_id": organization_id, "operation": operation}
        if operation == "upsert":
            payload.update({
                "role": role,
                "rank": rank,
                "primary": primary,
                "description": description,
                "metadata": copy.deepcopy(dict(metadata or {})),
            })
        self._add_command(
            expansion,
            receipt_id=context["receipt_id"],
            suffix=suffix,
            command_type=WorldCommandType.MODIFY_MEMBERSHIP,
            sim_time=context["sim_time"],
            payload=payload,
        )

    def _select_candidate(
        self,
        definition: LifecyclePosition,
        *,
        incumbents: Sequence[str],
        lifecycle: Mapping[str, Any],
        actor_manager: Any,
    ) -> str:
        candidates = list(definition.candidate_actor_ids)
        if not candidates:
            lister = getattr(actor_manager, "list", None)
            if callable(lister):
                candidates = [str(item.get("actor_id", "")) for item in lister() if isinstance(item, Mapping)]
            else:
                dynamic = lifecycle.get("dynamic_actors", {}) if isinstance(lifecycle, Mapping) else {}
                candidates = [str(item) for item in dynamic] if isinstance(dynamic, Mapping) else []
        candidates = sorted({item for item in candidates if item and item not in incumbents})
        actors = {actor_id: self._actor_document(actor_id, lifecycle, actor_manager) for actor_id in candidates}
        candidates = [actor_id for actor_id in candidates if actors[actor_id] is not None]
        if not candidates:
            return ""

        def compare(left_id: str, right_id: str) -> int:
            left, right = actors[left_id], actors[right_id]
            for score in definition.scores:
                left_value = self._score_value(left_id, left, score)
                right_value = self._score_value(right_id, right, score)
                result = self._compare_score_values(left_value, right_value)
                if result:
                    if score.direction == "desc":
                        result = -result
                    if score.weight < 0:
                        result = -result
                    return result
            return (left_id > right_id) - (left_id < right_id)

        return sorted(candidates, key=cmp_to_key(compare))[0]

    @staticmethod
    def _compare_score_values(left: Any, right: Any) -> int:
        left_missing, right_missing = left is None, right is None
        if left_missing or right_missing:
            return 0 if left_missing and right_missing else (1 if left_missing else -1)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return (float(left) > float(right)) - (float(left) < float(right))
        left_text, right_text = str(left), str(right)
        return (left_text > right_text) - (left_text < right_text)

    @staticmethod
    def _score_value(actor_id: str, actor: Mapping[str, Any] | None, score: LifecycleScoreField) -> Any:
        if score.source == "actor_id":
            return actor_id
        if not isinstance(actor, Mapping):
            return None
        runtime = actor.get("runtime", {}) if isinstance(actor.get("runtime"), Mapping) else {}
        if score.source == "attribute":
            values = runtime.get("attributes", {}) if isinstance(runtime.get("attributes"), Mapping) else actor.get("attributes", {})
            value = values.get(score.name) if isinstance(values, Mapping) else None
        elif score.source == "state":
            values = runtime.get("states", {}) if isinstance(runtime.get("states"), Mapping) else actor.get("states", {})
            value = values.get(score.name) if isinstance(values, Mapping) else None
        else:
            memberships = actor.get("memberships", [])
            values = [
                item.get("rank")
                for item in memberships
                if isinstance(item, Mapping)
                and (not score.name or item.get("organization_id") == score.name)
                and item.get("rank") is not None
            ] if isinstance(memberships, Sequence) else []
            value = max(values) if values else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) * abs(score.weight)
        return value

    @staticmethod
    def _actor_document(actor_id: str, lifecycle: Mapping[str, Any], actor_manager: Any) -> Mapping[str, Any] | None:
        getter = getattr(actor_manager, "get", None)
        if callable(getter):
            actor = getter(actor_id)
            if isinstance(actor, Mapping):
                return actor
        dynamic = lifecycle.get("dynamic_actors", {}) if isinstance(lifecycle, Mapping) else {}
        actor = dynamic.get(actor_id) if isinstance(dynamic, Mapping) else None
        if not isinstance(actor, Mapping) or actor.get("retired") or actor.get("active") is False:
            return None
        return actor

    @classmethod
    def _actor_entity_id(cls, actor_id: str, lifecycle: Mapping[str, Any], actor_manager: Any) -> str:
        actor = cls._actor_document(actor_id, lifecycle, actor_manager)
        return str(actor.get("entity_id", "")) if isinstance(actor, Mapping) else ""

    @classmethod
    def _actors_in_organizations(cls, organization_ids: Sequence[str], lifecycle: Mapping[str, Any], actor_manager: Any) -> list[str]:
        ids: set[str] = set()
        lister = getattr(actor_manager, "list", None)
        actors = lister() if callable(lister) else []
        if not actors:
            dynamic = lifecycle.get("dynamic_actors", {}) if isinstance(lifecycle, Mapping) else {}
            actors = list(dynamic.values()) if isinstance(dynamic, Mapping) else []
        for actor in actors:
            if not isinstance(actor, Mapping):
                continue
            memberships = actor.get("memberships", [])
            if isinstance(memberships, Sequence) and any(
                isinstance(item, Mapping) and item.get("organization_id") in organization_ids
                for item in memberships
            ):
                actor_id = str(actor.get("actor_id", ""))
                if actor_id:
                    ids.add(actor_id)
        return sorted(ids)

    @staticmethod
    def _format(pattern: str, variables: Mapping[str, str]) -> str:
        if not pattern:
            return ""
        return pattern.format_map({key: str(value) for key, value in variables.items()}).strip()

    @classmethod
    def _feedback(cls, transition: LifecycleTransition, variables: Mapping[str, str]) -> list[dict[str, Any]]:
        if not transition.feedback:
            return []
        raw = copy.deepcopy(transition.feedback)
        message = raw.get("message", raw.get("success", ""))
        if message:
            try:
                raw["message"] = cls._format(str(message), variables)
            except (KeyError, ValueError):
                raw["message"] = str(message)
        raw.setdefault("display_name", transition.display_name)
        return [raw]
