from __future__ import annotations

import json

import pytest

from multiagent_framework.event_generator import GAME_SECONDS_PER_DAY
from multiagent_framework.temporal_contract import (
    TemporalContract,
    TemporalContractError,
    normalize_time_system_document,
)


def calendar_document(**overrides):
    calendar = {
        "calendar_id": "test_calendar",
        "display_name": "测试历",
        "epoch_label": "纪元",
        "start_year": 10,
        "start_month_id": "m2",
        "start_day": 2,
        "weekdays": ["甲", "乙", "丙"],
        "start_weekday": "乙",
        "months": [
            {"month_id": "m1", "display_name": "一月", "days": 2},
            {"month_id": "m2", "display_name": "二月", "days": 3},
            {"month_id": "m3", "display_name": "三月", "days": 4},
        ],
    }
    calendar.update(overrides)
    return {
        "enabled": True,
        "calendar": calendar,
        "seasons": [],
        "cycles": [],
        "boundary_events": [],
        "auto_pause_conditions": [],
    }


def test_normalization_supplies_complete_authoring_shape_without_mutating_input():
    source = {"enabled": True, "calendar": {"display_name": "自定义历"}}
    result = normalize_time_system_document(source)

    assert result["enabled"] is True
    assert result["calendar"]["display_name"] == "自定义历"
    assert len(result["calendar"]["months"]) == 12
    assert result["calendar"]["start_month_id"] == "month_01"
    assert source == {"enabled": True, "calendar": {"display_name": "自定义历"}}


def test_non_uniform_month_projection_custom_epoch_and_weekday():
    calendar = TemporalContract.from_mapping(calendar_document()).calendar
    assert calendar is not None

    day_zero = calendar.date_for_day(0)
    assert (day_zero["year"], day_zero["month_id"], day_zero["day"]) == (10, "m2", 2)
    assert (day_zero["day_of_year"], day_zero["weekday"], day_zero["weekday_index"]) == (4, "乙", 1)

    two_days_later = calendar.date_for_day(2)
    assert (two_days_later["year"], two_days_later["month_id"], two_days_later["day"]) == (10, "m3", 1)
    assert two_days_later["weekday"] == "甲"

    next_year = calendar.date_for_day(6)
    assert (next_year["year"], next_year["month_id"], next_year["day"], next_year["day_of_year"]) == (11, "m1", 1, 1)


def test_cross_year_season_is_selected_on_both_sides_of_year_boundary():
    document = calendar_document(start_month_id="m1", start_day=1)
    document["seasons"] = [
        {"season_id": "winter", "display_name": "冬季", "start_day": 8, "end_day": 2},
        {"season_id": "summer", "display_name": "夏季", "start_day": 3, "end_day": 5},
    ]
    contract = TemporalContract.from_mapping(document)
    assert contract.season_for_date(contract.calendar.date_for_day(7)).season_id == "winter"
    assert contract.season_for_date(contract.calendar.date_for_day(9)).season_id == "winter"
    assert contract.season_for_date(contract.calendar.date_for_day(2)).season_id == "summer"
    assert contract.season_for_date(contract.calendar.date_for_day(5)) is None


def test_overlapping_seasons_are_rejected():
    document = calendar_document(start_month_id="m1", start_day=1)
    document["seasons"] = [
        {"season_id": "a", "display_name": "A", "start_day": 8, "end_day": 3},
        {"season_id": "b", "display_name": "B", "start_day": 2, "end_day": 5},
    ]
    with pytest.raises(TemporalContractError) as exc_info:
        TemporalContract.from_mapping(document)
    assert exc_info.value.code == "time_system.season_overlap"
    assert exc_info.value.path == "time_system.seasons"


def test_turn_meeting_and_task_cycle_public_states():
    document = calendar_document()
    document["cycles"] = [
        {"cycle_id": "turn", "display_name": "指挥回合", "kind": "turn", "period_days": 7, "duration_days": 7},
        {"cycle_id": "meeting", "display_name": "联席会议", "kind": "meeting", "period_days": 30, "duration_days": 2, "offset_days": 5},
        {"cycle_id": "task", "display_name": "巡防任务", "kind": "task", "period_days": 14, "duration_days": 4, "offset_days": 2},
    ]
    state = TemporalContract.from_mapping(document).public_state(6 * GAME_SECONDS_PER_DAY)
    cycles = {item["cycle_id"]: item for item in state["cycles"]}

    assert cycles["turn"]["occurrence"] == 0
    assert cycles["turn"]["active"] is True
    assert cycles["turn"]["next_boundary_trigger"] == "cycle_end"
    assert cycles["meeting"]["active"] is True
    assert cycles["meeting"]["progress"] == pytest.approx(0.5)
    assert cycles["task"]["active"] is False
    assert cycles["task"]["next_boundary_day_index"] == 16


def test_boundary_event_accepts_authoritative_unicode_agent_ids():
    document = calendar_document()
    document["boundary_events"] = [{
        "boundary_event_id": "unicode_targets",
        "trigger": "day_start",
        "event_type": "daily_notice",
        "severity": 3,
        "target_agents": ["\u8fb9\u5883\u6307\u6325\u5b98", "\u54e8\u7ad9\u5b88\u5907\u5b98"],
    }]

    contract = TemporalContract.from_mapping(document)

    assert contract.boundary_events[0].target_agents == ("\u8fb9\u5883\u6307\u6325\u5b98", "\u54e8\u7ad9\u5b88\u5907\u5b98")


def test_contract_to_dict_and_fingerprint_are_json_safe_and_stable():
    document = calendar_document()
    document["cycles"] = [{"cycle_id": "turn", "display_name": "回合", "kind": "turn", "period_days": 2, "duration_days": 2}]
    document["auto_pause_conditions"] = [{
        "pause_id": "danger",
        "display_name": "危险",
        "trigger": "condition",
        "reason": "指标过低",
        "conditions": [{"kind": "time", "name": "day", "operator": "gte", "value": 1}],
    }]
    contract = TemporalContract.from_mapping(document)
    encoded = json.dumps(contract.to_dict(), ensure_ascii=False, sort_keys=True)
    assert "测试历" in encoded
    assert contract.fingerprint() == TemporalContract.from_mapping(document).fingerprint()


def test_invalid_pause_condition_reports_specific_array_path():
    document = calendar_document()
    document["auto_pause_conditions"] = [{
        "pause_id": "bad",
        "display_name": "错误条件",
        "trigger": "condition",
        "reason": "测试",
        "conditions": [{"kind": "not_supported", "operator": "eq", "value": 1}],
    }]
    with pytest.raises(TemporalContractError) as exc_info:
        TemporalContract.from_mapping(document)
    assert exc_info.value.path == "time_system.auto_pause_conditions[0].conditions[0]"


def test_public_state_does_not_expose_condition_values_or_hidden_fact_references():
    document = calendar_document()
    document["auto_pause_conditions"] = [{
        "pause_id": "secret_pause",
        "display_name": "秘密预警",
        "trigger": "condition",
        "reason": "公开的暂停说明",
        "conditions": [{
            "kind": "world_truth",
            "name": "hidden_enemy_plan",
            "operator": "eq",
            "value": "secret-value",
        }],
    }]
    public = TemporalContract.from_mapping(document).public_state(0)
    encoded = json.dumps(public, ensure_ascii=False)
    assert "公开的暂停说明" in encoded
    assert "hidden_enemy_plan" not in encoded
    assert "secret-value" not in encoded
    assert "conditions" not in public["auto_pause_conditions"][0]
