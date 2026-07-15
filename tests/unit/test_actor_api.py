from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.actor_contract import ActorManager
from routers import _state as state
from routers.worldstate import router


def test_actor_list_detail_and_404_api():
    manager = ActorManager()
    manager.configure([{"actor_id": "guard", "display_name": "??", "states": {"alert": True}}], generation=4)
    orchestrator = type("Orchestrator", (), {
        "actor_manager": manager,
        "actors_public_state": manager.public_state,
    })()
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with TestClient(app) as client:
            listed = client.get("/api/actors")
            assert listed.status_code == 200
            assert listed.json()["actor_count"] == 1
            assert listed.json()["generation"] == 4
            detail = client.get("/api/actors/guard")
            assert detail.status_code == 200
            assert detail.json()["runtime"]["states"]["alert"] is True
            assert "api_key" not in str(detail.json()).lower()
            missing = client.get("/api/actors/missing")
            assert missing.status_code == 404
    finally:
        state.orchestrator = previous
