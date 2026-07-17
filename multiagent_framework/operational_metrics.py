"""Secret-free operational metrics aggregation and Prometheus rendering."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping
from typing import Any


_DEFAULT_THRESHOLDS = {
    "llm_error_rate": 0.10,
    "llm_fallback_rate": 0.25,
    "dlc_reload_failure_rate": 0.10,
    "dlc_reload_max_ms": 5000.0,
    "world_command_rejection_rate": 0.25,
    "agent_turn_error_rate": 0.10,
    "archive_issues": 0.0,
    "busy_agent_ratio": 0.90,
}

_THRESHOLD_ENV = {
    "llm_error_rate": "WORLD_SIM_ALERT_LLM_ERROR_RATE",
    "llm_fallback_rate": "WORLD_SIM_ALERT_LLM_FALLBACK_RATE",
    "dlc_reload_failure_rate": "WORLD_SIM_ALERT_DLC_RELOAD_FAILURE_RATE",
    "dlc_reload_max_ms": "WORLD_SIM_ALERT_DLC_RELOAD_MAX_MS",
    "world_command_rejection_rate": "WORLD_SIM_ALERT_WORLD_COMMAND_REJECTION_RATE",
    "agent_turn_error_rate": "WORLD_SIM_ALERT_AGENT_TURN_ERROR_RATE",
    "archive_issues": "WORLD_SIM_ALERT_ARCHIVE_ISSUES",
    "busy_agent_ratio": "WORLD_SIM_ALERT_BUSY_AGENT_RATIO",
}

_RATIO_THRESHOLDS = {
    "llm_error_rate",
    "llm_fallback_rate",
    "dlc_reload_failure_rate",
    "world_command_rejection_rate",
    "agent_turn_error_rate",
    "busy_agent_ratio",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _integer(value: Any, default: int = 0) -> int:
    return max(0, int(_number(value, float(default))))


def _ratio(numerator: Any, denominator: Any) -> float:
    den = _number(denominator)
    if den <= 0:
        return 0.0
    return round(max(0.0, min(1.0, _number(numerator) / den)), 6)


def default_alert_thresholds() -> dict[str, float]:
    """Return a detached copy of the built-in safety ceilings."""
    return dict(_DEFAULT_THRESHOLDS)


def load_alert_thresholds(environ: Mapping[str, str] | None = None) -> dict[str, float]:
    source = environ if environ is not None else os.environ
    thresholds: dict[str, float] = {}
    for name, default in _DEFAULT_THRESHOLDS.items():
        raw = source.get(_THRESHOLD_ENV[name], "")
        value = _number(raw, default) if str(raw).strip() else default
        if value < 0:
            value = default
        if name in _RATIO_THRESHOLDS:
            value = min(1.0, value)
        thresholds[name] = value
    return thresholds


def _llm_snapshot(orchestrator: Any) -> dict[str, Any]:
    gateway = getattr(orchestrator, "model_gateway", None)
    raw = gateway.metrics_snapshot() if gateway and hasattr(gateway, "metrics_snapshot") else {}
    totals_raw = raw.get("totals", {}) if isinstance(raw, Mapping) else {}
    totals = {
        key: _integer(totals_raw.get(key, 0))
        for key in (
            "calls", "successes", "errors", "fallback_attempts",
            "input_tokens", "output_tokens", "total_tokens",
        )
    }
    entries: list[dict[str, Any]] = []
    for raw_entry in raw.get("entries", []) if isinstance(raw, Mapping) else []:
        if not isinstance(raw_entry, Mapping):
            continue
        entries.append({
            "task_type": str(raw_entry.get("task_type", "")),
            "model_profile": str(raw_entry.get("model_profile", "")),
            "provider": str(raw_entry.get("provider", "")),
            "model": str(raw_entry.get("model", "")),
            "calls": _integer(raw_entry.get("calls", 0)),
            "successes": _integer(raw_entry.get("successes", 0)),
            "errors": _integer(raw_entry.get("errors", 0)),
            "fallback_attempts": _integer(raw_entry.get("fallback_attempts", 0)),
            "input_tokens": _integer(raw_entry.get("input_tokens", 0)),
            "output_tokens": _integer(raw_entry.get("output_tokens", 0)),
            "total_tokens": _integer(raw_entry.get("total_tokens", 0)),
            "latency_ms_total": round(_number(raw_entry.get("latency_ms_total", 0)), 3),
            "avg_latency_ms": round(_number(raw_entry.get("avg_latency_ms", 0)), 3),
        })
    return {"totals": totals, "entries": entries}


def _dlc_snapshot(orchestrator: Any) -> dict[str, Any]:
    loader = getattr(orchestrator, "dlc_loader", None)
    if loader is None or not hasattr(loader, "public_runtime"):
        return {
            "enabled": False,
            "generation": 0,
            "metrics": {},
            "archive": {"enabled": False, "persisted_generations": 0, "issue_count": 0},
        }
    raw = loader.public_runtime()
    metrics_raw = raw.get("metrics", {}) if isinstance(raw, Mapping) else {}
    metric_keys = (
        "attempts", "successes", "failures", "rollback_attempts",
        "rollback_successes", "rollback_failures", "unsafe_rejections",
        "anchor_migration_rejections", "resource_kind_migration_rejections",
        "current_waiting_inflight_agents", "last_waiting_inflight_agents",
        "max_waiting_inflight_agents",
    )
    metrics = {key: _integer(metrics_raw.get(key, 0)) for key in metric_keys}
    for key in (
        "last_duration_ms", "total_duration_ms", "max_duration_ms",
        "last_memory_migration_ms", "total_memory_migration_ms",
    ):
        metrics[key] = round(_number(metrics_raw.get(key, 0)), 3)
    metrics["reload_in_progress"] = bool(metrics_raw.get("reload_in_progress", False))
    archive_raw = raw.get("generation_archive", {}) if isinstance(raw, Mapping) else {}
    archive = {
        "enabled": bool(archive_raw.get("enabled", False)),
        "persisted_generations": _integer(archive_raw.get("persisted_generations", 0)),
        "issue_count": _integer(archive_raw.get("issue_count", 0)),
    }
    return {
        "enabled": True,
        "generation": _integer(raw.get("generation", 0)),
        "available_generations": len(raw.get("available_generations", []) or []),
        "metrics": metrics,
        "archive": archive,
    }


def _world_command_snapshot(orchestrator: Any) -> dict[str, Any]:
    world_state = getattr(orchestrator, "world_state", None)
    bus = getattr(world_state, "command_bus", None) if world_state is not None else None
    if bus is None or not hasattr(bus, "metrics_snapshot"):
        return {
            "persisted_total": 0, "by_status": {}, "by_type": {},
            "by_error_code": {}, "runtime": {},
        }
    raw = bus.metrics_snapshot()
    return {
        "persisted_total": _integer(raw.get("persisted_total", 0)),
        "by_status": {str(k): _integer(v) for k, v in raw.get("by_status", {}).items()},
        "by_type": {str(k): _integer(v) for k, v in raw.get("by_type", {}).items()},
        "by_error_code": {
            str(k): _integer(v) for k, v in raw.get("by_error_code", {}).items()
        },
        "runtime": {str(k): _integer(v) for k, v in raw.get("runtime", {}).items()},
    }


def _economy_snapshot(orchestrator: Any, runtime: Mapping[str, Any]) -> dict[str, Any]:
    world_state = getattr(orchestrator, "world_state", None)
    raw = (
        world_state.get_economic_invariants()
        if world_state is not None and hasattr(world_state, "get_economic_invariants")
        else {}
    )
    inventory_raw = raw.get("inventory", {}) if isinstance(raw, Mapping) else {}
    ledger_raw = raw.get("ledger", {}) if isinstance(raw, Mapping) else {}
    transfer_raw = raw.get("transfers", {}) if isinstance(raw, Mapping) else {}
    batches = _integer(runtime.get("causal_effect_batches", 0))
    rejections = _integer(runtime.get("causal_effect_rejections", 0))
    failures = _integer(runtime.get("causal_effect_failures", 0))
    return {
        "inventory": {
            "asset_resource_count": _integer(inventory_raw.get("asset_resource_count", 0)),
            "holder_count": _integer(inventory_raw.get("holder_count", 0)),
            "total_base_value": round(_number(inventory_raw.get("total_base_value", 0)), 8),
        },
        "ledger": {
            "entries_total": _integer(ledger_raw.get("entries_total", 0)),
            "issued_base_value": round(_number(ledger_raw.get("issued_base_value", 0)), 8),
            "destroyed_base_value": round(_number(ledger_raw.get("destroyed_base_value", 0)), 8),
            "net_non_transfer_base_value": round(
                _number(ledger_raw.get("net_non_transfer_base_value", 0)), 8
            ),
        },
        "transfers": {
            "batch_count": _integer(transfer_raw.get("batch_count", 0)),
            "entry_count": _integer(transfer_raw.get("entry_count", 0)),
            "debit_base_value": round(_number(transfer_raw.get("debit_base_value", 0)), 8),
            "credit_base_value": round(_number(transfer_raw.get("credit_base_value", 0)), 8),
            "net_base_value": round(_number(transfer_raw.get("net_base_value", 0)), 8),
            "mismatch_batch_count": _integer(transfer_raw.get("mismatch_batch_count", 0)),
            "incomplete_batch_count": _integer(transfer_raw.get("incomplete_batch_count", 0)),
            "max_abs_mismatch": round(_number(transfer_raw.get("max_abs_mismatch", 0)), 8),
            "conserved": bool(transfer_raw.get("conserved", True)),
        },
        "causal_effects": {
            "batches": batches,
            "commands": _integer(runtime.get("causal_effect_commands", 0)),
            "rejections": rejections,
            "failures": failures,
            "rejection_rate": _ratio(rejections + failures, batches),
        },
    }


def _timeline_snapshot(orchestrator: Any) -> dict[str, Any]:
    runtime = getattr(orchestrator, "deferred_timeline", None)
    raw_value = (
        runtime.public_status(
            sim_time=_number(getattr(orchestrator, "sim_time", 0.0)),
        )
        if runtime is not None and hasattr(runtime, "public_status")
        else {}
    )
    raw = raw_value if isinstance(raw_value, Mapping) else {}
    snapshot_value = raw.get("last_snapshot", {})
    snapshot_raw = snapshot_value if isinstance(snapshot_value, Mapping) else {}
    oldest_raw = raw.get("oldest_occurrence_time")
    oldest = None if oldest_raw is None else round(max(0.0, _number(oldest_raw)), 6)
    error_code = str(raw.get("last_restore_error", ""))[:128]
    if error_code and not all(
        character.isascii() and (character.isalnum() or character in "._-")
        for character in error_code
    ):
        error_code = "deferred_timeline.restore_failed"
    return {
        "pending_count": _integer(raw.get("pending_count", 0)),
        "capacity": _integer(raw.get("capacity", 0)),
        "utilization": round(
            max(0.0, min(1.0, _number(raw.get("utilization", 0)))),
            6,
        ),
        "oldest_occurrence_time": oldest,
        "oldest_age_seconds": round(
            max(0.0, _number(raw.get("oldest_age_seconds", 0))),
            6,
        ),
        "archive_count": _integer(raw.get("archive_count", 0)),
        "archive_capacity": _integer(raw.get("archive_capacity", 0)),
        "backpressure_count": _integer(raw.get("backpressure_count", 0)),
        "restore_failure_count": _integer(raw.get("restore_failure_count", 0)),
        "last_restore_error": error_code,
        "last_snapshot": {
            "entry_count": _integer(snapshot_raw.get("entry_count", 0)),
            "build_ms": round(max(0.0, _number(snapshot_raw.get("build_ms", 0))), 3),
            "serialization_ms": round(
                max(0.0, _number(snapshot_raw.get("serialization_ms", 0))),
                3,
            ),
            "serialized_bytes": _integer(snapshot_raw.get("serialized_bytes", 0)),
        },
    }


def _agent_and_memory_snapshot(orchestrator: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    agents = list(getattr(orchestrator, "agents", {}).values())
    busy = sum(bool(getattr(agent, "is_busy", False)) for agent in agents)
    message_count = 0
    structured_count = 0
    summarized_agents = 0
    for agent in agents:
        memory = getattr(agent, "memory", ())
        try:
            message_count += len(memory)
        except TypeError:
            pass
        structured = getattr(agent, "structured_memory", ())
        try:
            structured_count += len(structured)
        except TypeError:
            pass
        summarized_agents += bool(getattr(agent, "_summary", None))
    return (
        {
            "total": len(agents),
            "busy": busy,
            "generation": _integer(getattr(orchestrator, "agent_generation", 0)),
        },
        {
            "messages": message_count,
            "structured_records": structured_count,
            "summarized_agents": summarized_agents,
        },
    )


class OperationalMetricsCollector:
    """Build one bounded, secret-free operational snapshot."""

    def __init__(self, orchestrator: Any, environ: Mapping[str, str] | None = None) -> None:
        self.orchestrator = orchestrator
        self.environ = environ

    def collect(self) -> dict[str, Any]:
        llm = _llm_snapshot(self.orchestrator)
        dlc = _dlc_snapshot(self.orchestrator)
        world_commands = _world_command_snapshot(self.orchestrator)
        timeline = _timeline_snapshot(self.orchestrator)
        agents, memory = _agent_and_memory_snapshot(self.orchestrator)
        runtime = (
            self.orchestrator.operational_metrics_snapshot()
            if hasattr(self.orchestrator, "operational_metrics_snapshot")
            else {}
        )
        economy = _economy_snapshot(self.orchestrator, runtime)
        router = {
            key: _integer(runtime.get(key, 0))
            for key in (
                "router_batches", "router_batches_completed", "router_failures",
                "router_actions", "router_commands",
            )
        }
        agent_turns = {
            key: _integer(runtime.get(key, 0))
            for key in (
                "agent_turns", "agent_turn_completed", "agent_turn_errors",
                "agent_turn_rounds", "agent_turn_tool_calls",
                "agent_turn_input_tokens", "agent_turn_output_tokens",
            )
        }
        simulator = {
            "up": 1,
            "running": bool(getattr(self.orchestrator, "is_running", False)),
            "paused": bool(getattr(self.orchestrator, "is_paused", False)),
            "sim_time": round(_number(getattr(self.orchestrator, "sim_time", 0)), 3),
            "pending_agent_tasks": len(getattr(self.orchestrator, "_agent_tasks", ())),
            "pending_wakeups": len(getattr(self.orchestrator, "_pending_wakeups", ())),
        }
        rates = {
            "llm_error_rate": _ratio(llm["totals"]["errors"], llm["totals"]["calls"]),
            "llm_fallback_rate": _ratio(
                llm["totals"]["fallback_attempts"], llm["totals"]["calls"]
            ),
            "dlc_reload_failure_rate": _ratio(
                dlc["metrics"].get("failures", 0), dlc["metrics"].get("attempts", 0)
            ),
            "world_command_rejection_rate": _ratio(
                world_commands["by_status"].get("rejected", 0),
                world_commands["persisted_total"],
            ),
            "agent_turn_error_rate": _ratio(
                agent_turns["agent_turn_errors"], agent_turns["agent_turns"]
            ),
            "busy_agent_ratio": _ratio(agents["busy"], agents["total"]),
            "causal_effect_rejection_rate": economy["causal_effects"]["rejection_rate"],
        }
        thresholds = load_alert_thresholds(self.environ)
        values = {
            **rates,
            "dlc_reload_max_ms": dlc["metrics"].get("max_duration_ms", 0.0),
            "archive_issues": dlc["archive"].get("issue_count", 0),
        }
        alerts = []
        for alert_id, threshold in thresholds.items():
            value = _number(values.get(alert_id, 0))
            if value > threshold:
                alerts.append({
                    "id": alert_id,
                    "severity": "warning",
                    "value": round(value, 6),
                    "threshold": threshold,
                    "comparison": ">",
                })
        return {
            "schema_version": "1.0",
            "status": "degraded" if alerts else "healthy",
            "collected_at": time.time(),
            "metrics": {
                "simulator": simulator,
                "llm": llm,
                "dlc": dlc,
                "router": router,
                "agent_turns": agent_turns,
                "agents": agents,
                "world_commands": world_commands,
                "timeline": timeline,
                "economy": economy,
                "memory": memory,
                "rates": rates,
            },
            "thresholds": thresholds,
            "alerts": alerts,
        }


def _prom_escape(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values: Any) -> str:
    if not values:
        return ""
    rendered = ",".join(f'{key}="{_prom_escape(value)}"' for key, value in values.items())
    return "{" + rendered + "}"


def render_prometheus(snapshot: Mapping[str, Any]) -> str:
    """Render a snapshot using the Prometheus text exposition format."""
    metrics = snapshot["metrics"]
    lines = [
        "# HELP world_sim_up Whether the simulator metrics collector is available.",
        "# TYPE world_sim_up gauge",
        f'world_sim_up {metrics["simulator"]["up"]}',
        "# HELP world_sim_health_status One when no configured alert is active.",
        "# TYPE world_sim_health_status gauge",
        f'world_sim_health_status {1 if snapshot["status"] == "healthy" else 0}',
        "# TYPE world_sim_running gauge",
        f'world_sim_running {1 if metrics["simulator"]["running"] else 0}',
        "# TYPE world_sim_pending_agent_tasks gauge",
        f'world_sim_pending_agent_tasks {metrics["simulator"]["pending_agent_tasks"]}',
        "# TYPE world_sim_pending_wakeups gauge",
        f'world_sim_pending_wakeups {metrics["simulator"]["pending_wakeups"]}',
        "# TYPE world_sim_agents_total gauge",
        f'world_sim_agents_total {metrics["agents"]["total"]}',
        "# TYPE world_sim_agents_busy gauge",
        f'world_sim_agents_busy {metrics["agents"]["busy"]}',
        "# TYPE world_sim_router_batches_total counter",
        f'world_sim_router_batches_total {metrics["router"]["router_batches"]}',
        "# TYPE world_sim_router_failures_total counter",
        f'world_sim_router_failures_total {metrics["router"]["router_failures"]}',
        "# TYPE world_sim_router_actions_total counter",
        f'world_sim_router_actions_total {metrics["router"]["router_actions"]}',
        "# TYPE world_sim_router_commands_total counter",
        f'world_sim_router_commands_total {metrics["router"]["router_commands"]}',
        "# TYPE world_sim_agent_turns_total counter",
        f'world_sim_agent_turns_total {metrics["agent_turns"]["agent_turns"]}',
        "# TYPE world_sim_agent_turn_errors_total counter",
        f'world_sim_agent_turn_errors_total {metrics["agent_turns"]["agent_turn_errors"]}',
        "# TYPE world_sim_agent_turn_completed_total counter",
        f'world_sim_agent_turn_completed_total {metrics["agent_turns"]["agent_turn_completed"]}',
        "# TYPE world_sim_agent_turn_rounds_total counter",
        f'world_sim_agent_turn_rounds_total {metrics["agent_turns"]["agent_turn_rounds"]}',
        "# TYPE world_sim_agent_turn_tool_calls_total counter",
        f'world_sim_agent_turn_tool_calls_total {metrics["agent_turns"]["agent_turn_tool_calls"]}',
        "# TYPE world_sim_agent_turn_input_tokens_total counter",
        f'world_sim_agent_turn_input_tokens_total {metrics["agent_turns"]["agent_turn_input_tokens"]}',
        "# TYPE world_sim_agent_turn_output_tokens_total counter",
        f'world_sim_agent_turn_output_tokens_total {metrics["agent_turns"]["agent_turn_output_tokens"]}',
        "# TYPE world_sim_dlc_generation gauge",
        f'world_sim_dlc_generation {metrics["dlc"]["generation"]}',
        "# TYPE world_sim_dlc_reload_in_progress gauge",
        f'world_sim_dlc_reload_in_progress {1 if metrics["dlc"]["metrics"].get("reload_in_progress", False) else 0}',
        "# TYPE world_sim_dlc_waiting_inflight_agents gauge",
        f'world_sim_dlc_waiting_inflight_agents {metrics["dlc"]["metrics"].get("current_waiting_inflight_agents", 0)}',
        "# TYPE world_sim_dlc_reload_attempts_total counter",
        f'world_sim_dlc_reload_attempts_total {metrics["dlc"]["metrics"].get("attempts", 0)}',
        "# TYPE world_sim_dlc_reload_failures_total counter",
        f'world_sim_dlc_reload_failures_total {metrics["dlc"]["metrics"].get("failures", 0)}',
        "# TYPE world_sim_dlc_reload_duration_max_milliseconds gauge",
        f'world_sim_dlc_reload_duration_max_milliseconds {metrics["dlc"]["metrics"].get("max_duration_ms", 0)}',
        "# TYPE world_sim_archive_issues gauge",
        f'world_sim_archive_issues {metrics["dlc"]["archive"].get("issue_count", 0)}',
        "# TYPE world_sim_memory_messages gauge",
        f'world_sim_memory_messages {metrics["memory"]["messages"]}',
        "# TYPE world_sim_memory_structured_records gauge",
        f'world_sim_memory_structured_records {metrics["memory"]["structured_records"]}',
        "# TYPE world_sim_timeline_pending gauge",
        f'world_sim_timeline_pending {metrics["timeline"]["pending_count"]}',
        "# TYPE world_sim_timeline_capacity gauge",
        f'world_sim_timeline_capacity {metrics["timeline"]["capacity"]}',
        "# TYPE world_sim_timeline_utilization_ratio gauge",
        f'world_sim_timeline_utilization_ratio {metrics["timeline"]["utilization"]}',
        "# TYPE world_sim_timeline_oldest_age_seconds gauge",
        f'world_sim_timeline_oldest_age_seconds {metrics["timeline"]["oldest_age_seconds"]}',
        "# TYPE world_sim_timeline_archives gauge",
        f'world_sim_timeline_archives {metrics["timeline"]["archive_count"]}',
        "# TYPE world_sim_timeline_backpressure_total counter",
        f'world_sim_timeline_backpressure_total {metrics["timeline"]["backpressure_count"]}',
        "# TYPE world_sim_timeline_restore_failures_total counter",
        f'world_sim_timeline_restore_failures_total {metrics["timeline"]["restore_failure_count"]}',
        "# TYPE world_sim_timeline_snapshot_bytes gauge",
        f'world_sim_timeline_snapshot_bytes {metrics["timeline"]["last_snapshot"]["serialized_bytes"]}',
        "# TYPE world_sim_timeline_snapshot_build_milliseconds gauge",
        f'world_sim_timeline_snapshot_build_milliseconds {metrics["timeline"]["last_snapshot"]["build_ms"]}',
        "# TYPE world_sim_timeline_snapshot_serialization_milliseconds gauge",
        f'world_sim_timeline_snapshot_serialization_milliseconds {metrics["timeline"]["last_snapshot"]["serialization_ms"]}',
    ]
    lines.extend((
        "# TYPE world_sim_llm_calls_total counter",
        "# TYPE world_sim_llm_errors_total counter",
        "# TYPE world_sim_llm_fallback_attempts_total counter",
        "# TYPE world_sim_llm_input_tokens_total counter",
        "# TYPE world_sim_llm_output_tokens_total counter",
    ))
    for entry in metrics["llm"]["entries"]:
        label = _labels(
            task_type=entry["task_type"], model_profile=entry["model_profile"],
            provider=entry["provider"], model=entry["model"],
        )
        lines.extend((
            f'world_sim_llm_calls_total{label} {entry["calls"]}',
            f'world_sim_llm_errors_total{label} {entry["errors"]}',
            f'world_sim_llm_fallback_attempts_total{label} {entry["fallback_attempts"]}',
            f'world_sim_llm_input_tokens_total{label} {entry["input_tokens"]}',
            f'world_sim_llm_output_tokens_total{label} {entry["output_tokens"]}',
        ))
    lines.append("# TYPE world_sim_world_commands_total counter")
    for status, count in sorted(metrics["world_commands"]["by_status"].items()):
        lines.append(f'world_sim_world_commands_total{_labels(status=status)} {count}')
    lines.append("# TYPE world_sim_world_commands_by_type_total counter")
    for command_type, count in sorted(metrics["world_commands"]["by_type"].items()):
        lines.append(
            f'world_sim_world_commands_by_type_total{_labels(command_type=command_type)} {count}'
        )
    lines.append("# TYPE world_sim_world_command_errors_total counter")
    for code, count in sorted(metrics["world_commands"]["by_error_code"].items()):
        lines.append(
            f'world_sim_world_command_errors_total{_labels(code=code)} {count}'
        )
    runtime_world = metrics["world_commands"]["runtime"]
    for key in ("execute_calls", "batch_calls", "batch_commands", "replays",
                "idempotency_conflicts", "crashes"):
        metric_name = f"world_sim_world_command_{key}_total"
        lines.append(f"# TYPE {metric_name} counter")
        lines.append(f"{metric_name} {runtime_world.get(key, 0)}")
    economy = metrics["economy"]
    economy_inventory = economy["inventory"]
    economy_ledger = economy["ledger"]
    economy_transfers = economy["transfers"]
    economy_causal = economy["causal_effects"]
    lines.extend((
        "# TYPE world_sim_economy_current_base_value gauge",
        f'world_sim_economy_current_base_value {economy_inventory["total_base_value"]}',
        "# TYPE world_sim_economy_issued_base_value_total counter",
        f'world_sim_economy_issued_base_value_total {economy_ledger["issued_base_value"]}',
        "# TYPE world_sim_economy_destroyed_base_value_total counter",
        f'world_sim_economy_destroyed_base_value_total {economy_ledger["destroyed_base_value"]}',
        "# TYPE world_sim_economy_transfer_batches_total counter",
        f'world_sim_economy_transfer_batches_total {economy_transfers["batch_count"]}',
        "# TYPE world_sim_economy_transfer_mismatch_batches gauge",
        f'world_sim_economy_transfer_mismatch_batches {economy_transfers["mismatch_batch_count"]}',
        "# TYPE world_sim_economy_transfer_incomplete_batches gauge",
        f'world_sim_economy_transfer_incomplete_batches {economy_transfers["incomplete_batch_count"]}',
        "# TYPE world_sim_economy_transfer_max_abs_mismatch gauge",
        f'world_sim_economy_transfer_max_abs_mismatch {economy_transfers["max_abs_mismatch"]}',
        "# TYPE world_sim_causal_effect_batches_total counter",
        f'world_sim_causal_effect_batches_total {economy_causal["batches"]}',
        "# TYPE world_sim_causal_effect_rejections_total counter",
        f'world_sim_causal_effect_rejections_total {economy_causal["rejections"]}',
        "# TYPE world_sim_causal_effect_failures_total counter",
        f'world_sim_causal_effect_failures_total {economy_causal["failures"]}',
    ))
    lines.append("# TYPE world_sim_operational_rate gauge")
    for rate, value in sorted(metrics["rates"].items()):
        lines.append(f'world_sim_operational_rate{_labels(rate=rate)} {value}')
    lines.append("# TYPE world_sim_alert_threshold gauge")
    for alert_id, value in snapshot["thresholds"].items():
        lines.append(
            f'world_sim_alert_threshold{_labels(alert=alert_id)} {value}'
        )
    lines.append("# TYPE world_sim_alert_active gauge")
    active = {alert["id"] for alert in snapshot["alerts"]}
    for alert_id in snapshot["thresholds"]:
        lines.append(
            f'world_sim_alert_active{_labels(alert=alert_id)} {1 if alert_id in active else 0}'
        )
    return "\n".join(lines) + "\n"
