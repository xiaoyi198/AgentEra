"""Generation-safe application of approved model-policy promotions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .model_gateway import GatewayConfig, ModelGateway, ModelGatewayError


class ModelPromotionApplyError(ValueError):
    """Raised when a promotion cannot be safely planned or applied."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = {
    "api_key", "authorization", "base_url", "provider_url", "secret",
    "credential", "credentials", "prompt", "prompts", "message", "messages",
    "response", "responses", "memory", "memories", "source_path", "config_path",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise ModelPromotionApplyError(f"{label}_invalid")
    return value.strip()


def _strict_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ModelPromotionApplyError(f"{label}_invalid")
    return value


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if (
                key in _FORBIDDEN_KEYS
                or key.endswith("_api_key")
                or key.endswith("_secret")
                or key.endswith("_credential")
                or key.endswith("_token")
                or key.endswith("_path")
                or key.endswith("_env")
            ):
                return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive(item) for item in value)
    elif isinstance(value, str):
        text = value.strip()
        return "://" in text or bool(re.match(r"^[A-Za-z]:[\\/]", text))
    return False


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _config_bytes(config: GatewayConfig) -> bytes:
    return (json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class ModelPromotionApplier:
    """Map approved cohort advice to central policies and activate it atomically."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        known_targets: Mapping[str, set[str]] | None = None,
    ) -> None:
        self.gateway = gateway
        self.known_targets = (
            {str(dlc): {str(agent) for agent in agents} for dlc, agents in known_targets.items()}
            if known_targets is not None
            else None
        )

    @staticmethod
    def _validate_promotion(promotion: Mapping[str, Any]) -> dict[str, Any]:
        if _contains_sensitive(promotion):
            raise ModelPromotionApplyError("promotion_contains_forbidden_content")
        if promotion.get("schema_version") != "1.0":
            raise ModelPromotionApplyError("promotion_schema_invalid")
        if promotion.get("approved") is not True or promotion.get("decision") != "approved":
            raise ModelPromotionApplyError("promotion_not_approved")
        policy_fingerprint = str(promotion.get("policy_fingerprint", ""))
        matrix_fingerprint = str(promotion.get("matrix_fingerprint", ""))
        if not _SHA256.fullmatch(policy_fingerprint):
            raise ModelPromotionApplyError("policy_fingerprint_invalid")
        if not _SHA256.fullmatch(matrix_fingerprint):
            raise ModelPromotionApplyError("matrix_fingerprint_invalid")
        patch = promotion.get("policy_patch")
        if not isinstance(patch, Mapping):
            raise ModelPromotionApplyError("policy_patch_invalid")
        task_policies = GatewayConfig._parse_task_policy_map(
            patch.get("task_policies", {}), "promotion.task_policies"
        )
        cohort_policies = GatewayConfig._parse_scoped_task_policies(
            patch.get("cohort_task_policies", {}), "promotion.cohort_task_policies"
        )
        return {
            "gate_id": _safe_id(promotion.get("gate_id"), "gate_id"),
            "environment_id": _safe_id(
                promotion.get("environment_id"), "environment_id"
            ),
            "matrix_id": _safe_id(promotion.get("matrix_id"), "matrix_id"),
            "policy_fingerprint": policy_fingerprint,
            "matrix_fingerprint": matrix_fingerprint,
            "task_policies": task_policies,
            "cohort_task_policies": cohort_policies,
        }

    @staticmethod
    def _validate_mapping(
        mapping: Mapping[str, Any], expected_cohorts: set[str], policy_fingerprint: str
    ) -> dict[str, Any]:
        if _contains_sensitive(mapping):
            raise ModelPromotionApplyError("mapping_contains_forbidden_content")
        if mapping.get("schema_version") != "1.0":
            raise ModelPromotionApplyError("mapping_schema_invalid")
        promotion_id = _safe_id(mapping.get("promotion_id"), "promotion_id")
        expected_generation = _strict_int(
            mapping.get("expected_gateway_generation"), "expected_gateway_generation"
        )
        if mapping.get("expected_policy_fingerprint") != policy_fingerprint:
            raise ModelPromotionApplyError("policy_fingerprint_mismatch")
        raw_targets = mapping.get("cohort_targets", {})
        if not isinstance(raw_targets, Mapping):
            raise ModelPromotionApplyError("cohort_targets_invalid")
        cohort_keys = {_safe_id(item, "cohort_id") for item in raw_targets}
        if cohort_keys != expected_cohorts:
            raise ModelPromotionApplyError("cohort_mapping_incomplete")
        targets: dict[str, dict[str, Any]] = {}
        claimed: set[tuple[str, str, str]] = set()
        for cohort, raw in raw_targets.items():
            if not isinstance(raw, Mapping):
                raise ModelPromotionApplyError("cohort_target_invalid")
            raw_dlcs = raw.get("dlcs", [])
            raw_agents = raw.get("agents", [])
            if not isinstance(raw_dlcs, list) or not isinstance(raw_agents, list):
                raise ModelPromotionApplyError("cohort_target_lists_invalid")
            dlcs = [_safe_id(item, "target_dlc") for item in raw_dlcs]
            if len(set(dlcs)) != len(dlcs):
                raise ModelPromotionApplyError("target_dlc_duplicate")
            agents: list[tuple[str, str]] = []
            for item in raw_agents:
                if not isinstance(item, Mapping):
                    raise ModelPromotionApplyError("target_agent_invalid")
                agents.append((
                    _safe_id(item.get("dlc_id"), "target_agent_dlc"),
                    _safe_id(item.get("agent_id"), "target_agent_id"),
                ))
            if len(set(agents)) != len(agents):
                raise ModelPromotionApplyError("target_agent_duplicate")
            if not dlcs and not agents:
                raise ModelPromotionApplyError("cohort_target_empty")
            for dlc_id in dlcs:
                key = ("dlc", dlc_id, "")
                if key in claimed:
                    raise ModelPromotionApplyError("cohort_target_overlap")
                claimed.add(key)
            for dlc_id, agent_id in agents:
                key = ("agent", dlc_id, agent_id)
                if key in claimed:
                    raise ModelPromotionApplyError("cohort_target_overlap")
                claimed.add(key)
            targets[cohort] = {"dlcs": dlcs, "agents": agents}
        return {
            "promotion_id": promotion_id,
            "expected_generation": expected_generation,
            "targets": targets,
        }

    def _plan(
        self, promotion: Mapping[str, Any], mapping: Mapping[str, Any]
    ) -> tuple[dict[str, Any], GatewayConfig]:
        spec = self._validate_promotion(promotion)
        target_spec = self._validate_mapping(
            mapping,
            set(spec["cohort_task_policies"]),
            spec["policy_fingerprint"],
        )
        if self.known_targets is not None:
            for target in target_spec["targets"].values():
                for dlc_id in target["dlcs"]:
                    if dlc_id not in self.known_targets:
                        raise ModelPromotionApplyError("target_dlc_unknown")
                for dlc_id, agent_id in target["agents"]:
                    if agent_id not in self.known_targets.get(dlc_id, set()):
                        raise ModelPromotionApplyError("target_agent_unknown")
        generation, current = self.gateway.configuration_snapshot()
        if current.legacy_mode:
            raise ModelPromotionApplyError("legacy_configuration_not_supported")
        if generation != target_spec["expected_generation"]:
            raise ModelPromotionApplyError("gateway_generation_stale")
        source = Path(current.source_path)
        try:
            disk = GatewayConfig.from_file(str(source))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ModelPromotionApplyError("source_configuration_invalid") from exc
        if disk.to_dict() != current.to_dict():
            raise ModelPromotionApplyError("source_configuration_changed")

        candidate = copy.deepcopy(current)
        known_profiles = set(candidate.models)
        for policies in [
            spec["task_policies"],
            *(spec["cohort_task_policies"].values()),
        ]:
            for profiles in policies.values():
                if any(profile not in known_profiles for profile in profiles):
                    raise ModelPromotionApplyError("promoted_profile_unknown")

        changed_global = 0
        for task, profiles in spec["task_policies"].items():
            if candidate.task_policies.get(task) != profiles:
                changed_global += 1
            candidate.task_policies[task] = list(profiles)

        changed_dlc = 0
        changed_agent = 0
        dlc_targets: set[str] = set()
        agent_targets: set[tuple[str, str]] = set()
        for cohort, policies in spec["cohort_task_policies"].items():
            targets = target_spec["targets"][cohort]
            for dlc_id in targets["dlcs"]:
                dlc_targets.add(dlc_id)
                destination = candidate.dlc_task_policies.setdefault(dlc_id, {})
                for task, profiles in policies.items():
                    if destination.get(task) != profiles:
                        changed_dlc += 1
                    destination[task] = list(profiles)
            for dlc_id, agent_id in targets["agents"]:
                agent_targets.add((dlc_id, agent_id))
                destination = (
                    candidate.agent_task_policies
                    .setdefault(dlc_id, {})
                    .setdefault(agent_id, {})
                )
                for task, profiles in policies.items():
                    if destination.get(task) != profiles:
                        changed_agent += 1
                    destination[task] = list(profiles)
        candidate.validate()

        current_fingerprint = _fingerprint(current.to_dict())
        candidate_policy = {
            "task_policies": candidate.task_policies,
            "dlc_task_policies": candidate.dlc_task_policies,
            "agent_task_policies": candidate.agent_task_policies,
        }
        candidate_policy_fingerprint = _fingerprint(candidate_policy)
        plan_core = {
            "schema_version": "1.0",
            "promotion_id": target_spec["promotion_id"],
            "gate_id": spec["gate_id"],
            "environment_id": spec["environment_id"],
            "matrix_id": spec["matrix_id"],
            "matrix_fingerprint": spec["matrix_fingerprint"],
            "policy_fingerprint": spec["policy_fingerprint"],
            "current_config_fingerprint": current_fingerprint,
            "candidate_policy_fingerprint": candidate_policy_fingerprint,
            "current_generation": generation,
            "next_generation": generation + 1,
            "global_task_changes": changed_global,
            "dlc_task_changes": changed_dlc,
            "agent_task_changes": changed_agent,
            "dlc_target_count": len(dlc_targets),
            "agent_target_count": len(agent_targets),
        }
        plan = dict(plan_core)
        plan["plan_fingerprint"] = _fingerprint(plan_core)
        return plan, candidate

    def preview(
        self, promotion: Mapping[str, Any], mapping: Mapping[str, Any]
    ) -> dict[str, Any]:
        plan, _ = self._plan(promotion, mapping)
        return plan

    async def apply(
        self,
        promotion: Mapping[str, Any],
        mapping: Mapping[str, Any],
        *,
        expected_plan_fingerprint: str,
    ) -> dict[str, Any]:
        if not _SHA256.fullmatch(str(expected_plan_fingerprint or "")):
            raise ModelPromotionApplyError("plan_fingerprint_invalid")
        plan, candidate = self._plan(promotion, mapping)
        if plan["plan_fingerprint"] != expected_plan_fingerprint:
            raise ModelPromotionApplyError("plan_fingerprint_mismatch")
        target = Path(candidate.source_path)
        try:
            original = target.read_bytes()
            disk_payload = json.loads(original.decode("utf-8-sig"))
            disk_config = GatewayConfig.from_dict(
                disk_payload, source_path=str(target)
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ModelPromotionApplyError("source_configuration_unreadable") from exc
        if _fingerprint(disk_config.to_dict()) != plan["current_config_fingerprint"]:
            raise ModelPromotionApplyError("source_configuration_changed")
        _atomic_write_bytes(target, _config_bytes(candidate))
        try:
            report = await self.gateway.replace_config(
                candidate, expected_generation=plan["current_generation"]
            )
        except Exception as exc:
            try:
                _atomic_write_bytes(target, original)
            except OSError as restore_exc:
                raise ModelPromotionApplyError(
                    "configuration_reload_and_rollback_failed"
                ) from restore_exc
            if isinstance(exc, ModelGatewayError):
                raise ModelPromotionApplyError("configuration_reload_failed") from exc
            raise ModelPromotionApplyError("configuration_reload_failed") from exc
        return {
            **plan,
            "status": "applied",
            "generation": report["generation"],
        }
