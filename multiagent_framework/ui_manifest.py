"""DLC-authored, capability-safe user interface manifest.

DLC packages may choose labels, built-in views, dashboard widgets, and visible
fields. They cannot provide arbitrary URLs, HTML, CSS, JavaScript, or React
component names; all executable capabilities remain framework-owned.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

MAIN_VIEWS = frozenset({"dashboard", "world", "relations", "debug"})
SHELL_VIEWS = frozenset({"gameplay", "editor", "dlc_studio", "assistant"})
NAV_SURFACES = frozenset({"main", "shell"})
DASHBOARD_LAYOUTS = frozenset({"three_column", "two_column", "single_column"})
DASHBOARD_REGIONS = frozenset({"top", "left", "main", "right", "bottom"})
DASHBOARD_WIDGETS = frozenset({
    "objectives", "derived_metrics", "issue_list", "issue_detail", "player_message", "in_transit",
})
ISSUE_LIST_FIELDS = frozenset({
    "title", "source", "urgency", "created_at", "status", "issue_type", "case_id",
})
ISSUE_DETAIL_FIELDS = frozenset({
    "title", "source", "summary", "status", "case_id", "deadline", "evidence",
    "decisions", "urgency", "created_at", "issue_type", "external_id",
})
ENTITY_VISIBLE_FIELDS = frozenset({
    "id", "type", "name", "description", "current_status", "updated_at", "version",
})
ENTITY_SECTIONS = frozenset({
    "summary", "ongoing_events", "location", "inventory", "metrics", "parameters",
})
TERMINOLOGY_KEYS = frozenset({
    "player", "issue", "issue_plural", "case", "decision", "evidence", "source",
    "deadline", "message", "in_transit", "world", "entity", "entities",
    "relations", "objective", "objectives", "dashboard", "debug", "status",
    "content", "urgency", "created_at", "issue_type", "inventory", "metrics",
    "parameters", "ongoing_events", "location", "search", "publish_message",
})

_TEXT_LIMITS = {
    "title": 80,
    "subtitle": 240,
    "short_mark": 4,
    "label": 40,
    "icon": 4,
    "term": 40,
}

_DEFAULT_TERMINOLOGY = {
    "player": "玩家",
    "issue": "事项",
    "issue_plural": "待处理事项",
    "case": "事件档案",
    "decision": "处置方案",
    "evidence": "证据",
    "source": "来源",
    "deadline": "期限",
    "message": "消息",
    "in_transit": "在途消息",
    "world": "世界状态",
    "entity": "实体",
    "entities": "实体",
    "relations": "关系",
    "objective": "目标",
    "objectives": "目标",
    "dashboard": "控制台",
    "debug": "调试",
    "status": "状态",
    "content": "内容",
    "urgency": "紧急度",
    "created_at": "创建时间",
    "issue_type": "事项类型",
    "inventory": "库存",
    "metrics": "指标",
    "parameters": "参数",
    "ongoing_events": "持续事件",
    "location": "位置",
    "search": "搜索",
    "publish_message": "发布消息",
}

_DEFAULT_MAIN_NAV = [
    {"nav_id": "dashboard", "surface": "main", "view": "dashboard", "label": "控制台", "icon": "台", "order": 0, "visible": True},
    {"nav_id": "world", "surface": "main", "view": "world", "label": "世界状态", "icon": "界", "order": 10, "visible": True},
    {"nav_id": "relations", "surface": "main", "view": "relations", "label": "关系", "icon": "联", "order": 20, "visible": True},
    {"nav_id": "debug", "surface": "main", "view": "debug", "label": "调试", "icon": "析", "order": 30, "visible": True},
]
_DEFAULT_SHELL_NAV = [
    {"nav_id": "gameplay", "surface": "shell", "view": "gameplay", "label": "模拟", "icon": "世", "order": 0, "visible": True},
    {"nav_id": "editor", "surface": "shell", "view": "editor", "label": "内容编辑", "icon": "编", "order": 10, "visible": True},
    {"nav_id": "dlc_studio", "surface": "shell", "view": "dlc_studio", "label": "DLC Studio", "icon": "扩", "order": 20, "visible": True},
    {"nav_id": "assistant", "surface": "shell", "view": "assistant", "label": "智能助手", "icon": "智", "order": 30, "visible": True},
]
_DEFAULT_WIDGETS = [
    {"widget_id": "objectives", "kind": "objectives", "region": "top", "order": 0, "visible": True},
    {"widget_id": "derived_metrics", "kind": "derived_metrics", "region": "top", "order": 5, "visible": True},
    {"widget_id": "issues", "kind": "issue_list", "region": "left", "order": 10, "visible": True},
    {"widget_id": "issue_detail", "kind": "issue_detail", "region": "main", "order": 20, "visible": True},
    {"widget_id": "message", "kind": "player_message", "region": "main", "order": 30, "visible": True},
    {"widget_id": "transit", "kind": "in_transit", "region": "right", "order": 40, "visible": True},
]


@dataclass(frozen=True)
class UIManifestValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"


def default_ui_manifest(*, package_name: str = "") -> dict[str, Any]:
    """Return a complete generic fallback with no scenario-specific vocabulary."""
    title = str(package_name or "世界模拟器").strip()[:_TEXT_LIMITS["title"]] or "世界模拟器"
    return {
        "title": title,
        "subtitle": "",
        "brand": {"short_mark": "世"},
        "terminology": copy.deepcopy(_DEFAULT_TERMINOLOGY),
        "navigation": copy.deepcopy(_DEFAULT_MAIN_NAV + _DEFAULT_SHELL_NAV),
        "dashboard": {"layout": "three_column", "widgets": copy.deepcopy(_DEFAULT_WIDGETS)},
        "issue_page": {
            "list_fields": ["title", "source", "urgency", "created_at", "status"],
            "detail_fields": ["source", "summary", "status", "case_id", "deadline", "evidence", "decisions"],
            "show_resolved": True,
        },
        "entity_detail": {
            "visible_fields": ["id", "type", "name", "description", "current_status"],
            "sections": ["summary", "ongoing_events", "location", "inventory", "metrics", "parameters"],
        },
    }


def _text(value: Any, fallback: str, limit: int) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    return value[:limit] if value else fallback


def _order(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        return max(-10000, min(10000, int(value)))
    except (TypeError, ValueError):
        return fallback


def normalize_ui_manifest_document(
    document: dict[str, Any] | None, *, package_name: str = ""
) -> dict[str, Any]:
    """Complete a manifest with deterministic generic defaults.

    Unknown capability identifiers are preserved where practical so validation can
    report them; frontends and runtime consumers still apply their own allowlists.
    """
    source = document if isinstance(document, dict) else {}
    result = default_ui_manifest(package_name=package_name)
    result["title"] = _text(source.get("title"), result["title"], _TEXT_LIMITS["title"])
    result["subtitle"] = _text(source.get("subtitle"), "", _TEXT_LIMITS["subtitle"])

    brand = source.get("brand") if isinstance(source.get("brand"), dict) else {}
    result["brand"]["short_mark"] = _text(
        brand.get("short_mark"), result["brand"]["short_mark"], _TEXT_LIMITS["short_mark"]
    )

    terminology = source.get("terminology") if isinstance(source.get("terminology"), dict) else {}
    for key in TERMINOLOGY_KEYS:
        if key in terminology:
            result["terminology"][key] = _text(
                terminology.get(key), result["terminology"][key], _TEXT_LIMITS["term"]
            )

    if isinstance(source.get("navigation"), list):
        result["navigation"] = []
        for index, item in enumerate(source["navigation"]):
            if not isinstance(item, dict):
                continue
            surface = str(item.get("surface", "main")).strip()
            view = str(item.get("view", "dashboard")).strip()
            result["navigation"].append({
                "nav_id": _text(item.get("nav_id"), f"nav_{index}", 64),
                "surface": surface,
                "view": view,
                "label": _text(item.get("label"), view or f"导航 {index + 1}", _TEXT_LIMITS["label"]),
                "icon": _text(item.get("icon"), "•", _TEXT_LIMITS["icon"]),
                "order": _order(item.get("order"), index * 10),
                "visible": bool(item.get("visible", True)),
            })

    dashboard = source.get("dashboard") if isinstance(source.get("dashboard"), dict) else {}
    result["dashboard"]["layout"] = str(dashboard.get("layout", result["dashboard"]["layout"])).strip()
    if isinstance(dashboard.get("widgets"), list):
        result["dashboard"]["widgets"] = []
        for index, item in enumerate(dashboard["widgets"]):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "")).strip()
            result["dashboard"]["widgets"].append({
                "widget_id": _text(item.get("widget_id"), f"widget_{index}", 64),
                "kind": kind,
                "region": str(item.get("region", "main")).strip(),
                "order": _order(item.get("order"), index * 10),
                "visible": bool(item.get("visible", True)),
            })

    issue_page = source.get("issue_page") if isinstance(source.get("issue_page"), dict) else {}
    for key in ("list_fields", "detail_fields"):
        if isinstance(issue_page.get(key), list):
            result["issue_page"][key] = [str(value).strip() for value in issue_page[key] if str(value).strip()]
    if "show_resolved" in issue_page:
        result["issue_page"]["show_resolved"] = bool(issue_page["show_resolved"])

    entity_detail = source.get("entity_detail") if isinstance(source.get("entity_detail"), dict) else {}
    for key in ("visible_fields", "sections"):
        if isinstance(entity_detail.get(key), list):
            result["entity_detail"][key] = [str(value).strip() for value in entity_detail[key] if str(value).strip()]
    return result


def _has_markup(value: str) -> bool:
    lowered = value.lower()
    return "<" in value or ">" in value or "javascript:" in lowered or "data:" in lowered


def validate_ui_manifest_document(document: dict[str, Any] | None) -> list[UIManifestValidationIssue]:
    source = document if isinstance(document, dict) else {}
    issues: list[UIManifestValidationIssue] = []

    def add(code: str, message: str, path: str) -> None:
        issues.append(UIManifestValidationIssue(code, message, path))

    forbidden_keys = {"href", "url", "component", "html", "css", "script"}

    def reject_executable_keys(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if str(key).strip().lower() in forbidden_keys:
                    add(
                        "ui_manifest.forbidden_capability",
                        f"Executable or remote UI capability is forbidden: {key}",
                        child_path,
                    )
                reject_executable_keys(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_executable_keys(child, f"{path}[{index}]")

    if document is not None and not isinstance(document, dict):
        return [UIManifestValidationIssue("ui_manifest.invalid_type", "ui_manifest must be an object", "ui_manifest")]

    reject_executable_keys(source, "ui_manifest")
    allowed_sections = {
        "title", "subtitle", "brand", "terminology", "navigation", "dashboard",
        "issue_page", "entity_detail",
    }
    for key in source:
        if key not in allowed_sections:
            add(
                "ui_manifest.unknown_section",
                f"Unknown UI manifest section: {key}",
                f"ui_manifest.{key}",
            )

    for key, limit in (("title", _TEXT_LIMITS["title"]), ("subtitle", _TEXT_LIMITS["subtitle"])):
        value = source.get(key)
        if value is not None and not isinstance(value, str):
            add("ui_manifest.invalid_text", f"{key} must be a string", f"ui_manifest.{key}")
        elif isinstance(value, str) and (len(value.strip()) > limit or _has_markup(value)):
            add("ui_manifest.unsafe_text", f"{key} exceeds {limit} characters or contains markup", f"ui_manifest.{key}")

    brand = source.get("brand", {})
    if not isinstance(brand, dict):
        add("ui_manifest.invalid_brand", "brand must be an object", "ui_manifest.brand")
    else:
        mark = brand.get("short_mark")
        if mark is not None and (not isinstance(mark, str) or not mark.strip() or len(mark.strip()) > _TEXT_LIMITS["short_mark"] or _has_markup(mark)):
            add("ui_manifest.invalid_short_mark", "short_mark must be 1-4 plain-text characters", "ui_manifest.brand.short_mark")

    terminology = source.get("terminology", {})
    if not isinstance(terminology, dict):
        add("ui_manifest.invalid_terminology", "terminology must be an object", "ui_manifest.terminology")
    else:
        for key, value in terminology.items():
            path = f"ui_manifest.terminology.{key}"
            if key not in TERMINOLOGY_KEYS:
                add("ui_manifest.unknown_term", f"Unknown terminology key: {key}", path)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > _TEXT_LIMITS["term"] or _has_markup(str(value)):
                add("ui_manifest.invalid_term", "Terminology values must be 1-40 plain-text characters", path)

    navigation = source.get("navigation", [])
    if not isinstance(navigation, list):
        add("ui_manifest.invalid_navigation", "navigation must be a list", "ui_manifest.navigation")
    else:
        seen: set[str] = set()
        for index, item in enumerate(navigation):
            path = f"ui_manifest.navigation[{index}]"
            if not isinstance(item, dict):
                add("ui_manifest.invalid_navigation_item", "Navigation item must be an object", path)
                continue
            forbidden = set(item) & {"href", "url", "component", "html", "css", "script"}
            if forbidden:
                add("ui_manifest.forbidden_navigation_capability", f"Arbitrary navigation capability is forbidden: {sorted(forbidden)[0]}", path)
            nav_id = str(item.get("nav_id", "")).strip()
            if not nav_id:
                add("ui_manifest.missing_nav_id", "nav_id is required", f"{path}.nav_id")
            elif nav_id in seen:
                add("ui_manifest.duplicate_nav_id", f"Duplicate nav_id: {nav_id}", f"{path}.nav_id")
            seen.add(nav_id)
            surface = str(item.get("surface", "")).strip()
            view = str(item.get("view", "")).strip()
            if surface not in NAV_SURFACES:
                add("ui_manifest.unknown_surface", f"Unknown navigation surface: {surface}", f"{path}.surface")
            allowed = MAIN_VIEWS if surface == "main" else SHELL_VIEWS
            if view not in allowed:
                add("ui_manifest.unknown_view", f"View {view!r} is not allowed on {surface!r}", f"{path}.view")
            for key in ("label", "icon"):
                value = item.get(key)
                limit = _TEXT_LIMITS[key]
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or _has_markup(str(value)):
                    add(f"ui_manifest.invalid_{key}", f"{key} must be 1-{limit} plain-text characters", f"{path}.{key}")
            if "visible" in item and not isinstance(item["visible"], bool):
                add("ui_manifest.invalid_visible", "visible must be a boolean", f"{path}.visible")

        normalized_navigation = normalize_ui_manifest_document(source)["navigation"]
        for surface in NAV_SURFACES:
            if not any(
                item.get("surface") == surface and item.get("visible") is True
                for item in normalized_navigation
            ):
                add(
                    "ui_manifest.missing_visible_navigation",
                    f"At least one visible {surface} navigation item is required",
                    "ui_manifest.navigation",
                )

    dashboard = source.get("dashboard", {})
    if not isinstance(dashboard, dict):
        add("ui_manifest.invalid_dashboard", "dashboard must be an object", "ui_manifest.dashboard")
    else:
        layout = str(dashboard.get("layout", "three_column"))
        if layout not in DASHBOARD_LAYOUTS:
            add("ui_manifest.unknown_layout", f"Unknown dashboard layout: {layout}", "ui_manifest.dashboard.layout")
        widgets = dashboard.get("widgets", [])
        if not isinstance(widgets, list):
            add("ui_manifest.invalid_widgets", "dashboard.widgets must be a list", "ui_manifest.dashboard.widgets")
        else:
            seen_widgets: set[str] = set()
            for index, item in enumerate(widgets):
                path = f"ui_manifest.dashboard.widgets[{index}]"
                if not isinstance(item, dict):
                    add("ui_manifest.invalid_widget", "Widget must be an object", path)
                    continue
                forbidden = set(item) & {"component", "html", "url", "script"}
                if forbidden:
                    add("ui_manifest.forbidden_widget_capability", "Widgets may only select framework-owned kinds", path)
                widget_id = str(item.get("widget_id", "")).strip()
                if not widget_id:
                    add("ui_manifest.missing_widget_id", "widget_id is required", f"{path}.widget_id")
                elif widget_id in seen_widgets:
                    add("ui_manifest.duplicate_widget_id", f"Duplicate widget_id: {widget_id}", f"{path}.widget_id")
                seen_widgets.add(widget_id)
                kind = str(item.get("kind", "")).strip()
                if kind not in DASHBOARD_WIDGETS:
                    add("ui_manifest.unknown_widget", f"Unknown dashboard widget: {kind}", f"{path}.kind")
                region = str(item.get("region", "")).strip()
                if region not in DASHBOARD_REGIONS:
                    add("ui_manifest.unknown_region", f"Unknown dashboard region: {region}", f"{path}.region")
                if "visible" in item and not isinstance(item["visible"], bool):
                    add("ui_manifest.invalid_visible", "visible must be a boolean", f"{path}.visible")

    issue_page = source.get("issue_page", {})
    if not isinstance(issue_page, dict):
        add("ui_manifest.invalid_issue_page", "issue_page must be an object", "ui_manifest.issue_page")
    else:
        for key, allowed in (("list_fields", ISSUE_LIST_FIELDS), ("detail_fields", ISSUE_DETAIL_FIELDS)):
            values = issue_page.get(key, [])
            if not isinstance(values, list):
                add("ui_manifest.invalid_field_list", f"{key} must be a list", f"ui_manifest.issue_page.{key}")
                continue
            for index, value in enumerate(values):
                if str(value) not in allowed:
                    add("ui_manifest.unknown_issue_field", f"Unknown issue field: {value}", f"ui_manifest.issue_page.{key}[{index}]")
            if len({str(value) for value in values}) != len(values):
                add("ui_manifest.duplicate_issue_field", f"{key} contains duplicates", f"ui_manifest.issue_page.{key}")
        if "show_resolved" in issue_page and not isinstance(issue_page["show_resolved"], bool):
            add(
                "ui_manifest.invalid_show_resolved",
                "show_resolved must be a boolean",
                "ui_manifest.issue_page.show_resolved",
            )

    entity_detail = source.get("entity_detail", {})
    if not isinstance(entity_detail, dict):
        add("ui_manifest.invalid_entity_detail", "entity_detail must be an object", "ui_manifest.entity_detail")
    else:
        for key, allowed in (("visible_fields", ENTITY_VISIBLE_FIELDS), ("sections", ENTITY_SECTIONS)):
            values = entity_detail.get(key, [])
            if not isinstance(values, list):
                add("ui_manifest.invalid_field_list", f"{key} must be a list", f"ui_manifest.entity_detail.{key}")
                continue
            for index, value in enumerate(values):
                if str(value) not in allowed:
                    add("ui_manifest.unknown_entity_capability", f"Unknown entity detail capability: {value}", f"ui_manifest.entity_detail.{key}[{index}]")
            if len({str(value) for value in values}) != len(values):
                add("ui_manifest.duplicate_entity_capability", f"{key} contains duplicates", f"ui_manifest.entity_detail.{key}")
    return issues


@dataclass(frozen=True)
class UIManifest:
    package_id: str = ""
    package_name: str = ""
    source: str = "default"
    configured: bool = False
    document: dict[str, Any] | None = None

    @classmethod
    def from_content(
        cls, document: dict[str, Any] | None, *, package_id: str = "", package_name: str = ""
    ) -> "UIManifest":
        configured = isinstance(document, dict) and bool(document)
        return cls(
            package_id=package_id,
            package_name=package_name,
            source="explicit" if configured else "default",
            configured=configured,
            document=normalize_ui_manifest_document(document, package_name=package_name),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UIManifest":
        source = data if isinstance(data, dict) else {}
        package_name = str(source.get("package_name", ""))
        raw_document = source.get("manifest")
        if not isinstance(raw_document, dict):
            raw_document = {key: value for key, value in source.items() if key in default_ui_manifest()}
        return cls(
            package_id=str(source.get("package_id", "")),
            package_name=package_name,
            source=str(source.get("source", "default")),
            configured=bool(source.get("configured", False)),
            document=normalize_ui_manifest_document(raw_document, package_name=package_name),
        )

    def to_dict(self) -> dict[str, Any]:
        manifest = normalize_ui_manifest_document(self.document, package_name=self.package_name)
        return {
            "configured": self.configured,
            "package_id": self.package_id,
            "package_name": self.package_name,
            "source": self.source,
            "manifest": manifest,
            **copy.deepcopy(manifest),
        }
