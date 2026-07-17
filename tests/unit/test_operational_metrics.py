from __future__ import annotations

import json
from types import SimpleNamespace

from multiagent_framework.operational_metrics import (
    OperationalMetricsCollector,
    load_alert_thresholds,
    render_prometheus,
)
from multiagent_framework.world_commands import WorldCommand, WorldCommandType
from multiagent_framework.world_state import Entity, WorldState


class FakeGateway:
    def metrics_snapshot(self):
        return {
            "totals": {
                "calls": 10, "successes": 8, "errors": 2,
                "fallback_attempts": 3, "input_tokens": 100,
                "output_tokens": 50, "total_tokens": 150,
                "api_key": "provider-secret",
            },
            "entries": [{
                "task_type": 'agent.turn",escaped',
                "model_profile": "fast",
                "provider": "primary",
                "model": "model-a",
                "calls": 10, "successes": 8, "errors": 2,
                "fallback_attempts": 3, "input_tokens": 100,
                "output_tokens": 50, "total_tokens": 150,
                "latency_ms_total": 25, "avg_latency_ms": 2.5,
                "provider_url": "https://secret.invalid/v1",
                "prompt": "private prompt body",
            }],
        }


class FakeLoader:
    def public_runtime(self):
        return {
            "generation": 4,
            "available_generations": [{"generation": 1}, {"generation": 4}],
            "metrics": {
                "attempts": 4, "successes": 3, "failures": 1,
                "rollback_attempts": 1, "rollback_successes": 1,
                "rollback_failures": 0, "max_duration_ms": 6000,
                "last_duration_ms": 120, "total_duration_ms": 9000,
                "last_memory_migration_ms": 10,
                "total_memory_migration_ms": 40,
                "reload_in_progress": False,
            },
            "generation_archive": {
                "enabled": True, "persisted_generations": 2,
                "issue_count": 1, "issues": [{"secret": "archive-secret"}],
                "path": "C:/private/archive",
            },
            "recent_events": [{"prompt": "private prompt body"}],
        }


class FakeTimeline:
    def public_status(self, *, sim_time=0.0):
        return {
            "pending_count": 3, "capacity": 32, "utilization": 0.09375,
            "oldest_occurrence_time": 100.0,
            "oldest_age_seconds": sim_time - 100.0,
            "archive_count": 2, "archive_capacity": 64,
            "backpressure_count": 4, "restore_failure_count": 1,
            "last_restore_error": "deferred_timeline.snapshot_invalid|api_key=timeline-secret",
            "payload": "timeline-secret",
            "agent_id": "private-agent",
            "last_snapshot": {
                "entry_count": 3, "build_ms": 1.25,
                "serialization_ms": 2.5, "serialized_bytes": 4096,
                "encoded_payload": "timeline-secret",
            },
        }


class FakeAgent:
    def __init__(self, busy=False):
        self.is_busy = busy
        self.memory = [{"content": "private memory"}]
        self.structured_memory = [object(), object()]
        self._summary = {"content": "private summary"}


def _orchestrator(world):
    return SimpleNamespace(
        model_gateway=FakeGateway(),
        dlc_loader=FakeLoader(),
        deferred_timeline=FakeTimeline(),
        world_state=world,
        agents={"a": FakeAgent(True), "b": FakeAgent(False)},
        agent_generation=4,
        is_running=True,
        is_paused=False,
        sim_time=123.5,
        _agent_tasks=set(),
        _pending_wakeups=[],
        operational_metrics_snapshot=lambda: {
            "router_batches": 5, "router_batches_completed": 4,
            "router_failures": 1, "router_actions": 9, "router_commands": 7,
            "agent_turns": 10, "agent_turn_completed": 8,
            "agent_turn_errors": 2, "agent_turn_rounds": 22,
            "agent_turn_tool_calls": 14, "agent_turn_input_tokens": 80,
            "agent_turn_output_tokens": 40,
            "causal_effect_batches": 5, "causal_effect_commands": 8,
            "causal_effect_rejections": 1, "causal_effect_failures": 1,
        },
    )


def test_thresholds_validate_finite_nonnegative_and_clamp_ratios():
    thresholds = load_alert_thresholds({
        "WORLD_SIM_ALERT_LLM_ERROR_RATE": "2.5",
        "WORLD_SIM_ALERT_LLM_FALLBACK_RATE": "nan",
        "WORLD_SIM_ALERT_DLC_RELOAD_MAX_MS": "-1",
        "WORLD_SIM_ALERT_ARCHIVE_ISSUES": "3",
    })
    assert thresholds["llm_error_rate"] == 1.0
    assert thresholds["llm_fallback_rate"] == 0.25
    assert thresholds["dlc_reload_max_ms"] == 5000.0
    assert thresholds["archive_issues"] == 3.0


def test_world_command_metrics_are_aggregate_and_track_replays(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    world.upsert_entity(Entity(id="e1", type="place", name="E1"))
    applied = WorldCommand(
        command_type=WorldCommandType.UPDATE_STATUS,
        command_id="same-id", entity_id="e1", payload={"new_status": "ready"},
    )
    world.command_bus.execute(applied)
    world.command_bus.execute(applied)
    world.command_bus.execute(WorldCommand(
        command_type=WorldCommandType.UPDATE_STATUS,
        command_id="same-id", entity_id="e1", payload={"new_status": "different"},
    ))
    snapshot = world.command_bus.metrics_snapshot()
    assert snapshot["persisted_total"] == 1
    assert snapshot["by_status"] == {"applied": 1}
    assert snapshot["by_type"] == {"update_status": 1}
    assert snapshot["runtime"]["execute_calls"] == 3
    assert snapshot["runtime"]["replays"] == 2
    assert snapshot["runtime"]["idempotency_conflicts"] == 1
    serialized = json.dumps(snapshot).lower()
    assert "ready" not in serialized
    assert "different" not in serialized
    assert "payload" not in serialized
    assert "error_message" not in serialized


def test_timeline_metrics_fail_closed_on_non_mapping_status(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    orchestrator = _orchestrator(world)
    orchestrator.deferred_timeline = SimpleNamespace(
        public_status=lambda **_kwargs: ["timeline-secret", "private-agent"],
    )

    timeline = OperationalMetricsCollector(orchestrator).collect()["metrics"]["timeline"]

    assert timeline == {
        "pending_count": 0,
        "capacity": 0,
        "utilization": 0.0,
        "oldest_occurrence_time": None,
        "oldest_age_seconds": 0.0,
        "archive_count": 0,
        "archive_capacity": 0,
        "backpressure_count": 0,
        "restore_failure_count": 0,
        "last_restore_error": "",
        "last_snapshot": {
            "entry_count": 0,
            "build_ms": 0.0,
            "serialization_ms": 0.0,
            "serialized_bytes": 0,
        },
    }
    assert "timeline-secret" not in json.dumps(timeline)
    assert "private-agent" not in json.dumps(timeline)


def test_collector_alerts_and_prometheus_are_secret_free(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    snapshot = OperationalMetricsCollector(
        _orchestrator(world),
        environ={
            "WORLD_SIM_ALERT_LLM_ERROR_RATE": "0.1",
            "WORLD_SIM_ALERT_LLM_FALLBACK_RATE": "0.2",
            "WORLD_SIM_ALERT_DLC_RELOAD_FAILURE_RATE": "0.2",
            "WORLD_SIM_ALERT_DLC_RELOAD_MAX_MS": "5000",
            "WORLD_SIM_ALERT_WORLD_COMMAND_REJECTION_RATE": "1",
            "WORLD_SIM_ALERT_AGENT_TURN_ERROR_RATE": "0.1",
            "WORLD_SIM_ALERT_ARCHIVE_ISSUES": "0",
            "WORLD_SIM_ALERT_BUSY_AGENT_RATIO": "0.9",
        },
    ).collect()
    assert snapshot["status"] == "degraded"
    assert {item["id"] for item in snapshot["alerts"]} == {
        "llm_error_rate", "llm_fallback_rate", "dlc_reload_failure_rate",
        "dlc_reload_max_ms", "agent_turn_error_rate", "archive_issues",
    }
    assert snapshot["metrics"]["memory"] == {
        "messages": 2, "structured_records": 4, "summarized_agents": 2,
    }
    assert snapshot["metrics"]["economy"]["causal_effects"] == {
        "batches": 5, "commands": 8, "rejections": 1,
        "failures": 1, "rejection_rate": 0.4,
    }
    assert snapshot["metrics"]["rates"]["causal_effect_rejection_rate"] == 0.4
    assert snapshot["metrics"]["timeline"] == {
        "pending_count": 3, "capacity": 32, "utilization": 0.09375,
        "oldest_occurrence_time": 100.0, "oldest_age_seconds": 23.5,
        "archive_count": 2, "archive_capacity": 64,
        "backpressure_count": 4, "restore_failure_count": 1,
        "last_restore_error": "deferred_timeline.restore_failed",
        "last_snapshot": {
            "entry_count": 3, "build_ms": 1.25,
            "serialization_ms": 2.5, "serialized_bytes": 4096,
        },
    }
    prometheus = render_prometheus(snapshot)
    assert "world_sim_economy_current_base_value 0.0" in prometheus
    assert "world_sim_causal_effect_rejections_total 1" in prometheus
    assert "world_sim_timeline_pending 3" in prometheus
    assert "world_sim_timeline_backpressure_total 4" in prometheus
    assert "world_sim_timeline_snapshot_bytes 4096" in prometheus
    assert 'world_sim_operational_rate{rate="causal_effect_rejection_rate"} 0.4' in prometheus
    assert 'task_type="agent.turn\\\",escaped"' in prometheus
    assert 'world_sim_alert_active{alert="archive_issues"} 1' in prometheus
    serialized = (json.dumps(snapshot) + prometheus).lower()
    for secret in (
        "provider-secret", "secret.invalid", "private prompt", "private memory",
        "private summary", "archive-secret", "c:/private", "api_key",
        "provider_url", "authorization", "timeline-secret", "private-agent",
    ):
        assert secret not in serialized
