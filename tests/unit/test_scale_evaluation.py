"""Tests for deterministic simulator scale evaluation."""

import asyncio
import json

import pytest

from multiagent_framework.scale_evaluation import (
    ScaleConfig,
    ScaleEvaluationError,
    ScaleEvaluator,
    load_scale_config,
)


def small_config(**overrides):
    values = {
        "dlc_count": 2,
        "agents_per_dlc": 3,
        "router_agent_count": 20,
        "router_action_count": 40,
        "router_fanout": 5,
        "world_entity_count": 5,
        "world_command_count": 30,
        "world_batch_size": 10,
        "memory_store_count": 4,
        "memory_records_per_store": 10,
        "memory_query_count": 12,
    }
    values.update(overrides)
    return ScaleConfig.from_mapping(values)


def test_scale_harness_exercises_real_paths_and_preserves_invariants(tmp_path):
    report = asyncio.run(ScaleEvaluator(small_config()).run(workspace=tmp_path))
    phases = {phase.phase: phase for phase in report.phases}

    assert report.passed
    assert set(phases) == {"dlc", "router", "world", "memory"}
    assert phases["dlc"].invariants["package_count"] == 2
    assert phases["dlc"].invariants["agent_count"] == 6
    assert phases["dlc"].invariants["validation_issues"] == 0
    assert phases["router"].invariants["commands"] == 40
    assert phases["router"].invariants["llm_calls"] == 0
    assert phases["router"].invariants["chain_preserved"]
    assert phases["world"].invariants["conservation_error"] == 0
    assert phases["world"].invariants["idempotent_replays"]
    assert phases["world"].invariants["batches_committed"] == 3
    assert phases["world"].invariants["replay_batch_committed"]
    assert phases["memory"].invariants["recall_rate"] == 1
    assert all(phase.operations_per_second > 0 for phase in phases.values())
    assert report.peak_memory_mb == max(
        phase.peak_memory_mb for phase in report.phases
    )


def test_phase_threshold_can_fail_without_losing_invariant_evidence(tmp_path):
    report = asyncio.run(
        ScaleEvaluator(small_config(thresholds_ms={"router": 0.000001})).run(
            workspace=tmp_path
        )
    )
    router = next(phase for phase in report.phases if phase.phase == "router")

    assert not report.passed
    assert not router.passed
    assert router.invariants_passed
    assert not router.performance_passed
    serialized = router.to_dict()
    assert serialized["invariants_passed"] is True
    assert serialized["performance_passed"] is False
    assert router.invariants["commands"] == 40
    assert router.invariants["deterministic"]


def test_scale_config_file_and_validation(tmp_path):
    source = tmp_path / "scale.json"
    source.write_text(json.dumps({"dlc_count": 3, "router_fanout": 99, "router_agent_count": 10}), encoding="utf-8")
    config = load_scale_config(source)
    assert config.dlc_count == 3
    assert config.router_fanout == 10

    with pytest.raises(ScaleEvaluationError, match="Unknown scale config fields"):
        ScaleConfig.from_mapping({"not_a_field": 1})
    with pytest.raises(ScaleEvaluationError, match="must be positive"):
        ScaleConfig.from_mapping({"world_command_count": 0})


def test_soak_phase_repeats_reload_ticks_mixed_batches_and_memory_compaction(tmp_path):
    report = asyncio.run(
        ScaleEvaluator(small_config(
            soak_cycles=3,
            soak_commands_per_cycle=4,
            soak_memory_records_per_cycle=6,
        )).run(workspace=tmp_path)
    )
    phases = {phase.phase: phase for phase in report.phases}
    soak = phases["soak"]

    assert report.passed
    assert soak.passed
    assert soak.invariants["cycles_completed"] == 3
    assert soak.invariants["dlc_reloads"] == 3
    assert soak.invariants["world_ticks"] == 3
    assert soak.invariants["matured_events_applied"] == 3
    assert soak.invariants["accepted_commands"] == 9
    assert soak.invariants["rejected_commands"] == 3
    assert soak.invariants["committed_mixed_batches"] == 3
    assert soak.invariants["conservation_error"] == 0
    assert soak.invariants["memory_compacted"] > 0
    assert soak.invariants["commitment_retained"]
    assert soak.invariants["remaining_events"] == 0


def test_soak_config_requires_one_rejected_and_one_accepted_command():
    with pytest.raises(ScaleEvaluationError, match="at least 2"):
        ScaleConfig.from_mapping({"soak_commands_per_cycle": 1})
