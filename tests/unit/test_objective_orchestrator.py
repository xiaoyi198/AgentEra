from __future__ import annotations

import asyncio
from types import SimpleNamespace

from multiagent_framework.orchestrator import GAME_SECONDS_PER_DAY, Orchestrator


class Court:
    def get_official(self, _agent_id):
        return None


class Timers:
    def pop_due(self, _sim_time):
        return []

    def get_in_transit(self):
        return []


class Router:
    def __init__(self):
        self.llm = SimpleNamespace(
            token_counter=SimpleNamespace(to_dict=lambda: {})
        )


def _orchestrator() -> Orchestrator:
    return Orchestrator(
        court_graph=Court(),
        timer_system=Timers(),
        chain_log=SimpleNamespace(),
        router=Router(),
    )


def test_tick_emits_objective_transitions_created_between_ticks_once():
    orchestrator = _orchestrator()
    orchestrator.configure_objective_catalog(
        [{
            "objective_id": "survive_day",
            "display_name": "Survive one day",
            "completion": {
                "kind": "sim_day",
                "operator": "gte",
                "value": 1,
            },
        }],
        [{
            "ending_id": "survived",
            "display_name": "Survived",
            "outcome": "victory",
            "trigger": {"objective_ids": ["survive_day"]},
        }],
        generation=4,
    )
    orchestrator._sim_time = GAME_SECONDS_PER_DAY

    # A GET/API/action can evaluate the objective before the next main-loop tick.
    state = orchestrator.objectives_public_state()
    assert state["current_ending"]["ending_id"] == "survived"

    first_tick = asyncio.run(orchestrator._tick())
    assert [item["type"] for item in first_tick.objective_transitions] == [
        "objective_completed",
        "ending_reached",
    ]

    second_tick = asyncio.run(orchestrator._tick())
    assert second_tick.objective_transitions == []


def test_tick_directly_evaluates_time_based_objective_and_emits_transition():
    orchestrator = _orchestrator()
    orchestrator.configure_objective_catalog(
        [{
            "objective_id": "deadline",
            "display_name": "Reach deadline",
            "category": "failure",
            "completion": {
                "kind": "sim_day",
                "operator": "gte",
                "value": 2,
            },
        }],
        [],
        generation=2,
    )
    orchestrator._sim_time = 2 * GAME_SECONDS_PER_DAY

    result = asyncio.run(orchestrator._tick())

    assert result.objective_transitions == [{
        "type": "objective_completed",
        "objective_id": "deadline",
        "category": "failure",
        "completed_at": float(2 * GAME_SECONDS_PER_DAY),
        "generation": 2,
    }]
