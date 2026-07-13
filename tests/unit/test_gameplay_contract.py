from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.gameplay_contract import GameplayContract
from multiagent_framework.orchestrator import Orchestrator
from routers import _state as state
from routers.simulation import router


def _orbital_contract() -> GameplayContract:
    return GameplayContract.from_content(
        {
            "player": {
                "identity_id": "station_commander",
                "display_name": "Station Commander",
                "actor_id": "commander_actor",
                "controlled_objects": [
                    {"kind": "entity", "id": "orbital_station", "mode": "direct"},
                    {"kind": "agent", "id": "operations_ai", "mode": "direct"},
                    {"kind": "agent", "id": "science_ai", "mode": "command"},
                ],
            }
        },
        [
            {"id": "operations_ai", "role": "autonomous"},
            {"id": "science_ai", "role": "autonomous"},
        ],
        package_id="orbital_station",
    )


def test_orchestrator_player_recipient_comes_from_contract_not_role():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.configure_gameplay_contract(_orbital_contract(), generation=4)

    assert orchestrator._is_player_recipient(
        "operations_ai", SimpleNamespace(role="autonomous")
    )
    assert not orchestrator._is_player_recipient(
        "science_ai", SimpleNamespace(role="player")
    )
    assert orchestrator.gameplay_public_state()["generation"] == 4


def test_gameplay_api_returns_stable_theme_neutral_contract():
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = SimpleNamespace(
        gameplay_public_state=lambda: {
            **_orbital_contract().to_dict(),
            "generation": 7,
        }
    )
    try:
        with TestClient(app) as client:
            response = client.get("/api/gameplay")
        assert response.status_code == 200
        body = response.json()
        assert body["package_id"] == "orbital_station"
        assert body["player"]["identity_id"] == "station_commander"
        assert body["player"]["controlled_objects"][1] == {
            "kind": "agent",
            "id": "operations_ai",
            "mode": "direct",
            "display_name": "",
        }
        assert "emperor" not in str(body).lower()
        assert "memorial" not in str(body).lower()
    finally:
        state.orchestrator = previous

def test_orchestrator_initializes_with_unconfigured_gameplay_contract():
    orchestrator = Orchestrator(
        court_graph=SimpleNamespace(),
        timer_system=SimpleNamespace(),
        chain_log=SimpleNamespace(),
        router=SimpleNamespace(_system_prompt="", llm=None),
    )

    assert orchestrator.gameplay_public_state() == {
        "configured": False,
        "package_id": "",
        "package_name": "",
        "source": "none",
        "player": None,
        "generation": 0,
    }


def test_gameplay_api_uninitialized_fallback_keeps_stable_shape():
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = None
    try:
        with TestClient(app) as client:
            response = client.get("/api/gameplay")
        assert response.status_code == 200
        assert response.json() == {
            "configured": False,
            "generation": 0,
            "package_id": "",
            "package_name": "",
            "source": "none",
            "player": None,
        }
    finally:
        state.orchestrator = previous


def test_published_empty_contract_disables_legacy_role_fallback():
    orchestrator = Orchestrator.__new__(Orchestrator)
    orchestrator.configure_gameplay_contract(
        GameplayContract(package_id="primary_without_player"), generation=2
    )

    assert not orchestrator._is_player_recipient(
        "secondary_legacy_player", SimpleNamespace(role="player")
    )


def test_legacy_contract_ignores_disabled_player_agents():
    contract = GameplayContract.from_content(
        {},
        [
            {"id": "disabled_player", "role": "player", "enabled": False},
            {"id": "active_player", "name": "Active", "role": "player"},
        ],
        package_id="legacy_world",
    )

    assert contract.player is not None
    assert contract.player.identity_id == "active_player"
    assert contract.direct_agent_ids == {"active_player"}
