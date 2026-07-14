"""详细中文设置中心 API。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from multiagent_framework.model_gateway import GatewayConfig, ModelGatewayError
from multiagent_framework.runtime_settings import (
    RuntimeSettings,
    RuntimeSettingsError,
    atomic_write_json,
    merge_known_model_config,
    settings_schema,
)
from . import _state as state


router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsDocumentRequest(BaseModel):
    model_gateway: dict[str, Any]
    runtime: dict[str, Any]
    runtime_keys: dict[str, str] = Field(default_factory=dict)
    clear_runtime_keys: list[str] = Field(default_factory=list)
    expected_generation: int | None = Field(default=None, ge=1)
    allow_secret_host_change: bool = False


def _orchestrator():
    if not state.orchestrator:
        raise HTTPException(status_code=503, detail="世界模拟器尚未初始化")
    return state.orchestrator


def _gateway():
    gateway = getattr(_orchestrator(), "model_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="模型网关尚未初始化")
    return gateway


def _known_model_document(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "providers": copy.deepcopy(value.get("providers", {})),
        "models": copy.deepcopy(value.get("models", {})),
        "task_policies": copy.deepcopy(value.get("task_policies", {})),
        "dlc_task_policies": copy.deepcopy(value.get("dlc_task_policies", {})),
        "agent_task_policies": copy.deepcopy(value.get("agent_task_policies", {})),
    }


def _candidate(request: SettingsDocumentRequest) -> tuple[GatewayConfig, RuntimeSettings]:
    gateway = _gateway()
    source_path = "config/models.json" if gateway.config.legacy_mode else gateway.config.source_path
    model = GatewayConfig.from_dict(
        _known_model_document(request.model_gateway), source_path=source_path
    )
    runtime_source = getattr(
        getattr(_orchestrator(), "runtime_settings", None),
        "source_path",
        "config/runtime.json",
    )
    runtime = RuntimeSettings.from_dict(request.runtime, source_path=runtime_source)
    return model, runtime


def _configured_provider_names(gateway, candidate: GatewayConfig, request: SettingsDocumentRequest) -> set[str]:
    with gateway._state_lock:  # 内部事务读取；密钥永不离开本进程。
        current_runtime_keys = dict(gateway._runtime_keys)
    configured: set[str] = set()
    clears = set(request.clear_runtime_keys)
    for name, provider in candidate.providers.items():
        submitted = str(request.runtime_keys.get(name, "") or "").strip()
        if submitted or (name not in clears and current_runtime_keys.get(name)):
            configured.add(name)
        elif provider.api_key_env and os.environ.get(provider.api_key_env):
            configured.add(name)
    return configured


def _validate_secret_host_changes(
    gateway, candidate: GatewayConfig, request: SettingsDocumentRequest
) -> None:
    if request.allow_secret_host_change:
        return
    current = gateway.config
    risky: list[str] = []
    for name, provider in candidate.providers.items():
        old = current.providers.get(name)
        if old is None or old.base_url.rstrip("/") == provider.base_url.rstrip("/"):
            continue
        had_secret = bool(
            gateway.display_config().get("providers", {}).get(name, {}).get("api_key_configured")
            or old.api_key_env
        )
        if had_secret:
            risky.append(name)
    if risky:
        raise RuntimeSettingsError(
            "以下接口服务商修改了地址，可能把既有密钥发送到新主机："
            + "、".join(risky)
            + "。请在界面勾选安全确认后再次保存。"
        )


def _warnings(gateway, candidate: GatewayConfig, request: SettingsDocumentRequest) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    configured = _configured_provider_names(gateway, candidate, request)
    for name in candidate.providers:
        if name not in configured:
            warnings.append({
                "path": f"model_gateway.providers.{name}",
                "code": "provider.api_key.missing",
                "message": f"接口服务商“{name}”当前没有可用密钥；调用时会失败并尝试下一个候选模型。",
            })
    if "default" not in candidate.task_policies:
        warnings.append({
            "path": "model_gateway.task_policies.default",
            "code": "routing.default.missing",
            "message": "未配置 default 路由；未命中任务规则时将按模型 Profile 定义顺序尝试。",
        })
    return warnings


def _environment_status() -> dict[str, Any]:
    truthy = {"1", "true", "yes", "on"}
    def enabled(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in truthy
    def present(name: str) -> bool:
        return bool(os.environ.get(name, ""))

    alert_names = {
        "WORLD_SIM_ALERT_LLM_ERROR_RATE": "模型错误率告警阈值",
        "WORLD_SIM_ALERT_LLM_FALLBACK_RATE": "模型回退率告警阈值",
        "WORLD_SIM_ALERT_DLC_RELOAD_FAILURE_RATE": "DLC 重载失败率阈值",
        "WORLD_SIM_ALERT_DLC_RELOAD_MAX_MS": "DLC 重载耗时阈值（毫秒）",
        "WORLD_SIM_ALERT_WORLD_COMMAND_REJECTION_RATE": "世界命令拒绝率阈值",
        "WORLD_SIM_ALERT_AGENT_TURN_ERROR_RATE": "Agent 回合错误率阈值",
        "WORLD_SIM_ALERT_ARCHIVE_ISSUES": "归档问题数量阈值",
        "WORLD_SIM_ALERT_BUSY_AGENT_RATIO": "繁忙 Agent 比例阈值",
    }
    return {
        "items": [
            {"name": "WORLD_SIM_ENABLE_ADMIN_API", "label": "管理 API", "description": "控制模型代次回滚、晋级和 DLC 热重载管理接口。", "kind": "boolean", "value": enabled("WORLD_SIM_ENABLE_ADMIN_API"), "editable": False, "restart_required": True},
            {"name": "WORLD_SIM_ADMIN_TOKEN", "label": "管理 API Token", "description": "管理接口通过 X-Admin-Token 请求头校验；界面只显示是否配置。", "kind": "secret", "configured": present("WORLD_SIM_ADMIN_TOKEN"), "editable": False, "restart_required": True},
            {"name": "WORLD_SIM_ENABLE_METRICS_API", "label": "指标 API", "description": "控制运维指标接口是否对外提供。", "kind": "boolean", "value": enabled("WORLD_SIM_ENABLE_METRICS_API"), "editable": False, "restart_required": True},
            {"name": "WORLD_SIM_METRICS_TOKEN", "label": "指标 API Token", "description": "指标接口访问凭证；界面只显示是否配置。", "kind": "secret", "configured": present("WORLD_SIM_METRICS_TOKEN"), "editable": False, "restart_required": True},
            {"name": "WORLD_SIM_DLC_ARCHIVE_KEY", "label": "DLC 代次归档签名密钥", "description": "用于验证 DLC 代次归档没有被离线篡改。", "kind": "secret", "configured": present("WORLD_SIM_DLC_ARCHIVE_KEY"), "editable": False, "restart_required": True},
            {"name": "WORLD_SIM_MODEL_ARCHIVE_KEY", "label": "模型代次归档签名密钥", "description": "用于验证模型配置代次归档完整性。", "kind": "secret", "configured": present("WORLD_SIM_MODEL_ARCHIVE_KEY"), "editable": False, "restart_required": True},
            {"name": "WORLD_SIM_BASELINE_REGISTRY_KEY", "label": "评估基线签名密钥", "description": "用于保护模型评估与晋级基线注册表。", "kind": "secret", "configured": present("WORLD_SIM_BASELINE_REGISTRY_KEY"), "editable": False, "restart_required": True},
        ],
        "alerts": [
            {
                "name": name,
                "label": label,
                "value": os.environ.get(name, ""),
                "configured": present(name),
                "editable": False,
                "restart_required": True,
                "description": "通过环境变量配置；留空时使用程序默认值。",
            }
            for name, label in alert_names.items()
        ],
        "note": "环境变量由启动进程注入。为避免把管理凭证或签名密钥写入仓库，本设置中心仅展示状态，不在线修改。",
    }


def _document() -> dict[str, Any]:
    orch = _orchestrator()
    gateway = _gateway()
    runtime = getattr(orch, "runtime_settings", None)
    if runtime is None:
        runtime = RuntimeSettings.from_file("config/runtime.json")
    return {
        "schema_version": "1.0",
        "generation": gateway.generation,
        "model_gateway": gateway.display_config(),
        "runtime": runtime.to_dict(),
        "environment": _environment_status(),
        "runtime_state": {
            "simulation_running": bool(getattr(orch, "is_running", False)),
            "simulation_paused": bool(getattr(orch, "is_paused", False)),
            "agent_count": len(getattr(orch, "agents", {})),
            "active_dlc_generation": int(getattr(getattr(orch, "dlc_loader", None), "generation", 0)),
        },
    }


@router.get("/schema")
async def get_settings_schema():
    return settings_schema()


@router.get("")
async def get_settings():
    return _document()


@router.post("/validate")
async def validate_settings(request: SettingsDocumentRequest):
    try:
        gateway = _gateway()
        candidate, runtime = _candidate(request)
        _validate_secret_host_changes(gateway, candidate, request)
        preview = gateway.preview_config(candidate)
        return {
            "valid": True,
            "preview": preview,
            "runtime": runtime.to_dict(),
            "warnings": _warnings(gateway, candidate, request),
        }
    except (RuntimeSettingsError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _file_snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _restore_file(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(previous)


def _apply_runtime_settings(orch, runtime: RuntimeSettings) -> None:
    orch.runtime_settings = runtime
    orch._tick_interval = runtime.simulation.tick_interval
    orch.set_time_scale(runtime.simulation.seconds_per_day)

    defaults = runtime.agent_defaults.__dict__
    for agent in getattr(orch, "agents", {}).values():
        for name, value in defaults.items():
            setattr(agent.config, name, value)
        if hasattr(agent, "structured_memory"):
            agent.structured_memory.max_records = runtime.agent_defaults.structured_memory_max_records
    loader = getattr(orch, "dlc_loader", None)
    if loader is not None and hasattr(loader, "agent_factory"):
        loader.agent_factory.agent_defaults = dict(defaults)

    action_agent = getattr(orch, "action_agent", None)
    if action_agent is not None:
        for name, value in runtime.action_agent.__dict__.items():
            setattr(action_agent, name, value)
    compressor = getattr(orch, "_memory_compressor", None)
    if compressor is not None:
        compressor._temperature = runtime.memory.compression_temperature


@router.post("")
async def save_settings(request: SettingsDocumentRequest):
    gateway = _gateway()
    orch = _orchestrator()
    try:
        candidate, runtime = _candidate(request)
        _validate_secret_host_changes(gateway, candidate, request)
        preview = gateway.preview_config(candidate)
        warnings = _warnings(gateway, candidate, request)
    except (RuntimeSettingsError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    with gateway._state_lock:  # 复制后立即离开锁；不会写日志或返回。
        runtime_keys = dict(gateway._runtime_keys)
    for name in request.clear_runtime_keys:
        runtime_keys.pop(name, None)
    for name, secret in request.runtime_keys.items():
        if name in candidate.providers and str(secret or "").strip():
            runtime_keys[name] = str(secret).strip()
    runtime_keys = {name: key for name, key in runtime_keys.items() if name in candidate.providers and key}

    model_path = Path(candidate.source_path)
    runtime_path = Path(runtime.source_path)
    legacy_time_path = runtime_path.parent / "time.json"
    previous_model = _file_snapshot(model_path)
    previous_runtime = _file_snapshot(runtime_path)
    previous_time = _file_snapshot(legacy_time_path)
    previous_runtime_object = getattr(orch, "runtime_settings", None)

    try:
        old_raw: Mapping[str, Any] = {}
        if model_path.exists():
            loaded = json.loads(model_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, Mapping):
                old_raw = loaded
        persisted_model = merge_known_model_config(old_raw, candidate.to_dict())
        atomic_write_json(model_path, persisted_model)
        runtime.save_to_file()
        atomic_write_json(legacy_time_path, runtime.simulation.__dict__)
        report = await gateway.replace_config(
            candidate,
            runtime_keys=runtime_keys,
            expected_generation=request.expected_generation,
        )
        _apply_runtime_settings(orch, runtime)
    except (OSError, ValueError, KeyError, RuntimeError, ModelGatewayError) as exc:
        _restore_file(model_path, previous_model)
        _restore_file(runtime_path, previous_runtime)
        _restore_file(legacy_time_path, previous_time)
        if previous_runtime_object is not None:
            _apply_runtime_settings(orch, previous_runtime_object)
        raise HTTPException(status_code=409, detail=f"设置保存失败，已恢复原配置：{exc}") from None

    return {
        "status": "saved",
        "report": report,
        "preview": preview,
        "warnings": warnings,
        "settings": _document(),
    }
