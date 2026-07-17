"""DLC-authored generic Issue / Case / DecisionRequest lifecycle contracts."""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
import re
import threading
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence

from .gameplay_contract import GameplayContract

DAY = 86400.0
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
URGENCIES = {"low", "normal", "high", "critical"}
SOURCE_KINDS = {"agent", "entity", "event", "system", "player"}
ISSUE_STATUSES = {"queued", "open", "monitoring", "overdue", "resolved", "closed", "cancelled"}
CASE_STATUSES = {"queued", "open", "in_review", "monitoring", "overdue", "resolved", "closed", "cancelled"}
FINAL_ISSUE = {"resolved", "closed", "cancelled"}
FINAL_CASE = {"resolved", "closed", "cancelled"}
ACTIVE_ISSUE_STATUSES = {"open", "monitoring", "overdue"}
ACTIVE_CASE_STATUSES = {"open", "in_review", "monitoring", "overdue"}
MAX_RECORDS = 5000


class IssueContractError(ValueError):
    def __init__(self, code: str, message: str | None = None, path: str = "", details: dict[str, Any] | None = None):
        super().__init__(message or code)
        self.code = code
        self.path = path
        self.details = details or {}


@dataclass(frozen=True)
class IssueValidationIssue:
    code: str
    message: str
    path: str = ""


def _map(value: Any, code: str, path: str = "") -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IssueContractError(code, path=path)
    return value


def _list(value: Any, code: str, path: str = "", limit: int = 256) -> Sequence[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise IssueContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str = "", required: bool = True, limit: int = 12000) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise IssueContractError(code, path=path)
    result = value.strip()
    if required and not result:
        raise IssueContractError(code, path=path)
    if len(result) > limit:
        raise IssueContractError(code, path=path)
    return result


def _id(value: Any, code: str, path: str = "") -> str:
    result = _text(value, code, path, limit=128)
    if not ID_PATTERN.fullmatch(result):
        raise IssueContractError(code, path=path)
    return result


def _days(value: Any, code: str, path: str = "") -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IssueContractError(code, path=path)
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise IssueContractError(code, path=path)
    return result


def _strings(value: Any, code: str, path: str = "", allowed: set[str] | None = None) -> list[str]:
    result: list[str] = []
    for index, item in enumerate(_list(value, code, path)):
        text = _text(item, code, f"{path}[{index}]", limit=128)
        if allowed is not None and text not in allowed:
            raise IssueContractError(code, path=f"{path}[{index}]")
        if text not in result:
            result.append(text)
    return result


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_evidence_schema(value: Any, path: str) -> list[dict[str, Any]]:
    result, seen = [], set()
    for index, raw in enumerate(_list(value, "issue.evidence.invalid", path)):
        item_path = f"{path}[{index}]"
        item = _map(raw, "issue.evidence.invalid", item_path)
        kind = _id(item.get("kind"), "issue.evidence.missing_kind", item_path + ".kind")
        if kind in seen:
            raise IssueContractError("issue.evidence.duplicate", path=item_path + ".kind")
        seen.add(kind)
        result.append({
            "kind": kind,
            "display_name": _text(item.get("display_name", kind), "issue.evidence.invalid_name", item_path + ".display_name", limit=256),
            "description": _text(item.get("description", ""), "issue.evidence.invalid_description", item_path + ".description", required=False),
            "required": bool(item.get("required", False)),
        })
    return result


def _parse_option(raw: Any, path: str) -> dict[str, Any]:
    item = _map(raw, "issue.option.invalid", path)
    parameters = item.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise IssueContractError("issue.option.invalid_parameters", path=path + ".parameters")
    resolution = _map(item.get("resolution", {}), "issue.option.invalid_resolution", path + ".resolution")
    issue_status = str(resolution.get("issue_status", "resolved"))
    case_status = str(resolution.get("case_status", "resolved"))
    if issue_status not in ISSUE_STATUSES:
        raise IssueContractError("issue.option.invalid_issue_status", path=path + ".resolution.issue_status")
    if case_status not in CASE_STATUSES:
        raise IssueContractError("issue.option.invalid_case_status", path=path + ".resolution.case_status")
    metadata = item.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise IssueContractError("issue.option.invalid_metadata", path=path + ".metadata")
    return {
        "option_id": _id(item.get("option_id"), "issue.option.missing_id", path + ".option_id"),
        "display_name": _text(item.get("display_name", item.get("option_id", "")), "issue.option.invalid_name", path + ".display_name", limit=256),
        "description": _text(item.get("description", ""), "issue.option.invalid_description", path + ".description", required=False),
        "action_id": _text(item.get("action_id", ""), "issue.option.invalid_action", path + ".action_id", required=False, limit=128),
        "control_object_id": _text(item.get("control_object_id", ""), "issue.option.invalid_control", path + ".control_object_id", required=False, limit=128),
        "target_id": _text(item.get("target_id", ""), "issue.option.invalid_target", path + ".target_id", required=False, limit=128),
        "parameters": copy.deepcopy(dict(parameters)),
        "resolution": {
            "issue_status": issue_status,
            "case_status": case_status,
            "summary": _text(resolution.get("summary", ""), "issue.option.invalid_summary", path + ".resolution.summary", required=False),
        },
        "metadata": copy.deepcopy(dict(metadata)),
    }


def _parse_request(raw: Any, path: str) -> dict[str, Any]:
    item = _map(raw, "issue.decision.invalid", path)
    options, seen = [], set()
    for index, raw_option in enumerate(_list(item.get("options", []), "issue.decision.invalid_options", path + ".options")):
        option = _parse_option(raw_option, f"{path}.options[{index}]")
        if option["option_id"] in seen:
            raise IssueContractError("issue.option.duplicate", path=f"{path}.options[{index}].option_id")
        seen.add(option["option_id"])
        options.append(option)
    if not options:
        raise IssueContractError("issue.decision.empty_options", path=path + ".options")
    metadata = item.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise IssueContractError("issue.decision.invalid_metadata", path=path + ".metadata")
    return {
        "request_id": _id(item.get("request_id"), "issue.decision.missing_id", path + ".request_id"),
        "title": _text(item.get("title", item.get("request_id", "")), "issue.decision.invalid_title", path + ".title", limit=512),
        "prompt": _text(item.get("prompt", ""), "issue.decision.invalid_prompt", path + ".prompt", required=False),
        "required": bool(item.get("required", True)),
        "deadline_days": _days(item.get("deadline_days"), "issue.decision.invalid_deadline", path + ".deadline_days"),
        "options": options,
        "metadata": copy.deepcopy(dict(metadata)),
    }


def parse_issue_definition(raw: Any, path: str = "issue_type") -> dict[str, Any]:
    item = _map(raw, "issue.invalid", path)
    source = _map(item.get("source", {}), "issue.source.invalid", path + ".source")
    match = _map(item.get("match", {}), "issue.match.invalid", path + ".match")
    requests, seen = [], set()
    for index, raw_request in enumerate(_list(item.get("decision_requests", []), "issue.invalid_decisions", path + ".decision_requests")):
        request = _parse_request(raw_request, f"{path}.decision_requests[{index}]")
        if request["request_id"] in seen:
            raise IssueContractError("issue.decision.duplicate", path=f"{path}.decision_requests[{index}].request_id")
        seen.add(request["request_id"])
        requests.append(request)
    urgency = str(item.get("default_urgency", "normal"))
    if urgency not in URGENCIES:
        raise IssueContractError("issue.invalid_urgency", path=path + ".default_urgency")
    allowed_kinds = _strings(source.get("allowed_kinds", sorted(SOURCE_KINDS)), "issue.source.invalid_kinds", path + ".source.allowed_kinds", SOURCE_KINDS)
    if not allowed_kinds:
        raise IssueContractError("issue.source.empty_kinds", path=path + ".source.allowed_kinds")
    metadata = item.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise IssueContractError("issue.invalid_metadata", path=path + ".metadata")
    issue_type_id = _id(item.get("issue_type_id"), "issue.missing_id", path + ".issue_type_id")
    return {
        "issue_type_id": issue_type_id,
        "display_name": _text(item.get("display_name", issue_type_id), "issue.invalid_name", path + ".display_name", limit=512),
        "description": _text(item.get("description", ""), "issue.invalid_description", path + ".description", required=False),
        "enabled": bool(item.get("enabled", True)),
        "category": _text(item.get("category", "general"), "issue.invalid_category", path + ".category", limit=128),
        "default_urgency": urgency,
        "default_deadline_days": _days(item.get("default_deadline_days"), "issue.invalid_deadline", path + ".default_deadline_days"),
        "source": {"allowed_kinds": allowed_kinds, "allowed_ids": _strings(source.get("allowed_ids", []), "issue.source.invalid_ids", path + ".source.allowed_ids")},
        "match": {
            "payload_types": _strings(match.get("payload_types", []), "issue.match.invalid_payload_types", path + ".match.payload_types"),
            "source_ids": _strings(match.get("source_ids", []), "issue.match.invalid_source_ids", path + ".match.source_ids"),
            "default_for_player_reports": bool(match.get("default_for_player_reports", False)),
        },
        "evidence": _parse_evidence_schema(item.get("evidence", []), path + ".evidence"),
        "decision_requests": requests,
        "metadata": copy.deepcopy(dict(metadata)),
    }


def normalize_issue_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    return [parse_issue_definition(item, f"issue_types[{index}]") for index, item in enumerate(_list(value, "issues.invalid", "issue_types"))]


def validate_issue_definitions(value: Any, *, action_ids: Iterable[str] = (), entity_ids: Iterable[str] = (), agent_ids: Iterable[str] = (), gameplay: GameplayContract | None = None) -> list[IssueValidationIssue]:
    try:
        definitions = normalize_issue_document(value)
    except IssueContractError as exc:
        return [IssueValidationIssue(exc.code, str(exc), exc.path)]
    issues: list[IssueValidationIssue] = []
    actions, entities, agents = set(action_ids), set(entity_ids), set(agent_ids)
    controls = {item.id for item in (gameplay.player.controlled_objects if gameplay and gameplay.player else ())}
    actor_id = gameplay.player.actor_id if gameplay and gameplay.player else ""
    seen, defaults = set(), 0
    for index, definition in enumerate(definitions):
        path = f"issue_types[{index}]"
        if definition["issue_type_id"] in seen:
            issues.append(IssueValidationIssue("issues.duplicate", f"Duplicate issue_type_id: {definition['issue_type_id']}", path + ".issue_type_id"))
        seen.add(definition["issue_type_id"])
        defaults += int(definition["match"]["default_for_player_reports"])
        kinds = set(definition["source"]["allowed_kinds"])
        for source_id in definition["source"]["allowed_ids"]:
            valid = (("agent" in kinds and source_id in agents) or ("entity" in kinds and source_id in entities) or ("player" in kinds and source_id in controls | {actor_id}) or bool(kinds & {"event", "system"}))
            if not valid:
                issues.append(IssueValidationIssue("issue.source.unknown_id", f"Unknown source id: {source_id}", path + ".source.allowed_ids"))
        for rindex, request in enumerate(definition["decision_requests"]):
            for oindex, option in enumerate(request["options"]):
                opath = f"{path}.decision_requests[{rindex}].options[{oindex}]"
                if option["action_id"] and option["action_id"] not in actions:
                    issues.append(IssueValidationIssue("issue.option.unknown_action", f"Unknown action id: {option['action_id']}", opath + ".action_id"))
                if option["control_object_id"] and option["control_object_id"] not in controls:
                    issues.append(IssueValidationIssue("issue.option.unknown_control", f"Unknown controlled object: {option['control_object_id']}", opath + ".control_object_id"))
                target = option["target_id"]
                if target and not target.startswith("$") and target not in entities | agents:
                    issues.append(IssueValidationIssue("issue.option.unknown_target", f"Unknown target: {target}", opath + ".target_id"))
    if defaults > 1:
        issues.append(IssueValidationIssue("issues.multiple_defaults", "Only one issue type may be the default for player reports", "issue_types"))
    return issues


def _runtime_evidence(raw: Any, sim_time: float, index: int) -> dict[str, Any]:
    item = _map(raw, "issue.evidence.invalid", "evidence")
    confidence = item.get("confidence", 1.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)):
        raise IssueContractError("issue.evidence.invalid_confidence")
    details = item.get("details", {})
    if not isinstance(details, Mapping):
        raise IssueContractError("issue.evidence.invalid_details")
    return {
        "evidence_id": _text(item.get("evidence_id", ""), "issue.evidence.invalid_id", required=False, limit=128) or f"evidence_{uuid.uuid4().hex[:16]}",
        "kind": _id(item.get("kind", "report"), "issue.evidence.missing_kind"),
        "source_id": _text(item.get("source_id", ""), "issue.evidence.invalid_source", required=False, limit=128),
        "summary": _text(item.get("summary", ""), "issue.evidence.invalid_summary", required=False),
        "details": copy.deepcopy(dict(details)),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "observed_at": float(item.get("observed_at", sim_time)),
        "added_at": sim_time,
        "index": index,
    }


def _resolve(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        if value in context:
            return copy.deepcopy(context[value])
        if value.startswith("$payload."):
            current: Any = context.get("$payload", {})
            for part in value[9:].split("."):
                if not isinstance(current, Mapping) or part not in current:
                    raise IssueContractError("issue.template.missing_reference", details={"reference": value})
                current = current[part]
            return copy.deepcopy(current)
    if isinstance(value, Mapping):
        return {str(key): _resolve(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, context) for item in value]
    return copy.deepcopy(value)


class IssueManager:
    """Generation-published definitions plus durable issue lifecycle state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._definitions: dict[str, dict[str, Any]] = {}
        self._generation = 0
        self._fingerprint = _hash([])
        self._action_ids: set[str] = set()
        self._gameplay = GameplayContract()
        self._issues: dict[str, dict[str, Any]] = {}
        self._cases: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._issue_order: deque[str] = deque()
        self._case_order: deque[str] = deque()
        self._request_order: deque[str] = deque()
        self._external_receipts: dict[str, dict[str, str]] = {}
        self._decision_receipts: dict[str, dict[str, Any]] = {}
        self._decision_order: deque[str] = deque()
        self._rhythm_controller: Any = None

    def set_rhythm_controller(self, controller: Any) -> None:
        """Attach the optional pacing controller used for issue admission."""
        with self._lock:
            self._rhythm_controller = controller

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def configure(self, definitions: Iterable[Mapping[str, Any]], *, generation: int = 0, action_ids: Iterable[str] = (), gameplay: GameplayContract | None = None) -> None:
        parsed = normalize_issue_document(list(definitions))
        mapping = {item["issue_type_id"]: item for item in parsed}
        if len(mapping) != len(parsed):
            raise IssueContractError("issues.duplicate")
        with self._lock:
            self._definitions = mapping
            self._generation = max(0, int(generation))
            self._fingerprint = _hash(parsed)
            self._action_ids = set(action_ids)
            self._gameplay = gameplay or GameplayContract()

    def public_state(self, *, sim_time: float = 0.0) -> dict[str, Any]:
        with self._lock:
            self._refresh_locked(sim_time)
            return {
                "configured": bool(self._definitions),
                "generation": self._generation,
                "fingerprint": self._fingerprint,
                "issue_types": copy.deepcopy(list(self._definitions.values())),
                "counts": {
                    "issues": len(self._issues),
                    "open_issues": sum(item["status"] in ACTIVE_ISSUE_STATUSES for item in self._issues.values()),
                    "queued_issues": sum(item["status"] == "queued" for item in self._issues.values()),
                    "cases": len(self._cases),
                    "open_cases": sum(item["status"] in ACTIVE_CASE_STATUSES for item in self._cases.values()),
                    "queued_cases": sum(item["status"] == "queued" for item in self._cases.values()),
                    "pending_decision_requests": sum(item["status"] == "pending" for item in self._requests.values()),
                },
            }

    @staticmethod
    def _fallback_definition() -> dict[str, Any]:
        return parse_issue_definition({
            "issue_type_id": "framework.agent_report",
            "display_name": "Agent report",
            "category": "report",
            "source": {"allowed_kinds": sorted(SOURCE_KINDS)},
            "decision_requests": [{
                "request_id": "review",
                "title": "Review report",
                "prompt": "Choose how this report should be handled.",
                "options": [
                    {"option_id": "acknowledge", "display_name": "Acknowledge", "resolution": {"issue_status": "resolved", "case_status": "resolved"}},
                    {"option_id": "monitor", "display_name": "Keep monitoring", "resolution": {"issue_status": "monitoring", "case_status": "monitoring"}},
                ],
            }],
        })

    def _definition(self, payload: Mapping[str, Any], source_id: str) -> dict[str, Any]:
        explicit = str(payload.get("issue_type_id", "")).strip()
        if explicit:
            definition = self._definitions.get(explicit)
            if definition is None or not definition["enabled"]:
                raise IssueContractError("issue.type_not_found", details={"issue_type_id": explicit})
            return definition
        payload_type = str(payload.get("type", payload.get("event_type", ""))).strip()
        for definition in self._definitions.values():
            match = definition["match"]
            if definition["enabled"] and payload_type in match["payload_types"] and (not match["source_ids"] or source_id in match["source_ids"]):
                return definition
        for definition in self._definitions.values():
            if definition["enabled"] and definition["match"]["default_for_player_reports"]:
                return definition
        return self._fallback_definition()

    def _check_generation(self, expected: Any) -> None:
        if expected is None:
            return
        try:
            value = int(expected)
        except (TypeError, ValueError) as exc:
            raise IssueContractError("issue.generation.invalid") from exc
        if value != self._generation:
            raise IssueContractError("issue.generation_conflict", details={"expected_generation": value, "actual_generation": self._generation})

    def open_issue(self, payload: Mapping[str, Any], *, sim_time: float = 0.0) -> dict[str, Any]:
        with self._lock:
            return self._open_locked(payload, sim_time)

    def _open_locked(self, payload: Mapping[str, Any], sim_time: float) -> dict[str, Any]:
        data = _map(payload, "issue.open.invalid", "payload")
        self._check_generation(data.get("expected_generation"))
        external_id = _text(data.get("external_id", ""), "issue.invalid_external_id", required=False, limit=128)
        request_hash = _hash({key: data[key] for key in sorted(data) if key != "expected_generation"})
        if external_id and external_id in self._external_receipts:
            receipt = self._external_receipts[external_id]
            if receipt["hash"] != request_hash:
                raise IssueContractError("issue.external_id_conflict")
            issue = self._issues[receipt["issue_id"]]
            case = self._cases[issue["case_id"]]
            requests = [self._requests[item] for item in issue["decision_request_ids"]]
            return {"ok": True, "replayed": True, "issue": copy.deepcopy(issue), "case": copy.deepcopy(case), "decision_requests": copy.deepcopy(requests)}

        source_raw = data.get("source", {})
        if isinstance(source_raw, str):
            source_raw = {"kind": "agent", "id": source_raw}
        source = _map(source_raw, "issue.source.invalid", "source")
        source_id = _text(source.get("id", data.get("source_id", data.get("from", ""))), "issue.source.invalid_id", required=False, limit=128)
        source_kind = str(source.get("kind", "agent" if source_id else "system"))
        if source_kind not in SOURCE_KINDS:
            raise IssueContractError("issue.source.invalid_kind")
        definition = self._definition(data, source_id)
        allowed = definition["source"]
        if source_kind not in allowed["allowed_kinds"]:
            raise IssueContractError("issue.source.forbidden_kind")
        if allowed["allowed_ids"] and source_id not in allowed["allowed_ids"]:
            raise IssueContractError("issue.source.forbidden_id")
        urgency = str(data.get("urgency", definition["default_urgency"]))
        if urgency not in URGENCIES:
            raise IssueContractError("issue.invalid_urgency")
        deadline_at_raw = data.get("deadline_at")
        if deadline_at_raw is not None:
            if isinstance(deadline_at_raw, bool) or not isinstance(deadline_at_raw, (int, float)) or not math.isfinite(float(deadline_at_raw)):
                raise IssueContractError("issue.invalid_deadline")
            deadline_at = float(deadline_at_raw)
        else:
            deadline_days = _days(data.get("deadline_days", definition["default_deadline_days"]), "issue.invalid_deadline")
            deadline_at = sim_time + deadline_days * DAY if deadline_days is not None else None
        evidence = [_runtime_evidence(item, sim_time, index) for index, item in enumerate(_list(data.get("evidence", []), "issue.evidence.invalid"))]
        present = {item["kind"] for item in evidence}
        missing = [item["kind"] for item in definition["evidence"] if item["required"] and item["kind"] not in present]
        if missing:
            raise IssueContractError("issue.evidence.required", details={"missing_kinds": missing})

        issue_id = _text(data.get("issue_id", ""), "issue.invalid_id", required=False, limit=128) or f"issue_{uuid.uuid4().hex[:16]}"
        if issue_id in self._issues:
            raise IssueContractError("issue.id_conflict")
        rhythm_projection: dict[str, Any] = {}
        initial_status = "open"
        controller = self._rhythm_controller
        if controller is not None:
            prepare = getattr(controller, "prepare_issue", None)
            if callable(prepare):
                admission = prepare(
                    issue_type_id=definition["issue_type_id"],
                    urgency=urgency,
                    event_type=str(data.get("event_type", data.get("type", ""))).strip(),
                    payload=data,
                    sim_time=sim_time,
                    issue_manager=self,
                )
                if isinstance(admission, Mapping) and admission.get("managed"):
                    if admission.get("reject"):
                        raise IssueContractError(
                            "issue.rhythm_queue_full",
                            details={"reason": admission.get("reason", "queue_limit")},
                        )
                    initial_status = "queued" if admission.get("queued") else "open"
                    rhythm_projection = {
                        "managed": True,
                        "rule_id": str(admission.get("rule_id", "")),
                        "attention_cost": float(admission.get("attention_cost", 0.0) or 0.0),
                        "priority": int(admission.get("priority", 0) or 0),
                        "level": int(admission.get("level", 0) or 0),
                        "queue_reason": str(admission.get("reason", "")) if initial_status == "queued" else "",
                    }
        case_id = _text(data.get("case_id", ""), "case.invalid_id", required=False, limit=128) or f"case_{uuid.uuid4().hex[:16]}"
        case = self._cases.get(case_id)
        if case and case["status"] in FINAL_CASE:
            raise IssueContractError("case.closed")
        metadata = data.get("metadata", {})
        details = data.get("details", {})
        if not isinstance(metadata, Mapping) or not isinstance(details, Mapping):
            raise IssueContractError("issue.invalid_metadata")
        summary = _text(data.get("summary", data.get("message", data.get("content", data.get("description", "")))), "issue.invalid_summary", required=False)
        issue = {
            "issue_id": issue_id,
            "issue_type_id": definition["issue_type_id"],
            "case_id": case_id,
            "title": _text(data.get("title", definition["display_name"]), "issue.invalid_title", limit=512),
            "summary": summary,
            "details": copy.deepcopy(dict(details)),
            "category": definition["category"],
            "source": {
                "kind": source_kind,
                "id": source_id,
                "display_name": _text(source.get("display_name", source_id), "issue.source.invalid_name", required=False, limit=256),
            },
            "evidence": evidence,
            "urgency": urgency,
            "status": initial_status,
            "rhythm": rhythm_projection,
            "created_at": sim_time,
            "updated_at": sim_time,
            "deadline_at": deadline_at,
            "decision_request_ids": [],
            "outcome": {},
            "metadata": copy.deepcopy(dict(metadata)),
            "external_id": external_id,
            "generation": self._generation,
        }
        context = {"$source": source_id, "$source_id": source_id, "$issue": issue_id, "$case": case_id, "$payload": copy.deepcopy(dict(data))}
        requests: list[dict[str, Any]] = []
        for template in definition["decision_requests"]:
            if data.get("deterministic_ids"):
                digest = hashlib.sha256(
                    f"{issue_id}\0{template['request_id']}".encode("utf-8")
                ).hexdigest()[:16]
                request_id = f"decision_{digest}"
            else:
                request_id = f"decision_{uuid.uuid4().hex[:16]}"
            request_deadline = sim_time + template["deadline_days"] * DAY if template["deadline_days"] is not None else deadline_at
            request = {
                "decision_request_id": request_id,
                "template_request_id": template["request_id"],
                "issue_id": issue_id,
                "case_id": case_id,
                "title": template["title"],
                "prompt": template["prompt"],
                "required": template["required"],
                "urgency": urgency,
                "status": "queued" if initial_status == "queued" else "pending",
                "created_at": sim_time,
                "updated_at": sim_time,
                "deadline_at": request_deadline,
                "options": [_resolve(option, context) for option in template["options"]],
                "selected_option_id": "",
                "decided_at": None,
                "outcome": {},
                "generation": self._generation,
                "metadata": copy.deepcopy(template["metadata"]),
            }
            requests.append(request)
            issue["decision_request_ids"].append(request_id)
            self._requests[request_id] = request
            self._request_order.append(request_id)

        if case is None:
            case_metadata = copy.deepcopy(dict(metadata))
            case = {
                "case_id": case_id,
                "title": _text(data.get("case_title", issue["title"]), "case.invalid_title", limit=512),
                "summary": _text(data.get("case_summary", summary), "case.invalid_summary", required=False),
                "status": "queued" if initial_status == "queued" else "open",
                "urgency": urgency,
                "created_at": sim_time,
                "updated_at": sim_time,
                "deadline_at": deadline_at,
                "issue_ids": [issue_id],
                "decision_request_ids": list(issue["decision_request_ids"]),
                "outcome": {},
                "generation": self._generation,
                "metadata": case_metadata,
            }
            self._cases[case_id] = case
            self._case_order.append(case_id)
        else:
            case["issue_ids"].append(issue_id)
            case["decision_request_ids"].extend(issue["decision_request_ids"])
            case["updated_at"] = sim_time
            if case["status"] == "queued" and initial_status != "queued":
                case["status"] = "open"
            if deadline_at is not None and (case["deadline_at"] is None or deadline_at < case["deadline_at"]):
                case["deadline_at"] = deadline_at
        self._issues[issue_id] = issue
        self._issue_order.append(issue_id)
        if external_id:
            self._external_receipts[external_id] = {"hash": request_hash, "issue_id": issue_id}
        self._trim_locked()
        if controller is not None:
            recorder = getattr(controller, "record_issue", None)
            if callable(recorder):
                recorder(issue, sim_time=sim_time)
        return {"ok": True, "replayed": False, "issue": copy.deepcopy(issue), "case": copy.deepcopy(case), "decision_requests": copy.deepcopy(requests)}

    def project_effect_issues(
        self,
        payloads: Iterable[Mapping[str, Any]],
        *,
        sim_time: float,
    ) -> list[dict[str, Any]]:
        """Idempotently materialize SQLite-authoritative causal issues."""
        results: list[dict[str, Any]] = []
        for payload in payloads:
            if not isinstance(payload, Mapping):
                continue
            created_at = payload.get("created_at", sim_time)
            try:
                issue_time = float(created_at)
            except (TypeError, ValueError, OverflowError):
                issue_time = float(sim_time)
            normalized_payload = copy.deepcopy(dict(payload))
            if not str(normalized_payload.get("title", "")).strip():
                normalized_payload.pop("title", None)
            results.append(self.open_issue(normalized_payload, sim_time=issue_time))
        return results

    def _trim_locked(self) -> None:
        while len(self._decision_order) > MAX_RECORDS:
            self._decision_receipts.pop(self._decision_order.popleft(), None)
        while len(self._issue_order) > MAX_RECORDS:
            oldest = self._issue_order[0]
            if self._issues.get(oldest, {}).get("status") not in FINAL_ISSUE:
                break
            self._issues.pop(self._issue_order.popleft(), None)

    @staticmethod
    def _option(request: Mapping[str, Any], option_id: str) -> dict[str, Any]:
        for option in request.get("options", []):
            if option.get("option_id") == option_id:
                return dict(option)
        raise IssueContractError("decision.option_not_found")

    def _invocation(self, option: Mapping[str, Any], supplied: Mapping[str, Any], decision_id: str = "") -> dict[str, Any]:
        player = self._gameplay.player
        parameters = copy.deepcopy(dict(option.get("parameters", {})))
        override = supplied.get("parameters", {})
        if not isinstance(override, Mapping):
            raise IssueContractError("decision.invalid_parameters")
        parameters.update(copy.deepcopy(dict(override)))
        return {
            "invocation_id": str(supplied.get("invocation_id", "")) or (f"decision:{decision_id}" if decision_id else ""),
            "expected_generation": self._generation,
            "actor_id": str(supplied.get("actor_id", "")) or (player.actor_id if player else ""),
            "control_object_id": str(supplied.get("control_object_id", "")) or str(option.get("control_object_id", "")),
            "target_id": str(supplied.get("target_id", "")) or str(option.get("target_id", "")),
            "parameters": parameters,
        }

    def preview_decision(self, decision_request_id: str, payload: Mapping[str, Any], *, sim_time: float, action_preview: Callable[[str, dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._refresh_locked(sim_time)
            data = _map(payload, "decision.invalid", "payload")
            self._check_generation(data.get("expected_generation"))
            request = self._requests.get(decision_request_id)
            if request is None:
                raise IssueContractError("decision.not_found")
            if request["status"] != "pending":
                raise IssueContractError("decision.not_pending", details={"status": request["status"]})
            option_id = _id(data.get("option_id"), "decision.missing_option", "option_id")
            option = self._option(request, option_id)
            action_id = str(option.get("action_id", ""))
            supplied = data.get("invocation", {})
            if not isinstance(supplied, Mapping):
                raise IssueContractError("decision.invalid_invocation")
            invocation = self._invocation(option, supplied)
            action_result = action_preview(action_id, invocation) if action_id else None
            return {
                "ok": True,
                "generation": self._generation,
                "decision_request_id": decision_request_id,
                "issue_id": request["issue_id"],
                "case_id": request["case_id"],
                "option": copy.deepcopy(option),
                "action_id": action_id,
                "invocation": invocation,
                "action_preview": action_result,
                "resolution": copy.deepcopy(option["resolution"]),
            }

    def resolve_decision(self, decision_request_id: str, payload: Mapping[str, Any], *, sim_time: float, action_preview: Callable[[str, dict[str, Any]], dict[str, Any]], action_execute: Callable[[str, dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            data = _map(payload, "decision.invalid", "payload")
            decision_id = _id(data.get("decision_id"), "decision.missing_id", "decision_id")
            receipt_hash = _hash(dict(data))
            if decision_id in self._decision_receipts:
                receipt = self._decision_receipts[decision_id]
                if receipt["hash"] != receipt_hash:
                    raise IssueContractError("decision.id_conflict")
                response = copy.deepcopy(receipt["response"])
                response["replayed"] = True
                return response
            preview = self.preview_decision(decision_request_id, data, sim_time=sim_time, action_preview=action_preview)
            request = self._requests[decision_request_id]
            option = preview["option"]
            supplied = data.get("invocation", {})
            invocation = self._invocation(option, supplied, decision_id)
            action_id = preview["action_id"]
            action_result = action_execute(action_id, invocation) if action_id else None
            outcome = {
                "decision_id": decision_id,
                "summary": option["resolution"].get("summary", ""),
                "note": _text(data.get("note", ""), "decision.invalid_note", required=False),
                "action_id": action_id,
                "action_result": copy.deepcopy(action_result),
            }
            request.update({"status": "decided", "selected_option_id": option["option_id"], "decided_at": sim_time, "updated_at": sim_time, "outcome": outcome})
            issue = self._issues[request["issue_id"]]
            case = self._cases[request["case_id"]]
            issue.update({"status": option["resolution"]["issue_status"], "updated_at": sim_time, "outcome": copy.deepcopy(outcome)})
            pending_required = [self._requests[item] for item in case["decision_request_ids"] if self._requests[item]["required"] and self._requests[item]["status"] == "pending"]
            if pending_required:
                case["status"] = "in_review"
            else:
                case["status"] = option["resolution"]["case_status"]
                case["outcome"] = {
                    "summary": option["resolution"].get("summary", ""),
                    "decisions": [copy.deepcopy(self._requests[item]["outcome"]) for item in case["decision_request_ids"] if self._requests[item]["status"] == "decided"],
                }
            case["updated_at"] = sim_time
            response = {
                "ok": True,
                "replayed": False,
                "generation": self._generation,
                "decision_request": copy.deepcopy(request),
                "issue": copy.deepcopy(issue),
                "case": copy.deepcopy(case),
                "action_result": copy.deepcopy(action_result),
            }
            self._decision_receipts[decision_id] = {"hash": receipt_hash, "response": copy.deepcopy(response)}
            self._decision_order.append(decision_id)
            self._trim_locked()
            return response

    def append_evidence(self, issue_id: str, payload: Mapping[str, Any], *, sim_time: float) -> dict[str, Any]:
        with self._lock:
            issue = self._issues.get(issue_id)
            if issue is None:
                raise IssueContractError("issue.not_found")
            if issue["status"] in FINAL_ISSUE:
                raise IssueContractError("issue.closed")
            evidence = _runtime_evidence(payload, sim_time, len(issue["evidence"]))
            existing = next((item for item in issue["evidence"] if item["evidence_id"] == evidence["evidence_id"]), None)
            if existing is not None:
                normalized = copy.deepcopy(evidence)
                normalized["index"] = existing["index"]
                normalized["added_at"] = existing["added_at"]
                if _hash(existing) != _hash(normalized):
                    raise IssueContractError("issue.evidence.id_conflict")
                return {"ok": True, "replayed": True, "issue": copy.deepcopy(issue), "evidence": copy.deepcopy(existing)}
            issue["evidence"].append(evidence)
            issue["updated_at"] = sim_time
            return {"ok": True, "replayed": False, "issue": copy.deepcopy(issue), "evidence": copy.deepcopy(evidence)}

    def close_case(self, case_id: str, payload: Mapping[str, Any], *, sim_time: float) -> dict[str, Any]:
        with self._lock:
            data = _map(payload, "case.invalid", "payload")
            self._check_generation(data.get("expected_generation"))
            case = self._cases.get(case_id)
            if case is None:
                raise IssueContractError("case.not_found")
            status = str(data.get("status", "closed"))
            if status not in FINAL_CASE:
                raise IssueContractError("case.invalid_status")
            details = data.get("details", {})
            if not isinstance(details, Mapping):
                raise IssueContractError("case.invalid_details")
            outcome = {"summary": _text(data.get("summary", ""), "case.invalid_summary", required=False), "details": copy.deepcopy(dict(details))}
            case.update({"status": status, "updated_at": sim_time, "outcome": outcome})
            for issue_id in case["issue_ids"]:
                issue = self._issues[issue_id]
                if issue["status"] not in FINAL_ISSUE:
                    issue.update({"status": "closed" if status == "closed" else status, "updated_at": sim_time, "outcome": copy.deepcopy(outcome)})
            for request_id in case["decision_request_ids"]:
                request = self._requests[request_id]
                if request["status"] == "pending":
                    request.update({"status": "cancelled", "updated_at": sim_time})
            return {"ok": True, "case": copy.deepcopy(case), "issues": [copy.deepcopy(self._issues[item]) for item in case["issue_ids"]]}

    def rhythm_records(self) -> list[dict[str, Any]]:
        """Return the durable issue projection consumed by RhythmRuntime."""
        with self._lock:
            return [copy.deepcopy(self._issues[item]) for item in self._issue_order if item in self._issues]

    def rhythm_update_issue(
        self, issue_id: str, *, sim_time: float, status: str | None = None,
        urgency: str | None = None, level: int | None = None,
        queue_reason: str | None = None, case_status: str | None = None,
        outcome: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a pacing transition atomically across Issue, Case and requests."""
        with self._lock:
            issue = self._issues.get(issue_id)
            if issue is None:
                raise IssueContractError("issue.not_found")
            if status is not None and status not in ISSUE_STATUSES:
                raise IssueContractError("issue.invalid_status")
            if urgency is not None and urgency not in URGENCIES:
                raise IssueContractError("issue.invalid_urgency")
            if case_status is not None and case_status not in CASE_STATUSES:
                raise IssueContractError("case.invalid_status")
            rhythm = issue.get("rhythm")
            if not isinstance(rhythm, dict):
                rhythm = {}
                issue["rhythm"] = rhythm
            if level is not None:
                rhythm["level"] = max(0, int(level))
            if queue_reason is not None:
                rhythm["queue_reason"] = str(queue_reason)
            old_status = str(issue.get("status", ""))
            if status is not None:
                issue["status"] = status
            if urgency is not None:
                issue["urgency"] = urgency
            issue["updated_at"] = float(sim_time)
            if outcome is not None:
                issue["outcome"] = copy.deepcopy(dict(outcome))

            case = self._cases.get(str(issue.get("case_id", "")))
            requests = [self._requests[item] for item in issue.get("decision_request_ids", []) if item in self._requests]
            if urgency is not None:
                for request in requests:
                    request["urgency"] = urgency
                    request["updated_at"] = float(sim_time)
                if case is not None:
                    case["urgency"] = urgency
            if old_status == "queued" and issue.get("status") in ACTIVE_ISSUE_STATUSES:
                for request in requests:
                    if request.get("status") == "queued":
                        request.update({"status": "pending", "updated_at": float(sim_time)})
                if case is not None and case.get("status") == "queued":
                    case["status"] = "open"
            if issue.get("status") in FINAL_ISSUE:
                for request in requests:
                    if request.get("status") in {"queued", "pending"}:
                        request.update({"status": "cancelled", "updated_at": float(sim_time)})
                if case is not None:
                    target_case_status = case_status or ("closed" if issue["status"] == "closed" else issue["status"])
                    case["status"] = target_case_status
                    if outcome is not None:
                        case["outcome"] = copy.deepcopy(dict(outcome))
                    for request_id in case.get("decision_request_ids", []):
                        request = self._requests.get(request_id)
                        if request is not None and request.get("status") in {"queued", "pending"}:
                            request.update({"status": "cancelled", "updated_at": float(sim_time)})
            elif case is not None and case_status is not None:
                case["status"] = case_status
            if case is not None:
                case["updated_at"] = float(sim_time)
            return copy.deepcopy(issue)

    def _refresh_locked(self, sim_time: float) -> None:
        for request in self._requests.values():
            if request["status"] == "pending" and request["deadline_at"] is not None and sim_time > request["deadline_at"]:
                request.update({"status": "expired", "updated_at": sim_time})
        for issue in self._issues.values():
            if issue["status"] in {"open", "monitoring"} and issue["deadline_at"] is not None and sim_time > issue["deadline_at"]:
                issue.update({"status": "overdue", "updated_at": sim_time})
        for case in self._cases.values():
            if case["status"] not in FINAL_CASE and case["deadline_at"] is not None and sim_time > case["deadline_at"]:
                case.update({"status": "overdue", "updated_at": sim_time})

    def list_issues(self, *, sim_time: float, status: str = "") -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked(sim_time)
            return [copy.deepcopy(self._issues[item]) for item in self._issue_order if item in self._issues and (not status or self._issues[item]["status"] == status)]

    def list_cases(self, *, sim_time: float, status: str = "") -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked(sim_time)
            return [copy.deepcopy(self._cases[item]) for item in self._case_order if item in self._cases and (not status or self._cases[item]["status"] == status)]

    def list_decision_requests(self, *, sim_time: float, status: str = "") -> list[dict[str, Any]]:
        with self._lock:
            self._refresh_locked(sim_time)
            return [copy.deepcopy(self._requests[item]) for item in self._request_order if item in self._requests and (not status or self._requests[item]["status"] == status)]

    def legacy_memorials(self, *, sim_time: float) -> list[dict[str, Any]]:
        return [{
            "memorial_id": issue["issue_id"],
            "from": issue["source"].get("display_name") or issue["source"].get("id") or issue["source"].get("kind"),
            "content": issue["summary"],
            "chain_id": issue["metadata"].get("chain_id", ""),
            "event_id": issue["metadata"].get("event_id", ""),
            "received_at": issue["created_at"],
            "issue_type_id": issue["issue_type_id"],
            "case_id": issue["case_id"],
            "urgency": issue["urgency"],
            "deadline_at": issue["deadline_at"],
        } for issue in self.list_issues(sim_time=sim_time) if issue["status"] in ACTIVE_ISSUE_STATUSES]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": "1.0",
                "issues": copy.deepcopy(self._issues),
                "cases": copy.deepcopy(self._cases),
                "decision_requests": copy.deepcopy(self._requests),
                "issue_order": list(self._issue_order),
                "case_order": list(self._case_order),
                "decision_request_order": list(self._request_order),
                "external_receipts": copy.deepcopy(self._external_receipts),
                "decision_receipts": copy.deepcopy(self._decision_receipts),
                "decision_order": list(self._decision_order),
            }

    def restore(self, value: Any, *, sim_time: float = 0.0, legacy_memorials: Iterable[Mapping[str, Any]] = ()) -> None:
        with self._lock:
            if value not in (None, {}):
                data = _map(value, "issue.snapshot.invalid", "issue_runtime")
                for key in ("issues", "cases", "decision_requests", "external_receipts", "decision_receipts"):
                    if not isinstance(data.get(key, {}), Mapping):
                        raise IssueContractError("issue.snapshot.invalid", path=key)
                self._issues = copy.deepcopy(dict(data.get("issues", {})))
                self._cases = copy.deepcopy(dict(data.get("cases", {})))
                self._requests = copy.deepcopy(dict(data.get("decision_requests", {})))
                self._issue_order = deque(item for item in data.get("issue_order", self._issues) if item in self._issues)
                self._case_order = deque(item for item in data.get("case_order", self._cases) if item in self._cases)
                self._request_order = deque(item for item in data.get("decision_request_order", self._requests) if item in self._requests)
                self._external_receipts = copy.deepcopy(dict(data.get("external_receipts", {})))
                self._decision_receipts = copy.deepcopy(dict(data.get("decision_receipts", {})))
                self._decision_order = deque(item for item in data.get("decision_order", self._decision_receipts) if item in self._decision_receipts)
            elif legacy_memorials:
                for index, memorial in enumerate(legacy_memorials):
                    if not isinstance(memorial, Mapping):
                        continue
                    self._open_locked({
                        "external_id": f"legacy:{memorial.get('memorial_id', index)}",
                        "source": {"kind": "agent", "id": str(memorial.get("from", ""))},
                        "title": str(memorial.get("title", "Agent report")),
                        "summary": str(memorial.get("content", "")),
                        "metadata": {"chain_id": memorial.get("chain_id", ""), "event_id": memorial.get("event_id", ""), "legacy_memorial_id": memorial.get("memorial_id", "")},
                    }, float(memorial.get("received_at", sim_time)))
            self._refresh_locked(sim_time)
            self._trim_locked()
