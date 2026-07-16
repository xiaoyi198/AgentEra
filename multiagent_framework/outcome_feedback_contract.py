"""DLC-authored player-facing outcome feedback contracts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .causal_conditions import CausalCondition, CausalEventError

DAY = 86400.0
MAX_RULES = 256
MAX_CONDITIONS = 32
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SOURCE_KINDS = ("action", "decision", "case_closed", "causal_event", "lifecycle", "rhythm")
DELIVERY_KINDS = ("immediate", "delayed", "causal_reveal")


class OutcomeFeedbackContractError(ValueError):
    def __init__(self, code: str, *, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(code)


def _mapping(value: Any, code: str, path: str, *, limit: int = 256) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or len(value) > limit:
        raise OutcomeFeedbackContractError(code, path=path)
    return value


def _sequence(value: Any, code: str, path: str, *, limit: int = 256) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) > limit:
        raise OutcomeFeedbackContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 1000) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise OutcomeFeedbackContractError(code, path=path)
    if any(ord(char) < 32 and char not in "\n\t" for char in result):
        raise OutcomeFeedbackContractError(code, path=path)
    return result


def _identifier(value: Any, code: str, path: str, *, required: bool = True) -> str:
    result = _text(value, code, path, required=required, limit=128)
    if result and not SAFE_ID.fullmatch(result):
        raise OutcomeFeedbackContractError(code, path=path)
    return result


def _identifiers(value: Any, code: str, path: str) -> tuple[str, ...]:
    result: list[str] = []
    for index, raw in enumerate(_sequence(value, code, path)):
        item = _identifier(raw, code, f"{path}[{index}]")
        if item not in result:
            result.append(item)
    return tuple(result)


def _integer(value: Any, code: str, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise OutcomeFeedbackContractError(code, path=path)
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OutcomeFeedbackContractError(code, path=path) from exc
    if result < minimum or result > maximum:
        raise OutcomeFeedbackContractError(code, path=path)
    return result


def _number(value: Any, code: str, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise OutcomeFeedbackContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OutcomeFeedbackContractError(code, path=path) from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise OutcomeFeedbackContractError(code, path=path)
    return result


def _conditions(value: Any, path: str) -> tuple[CausalCondition, ...]:
    result: list[CausalCondition] = []
    for index, raw in enumerate(_sequence(value, "outcome_feedback.conditions.invalid", path, limit=MAX_CONDITIONS)):
        try:
            result.append(CausalCondition.from_mapping(raw))
        except CausalEventError as exc:
            raise OutcomeFeedbackContractError(str(exc), path=f"{path}[{index}]") from exc
    return tuple(result)


def normalize_outcome_feedback_document(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise OutcomeFeedbackContractError("outcome_feedback.invalid", path="outcome_feedback")
    return dict(value)


@dataclass(frozen=True)
class ImmediateFeedbackSettings:
    enabled: bool = True
    include_command_changes: bool = True
    include_metric_changes: bool = True
    max_changes: int = 12
    max_metric_changes: int = 8

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "ImmediateFeedbackSettings":
        if value in (None, {}):
            return cls()
        item = _mapping(value, "outcome_feedback.immediate.invalid", path)
        return cls(
            enabled=bool(item.get("enabled", True)),
            include_command_changes=bool(item.get("include_command_changes", True)),
            include_metric_changes=bool(item.get("include_metric_changes", True)),
            max_changes=_integer(item.get("max_changes", 12), "outcome_feedback.immediate.max_changes_invalid", path + ".max_changes", 0, 64),
            max_metric_changes=_integer(item.get("max_metric_changes", 8), "outcome_feedback.immediate.max_metric_changes_invalid", path + ".max_metric_changes", 0, 64),
        )


@dataclass(frozen=True)
class CaseSummarySettings:
    enabled: bool = True
    include_decisions: bool = True
    include_changes: bool = True
    include_metric_changes: bool = True

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "CaseSummarySettings":
        if value in (None, {}):
            return cls()
        item = _mapping(value, "outcome_feedback.case_summaries.invalid", path)
        return cls(
            enabled=bool(item.get("enabled", True)),
            include_decisions=bool(item.get("include_decisions", True)),
            include_changes=bool(item.get("include_changes", True)),
            include_metric_changes=bool(item.get("include_metric_changes", True)),
        )


@dataclass(frozen=True)
class OutcomeFeedbackRule:
    rule_id: str
    display_name: str
    enabled: bool = True
    priority: int = 0
    source_kinds: tuple[str, ...] = ()
    delivery: str = "delayed"
    action_ids: tuple[str, ...] = ()
    issue_type_ids: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    delay_days: float = 0.0
    conditions: tuple[CausalCondition, ...] = ()
    title: str = ""
    summary: str = ""
    reason: str = ""
    reveal_text: str = ""
    create_issue_type_id: str = ""
    attach_evidence: bool = False

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "OutcomeFeedbackRule":
        item = _mapping(value, "outcome_feedback.rule.invalid", path)
        source_kinds = _identifiers(item.get("source_kinds", []), "outcome_feedback.rule.sources_invalid", path + ".source_kinds")
        if not source_kinds or any(kind not in SOURCE_KINDS for kind in source_kinds):
            raise OutcomeFeedbackContractError("outcome_feedback.rule.sources_invalid", path=path + ".source_kinds")
        delivery = _identifier(item.get("delivery", "delayed"), "outcome_feedback.rule.delivery_invalid", path + ".delivery")
        if delivery not in DELIVERY_KINDS:
            raise OutcomeFeedbackContractError("outcome_feedback.rule.delivery_invalid", path=path + ".delivery")
        reveal_text = _text(item.get("reveal_text", ""), "outcome_feedback.rule.reveal_invalid", path + ".reveal_text", required=False, limit=4000)
        if delivery == "causal_reveal" and not reveal_text:
            raise OutcomeFeedbackContractError("outcome_feedback.rule.reveal_required", path=path + ".reveal_text")
        return cls(
            rule_id=_identifier(item.get("rule_id", ""), "outcome_feedback.rule.id_invalid", path + ".rule_id"),
            display_name=_text(item.get("display_name", ""), "outcome_feedback.rule.name_invalid", path + ".display_name", limit=160),
            enabled=bool(item.get("enabled", True)),
            priority=_integer(item.get("priority", 0), "outcome_feedback.rule.priority_invalid", path + ".priority", -10000, 10000),
            source_kinds=source_kinds,
            delivery=delivery,
            action_ids=_identifiers(item.get("action_ids", []), "outcome_feedback.rule.action_ids_invalid", path + ".action_ids"),
            issue_type_ids=_identifiers(item.get("issue_type_ids", []), "outcome_feedback.rule.issue_type_ids_invalid", path + ".issue_type_ids"),
            event_types=_identifiers(item.get("event_types", []), "outcome_feedback.rule.event_types_invalid", path + ".event_types"),
            metric_ids=_identifiers(item.get("metric_ids", []), "outcome_feedback.rule.metric_ids_invalid", path + ".metric_ids"),
            delay_days=_number(item.get("delay_days", 0), "outcome_feedback.rule.delay_invalid", path + ".delay_days", 0, 36500),
            conditions=_conditions(item.get("conditions", []), path + ".conditions"),
            title=_text(item.get("title", ""), "outcome_feedback.rule.title_invalid", path + ".title", required=False, limit=240),
            summary=_text(item.get("summary", ""), "outcome_feedback.rule.summary_invalid", path + ".summary", required=False, limit=4000),
            reason=_text(item.get("reason", ""), "outcome_feedback.rule.reason_invalid", path + ".reason", required=False, limit=2000),
            reveal_text=reveal_text,
            create_issue_type_id=_identifier(item.get("create_issue_type_id", ""), "outcome_feedback.rule.issue_type_invalid", path + ".create_issue_type_id", required=False),
            attach_evidence=bool(item.get("attach_evidence", False)),
        )

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["conditions"] = [asdict(item) for item in self.conditions]
        return result

    def public_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "priority": self.priority,
            "source_kinds": list(self.source_kinds),
            "delivery": self.delivery,
            "delay_days": self.delay_days,
        }


@dataclass(frozen=True)
class OutcomeFeedbackContract:
    enabled: bool = False
    recent_limit: int = 128
    pending_limit: int = 256
    receipt_limit: int = 2048
    metric_delta_epsilon: float = 0.01
    immediate: ImmediateFeedbackSettings = field(default_factory=ImmediateFeedbackSettings)
    case_summaries: CaseSummarySettings = field(default_factory=CaseSummarySettings)
    rules: tuple[OutcomeFeedbackRule, ...] = ()

    @classmethod
    def from_mapping(cls, value: Any) -> "OutcomeFeedbackContract":
        item = normalize_outcome_feedback_document(value)
        if not item:
            return cls()
        raw_rules = _sequence(item.get("rules", []), "outcome_feedback.rules_invalid", "outcome_feedback.rules", limit=MAX_RULES)
        rules = tuple(OutcomeFeedbackRule.from_mapping(raw, f"outcome_feedback.rules[{index}]") for index, raw in enumerate(raw_rules))
        ids = [rule.rule_id for rule in rules]
        if len(ids) != len(set(ids)):
            raise OutcomeFeedbackContractError("outcome_feedback.rule.duplicate_id", path="outcome_feedback.rules")
        return cls(
            enabled=bool(item.get("enabled", True)),
            recent_limit=_integer(item.get("recent_limit", 128), "outcome_feedback.recent_limit_invalid", "outcome_feedback.recent_limit", 1, 1024),
            pending_limit=_integer(item.get("pending_limit", 256), "outcome_feedback.pending_limit_invalid", "outcome_feedback.pending_limit", 1, 4096),
            receipt_limit=_integer(item.get("receipt_limit", 2048), "outcome_feedback.receipt_limit_invalid", "outcome_feedback.receipt_limit", 1, 16384),
            metric_delta_epsilon=_number(item.get("metric_delta_epsilon", 0.01), "outcome_feedback.metric_delta_invalid", "outcome_feedback.metric_delta_epsilon", 0, 1e12),
            immediate=ImmediateFeedbackSettings.from_mapping(item.get("immediate", {}), "outcome_feedback.immediate"),
            case_summaries=CaseSummarySettings.from_mapping(item.get("case_summaries", {}), "outcome_feedback.case_summaries"),
            rules=tuple(sorted(rules, key=lambda rule: (-rule.priority, rule.rule_id))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "recent_limit": self.recent_limit,
            "pending_limit": self.pending_limit,
            "receipt_limit": self.receipt_limit,
            "metric_delta_epsilon": self.metric_delta_epsilon,
            "immediate": asdict(self.immediate),
            "case_summaries": asdict(self.case_summaries),
            "rules": [rule.to_dict() for rule in self.rules],
        }

    def public_state(self, *, generation: int = 0) -> dict[str, Any]:
        return {
            "configured": self.enabled or bool(self.rules),
            "enabled": self.enabled,
            "generation": int(generation),
            "recent_limit": self.recent_limit,
            "pending_limit": self.pending_limit,
            "metric_delta_epsilon": self.metric_delta_epsilon,
            "immediate": asdict(self.immediate),
            "case_summaries": asdict(self.case_summaries),
            "rules": [rule.public_dict() for rule in self.rules],
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
