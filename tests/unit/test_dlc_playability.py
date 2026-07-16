from __future__ import annotations

from multiagent_framework.dlc_playability import audit_dlc_playability


def _complete_content() -> dict:
    return {
        "gameplay": {"player": {"identity_id": "captain", "actor_id": "captain_actor", "display_name": "站长", "controlled_objects": [{"kind": "agent", "id": "captain", "mode": "direct"}, {"kind": "entity", "id": "station", "mode": "direct"}] }},
        "ui_manifest": {"title": "轨道站控制台", "navigation": [{"nav_id": "main"}]},
        "agents": [{"id": "captain", "actor_id": "captain_actor"}, {"id": "engineer", "actor_id": "engineer_actor"}],
        "actions": [{
            "action_id": "repair", "costs": [{"resource_name": "energy", "base_value": 5}], "duration": {"proposed_days": 1},
            "result": {"mode": "composite", "results": [
                {"mode": "world_commands", "effects": [{"effect_type": "adjust_resource"}]},
                {"mode": "ongoing_event", "effect": {"effect_type": "create_ongoing_event"}},
                {"mode": "wake_agent", "agent_id": "engineer"},
                {"mode": "emit_event", "event": {"event_type": "repair_started"}},
            ]},
        }],
        "capabilities": [],
        "issue_types": [{
            "issue_type_id": "breach", "evidence": [{"kind": "scan", "required": True}],
            "decision_requests": [{"options": [{"option_id": "repair", "action_id": "repair"}]}],
        }],
        "initial_events": [{"event_id": "opening", "event_type": "breach_report", "target_agents": ["captain"], "initial_payload": {"issue_type_id": "breach"}}],
        "random_events": [],
        "causal_event_rules": [{"rule_id": "repair_chain", "source_event_types": ["repair_started"], "world_effects": [{"effect_type": "adjust_resource"}]}],
        "outcome_feedback": {"enabled": True, "immediate": {"enabled": True}, "rules": [{"rule_id": "repair_feedback", "source_kinds": ["action"], "enabled": True}]},
        "objectives": [{"objective_id": "stabilize"}],
        "endings": [{"ending_id": "saved"}],
        "time_system": {},
    }


def test_complete_cross_genre_loop_passes_all_seven_checks() -> None:
    report = audit_dlc_playability("orbital", _complete_content()).to_dict()
    assert report["playable"] is True
    assert report["passed_count"] == report["total_count"] == 7
    assert [item["check_id"] for item in report["checks"]] == [
        "player_surface", "action_roles", "issue_intake", "costly_decision",
        "world_simulation", "outcome_feedback", "progression",
    ]


def test_audit_reports_chinese_actionable_gaps_without_registry_errors() -> None:
    report = audit_dlc_playability("empty", {}).to_dict()
    assert report["playable"] is False
    assert report["passed_count"] == 0
    assert all(item["status"] == "gap" for item in report["checks"])
    assert "gameplay.player.identity_id" in report["checks"][0]["gaps"][0]
    assert "wake_agent" in report["checks"][1]["gaps"][1]
    assert "outcome_feedback" in report["checks"][5]["gaps"][0]


def test_sandbox_cycle_is_a_valid_progression_path() -> None:
    content = _complete_content()
    content["objectives"] = []
    content["endings"] = []
    content["time_system"] = {"enabled": True, "cycles": [{"cycle_id": "shift", "enabled": True}]}
    report = audit_dlc_playability("sandbox", content).to_dict()
    progression = report["checks"][-1]
    assert progression["passed"] is True
    assert progression["evidence"][0]["kind"] == "sandbox_cycle"
