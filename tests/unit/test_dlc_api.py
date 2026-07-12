from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.dlc_registry import DLCRegistry
from routers import _state as state
from routers.dlc import router


def _legacy_content() -> dict:
    return {
        "schema_version": "0.9",
        "resources": [{"name": "coin", "type": "asset", "anchor_rate": 1}],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "resource_name": "coin", "quantity": 3}],
        "entity_values": [],
        "relations": [],
        "agents": [],
        "initial_events": [],
        "ongoing_events": [],
        "personas": {},
        "prompts": {},
    }


def test_dlc_migration_preview_and_apply_api(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "legacy_bundle", "name": "Legacy Bundle", "version": "0.1.0"})
    registry.save_content("legacy_bundle", _legacy_content())
    orchestrator = type("Orchestrator", (), {"dlc_registry": registry})()
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with TestClient(app) as client:
            preview = client.post(
                "/api/dlcs/legacy_bundle/migration/preview",
                json={"target_schema_version": "1.0"},
            )
            assert preview.status_code == 200
            report = preview.json()
            assert report["source_schema_version"] == "0.9"
            assert report["content"]["inventory"][0]["item_name"] == "coin"

            applied = client.post(
                "/api/dlcs/legacy_bundle/migration/apply",
                json={
                    "target_schema_version": "1.0",
                    "expected_source_schema_version": "0.9",
                },
            )
            assert applied.status_code == 200
            body = applied.json()
            assert body["package"]["content"]["schema_version"] == "1.0"
            assert body["package"]["valid"] is True

            stale = client.post(
                "/api/dlcs/legacy_bundle/migration/apply",
                json={
                    "target_schema_version": "1.0",
                    "expected_source_schema_version": "0.9",
                },
            )
            assert stale.status_code == 400
            assert "changed since preview" in stale.json()["detail"]
    finally:
        state.orchestrator = previous
