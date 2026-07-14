from __future__ import annotations

from dataclasses import dataclass

from multiagent_framework.objective_contract import (
    ObjectiveManager,
    normalize_ending_document,
    normalize_objective_document,
    validate_objective_definitions,
)


@dataclass
class Entity:
    id: str
    current_status: str = "stable"


@dataclass
class Item:
    item_name: str
    base_value: float


@dataclass
class Value:
    name: str
    value: float


class World:
    def __init__(self):
        self.entities = {"station": Entity("station")}
        self.inventory = {"station": [Item("credits", 20.0)]}
        self.values = {("station", "metric"): [Value("alert", 2.0)]}

    def get_entity(self, entity_id):
        return self.entities.get(entity_id)

    def get_inventory(self, entity_id):
        return self.inventory.get(entity_id, [])

    def get_entity_values(self, entity_id, kind=None):
        return self.values.get((entity_id, kind), [])


class Actions:
    def __init__(self, count=0):
        self.count = count

    def snapshot(self):
        return {
            "receipts": {
                f"run-{index}": {
                    "response": {
                        "ok": True,
                        "action_id": "patrol",
                        "invocation": {
                            "actor_id": "player",
                            "control_object_id": "station",
                            "target_id": "station",
                        },
                    }
                }
                for index in range(self.count)
            }
        }


def objective_defs():
    return [{
        "objective_id": "secure_frontier",
        "display_name": "Secure frontier",
        "category": "primary",
        "milestone_mode": "ordered",
        "milestones": [
            {
                "milestone_id": "funded",
                "display_name": "Fund patrol",
                "condition": {
                    "kind": "anchored_inventory", "entity_id": "station",
                    "name": "credits", "operator": "gte", "base_value": 10,
                },
            },
            {
                "milestone_id": "patrolled",
                "display_name": "Patrol twice",
                "condition": {
                    "kind": "action_count", "action_id": "patrol",
                    "operator": "gte", "value": 2,
                },
            },
        ],
        "completion": {
            "kind": "metric", "entity_id": "station", "name": "alert",
            "operator": "gte", "value": 8,
        },
    }]


def ending_defs():
    return [{
        "ending_id": "frontier_safe",
        "display_name": "Frontier secured",
        "outcome": "victory",
        "trigger": {"objective_ids": ["secure_frontier"], "operator": "all"},
        "priority": 10,
    }]


def test_zero_objectives_is_explicit_sandbox_and_never_auto_wins():
    manager = ObjectiveManager()
    manager.configure([], [], generation=3)
    result = manager.evaluate(world_state=World(), sim_time=100 * 86400)
    assert result["changed"] is False
    assert result["state"]["sandbox"] is True
    assert result["state"]["status"] == "sandbox"
    assert result["state"]["current_ending"] is None


def test_ordered_milestones_authoritative_progress_and_ending_are_idempotent():
    world = World()
    actions = Actions(0)
    manager = ObjectiveManager()
    manager.configure(objective_defs(), ending_defs(), generation=4)

    first = manager.evaluate(world_state=world, sim_time=1, action_catalog=actions)
    assert [item["type"] for item in first["transitions"]] == ["milestone_completed"]
    objective = first["state"]["objectives"][0]
    assert [item["status"] for item in objective["milestones"]] == ["completed", "pending"]
    assert objective["status"] == "active"

    actions.count = 2
    second = manager.evaluate(world_state=world, sim_time=2, action_catalog=actions)
    assert [item["type"] for item in second["transitions"]] == ["milestone_completed"]
    assert second["state"]["status"] == "in_progress"

    world.values[("station", "metric")][0].value = 8
    final = manager.evaluate(world_state=world, sim_time=3, action_catalog=actions)
    assert [item["type"] for item in final["transitions"]] == ["objective_completed", "ending_reached"]
    assert final["state"]["current_ending"]["ending_id"] == "frontier_safe"

    replay = manager.evaluate(world_state=world, sim_time=4, action_catalog=actions)
    assert replay["changed"] is False
    assert replay["transitions"] == []
    assert replay["state"]["current_ending"]["reached_at"] == 3


def test_simultaneous_endings_use_priority_and_final_result_does_not_reverse():
    objectives = [
        {"objective_id": "win", "display_name": "Win", "completion": {"kind": "sim_day", "operator": "gte", "value": 1}},
        {"objective_id": "lose", "display_name": "Lose", "category": "failure", "completion": {"kind": "sim_day", "operator": "gte", "value": 1}},
    ]
    endings = [
        {"ending_id": "victory", "display_name": "Victory", "outcome": "victory", "priority": 10, "trigger": {"objective_ids": ["win"]}},
        {"ending_id": "defeat", "display_name": "Defeat", "outcome": "defeat", "priority": 100, "trigger": {"objective_ids": ["lose"]}},
    ]
    manager = ObjectiveManager()
    manager.configure(objectives, endings, generation=1)
    result = manager.evaluate(world_state=World(), sim_time=86400)
    assert result["state"]["current_ending"]["ending_id"] == "defeat"

    manager.configure(objectives, [endings[0]], generation=2)
    later = manager.evaluate(world_state=World(), sim_time=2 * 86400)
    assert later["state"]["current_ending"]["ending_id"] == "defeat"


def test_snapshot_restore_preserves_milestones_objectives_and_ending():
    world = World()
    world.values[("station", "metric")][0].value = 8
    manager = ObjectiveManager()
    manager.configure(objective_defs(), ending_defs(), generation=5)
    manager.evaluate(world_state=world, sim_time=10, action_catalog=Actions(2))
    snapshot = manager.snapshot()

    restored = ObjectiveManager()
    restored.configure(objective_defs(), ending_defs(), generation=6)
    restored.restore(snapshot, sim_time=20)
    state = restored.public_state(world_state=world, sim_time=20, action_catalog=Actions(2))
    assert state["current_ending"]["ending_id"] == "frontier_safe"
    assert state["current_ending"]["reached_at"] == 10
    assert state["objectives"][0]["status"] == "completed"


def test_normalization_and_reference_validation():
    objectives = normalize_objective_document(objective_defs())
    endings = normalize_ending_document(ending_defs())
    assert objectives[0]["milestones"][0]["condition"]["value"] == 10
    issues = validate_objective_definitions(
        objectives, endings,
        entity_ids={"other"},
        resources={"credits": {"kind": "metric"}, "alert": {"kind": "metric"}},
        action_ids=set(),
    )
    codes = {item.code for item in issues}
    assert "objective.condition.unknown_entity" in codes
    assert "objective.condition.resource_kind" in codes
    assert "objective.condition.unknown_action" in codes


def test_nested_validation_reports_exact_condition_path():
    objectives = [{
        "objective_id": "nested",
        "display_name": "Nested",
        "completion": {
            "op": "all",
            "conditions": [
                {"kind": "sim_day", "operator": "gte", "value": 1},
                {
                    "op": "any",
                    "conditions": [{
                        "kind": "action_count",
                        "action_id": "missing_action",
                        "operator": "gte",
                        "value": 1,
                    }],
                },
            ],
        },
    }]
    issues = validate_objective_definitions(objectives, [], action_ids=set())
    issue = next(item for item in issues if item.code == "objective.condition.unknown_action")
    assert issue.path == "objectives[0].completion.conditions[1].conditions[0].action_id"


def test_disabled_objective_and_ending_are_not_active_or_eligible():
    manager = ObjectiveManager()
    manager.configure(
        [{
            "objective_id": "disabled",
            "display_name": "Disabled",
            "enabled": False,
            "completion": {"kind": "sim_day", "operator": "gte", "value": 0},
        }],
        [{
            "ending_id": "disabled_ending",
            "display_name": "Disabled ending",
            "enabled": False,
            "trigger": {"objective_ids": ["disabled"]},
        }],
    )
    state = manager.public_state(world_state=World(), sim_time=100)
    assert state["sandbox"] is True
    assert state["objectives"][0]["status"] == "disabled"
    assert state["endings"][0]["eligible"] is False
