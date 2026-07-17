from __future__ import annotations

from dataclasses import dataclass

import pytest

from multiagent_framework.causal_conditions import CausalConditionContext
from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.temporal_contract import TemporalContract, TemporalRuntime


DAY = GAME_SECONDS_PER_DAY


def time_document(*, cycles=None, boundary_events=None, auto_pauses=None, seasons=None):
    return {
        "enabled": True,
        "calendar": {
            "calendar_id": "runtime_calendar",
            "display_name": "运行历",
            "epoch_label": "纪元",
            "start_year": 1,
            "start_month_id": "m1",
            "start_day": 1,
            "weekdays": ["一", "二", "三", "四", "五", "六", "日"],
            "start_weekday": "一",
            "months": [
                {"month_id": "m1", "display_name": "一月", "days": 4},
                {"month_id": "m2", "display_name": "二月", "days": 3},
            ],
        },
        "seasons": seasons or [],
        "cycles": cycles or [],
        "boundary_events": boundary_events or [],
        "auto_pause_conditions": auto_pauses or [],
    }


@dataclass
class MetricValue:
    name: str
    value: float


class MutableWorld:
    def __init__(self, value=0):
        self.value = value

    def get_entity_values(self, entity_id, kind=None):
        if entity_id == "fort" and kind == "metric":
            return [MetricValue("readiness", self.value)]
        return []


def metric_condition(value=30, operator="lt"):
    return {"kind": "metric", "entity_id": "fort", "name": "readiness", "operator": operator, "value": value}


def test_cycle_end_sorts_before_cycle_start_on_same_day():
    contract = TemporalContract.from_mapping(time_document(cycles=[
        {"cycle_id": "meeting", "display_name": "会议", "kind": "meeting", "period_days": 2, "duration_days": 2},
    ]))
    runtime = TemporalRuntime(contract)
    result = runtime.poll(2 * DAY)
    day_two = [item for item in result.markers if item["day_index"] == 2 and item["trigger"].startswith("cycle_")]
    assert [(item["trigger"], item["occurrence"]) for item in day_two] == [
        ("cycle_end", 0),
        ("cycle_start", 1),
    ]


def test_cross_year_season_does_not_emit_false_boundary_at_year_start():
    contract = TemporalContract.from_mapping(time_document(seasons=[
        {"season_id": "winter", "display_name": "冬季", "start_day": 6, "end_day": 2},
    ]))
    runtime = TemporalRuntime(contract)
    result = runtime.poll(7 * DAY)
    year_start = [item for item in result.markers if item["day_index"] == 7]
    triggers = [item["trigger"] for item in year_start]
    assert "year_start" in triggers
    assert "season_start" not in triggers
    assert "season_end" not in triggers


def test_boundary_event_ids_are_deterministic_and_distinct_for_multiple_cycles():
    cycles = [
        {"cycle_id": "alpha", "display_name": "A", "kind": "turn", "period_days": 2, "duration_days": 1},
        {"cycle_id": "beta", "display_name": "B", "kind": "task", "period_days": 2, "duration_days": 1},
    ]
    boundary_events = [{
        "boundary_event_id": "generic_start",
        "trigger": "cycle_start",
        "event_type": "cycle_notice",
        "severity": 4,
        "target_agents": ["observer"],
        "payload": {"message": "开始"},
    }]
    contract = TemporalContract.from_mapping(time_document(cycles=cycles, boundary_events=boundary_events))
    first = TemporalRuntime(contract, generation=8).poll(0)
    second = TemporalRuntime(contract, generation=8).poll(0)
    first_ids = [item.event_id for item in first.events]
    assert first_ids == [item.event_id for item in second.events]
    assert len(first_ids) == 2
    assert len(set(first_ids)) == 2


def test_condition_pause_edge_triggered_only_fires_on_false_to_true_transition():
    world = MutableWorld(50)
    contract = TemporalContract.from_mapping(time_document(auto_pauses=[{
        "pause_id": "low_readiness",
        "display_name": "战备过低",
        "trigger": "condition",
        "reason": "战备已经低于阈值",
        "conditions": [metric_condition()],
        "edge_triggered": True,
    }]))
    runtime = TemporalRuntime(contract)
    context = CausalConditionContext(world_state=world)

    assert runtime.poll(0, condition_context=context).pause_requests == ()
    world.value = 20
    assert len(runtime.poll(0.25 * DAY, condition_context=context).pause_requests) == 1
    assert runtime.poll(0.5 * DAY, condition_context=context).pause_requests == ()
    world.value = 50
    assert runtime.poll(0.75 * DAY, condition_context=context).pause_requests == ()
    world.value = 10
    assert len(runtime.poll(DAY, condition_context=context).pause_requests) == 1


def test_condition_pause_cooldown_and_once_semantics():
    world = MutableWorld(10)
    context = CausalConditionContext(world_state=world)
    cooldown_contract = TemporalContract.from_mapping(time_document(auto_pauses=[{
        "pause_id": "cooldown",
        "display_name": "冷却预警",
        "trigger": "condition",
        "reason": "冷却测试",
        "conditions": [metric_condition()],
        "edge_triggered": False,
        "cooldown_days": 1,
    }]))
    runtime = TemporalRuntime(cooldown_contract)
    assert len(runtime.poll(0, condition_context=context).pause_requests) == 1
    assert runtime.poll(0.5 * DAY, condition_context=context).pause_requests == ()
    assert len(runtime.poll(DAY, condition_context=context).pause_requests) == 1

    once_contract = TemporalContract.from_mapping(time_document(auto_pauses=[{
        "pause_id": "once",
        "display_name": "一次预警",
        "trigger": "condition",
        "reason": "只触发一次",
        "conditions": [metric_condition()],
        "edge_triggered": False,
        "once": True,
    }]))
    once_runtime = TemporalRuntime(once_contract)
    assert len(once_runtime.poll(0, condition_context=context).pause_requests) == 1
    assert once_runtime.poll(DAY, condition_context=context).pause_requests == ()


def test_boundary_pause_matches_cycle_and_records_public_receipt():
    contract = TemporalContract.from_mapping(time_document(
        cycles=[{"cycle_id": "council", "display_name": "会议", "kind": "meeting", "period_days": 3, "duration_days": 1}],
        auto_pauses=[{
            "pause_id": "meeting_pause",
            "display_name": "会议开始",
            "trigger": "cycle_start",
            "cycle_id": "council",
            "reason": "联席会议已经开始",
            "conditions": [],
        }],
    ))
    runtime = TemporalRuntime(contract, generation=3)
    result = runtime.poll(0)
    assert len(result.pause_requests) == 1
    assert result.pause_requests[0]["marker"]["cycle_id"] == "council"
    public = runtime.public_state(0)
    assert public["auto_pause_conditions"][0]["fired_count"] == 1
    assert public["recent_auto_pauses"][0]["reason"] == "联席会议已经开始"


def test_snapshot_restore_does_not_repeat_already_processed_boundary():
    contract = TemporalContract.from_mapping(time_document(
        cycles=[{"cycle_id": "turn", "display_name": "回合", "kind": "turn", "period_days": 2, "duration_days": 2}],
        auto_pauses=[{
            "pause_id": "turn_pause",
            "display_name": "回合开始",
            "trigger": "cycle_start",
            "cycle_id": "turn",
            "reason": "新回合",
            "conditions": [],
        }],
    ))
    runtime = TemporalRuntime(contract)
    assert len(runtime.poll(0).pause_requests) == 1
    snapshot = runtime.snapshot()

    restored = TemporalRuntime(contract)
    restored.restore(snapshot, sim_time=0)
    assert restored.poll(0).pause_requests == ()
    assert len(restored.poll(2 * DAY).pause_requests) == 1
    assert restored.pause_receipts["turn_pause"]["fired_count"] == 2


def test_hot_reload_preserves_receipts_and_starts_at_current_time_without_replay():
    base = TemporalContract.from_mapping(time_document(
        cycles=[{"cycle_id": "turn", "display_name": "回合", "kind": "turn", "period_days": 2, "duration_days": 1}],
        auto_pauses=[{
            "pause_id": "turn_pause",
            "display_name": "回合开始",
            "trigger": "cycle_start",
            "cycle_id": "turn",
            "reason": "新回合",
            "conditions": [],
        }],
    ))
    runtime = TemporalRuntime(base, generation=1)
    runtime.poll(0)

    changed_document = time_document(
        cycles=[{"cycle_id": "turn", "display_name": "新回合", "kind": "turn", "period_days": 2, "duration_days": 1}],
        auto_pauses=[{
            "pause_id": "turn_pause",
            "display_name": "回合开始",
            "trigger": "cycle_start",
            "cycle_id": "turn",
            "reason": "新回合",
            "conditions": [],
        }],
    )
    runtime.configure(TemporalContract.from_mapping(changed_document), generation=2, sim_time=10 * DAY, preserve_state=True)
    assert runtime.poll(10 * DAY).markers == ()
    assert runtime.pause_receipts["turn_pause"]["fired_count"] == 1
    assert all(item["day_index"] == 11 for item in runtime.poll(11 * DAY).markers)


def test_marker_limit_never_drops_unprocessed_markers_from_a_dense_day():
    cycles = [
        {"cycle_id": f"cycle_{index}", "display_name": str(index), "kind": "custom", "period_days": 2, "duration_days": 1}
        for index in range(3)
    ]
    runtime = TemporalRuntime(TemporalContract.from_mapping(time_document(cycles=cycles)))
    first = runtime.poll(DAY, max_markers=2)
    assert first.truncated is True
    assert {item["cycle_id"] for item in first.markers if item["trigger"] == "cycle_start"} == {"cycle_0", "cycle_1", "cycle_2"}
    assert {item["day_index"] for item in first.markers} == {0}

    second = runtime.poll(DAY, max_markers=2)
    assert {item["day_index"] for item in second.markers} == {1}


def test_polling_an_earlier_time_does_not_rewind_marker_cursor():
    runtime = TemporalRuntime(TemporalContract.from_mapping(time_document()))
    runtime.poll(2 * DAY)
    previous = runtime.last_polled_time
    result = runtime.poll(DAY)
    assert result.markers == ()
    assert runtime.last_polled_time == previous
