from __future__ import annotations

from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multiagent_framework.orchestrator import Orchestrator
from multiagent_framework.dlc_registry import DLCRegistry
from multiagent_framework.ui_manifest import (
    UIManifest,
    default_ui_manifest,
    normalize_ui_manifest_document,
    validate_ui_manifest_document,
)
from routers import _state as state
from routers.simulation import router


def test_default_manifest_is_complete_and_theme_neutral():
    manifest = default_ui_manifest(package_name="Orbital Frontier")

    assert manifest["title"] == "Orbital Frontier"
    assert manifest["terminology"]["player"] == "玩家"
    assert {item["view"] for item in manifest["navigation"] if item["surface"] == "main"} == {
        "dashboard", "world", "relations", "debug",
    }
    assert {item["kind"] for item in manifest["dashboard"]["widgets"]} == {
        "objectives", "issue_list", "issue_detail", "player_message", "in_transit",
    }
    lowered = str(manifest).lower()
    assert "emperor" not in lowered
    assert "memorial" not in lowered
    assert "朝堂" not in str(manifest)


def test_partial_manifest_is_completed_without_accepting_arbitrary_capabilities():
    manifest = normalize_ui_manifest_document({
        "title": "边境指挥中心",
        "terminology": {"player": "指挥官", "issue": "前线事项"},
        "navigation": [{
            "nav_id": "command", "surface": "main", "view": "dashboard",
            "label": "指挥台", "icon": "台", "order": 8,
        }],
        "dashboard": {"layout": "single_column", "widgets": [{
            "widget_id": "mission", "kind": "objectives", "region": "top",
        }]},
        "entity_detail": {"sections": ["summary", "inventory"]},
    }, package_name="Frontier")

    assert manifest["title"] == "边境指挥中心"
    assert manifest["terminology"]["player"] == "指挥官"
    assert manifest["terminology"]["world"] == "世界状态"
    assert manifest["navigation"][0]["view"] == "dashboard"
    assert manifest["dashboard"]["widgets"][0]["kind"] == "objectives"
    assert manifest["entity_detail"]["sections"] == ["summary", "inventory"]


def test_validation_rejects_unknown_views_widgets_fields_duplicates_and_urls():
    issues = validate_ui_manifest_document({
        "title": "<script>alert(1)</script>",
        "url": "https://evil.test/root",
        "terminology": {"unknown": "value"},
        "navigation": [
            {"nav_id": "x", "surface": "main", "view": "dashboard", "label": "A", "icon": "A", "href": "https://evil.test"},
            {"nav_id": "x", "surface": "main", "view": "remote_component", "label": "B", "icon": "B"},
        ],
        "dashboard": {"layout": "freeform", "widgets": [
            {"widget_id": "x", "kind": "objectives", "region": "top"},
            {"widget_id": "x", "kind": "arbitrary_react", "region": "overlay", "component": "Evil"},
        ]},
        "issue_page": {"list_fields": ["title", "raw_html"], "detail_fields": []},
        "entity_detail": {"visible_fields": ["name", "secret"], "sections": ["summary", "iframe"]},
    })
    codes = {item.code for item in issues}

    assert "ui_manifest.unsafe_text" in codes
    assert "ui_manifest.forbidden_capability" in codes
    assert "ui_manifest.unknown_section" in codes
    assert "ui_manifest.unknown_term" in codes
    assert "ui_manifest.forbidden_navigation_capability" in codes
    assert "ui_manifest.duplicate_nav_id" in codes
    assert "ui_manifest.unknown_view" in codes
    assert "ui_manifest.unknown_layout" in codes
    assert "ui_manifest.duplicate_widget_id" in codes
    assert "ui_manifest.unknown_widget" in codes
    assert "ui_manifest.unknown_region" in codes
    assert "ui_manifest.unknown_issue_field" in codes
    assert "ui_manifest.unknown_entity_capability" in codes


def test_validation_requires_usable_navigation_and_boolean_flags():
    issues = validate_ui_manifest_document({
        "navigation": [
            {
                "nav_id": "main", "surface": "main", "view": "dashboard",
                "label": "Main", "icon": "M", "visible": False,
            },
            {
                "nav_id": "shell", "surface": "shell", "view": "gameplay",
                "label": "Shell", "icon": "S", "visible": False,
            },
        ],
        "dashboard": {
            "layout": "single_column",
            "widgets": [{
                "widget_id": "objectives", "kind": "objectives",
                "region": "main", "visible": "yes",
            }],
        },
        "issue_page": {"show_resolved": 0},
    })
    codes = [item.code for item in issues]

    assert codes.count("ui_manifest.missing_visible_navigation") == 2
    assert "ui_manifest.invalid_visible" in codes
    assert "ui_manifest.invalid_show_resolved" in codes


def test_orchestrator_publishes_generation_bound_ui_manifest():
    orchestrator = Orchestrator.__new__(Orchestrator)
    manifest = UIManifest.from_content(
        {"title": "边境指挥中心", "terminology": {"player": "指挥官"}},
        package_id="frontier", package_name="Frontier",
    )
    orchestrator.configure_ui_manifest(manifest, generation=9)

    public = orchestrator.ui_manifest_public_state()
    assert public["generation"] == 9
    assert public["configured"] is True
    assert public["package_id"] == "frontier"
    assert public["title"] == "边境指挥中心"
    assert public["manifest"]["terminology"]["player"] == "指挥官"


def test_ui_manifest_api_returns_public_manifest_and_safe_fallback():
    app = FastAPI()
    app.include_router(router)
    previous = state.orchestrator
    try:
        published = UIManifest.from_content(
            {"title": "海港管理局"}, package_id="harbor", package_name="Harbor"
        )
        state.orchestrator = SimpleNamespace(
            ui_manifest_public_state=lambda: {**published.to_dict(), "generation": 3}
        )
        with TestClient(app) as client:
            response = client.get("/api/ui-manifest")
        assert response.status_code == 200
        assert response.json()["title"] == "海港管理局"
        assert response.json()["generation"] == 3

        state.orchestrator = None
        with TestClient(app) as client:
            fallback = client.get("/api/ui-manifest").json()
        assert fallback["configured"] is False
        assert fallback["generation"] == 0
        assert fallback["title"] == "世界模拟器"
    finally:
        state.orchestrator = previous



def test_registry_rejects_unsafe_ui_manifest(tmp_path):
    registry = DLCRegistry(tmp_path, manifest_roots=[tmp_path / "dlcs"])
    registry.create({"id": "unsafe_ui", "name": "Unsafe UI", "version": "1.0.0"})
    content = registry.load_content("unsafe_ui")
    content["ui_manifest"] = {
        "title": "Unsafe",
        "navigation": [{
            "nav_id": "remote", "surface": "main", "view": "dashboard",
            "label": "Remote", "icon": "R", "url": "https://example.invalid",
        }],
    }
    registry.save_content("unsafe_ui", content)

    issues = registry.validate("unsafe_ui")
    assert any(issue.code == "ui_manifest.forbidden_navigation_capability" for issue in issues)
    with pytest.raises(Exception, match="validation failed"):
        registry.publish("unsafe_ui")
