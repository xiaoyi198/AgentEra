"""Transactional execution for materialized causal world effects.

Causal rules compile immutable command plans when a child event is scheduled.
This module converts those internal plans into idempotent WorldCommand batches
when the child becomes due. Agent payloads and public event APIs never receive
the command bodies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .types import EventObject
from .world_commands import (
    WorldCommand, WorldCommandBatchResult, WorldCommandError, WorldCommandType,
)


def execute_causal_world_commands(
    world_state: Any, event: EventObject, *, sim_time: float,
) -> WorldCommandBatchResult | None:
    """Execute one causal child's materialized effects as an atomic batch.

    Deterministic command IDs make restart replay idempotent. A rejected effect
    rolls back every sibling command, including anchored transfers and ongoing
    event creation.
    """

    plans = event.world_commands
    if not plans:
        return None
    if not isinstance(plans, list):
        raise WorldCommandError("causal_plan_invalid", "causal world command plan must be an array")
    payload = event.initial_payload if isinstance(event.initial_payload, Mapping) else {}
    chain_id = str(payload.get("causal_chain_id", ""))
    parent_id = str(payload.get("causal_parent_event_id", ""))
    rule_id = str(payload.get("causal_rule_id", ""))
    commands: list[WorldCommand] = []
    for index, plan in enumerate(plans):
        if not isinstance(plan, Mapping) or not isinstance(plan.get("payload"), Mapping):
            raise WorldCommandError("causal_plan_invalid", "causal world command plan is invalid")
        try:
            command_type = WorldCommandType(str(plan.get("command_type", "")))
        except ValueError as exc:
            raise WorldCommandError("causal_plan_invalid", "causal world command type is invalid") from exc
        command_payload = dict(plan["payload"])
        metadata = {
            "source": "causal_event",
            "causal_event_id": event.event_id,
            "causal_rule_id": rule_id,
            "command_index": index,
            "effect_index": int(plan.get("effect_index", index)),
            "effect_type": str(plan.get("effect_type", plan.get("command_type", ""))),
            "sub_index": int(plan.get("sub_index", 0)),
        }
        for key in ("anchored_base_value", "anchor_rate", "transfer_side", "transfer_id"):
            if key in command_payload:
                metadata[key] = command_payload[key]
        commands.append(WorldCommand(
            command_type=command_type,
            command_id=f"causal:{event.event_id}:{index}",
            entity_id=str(plan.get("entity_id", "")),
            chain_id=chain_id,
            intent_id=parent_id,
            sim_time=float(sim_time),
            payload=command_payload,
            metadata=metadata,
        ))
    return world_state.command_bus.execute_batch(commands, atomic=True)
