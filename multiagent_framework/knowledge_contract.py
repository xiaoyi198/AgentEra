"""Contracts for the simulation's three-layer knowledge model.

The framework keeps authoritative world truth separate from Actor beliefs and
player-visible knowledge.  This module contains only deterministic, JSON-safe
normalization and projection helpers; mutation semantics live in
``knowledge_runtime``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import math
from typing import Any

SCHEMA_VERSION = "1.0"
KNOWLEDGE_LAYERS = frozenset({"world_truth", "actor_belief", "player_knowledge"})
HOLDER_LAYERS = frozenset({"actor_belief", "player_knowledge"})
KNOWLEDGE_STATUSES = frozenset({"active", "disputed", "retracted"})
INVESTIGATION_STATUSES = frozenset({"active", "paused", "completed", "cancelled"})
SECRECY_LEVELS = ("public", "restricted", "confidential", "secret", "top_secret")
SECRECY_RANK = {name: index for index, name in enumerate(SECRECY_LEVELS)}
MAX_RECORDS = 10_000
MAX_PROVENANCE = 32
MAX_EVIDENCE = 64
MAX_ID_LENGTH = 128


class KnowledgeContractError(ValueError):
    """Stable validation error used by effect and runtime adapters."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value: Any, code: str, *, required: bool = True, limit: int = MAX_ID_LENGTH) -> str:
    text = str(value or "").strip()
    if (required and not text) or len(text) > limit:
        raise KnowledgeContractError(code)
    return text


def _finite(value: Any, code: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise KnowledgeContractError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise KnowledgeContractError(code) from exc
    if not math.isfinite(number):
        raise KnowledgeContractError(code)
    if minimum is not None and number < minimum:
        raise KnowledgeContractError(code)
    if maximum is not None and number > maximum:
        raise KnowledgeContractError(code)
    return number


def _safe_json(value: Any, code: str, *, depth: int = 0) -> Any:
    if depth > 8:
        raise KnowledgeContractError(code)
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > 12_000:
            raise KnowledgeContractError(code)
        return copy.deepcopy(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _finite(value, code)
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise KnowledgeContractError(code)
        return {
            _text(key, code, limit=128): _safe_json(item, code, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 128:
            raise KnowledgeContractError(code)
        return [_safe_json(item, code, depth=depth + 1) for item in value]
    raise KnowledgeContractError(code)


def empty_knowledge_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "world_truths": {},
        "actor_beliefs": {},
        "player_knowledge": {},
        "investigations": {},
        "investigation_order": [],
    }


def knowledge_key(
    layer: str, subject_entity_id: str, knowledge_id: str, holder_id: str = "",
) -> str:
    if layer not in KNOWLEDGE_LAYERS:
        raise KnowledgeContractError("knowledge_layer_invalid")
    subject = _text(subject_entity_id, "knowledge_subject_invalid", required=False)
    name = _text(knowledge_id, "knowledge_id_invalid")
    holder = _text(
        holder_id,
        "knowledge_holder_invalid",
        required=layer in HOLDER_LAYERS,
    )
    return f"{holder}:{subject}:{name}" if layer in HOLDER_LAYERS else f"{subject}:{name}"


def bucket_for_layer(layer: str) -> str:
    return {
        "world_truth": "world_truths",
        "actor_belief": "actor_beliefs",
        "player_knowledge": "player_knowledge",
    }.get(layer, "")


def normalize_provenance(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for raw in list(value)[-MAX_PROVENANCE:]:
        if not isinstance(raw, Mapping):
            continue
        layer = str(raw.get("layer", "")).strip()
        if layer not in KNOWLEDGE_LAYERS:
            continue
        item = {
            "layer": layer,
            "holder_id": str(raw.get("holder_id", ""))[:MAX_ID_LENGTH],
            "subject_entity_id": str(raw.get("subject_entity_id", ""))[:MAX_ID_LENGTH],
            "knowledge_id": str(raw.get("knowledge_id", ""))[:MAX_ID_LENGTH],
            "source": str(raw.get("source", ""))[:512],
            "observed_at": float(raw.get("observed_at", 0.0) or 0.0),
            "confidence": max(0.0, min(1.0, float(raw.get("confidence", 1.0) or 0.0))),
            "command_id": str(raw.get("command_id", ""))[:256],
        }
        result.append(item)
    return result


def normalize_knowledge_record(value: Any, *, layer: str, key_hint: str = "") -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or layer not in KNOWLEDGE_LAYERS:
        return None
    try:
        subject = _text(value.get("subject_entity_id", value.get("entity_id", "")), "knowledge_subject_invalid", required=False)
        name = _text(value.get("knowledge_id", ""), "knowledge_id_invalid")
        holder = _text(value.get("holder_id", ""), "knowledge_holder_invalid", required=layer in HOLDER_LAYERS)
        expected_key = knowledge_key(layer, subject, name, holder)
        if key_hint and expected_key != str(key_hint):
            return None
        confidence = _finite(value.get("confidence", 1.0), "knowledge_confidence_invalid", minimum=0.0, maximum=1.0)
        observed_at = _finite(value.get("observed_at", value.get("revealed_at", value.get("updated_at", 0.0))), "knowledge_time_invalid", minimum=0.0)
        stale_after_seconds = value.get("stale_after_seconds")
        if stale_after_seconds is not None:
            stale_after_seconds = _finite(stale_after_seconds, "knowledge_freshness_invalid", minimum=0.0)
        expires_at = value.get("expires_at")
        if expires_at is not None:
            expires_at = _finite(expires_at, "knowledge_freshness_invalid", minimum=observed_at)
        secrecy = str(value.get("secrecy_level", "public") or "public")
        if secrecy not in SECRECY_RANK:
            return None
        status = str(value.get("status", "active") or "active")
        if status not in KNOWLEDGE_STATUSES:
            return None
        visibility = value.get("visibility_scopes", [])
        if not isinstance(visibility, Sequence) or isinstance(visibility, (str, bytes, bytearray)):
            visibility = []
        distortion = value.get("distortion", {})
        if not isinstance(distortion, Mapping):
            distortion = {}
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        return {
            "layer": layer,
            "holder_id": holder,
            "subject_entity_id": subject,
            "entity_id": subject,
            "knowledge_id": name,
            "value": _safe_json(value.get("value"), "knowledge_value_invalid"),
            "source": str(value.get("source", ""))[:512],
            "provenance": normalize_provenance(value.get("provenance", [])),
            "confidence": confidence,
            "observed_at": observed_at,
            "stale_after_seconds": stale_after_seconds,
            "expires_at": expires_at,
            "secrecy_level": secrecy,
            "visibility_scopes": [str(item)[:MAX_ID_LENGTH] for item in list(visibility)[:64] if str(item)],
            "distortion": _safe_json(dict(distortion), "knowledge_distortion_invalid"),
            "status": status,
            "metadata": _safe_json(dict(metadata), "knowledge_metadata_invalid"),
            "reason": str(value.get("reason", ""))[:2000],
            "version": max(1, int(value.get("version", 1) or 1)),
            "updated_at": _finite(value.get("updated_at", observed_at), "knowledge_time_invalid", minimum=0.0),
            "command_id": str(value.get("command_id", ""))[:256],
        }
    except (KnowledgeContractError, TypeError, ValueError, OverflowError):
        return None


def normalize_investigation(value: Any, *, key_hint: str = "") -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        investigation_id = _text(value.get("investigation_id", ""), "investigation_id_invalid")
        if key_hint and investigation_id != str(key_hint):
            return None
        investigator_layer = str(value.get("investigator_layer", "player_knowledge") or "player_knowledge")
        if investigator_layer not in HOLDER_LAYERS:
            return None
        investigator_id = _text(value.get("investigator_id", ""), "investigator_id_invalid")
        status = str(value.get("status", "active") or "active")
        if status not in INVESTIGATION_STATUSES:
            return None
        progress = _finite(value.get("progress", 0.0), "investigation_progress_invalid", minimum=0.0)
        required = _finite(value.get("required_progress", 1.0), "investigation_progress_invalid", minimum=0.000001)
        evidence = value.get("evidence", [])
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
            evidence = []
        result_policy = value.get("result_policy", {})
        if not isinstance(result_policy, Mapping):
            result_policy = {}
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        return {
            "investigation_id": investigation_id,
            "subject_entity_id": _text(value.get("subject_entity_id", value.get("entity_id", "")), "knowledge_subject_invalid", required=False),
            "knowledge_id": _text(value.get("knowledge_id", ""), "knowledge_id_invalid"),
            "investigator_layer": investigator_layer,
            "investigator_id": investigator_id,
            "status": status,
            "progress": min(progress, required),
            "required_progress": required,
            "evidence": [_safe_json(item, "investigation_evidence_invalid") for item in list(evidence)[-MAX_EVIDENCE:]],
            "result_policy": _safe_json(dict(result_policy), "investigation_result_invalid"),
            "metadata": _safe_json(dict(metadata), "investigation_metadata_invalid"),
            "reason": str(value.get("reason", ""))[:2000],
            "started_at": _finite(value.get("started_at", value.get("updated_at", 0.0)), "knowledge_time_invalid", minimum=0.0),
            "updated_at": _finite(value.get("updated_at", 0.0), "knowledge_time_invalid", minimum=0.0),
            "completed_at": None if value.get("completed_at") is None else _finite(value.get("completed_at"), "knowledge_time_invalid", minimum=0.0),
            "version": max(1, int(value.get("version", 1) or 1)),
            "command_id": str(value.get("command_id", ""))[:256],
        }
    except (KnowledgeContractError, TypeError, ValueError, OverflowError):
        return None


def normalize_knowledge_state(value: Any, *, legacy_knowledge: Any = None) -> dict[str, Any]:
    state = empty_knowledge_state()
    if isinstance(value, Mapping):
        for layer, bucket in (
            ("world_truth", "world_truths"),
            ("actor_belief", "actor_beliefs"),
            ("player_knowledge", "player_knowledge"),
        ):
            raw_bucket = value.get(bucket, {})
            if isinstance(raw_bucket, Mapping):
                for key, raw in list(raw_bucket.items())[-MAX_RECORDS:]:
                    normalized = normalize_knowledge_record(raw, layer=layer, key_hint=str(key))
                    if normalized is not None:
                        state[bucket][str(key)] = normalized
        investigations = value.get("investigations", {})
        if isinstance(investigations, Mapping):
            for key, raw in list(investigations.items())[-MAX_RECORDS:]:
                normalized = normalize_investigation(raw, key_hint=str(key))
                if normalized is not None:
                    state["investigations"][str(key)] = normalized
        order = value.get("investigation_order", [])
        if isinstance(order, list):
            state["investigation_order"] = [
                str(item) for item in order[-MAX_RECORDS:]
                if str(item) in state["investigations"]
            ]
    if isinstance(legacy_knowledge, Mapping):
        for raw in list(legacy_knowledge.values())[-MAX_RECORDS:]:
            if not isinstance(raw, Mapping):
                continue
            migrated = dict(raw)
            migrated.setdefault("holder_id", "player")
            migrated.setdefault("layer", "player_knowledge")
            migrated.setdefault("subject_entity_id", migrated.get("entity_id", ""))
            normalized = normalize_knowledge_record(migrated, layer="player_knowledge")
            if normalized is not None:
                key = knowledge_key(
                    "player_knowledge", normalized["subject_entity_id"],
                    normalized["knowledge_id"], normalized["holder_id"],
                )
                state["player_knowledge"].setdefault(key, normalized)
    return state


def freshness_status(record: Mapping[str, Any], sim_time: float) -> str:
    now = max(0.0, float(sim_time))
    expires_at = record.get("expires_at")
    if expires_at is not None and now >= float(expires_at):
        return "expired"
    stale_after = record.get("stale_after_seconds")
    if stale_after is None:
        return "fresh"
    return "fresh" if now <= float(record.get("observed_at", 0.0)) + float(stale_after) else "stale"


def public_knowledge_record(record: Mapping[str, Any], *, sim_time: float) -> dict[str, Any]:
    """Return one holder-authorized projection without internal command receipts."""
    return {
        "layer": str(record.get("layer", "")),
        "holder_id": str(record.get("holder_id", "")),
        "subject_entity_id": str(record.get("subject_entity_id", "")),
        "entity_id": str(record.get("subject_entity_id", "")),
        "knowledge_id": str(record.get("knowledge_id", "")),
        "value": copy.deepcopy(record.get("value")),
        "source": str(record.get("source", "")),
        "provenance": copy.deepcopy(list(record.get("provenance", []))),
        "confidence": float(record.get("confidence", 0.0)),
        "observed_at": float(record.get("observed_at", 0.0)),
        "freshness": freshness_status(record, sim_time),
        "expires_at": record.get("expires_at"),
        "secrecy_level": str(record.get("secrecy_level", "public")),
        "distortion": copy.deepcopy(dict(record.get("distortion", {}))),
        "status": str(record.get("status", "active")),
        "metadata": copy.deepcopy(dict(record.get("metadata", {}))),
        "version": int(record.get("version", 1)),
        "updated_at": float(record.get("updated_at", 0.0)),
    }


def public_investigation(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(record.get(key))
        for key in (
            "investigation_id", "subject_entity_id", "knowledge_id",
            "investigator_layer", "investigator_id", "status", "progress",
            "required_progress", "evidence", "metadata", "reason", "started_at",
            "updated_at", "completed_at", "version",
        )
    }
