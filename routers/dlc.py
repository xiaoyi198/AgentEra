"""DLC Studio HTTP API backed by the filesystem-confined DLCRegistry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from multiagent_framework.causal_analysis import dry_run_causal_rules
from multiagent_framework.causal_events import CausalEventError
from multiagent_framework.dlc_registry import DLCRegistry, DLCRegistryError
from routers import _state as state

router = APIRouter(prefix="/api/dlcs", tags=["dlcs"])


class DLCCreateRequest(BaseModel):
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    priority: int = 0
    primary: bool = False
    dependencies: list[str] = Field(default_factory=list)
    dependency_constraints: dict[str, str] = Field(default_factory=dict)
    model_bindings: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ManifestUpdateRequest(BaseModel):
    name: str | None = None
    version: str | None = None
    schema_version: str | None = None
    description: str | None = None
    priority: int | None = None
    primary: bool | None = None
    dependencies: list[str] | None = None
    dependency_constraints: dict[str, str] | None = None
    entrypoints: dict[str, str] | None = None
    model_bindings: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ContentUpdateRequest(BaseModel):
    content: dict[str, Any]


class EnabledRequest(BaseModel):
    enabled: bool


class MigrationRequest(BaseModel):
    target_schema_version: str = "1.0"
    expected_source_schema_version: str | None = None


class KeyedObjectRequest(BaseModel):
    data: dict[str, Any]


class CausalDryRunRequest(BaseModel):
    content: dict[str, Any] | None = None
    source_event: dict[str, Any]
    horizon_days: float = Field(default=365.0, ge=0, le=3650)
    max_events: int = Field(default=128, ge=1, le=256)


def _registry() -> DLCRegistry:
    orchestrator = state.orchestrator
    registry = getattr(orchestrator, "dlc_registry", None) if orchestrator else None
    if not registry:
        raise HTTPException(status_code=503, detail="DLC Registry is not initialized")
    return registry


def _call(operation):
    try:
        return operation()
    except (DLCRegistryError, CausalEventError) as exc:
        message = str(exc)
        status = 404 if message.startswith("Unknown DLC:") else 400
        raise HTTPException(status_code=status, detail=message) from exc


def _package_payload(registry: DLCRegistry, dlc_id: str, *, content: bool = False) -> dict[str, Any]:
    package = registry.get(dlc_id)
    issues = registry.validate(dlc_id)
    payload: dict[str, Any] = {
        "manifest": package.manifest.to_dict(),
        "editable": package.manifest.format == "bundle-v1",
        "valid": not any(item.severity == "error" for item in issues),
        "issues": [item.to_dict() for item in issues],
    }
    if content:
        payload["content"] = registry.load_content(dlc_id)
    return payload


@router.get("")
async def list_dlcs():
    registry = _registry()
    return _call(lambda: [
        _package_payload(registry, package.manifest.id)
        for package in registry.list_packages()
    ])


@router.post("")
async def create_dlc(request: DLCCreateRequest):
    registry = _registry()
    package = _call(lambda: registry.create(request.model_dump()))
    return _call(lambda: _package_payload(registry, package.manifest.id, content=True))


@router.get("/{dlc_id}")
async def get_dlc(dlc_id: str):
    registry = _registry()
    return _call(lambda: _package_payload(registry, dlc_id, content=True))


@router.put("/{dlc_id}/manifest")
async def update_manifest(dlc_id: str, request: ManifestUpdateRequest):
    registry = _registry()
    updates = request.model_dump(exclude_none=True)
    _call(lambda: registry.save_manifest(dlc_id, updates))
    return _call(lambda: _package_payload(registry, dlc_id, content=True))


@router.put("/{dlc_id}/content")
async def update_content(dlc_id: str, request: ContentUpdateRequest):
    registry = _registry()
    _call(lambda: registry.save_content(dlc_id, request.content))
    return _call(lambda: _package_payload(registry, dlc_id, content=True))


@router.post("/{dlc_id}/validate")
async def validate_dlc(dlc_id: str):
    registry = _registry()
    return _call(lambda: _package_payload(registry, dlc_id))


@router.post("/{dlc_id}/migration/preview")
async def preview_dlc_migration(dlc_id: str, request: MigrationRequest):
    registry = _registry()
    return _call(lambda: registry.plan_content_migration(dlc_id, request.target_schema_version))


@router.post("/{dlc_id}/migration/apply")
async def apply_dlc_migration(dlc_id: str, request: MigrationRequest):
    registry = _registry()
    report = _call(lambda: registry.migrate_content(
        dlc_id,
        request.target_schema_version,
        expected_source_schema_version=request.expected_source_schema_version,
    ))
    report["package"] = _call(lambda: _package_payload(registry, dlc_id, content=True))
    return report


@router.post("/{dlc_id}/causal-events/dry-run")
async def dry_run_dlc_causal_events(dlc_id: str, request: CausalDryRunRequest):
    registry = _registry()

    def operation():
        # Accept an unsaved Studio draft without mutating the package on disk.
        registry.get(dlc_id)
        content = (
            registry.normalize_content_document(request.content)
            if request.content is not None
            else registry.load_content(dlc_id)
        )
        return dry_run_causal_rules(
            content,
            request.source_event,
            horizon_days=request.horizon_days,
            max_events=request.max_events,
        )

    return _call(operation)


@router.post("/{dlc_id}/publish")
async def publish_dlc(dlc_id: str):
    registry = _registry()
    _call(lambda: registry.publish(dlc_id))
    return _call(lambda: _package_payload(registry, dlc_id))


@router.post("/{dlc_id}/enabled")
async def set_dlc_enabled(dlc_id: str, request: EnabledRequest):
    registry = _registry()
    _call(lambda: registry.set_enabled(dlc_id, request.enabled))
    return _call(lambda: _package_payload(registry, dlc_id))


@router.put("/{dlc_id}/entities/{entity_id}")
async def upsert_entity(dlc_id: str, entity_id: str, request: KeyedObjectRequest):
    registry = _registry()
    created = _call(lambda: registry.upsert_entity(dlc_id, entity_id, request.data))
    return {"status": "ok", "created": created}


@router.delete("/{dlc_id}/entities/{entity_id}")
async def delete_entity(dlc_id: str, entity_id: str):
    registry = _registry()
    return {"status": "ok", "deleted": _call(lambda: registry.delete_entity(dlc_id, entity_id))}


@router.put("/{dlc_id}/agents/{agent_id}")
async def upsert_agent(dlc_id: str, agent_id: str, request: KeyedObjectRequest):
    registry = _registry()
    created = _call(lambda: registry.upsert_agent(dlc_id, agent_id, request.data))
    return {"status": "ok", "created": created}


@router.delete("/{dlc_id}/agents/{agent_id}")
async def delete_agent(dlc_id: str, agent_id: str):
    registry = _registry()
    return {"status": "ok", "deleted": _call(lambda: registry.delete_agent(dlc_id, agent_id))}


@router.put("/{dlc_id}/resources/{resource_name}")
async def upsert_resource(dlc_id: str, resource_name: str, request: KeyedObjectRequest):
    registry = _registry()
    created = _call(lambda: registry.upsert_resource(dlc_id, resource_name, request.data))
    return {"status": "ok", "created": created}


@router.delete("/{dlc_id}/resources/{resource_name}")
async def delete_resource(dlc_id: str, resource_name: str):
    registry = _registry()
    return {"status": "ok", "deleted": _call(lambda: registry.delete_resource(dlc_id, resource_name))}