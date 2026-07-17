from __future__ import annotations

from fastapi.testclient import TestClient

import routers.dlc as dlc_router
from main import app
from multiagent_framework.dlc_registry import DLCRegistry


class _Orchestrator:
    def __init__(self, registry: DLCRegistry) -> None:
        self.dlc_registry = registry


def test_dlc_package_payload_includes_seven_stage_playability_report(tmp_path, monkeypatch) -> None:
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "audit-demo", "name": "Audit Demo", "version": "0.1.0"})
    monkeypatch.setattr(dlc_router.state, "orchestrator", _Orchestrator(registry))

    response = TestClient(app).get("/api/dlcs/audit-demo")

    assert response.status_code == 200
    payload = response.json()
    assert payload["playability"]["dlc_id"] == "audit-demo"
    assert payload["playability"]["total_count"] == 7
    assert payload["playability"]["playable"] is False
    assert payload["valid"] is True
