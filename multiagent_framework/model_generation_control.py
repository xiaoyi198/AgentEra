"""Generation-safe preview and application of model configuration rollbacks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .model_gateway import GatewayConfig, ModelGateway, ModelGatewayError
from .model_promotion_apply import (
    _atomic_write_bytes,
    _config_bytes,
    _fingerprint,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ModelGenerationControlError(ValueError):
    """Raised when a model generation rollback cannot be safely performed."""


def _strict_generation(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ModelGenerationControlError(f"{label}_invalid")
    return value


def _changed_count(current: dict[str, Any], source: dict[str, Any]) -> int:
    keys = set(current) | set(source)
    return sum(current.get(key) != source.get(key) for key in keys)


def _scoped_policy_changed_count(
    current: dict[str, dict[str, Any]], source: dict[str, dict[str, Any]]
) -> int:
    return sum(
        _changed_count(current.get(scope, {}), source.get(scope, {}))
        for scope in set(current) | set(source)
    )


def _agent_policy_changed_count(
    current: dict[str, dict[str, dict[str, Any]]],
    source: dict[str, dict[str, dict[str, Any]]],
) -> int:
    return sum(
        _scoped_policy_changed_count(current.get(dlc, {}), source.get(dlc, {}))
        for dlc in set(current) | set(source)
    )


class ModelGenerationController:
    """Plan and atomically activate a retained model configuration generation."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def _plan(
        self, source_generation: int, expected_current_generation: int
    ) -> tuple[dict[str, Any], GatewayConfig]:
        source_generation = _strict_generation(source_generation, "source_generation")
        expected_current_generation = _strict_generation(
            expected_current_generation, "expected_current_generation"
        )
        current_generation, current = self.gateway.configuration_snapshot()
        if current_generation != expected_current_generation:
            raise ModelGenerationControlError("current_generation_stale")
        if current.legacy_mode:
            raise ModelGenerationControlError("legacy_configuration_unsupported")
        if source_generation == current_generation:
            raise ModelGenerationControlError("source_generation_is_current")
        try:
            source = self.gateway.generation_configuration(source_generation)
        except ModelGatewayError as exc:
            raise ModelGenerationControlError("source_generation_unavailable") from exc
        source.source_path = current.source_path
        source.validate()
        current_payload = current.to_dict()
        source_payload = source.to_dict()
        current_models = current_payload["models"]
        source_models = source_payload["models"]
        current_names = set(current_models)
        source_names = set(source_models)
        current_providers = current_payload["providers"]
        source_providers = source_payload["providers"]
        current_provider_names = set(current_providers)
        source_provider_names = set(source_providers)
        changed_providers = sum(
            current_providers[name] != source_providers[name]
            for name in current_provider_names & source_provider_names
        )
        changed_profiles = sum(
            current_models[name] != source_models[name]
            for name in current_names & source_names
        )
        core = {
            "schema_version": "1.0",
            "source_generation": source_generation,
            "current_generation": current_generation,
            "next_generation": current_generation + 1,
            "current_config_fingerprint": _fingerprint(current_payload),
            "source_config_fingerprint": _fingerprint(source_payload),
            "providers_added_count": len(source_provider_names - current_provider_names),
            "providers_removed_count": len(current_provider_names - source_provider_names),
            "providers_changed_count": changed_providers,
            "profiles_added_count": len(source_names - current_names),
            "profiles_removed_count": len(current_names - source_names),
            "profiles_changed_count": changed_profiles,
            "task_policy_changes": _changed_count(
                current_payload["task_policies"], source_payload["task_policies"]
            ),
            "dlc_policy_changes": _scoped_policy_changed_count(
                current_payload["dlc_task_policies"], source_payload["dlc_task_policies"]
            ),
            "agent_policy_changes": _agent_policy_changed_count(
                current_payload["agent_task_policies"], source_payload["agent_task_policies"]
            ),
        }
        plan = dict(core)
        plan["plan_fingerprint"] = _fingerprint(core)
        return plan, source

    def preview_rollback(
        self, source_generation: int, expected_current_generation: int
    ) -> dict[str, Any]:
        plan, _ = self._plan(source_generation, expected_current_generation)
        return plan

    async def apply_rollback(
        self,
        source_generation: int,
        expected_current_generation: int,
        expected_plan_fingerprint: str,
    ) -> dict[str, Any]:
        if not _SHA256.fullmatch(str(expected_plan_fingerprint or "")):
            raise ModelGenerationControlError("plan_fingerprint_invalid")
        plan, candidate = self._plan(
            source_generation, expected_current_generation
        )
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise ModelGenerationControlError("plan_fingerprint_mismatch")
        target = Path(candidate.source_path)
        try:
            original = target.read_bytes()
            disk_payload = json.loads(original.decode("utf-8-sig"))
            disk_config = GatewayConfig.from_dict(
                disk_payload, source_path=str(target)
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ModelGenerationControlError(
                "source_configuration_unreadable"
            ) from exc
        if _fingerprint(disk_config.to_dict()) != plan["current_config_fingerprint"]:
            raise ModelGenerationControlError("source_configuration_changed")
        _atomic_write_bytes(target, _config_bytes(candidate))
        try:
            report = await self.gateway.replace_config(
                candidate, expected_generation=plan["current_generation"]
            )
        except Exception as exc:
            try:
                _atomic_write_bytes(target, original)
            except OSError as restore_exc:
                raise ModelGenerationControlError(
                    "configuration_reload_and_restore_failed"
                ) from restore_exc
            raise ModelGenerationControlError("configuration_reload_failed") from exc
        return {
            **plan,
            "status": "applied",
            "generation": report["generation"],
            "archive_persisted": bool(report.get("archive_persisted")),
        }
