from __future__ import annotations

import pytest

from multiagent_framework.causal_conditions import (
    CausalCondition,
    CausalConditionContext,
    compare_condition_value,
    resolve_condition_value,
)
from multiagent_framework.gameplay_effect_runtime import GameplayEffectRuntime
from multiagent_framework.gameplay_effects import materialize_world_commands, normalize_world_effects
from multiagent_framework.knowledge_runtime import KnowledgeRuntime, reduce_knowledge_state
from multiagent_framework.types import EventObject
from multiagent_framework.causal_world_effects import execute_causal_world_commands
from multiagent_framework.world_commands import WorldCommandError
from multiagent_framework.world_state import Entity, WorldState


def _reduce(state, command_type, payload, *, at=100, command_id="cmd-1"):
    return reduce_knowledge_state(
        state, command_type=command_type, payload=payload,
        sim_time=at, command_id=command_id,
    )


def test_three_layers_are_isolated_and_public_projection_never_contains_world_truth():
    state, _ = _reduce(None, "set_world_truth", {
        "entity_id": "site", "knowledge_id": "route", "value": "north",
        "secrecy_level": "secret",
    })
    state, _ = _reduce(state, "set_actor_belief", {
        "holder_id": "scout", "entity_id": "site", "knowledge_id": "route",
        "value": "east", "confidence": 0.4,
    }, command_id="cmd-2")
    state, _ = _reduce(state, "reveal_information", {
        "holder_id": "player", "entity_id": "site", "knowledge_id": "route",
        "value": "unknown", "confidence": 0.2,
    }, command_id="cmd-3")
    runtime = KnowledgeRuntime(state)

    assert runtime.get_world_truth("site", "route") == "north"
    assert runtime.get_actor_belief("scout", "site", "route") == "east"
    assert runtime.get_actor_belief("guard", "site", "route") is None
    assert runtime.get_player_knowledge("site", "route") == "unknown"

    public = runtime.player_projection(sim_time=100)
    assert [item["value"] for item in public["knowledge"]] == ["unknown"]
    assert "north" not in str(public)
    assert "east" not in str(public)


def test_public_gameplay_projection_does_not_reveal_hidden_layer_counts():
    state, _ = _reduce(None, "set_world_truth", {
        "entity_id": "site", "knowledge_id": "route", "value": "north",
    })
    state, _ = _reduce(state, "set_actor_belief", {
        "holder_id": "scout", "entity_id": "site", "knowledge_id": "route",
        "value": "east",
    }, command_id="cmd-2")

    class _World:
        @staticmethod
        def get_gameplay_effect_state():
            return {"schema_version": "2.0", "knowledge_runtime": state}

    runtime = GameplayEffectRuntime()
    runtime.refresh(_World())
    public = runtime.public_state(player_holder_id="player", sim_time=100)

    assert "world_truths" not in public["counts"]
    assert "actor_beliefs" not in public["counts"]
    assert "north" not in str(public)
    assert "east" not in str(public)


def test_expired_knowledge_cannot_be_propagated():
    state, _ = _reduce(None, "set_world_truth", {
        "entity_id": "site", "knowledge_id": "route", "value": "north",
        "expires_at": 105,
    }, at=100)

    with pytest.raises(WorldCommandError) as exc_info:
        _reduce(state, "propagate_knowledge", {
            "source_layer": "world_truth", "target_layer": "player_knowledge",
            "target_holder_id": "player", "entity_id": "site",
            "knowledge_id": "route",
        }, at=106, command_id="cmd-2")

    assert exc_info.value.code == "knowledge_source_expired"


def test_propagation_preserves_provenance_and_records_distortion():
    state, truth = _reduce(None, "set_world_truth", {
        "entity_id": "site", "knowledge_id": "strength", "value": 10,
        "source": "world_model", "confidence": 1,
    })
    state, belief = _reduce(state, "propagate_knowledge", {
        "source_layer": "world_truth", "target_layer": "actor_belief",
        "target_holder_id": "observer", "entity_id": "site",
        "knowledge_id": "strength", "confidence_multiplier": 0.7,
        "value_override": 15,
        "distortion": {"kind": "exaggerated", "note": "poor visibility"},
    }, at=110, command_id="cmd-2")

    assert truth["value"] == 10
    assert belief["value"] == 15
    assert belief["confidence"] == 0.7
    assert belief["distortion"]["kind"] == "exaggerated"
    assert belief["provenance"][-1]["layer"] == "world_truth"
    assert "value" not in belief["provenance"][-1]


def test_freshness_uses_simulation_time_and_expiry_hides_value_but_not_status():
    state, _ = _reduce(None, "reveal_information", {
        "holder_id": "player", "entity_id": "site", "knowledge_id": "weather",
        "value": "clear", "stale_after_seconds": 10, "expires_at": 130,
    }, at=100)
    runtime = KnowledgeRuntime(state)

    assert runtime.get_player_knowledge("site", "weather", sim_time=109) == "clear"
    assert runtime.get_player_knowledge("site", "weather", field="freshness", sim_time=111) == "stale"
    assert runtime.get_player_knowledge("site", "weather", sim_time=131) is None
    assert runtime.get_player_knowledge("site", "weather", field="freshness", sim_time=131) == "expired"


def test_investigation_completion_reveals_only_to_configured_holder():
    state, _ = _reduce(None, "set_world_truth", {
        "entity_id": "site", "knowledge_id": "cause", "value": "fault",
        "secrecy_level": "confidential",
    })
    state, started = _reduce(state, "start_investigation", {
        "investigation_id": "inspect-cause", "entity_id": "site",
        "knowledge_id": "cause", "investigator_layer": "actor_belief",
        "investigator_id": "engineer", "required_progress": 2,
        "result_policy": {
            "source_layer": "world_truth", "target_layer": "actor_belief",
            "target_holder_id": "engineer", "confidence_multiplier": 0.9,
        },
    }, command_id="cmd-2")
    assert started["status"] == "active"

    state, partial = _reduce(state, "advance_investigation", {
        "investigation_id": "inspect-cause", "mode": "adjust", "amount": 1,
        "evidence": [{"kind": "sample"}],
    }, at=120, command_id="cmd-3")
    assert partial["investigation"]["status"] == "active"

    state, completed = _reduce(state, "advance_investigation", {
        "investigation_id": "inspect-cause", "mode": "adjust", "amount": 1,
    }, at=130, command_id="cmd-4")
    runtime = KnowledgeRuntime(state)
    assert completed["investigation"]["status"] == "completed"
    assert runtime.get_actor_belief("engineer", "site", "cause") == "fault"
    assert runtime.get_actor_belief("observer", "site", "cause") is None
    assert runtime.get_player_knowledge("site", "cause") is None


def test_knowledge_conditions_address_explicit_layers_and_holders():
    state, _ = _reduce(None, "set_actor_belief", {
        "holder_id": "scout", "entity_id": "site", "knowledge_id": "route",
        "value": "east", "confidence": 0.6, "secrecy_level": "restricted",
    })
    provider = GameplayEffectRuntime()
    provider.knowledge.refresh(state)
    context = CausalConditionContext(knowledge_provider=provider, sim_time=100)

    belief = CausalCondition.from_mapping({
        "kind": "actor_belief", "entity_id": "site", "name": "route",
        "target_entity_id": "scout", "operator": "eq", "value": "east",
    })
    confidence = CausalCondition.from_mapping({
        "kind": "knowledge_confidence", "entity_id": "site", "name": "route",
        "target_entity_id": "scout", "filters": {"layer": "actor_belief"},
        "operator": "gte", "value": 0.5,
    })
    hidden_from_other = CausalCondition.from_mapping({
        "kind": "actor_belief", "entity_id": "site", "name": "route",
        "target_entity_id": "guard", "operator": "not_exists",
    })

    assert compare_condition_value(resolve_condition_value(belief, context), belief.operator, belief.value)
    assert compare_condition_value(resolve_condition_value(confidence, context), confidence.operator, confidence.value)
    assert compare_condition_value(resolve_condition_value(hidden_from_other, context), hidden_from_other.operator, hidden_from_other.value)


def test_causal_knowledge_batch_is_atomic_with_world_state(tmp_path):
    world = WorldState(str(tmp_path / "world.db"))
    world.upsert_entity(Entity(id="site", type="location", name="Site"))
    effects = normalize_world_effects([
        {"effect_type": "declare_world_truth", "entity_id": "site", "knowledge_id": "route", "value": "north"},
        {
            "effect_type": "propagate_knowledge", "source_layer": "actor_belief",
            "source_holder_id": "missing", "target_layer": "player_knowledge",
            "target_holder_id": "player", "entity_id": "site", "knowledge_id": "route",
        },
    ])
    commands = materialize_world_commands(
        effects, world_state=world, due_at=100, source_id="child", chain_id="chain",
    )
    event = EventObject(
        event_id="knowledge-event", event_type="knowledge", generated_at=100,
        initial_payload={"causal_chain_id": "chain", "causal_parent_event_id": "parent"},
        world_commands=commands,
    )

    result = execute_causal_world_commands(world, event, sim_time=100)

    assert result is not None and not result.ok and not result.committed
    runtime = GameplayEffectRuntime()
    runtime.refresh(world)
    assert runtime.get_world_truth("site", "route") is None
