"""DLC-authored pacing, attention, escalation, and climax contracts."""
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
URGENCIES = ("low", "normal", "high", "critical")
FINAL_ISSUE_STATUSES = {"resolved", "closed", "cancelled"}
FINAL_CASE_STATUSES = {"resolved", "closed", "cancelled"}


class RhythmContractError(ValueError):
    def __init__(self, code: str, *, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(code)


def _mapping(value: Any, code: str, path: str, *, limit: int = 256) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or len(value) > limit:
        raise RhythmContractError(code, path=path)
    return value


def _sequence(value: Any, code: str, path: str, *, limit: int = 256) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) > limit:
        raise RhythmContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 1000) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise RhythmContractError(code, path=path)
    if any(ord(char) < 32 and char not in "\n\t" for char in result):
        raise RhythmContractError(code, path=path)
    return result


def _identifier(value: Any, code: str, path: str, *, required: bool = True) -> str:
    result = _text(value, code, path, required=required, limit=128)
    if result and not SAFE_ID.fullmatch(result):
        raise RhythmContractError(code, path=path)
    return result


def _number(value: Any, code: str, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise RhythmContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RhythmContractError(code, path=path) from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise RhythmContractError(code, path=path)
    return result


def _integer(value: Any, code: str, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise RhythmContractError(code, path=path)
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RhythmContractError(code, path=path) from exc
    if result < minimum or result > maximum:
        raise RhythmContractError(code, path=path)
    return result


def _identifiers(value: Any, code: str, path: str) -> tuple[str, ...]:
    result: list[str] = []
    for index, raw in enumerate(_sequence(value, code, path)):
        item = _identifier(raw, code, f"{path}[{index}]")
        if item not in result:
            result.append(item)
    return tuple(result)


def _conditions(value: Any, path: str) -> tuple[CausalCondition, ...]:
    result: list[CausalCondition] = []
    for index, raw in enumerate(_sequence(value, "rhythm.conditions.invalid", path, limit=MAX_CONDITIONS)):
        try:
            result.append(CausalCondition.from_mapping(raw))
        except CausalEventError as exc:
            raise RhythmContractError(str(exc), path=f"{path}[{index}]") from exc
    return tuple(result)


def normalize_rhythm_document(value: Any) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise RhythmContractError("rhythm.invalid", path="rhythm")
    return dict(value)


@dataclass(frozen=True)
class RhythmEscalation:
    enabled: bool = False
    after_days: float = 3.0
    interval_days: float = 2.0
    max_level: int = 3
    urgency_by_level: tuple[str, ...] = ("normal", "high", "critical", "critical")
    feedback_by_level: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "RhythmEscalation":
        if value in (None, {}):
            return cls()
        item = _mapping(value, "rhythm.escalation.invalid", path)
        max_level = _integer(item.get("max_level", 3), "rhythm.escalation.level_invalid", path + ".max_level", 1, 16)
        raw_urgencies = _sequence(item.get("urgency_by_level", ["normal", "high", "critical", "critical"]), "rhythm.escalation.urgencies_invalid", path + ".urgency_by_level", limit=17)
        urgencies = tuple(str(raw).strip() for raw in raw_urgencies)
        if not urgencies or len(urgencies) > max_level + 1 or any(item not in URGENCIES for item in urgencies):
            raise RhythmContractError("rhythm.escalation.urgencies_invalid", path=path + ".urgency_by_level")
        raw_feedback = _sequence(item.get("feedback_by_level", []), "rhythm.escalation.feedback_invalid", path + ".feedback_by_level", limit=16)
        feedback = tuple(_text(raw, "rhythm.escalation.feedback_invalid", f"{path}.feedback_by_level[{index}]", required=False, limit=2000) for index, raw in enumerate(raw_feedback))
        return cls(
            enabled=bool(item.get("enabled", True)),
            after_days=_number(item.get("after_days", 3.0), "rhythm.escalation.after_invalid", path + ".after_days", 0.0, 36500.0),
            interval_days=_number(item.get("interval_days", 2.0), "rhythm.escalation.interval_invalid", path + ".interval_days", 0.000001, 36500.0),
            max_level=max_level,
            urgency_by_level=urgencies,
            feedback_by_level=feedback,
        )

    def urgency_for_level(self, level: int, fallback: str = "normal") -> str:
        if not self.urgency_by_level:
            return fallback
        return self.urgency_by_level[min(max(0, int(level)), len(self.urgency_by_level) - 1)]

    def feedback_for_level(self, level: int) -> str:
        if not self.feedback_by_level:
            return ""
        index = min(max(0, int(level) - 1), len(self.feedback_by_level) - 1)
        return self.feedback_by_level[index]


@dataclass(frozen=True)
class RhythmDeescalation:
    enabled: bool = False
    interval_days: float = 1.0
    target_level: int = 0
    issue_status: str = "monitoring"
    conditions: tuple[CausalCondition, ...] = ()
    feedback: str = ""

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "RhythmDeescalation":
        if value in (None, {}):
            return cls()
        item = _mapping(value, "rhythm.deescalation.invalid", path)
        status = str(item.get("issue_status", "monitoring")).strip()
        if status not in {"open", "monitoring"}:
            raise RhythmContractError("rhythm.deescalation.status_invalid", path=path + ".issue_status")
        return cls(
            enabled=bool(item.get("enabled", True)),
            interval_days=_number(item.get("interval_days", 1.0), "rhythm.deescalation.interval_invalid", path + ".interval_days", 0.000001, 36500.0),
            target_level=_integer(item.get("target_level", 0), "rhythm.deescalation.level_invalid", path + ".target_level", 0, 16),
            issue_status=status,
            conditions=_conditions(item.get("conditions", []), path + ".conditions"),
            feedback=_text(item.get("feedback", ""), "rhythm.deescalation.feedback_invalid", path + ".feedback", required=False, limit=2000),
        )


@dataclass(frozen=True)
class RhythmClimax:
    enabled: bool = False
    level: int = 3
    after_days: float = 0.0
    conditions: tuple[CausalCondition, ...] = ()
    issue_status: str = "resolved"
    case_status: str = "resolved"
    close_group: bool = True
    quiet_period_days: float = 3.0
    feedback: str = ""

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "RhythmClimax":
        if value in (None, {}):
            return cls()
        item = _mapping(value, "rhythm.climax.invalid", path)
        issue_status = str(item.get("issue_status", "resolved")).strip()
        case_status = str(item.get("case_status", "resolved")).strip()
        if issue_status not in FINAL_ISSUE_STATUSES:
            raise RhythmContractError("rhythm.climax.issue_status_invalid", path=path + ".issue_status")
        if case_status not in FINAL_CASE_STATUSES:
            raise RhythmContractError("rhythm.climax.case_status_invalid", path=path + ".case_status")
        return cls(
            enabled=bool(item.get("enabled", True)),
            level=_integer(item.get("level", 3), "rhythm.climax.level_invalid", path + ".level", 1, 16),
            after_days=_number(item.get("after_days", 0.0), "rhythm.climax.after_invalid", path + ".after_days", 0.0, 36500.0),
            conditions=_conditions(item.get("conditions", []), path + ".conditions"),
            issue_status=issue_status,
            case_status=case_status,
            close_group=bool(item.get("close_group", True)),
            quiet_period_days=_number(item.get("quiet_period_days", 3.0), "rhythm.climax.quiet_invalid", path + ".quiet_period_days", 0.0, 36500.0),
            feedback=_text(item.get("feedback", ""), "rhythm.climax.feedback_invalid", path + ".feedback", required=False, limit=2000),
        )


@dataclass(frozen=True)
class RhythmRule:
    rule_id: str
    display_name: str
    enabled: bool = True
    issue_type_ids: tuple[str, ...] = ()
    event_types: tuple[str, ...] = ()
    priority: int = 0
    attention_cost: float | None = None
    max_active: int = 1
    cooldown_days: float = 0.0
    mutual_exclusion_group: str = ""
    allow_during_quiet: bool = False
    quiet_period_days: float = 0.0
    escalation: RhythmEscalation = field(default_factory=RhythmEscalation)
    deescalation: RhythmDeescalation = field(default_factory=RhythmDeescalation)
    climax: RhythmClimax = field(default_factory=RhythmClimax)

    @classmethod
    def from_mapping(cls, value: Any, path: str) -> "RhythmRule":
        item = _mapping(value, "rhythm.rule.invalid", path)
        attention_raw = item.get("attention_cost")
        attention_cost = None if attention_raw in (None, "") else _number(attention_raw, "rhythm.rule.attention_invalid", path + ".attention_cost", 0.0, 10000.0)
        rule = cls(
            rule_id=_identifier(item.get("rule_id"), "rhythm.rule.id_invalid", path + ".rule_id"),
            display_name=_text(item.get("display_name", item.get("rule_id", "")), "rhythm.rule.name_invalid", path + ".display_name", limit=512),
            enabled=bool(item.get("enabled", True)),
            issue_type_ids=_identifiers(item.get("issue_type_ids", []), "rhythm.rule.issue_types_invalid", path + ".issue_type_ids"),
            event_types=_identifiers(item.get("event_types", []), "rhythm.rule.event_types_invalid", path + ".event_types"),
            priority=_integer(item.get("priority", 0), "rhythm.rule.priority_invalid", path + ".priority", -1000, 1000),
            attention_cost=attention_cost,
            max_active=_integer(item.get("max_active", 1), "rhythm.rule.max_active_invalid", path + ".max_active", 1, 64),
            cooldown_days=_number(item.get("cooldown_days", 0.0), "rhythm.rule.cooldown_invalid", path + ".cooldown_days", 0.0, 36500.0),
            mutual_exclusion_group=_identifier(item.get("mutual_exclusion_group", ""), "rhythm.rule.group_invalid", path + ".mutual_exclusion_group", required=False),
            allow_during_quiet=bool(item.get("allow_during_quiet", False)),
            quiet_period_days=_number(item.get("quiet_period_days", 0.0), "rhythm.rule.quiet_invalid", path + ".quiet_period_days", 0.0, 36500.0),
            escalation=RhythmEscalation.from_mapping(item.get("escalation", {}), path + ".escalation"),
            deescalation=RhythmDeescalation.from_mapping(item.get("deescalation", {}), path + ".deescalation"),
            climax=RhythmClimax.from_mapping(item.get("climax", {}), path + ".climax"),
        )
        if not rule.issue_type_ids and not rule.event_types:
            raise RhythmContractError("rhythm.rule.match_required", path=path)
        if rule.deescalation.enabled and rule.deescalation.target_level > rule.escalation.max_level:
            raise RhythmContractError("rhythm.deescalation.level_invalid", path=path + ".deescalation.target_level")
        if rule.climax.enabled and rule.climax.level > rule.escalation.max_level:
            raise RhythmContractError("rhythm.climax.level_invalid", path=path + ".climax.level")
        return rule


@dataclass(frozen=True)
class RhythmContract:
    enabled: bool = False
    max_active_issues: int = 8
    attention_budget: float = 12.0
    attention_recovery_per_day: float = 4.0
    queue_limit: int = 128
    recent_feedback_limit: int = 32
    quiet_period_days: float = 0.0
    critical_bypasses_quiet: bool = True
    urgency_costs: Mapping[str, float] = field(default_factory=lambda: {"low": 1.0, "normal": 2.0, "high": 3.0, "critical": 5.0})
    rules: tuple[RhythmRule, ...] = ()

    @classmethod
    def from_mapping(cls, value: Any) -> "RhythmContract":
        normalized = normalize_rhythm_document(value)
        if not normalized:
            return cls()
        item = _mapping(normalized, "rhythm.invalid", "rhythm")
        costs_raw = _mapping(item.get("urgency_costs", {}), "rhythm.urgency_costs_invalid", "rhythm.urgency_costs")
        defaults = {"low": 1.0, "normal": 2.0, "high": 3.0, "critical": 5.0}
        costs = {
            urgency: _number(costs_raw.get(urgency, default), "rhythm.urgency_cost_invalid", f"rhythm.urgency_costs.{urgency}", 0.0, 10000.0)
            for urgency, default in defaults.items()
        }
        rules: list[RhythmRule] = []
        seen: set[str] = set()
        for index, raw in enumerate(_sequence(item.get("rules", []), "rhythm.rules.invalid", "rhythm.rules", limit=MAX_RULES)):
            rule = RhythmRule.from_mapping(raw, f"rhythm.rules[{index}]")
            if rule.rule_id in seen:
                raise RhythmContractError("rhythm.rule.duplicate", path=f"rhythm.rules[{index}].rule_id")
            seen.add(rule.rule_id)
            rules.append(rule)
        rules.sort(key=lambda rule: (-rule.priority, rule.rule_id))
        return cls(
            enabled=bool(item.get("enabled", True)),
            max_active_issues=_integer(item.get("max_active_issues", 8), "rhythm.max_active_invalid", "rhythm.max_active_issues", 1, 64),
            attention_budget=_number(item.get("attention_budget", 12.0), "rhythm.attention_budget_invalid", "rhythm.attention_budget", 0.0, 10000.0),
            attention_recovery_per_day=_number(item.get("attention_recovery_per_day", 4.0), "rhythm.attention_recovery_invalid", "rhythm.attention_recovery_per_day", 0.0, 10000.0),
            queue_limit=_integer(item.get("queue_limit", 128), "rhythm.queue_limit_invalid", "rhythm.queue_limit", 1, 2048),
            recent_feedback_limit=_integer(item.get("recent_feedback_limit", 32), "rhythm.feedback_limit_invalid", "rhythm.recent_feedback_limit", 1, 256),
            quiet_period_days=_number(item.get("quiet_period_days", 0.0), "rhythm.quiet_invalid", "rhythm.quiet_period_days", 0.0, 36500.0),
            critical_bypasses_quiet=bool(item.get("critical_bypasses_quiet", True)),
            urgency_costs=costs,
            rules=tuple(rules),
        )

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.rules or self.max_active_issues or self.attention_budget)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def rule_for_issue(self, issue_type_id: str, event_type: str = "") -> RhythmRule | None:
        for rule in self.rules:
            if not rule.enabled:
                continue
            if issue_type_id and issue_type_id in rule.issue_type_ids:
                return rule
            if event_type and event_type in rule.event_types:
                return rule
        return None

    def rule_for_event(self, event_type: str) -> RhythmRule | None:
        for rule in self.rules:
            if rule.enabled and event_type in rule.event_types:
                return rule
        return None

    def public_state(self, *, generation: int = 0) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "generation": max(0, int(generation)),
            "fingerprint": self.fingerprint(),
            "max_active_issues": self.max_active_issues,
            "attention_budget": self.attention_budget,
            "attention_recovery_per_day": self.attention_recovery_per_day,
            "queue_limit": self.queue_limit,
            "quiet_period_days": self.quiet_period_days,
            "critical_bypasses_quiet": self.critical_bypasses_quiet,
            "urgency_costs": dict(self.urgency_costs),
            "rules": [asdict(rule) for rule in self.rules],
        }
