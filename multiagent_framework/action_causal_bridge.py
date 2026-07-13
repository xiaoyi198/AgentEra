"""Opt-in bridge from completed ActionAgent work to causal source events.

The bridge is deliberately explicit: no ActionResult becomes a causal event unless
the originating request supplies ``causal_event_type``.  This keeps existing DLCs
stable, while allowing new DLC rules to branch from real multi-round Agent work.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .types import ActionResult, EventObject

_SAFE_EVENT_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def build_action_causal_event(
    result: ActionResult,
    request_payload: Mapping[str, Any],
    *,
    chain_id: str,
    source_event_id: str = "",
    generated_at: float = 0.0,
) -> EventObject | None:
    """Build a deterministic, bounded causal source event for an opted-in action.

    Full prompts, FC arguments, result prose, and entity inventories are omitted.
    Rules receive only stable routing fields and aggregate economic evidence.
    """
    event_type = str(request_payload.get("causal_event_type", "") or "").strip()
    if not event_type:
        return None
    if not _SAFE_EVENT_TYPE.fullmatch(event_type):
        raise ValueError("action_causal_event_type_invalid")
    if not result.success and not bool(request_payload.get("causal_emit_on_failure", False)):
        return None

    changes = result.changes_made if isinstance(result.changes_made, Sequence) else []
    operations: list[str] = []
    net_base_value_delta = 0.0
    economic_change_count = 0
    for item in changes:
        if not isinstance(item, Mapping):
            continue
        operation = str(item.get("economic_operation", "") or "").strip()
        if operation:
            operations.append(operation)
            economic_change_count += 1
        net_base_value_delta += _finite(item.get("base_value_delta", 0.0))

    intent_id = str(request_payload.get("intent_id", "") or "")
    identity = "\0".join([
        chain_id, source_event_id, intent_id, event_type,
        str(result.action_type), str(result.executor_id), str(result.target_location),
    ])
    event_id = "action_result_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    payload = {
        "schema_version": "1.0",
        "action_type": str(result.action_type)[:128],
        "executor_id": str(result.executor_id)[:128],
        "target_location": str(result.target_location)[:128],
        "success": bool(result.success),
        "duration_days": _finite(result.duration_days),
        "economic_change_count": economic_change_count,
        "economic_operations": sorted(set(operations)),
        "net_base_value_delta": round(net_base_value_delta, 8),
    }
    return EventObject(
        event_id=event_id,
        event_type=event_type,
        severity=max(1, min(10, int(_finite(request_payload.get("severity", 5), 5)))),
        target_agents=[],
        initial_payload=payload,
        generated_at=_finite(generated_at),
    )
