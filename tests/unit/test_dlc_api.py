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



def test_causal_dry_run_api_accepts_unsaved_draft_without_mutating_package(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "causal_draft", "name": "Causal Draft", "version": "0.1.0"})
    saved = registry.load_content("causal_draft")
    draft = {
        **saved,
        "resources": [{"name": "grain", "kind": "asset", "anchor_rate": 2}],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "grain", "quantity": 20}],
        "agents": [{"id": "governor", "name": "Governor"}],
        "causal_event_rules": [{
            "rule_id": "low_grain",
            "source_event_types": ["shortage"],
            "child_event_type": "unrest",
            "target_agents": ["governor"],
            "delay_days": 1,
            "probability": 1,
            "conditions": [{
                "kind": "anchored_inventory",
                "entity_id": "city",
                "name": "grain",
                "operator": "lt",
                "value": 50,
            }],
        }],
    }
    orchestrator = type("Orchestrator", (), {"dlc_registry": registry})()
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/dlcs/causal_draft/causal-events/dry-run",
                json={
                    "content": draft,
                    "source_event": {"event_type": "shortage", "severity": 4},
                    "horizon_days": 10,
                    "max_events": 16,
                },
            )
            assert response.status_code == 200
            report = response.json()
            assert [item["event_type"] for item in report["nodes"]] == ["shortage", "unrest"]
            assert report["analysis"]["summary"]["rule_count"] == 1
            assert registry.load_content("causal_draft")["causal_event_rules"] == []

            invalid = client.post(
                "/api/dlcs/causal_draft/causal-events/dry-run",
                json={
                    "content": draft,
                    "source_event": {
                        "event_type": "shortage",
                        "initial_payload": {"causal_depth": 1},
                    },
                },
            )
            assert invalid.status_code == 400
            assert invalid.json()["detail"] == "causal_dry_run_source_invalid"
    finally:
        state.orchestrator = previous


def test_causal_dry_run_api_expands_unsaved_template_draft(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "template_draft", "name": "Template Draft", "version": "0.1.0"})
    draft = registry.load_content("template_draft")
    draft.update({
        "resources": [{"name": "coin", "kind": "asset", "anchor_rate": 1}],
        "entities": [{"id": "city", "type": "location", "name": "City"}],
        "inventory": [{"entity_id": "city", "item_name": "coin", "quantity": 20}],
        "agents": [{"id": "governor", "name": "Governor"}],
        "causal_condition_groups": [{
            "group_id": "funded", "conditions": [{
                "kind": "anchored_inventory", "entity_id": "city", "name": "coin",
                "operator": "gte", "value": 10,
            }],
        }],
        "causal_rule_templates": [{
            "template_id": "delayed", "rule": {"delay_days": 4, "probability": 1},
        }],
        "causal_event_rules": [{
            "rule_id": "seed_to_result", "template_id": "delayed",
            "condition_group_ids": ["funded"], "source_event_types": ["seed"],
            "child_event_type": "result", "target_agents": ["governor"],
        }],
    })
    orchestrator = type("Orchestrator", (), {"dlc_registry": registry})()
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/dlcs/template_draft/causal-events/dry-run",
                json={"content": draft, "source_event": {"event_type": "seed", "severity": 5}},
            )
            assert response.status_code == 200
            assert [item["event_type"] for item in response.json()["nodes"]] == ["seed", "result"]
            assert response.json()["nodes"][1]["day"] == 4
            assert registry.load_content("template_draft")["causal_rule_templates"] == []
    finally:
        state.orchestrator = previous


def test_causal_replay_api_uses_unsaved_draft_and_reports_drift(tmp_path: Path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "causal_replay", "name": "Causal Replay", "version": "0.1.0"})
    saved = registry.load_content("causal_replay")
    draft = {
        **saved,
        "causal_event_rules": [{
            "rule_id": "seed_to_result",
            "source_event_types": ["seed"],
            "child_event_type": "result",
            "delay_days": 2,
            "probability": 1,
            "max_depth": 4,
            "max_fires_per_chain": 1,
        }],
    }
    orchestrator = type("Orchestrator", (), {"dlc_registry": registry})()
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    state.orchestrator = orchestrator
    try:
        with TestClient(app) as client:
            preview = client.post(
                "/api/dlcs/causal_replay/causal-events/dry-run",
                json={
                    "content": draft,
                    "source_event": {"event_type": "seed", "severity": 5},
                    "horizon_days": 10,
                    "max_events": 16,
                },
            )
            assert preview.status_code == 200
            fixture = preview.json()["replay_fixture"]

            replay = client.post(
                "/api/dlcs/causal_replay/causal-events/replay",
                json={"content": draft, "fixture": fixture},
            )
            assert replay.status_code == 200
            assert replay.json()["comparison"]["matches"] is True
            assert registry.load_content("causal_replay")["causal_event_rules"] == []

            changed = {
                **draft,
                "causal_event_rules": [{**draft["causal_event_rules"][0], "delay_days": 4}],
            }
            drift = client.post(
                "/api/dlcs/causal_replay/causal-events/replay",
                json={"content": changed, "fixture": fixture},
            )
            assert drift.status_code == 200
            assert drift.json()["comparison"]["matches"] is False
            assert "analysis_fingerprint" in drift.json()["comparison"]["drift_fields"]
    finally:
        state.orchestrator = previous
