"""Deterministic, DLC-configurable action duration estimation.

The LLM may propose a duration, but it is only a bounded workload hint.  The
engine owns the authoritative duration used by timers and ongoing events.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Any, Iterable

SAFE_RULE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SAFE_ACTION_TYPE = re.compile(r"^(?:\*|[A-Za-z0-9][A-Za-z0-9._:-]{0,63})$")
DEFAULT_MAX_DAYS = 365.0


class TimeRuleError(ValueError):
    """Raised when a time-rule definition is unsafe or ambiguous."""


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimeRuleError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise TimeRuleError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise TimeRuleError(f"{name} must be >= {minimum:g}")
    return result


def _safe_hint(value: Any, fallback: float) -> tuple[float, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback, True
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return fallback, True
    return result, False


@dataclass(frozen=True)
class ActionTimeRule:
    rule_id: str
    action_types: tuple[str, ...] = ("*",)
    base_days: float = 0.0
    min_days: float = 0.1
    max_days: float = DEFAULT_MAX_DAYS
    severity_factor: float = 0.0
    workload_factor: float = 1.0
    resource_factor: float = 0.0
    distance_factor: float = 1.0
    success_factor: float = 1.0
    priority: int = 0
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ActionTimeRule":
        if not isinstance(raw, dict):
            raise TimeRuleError("time rule must be an object")
        rule_id = str(raw.get("rule_id", "")).strip()
        if not SAFE_RULE_ID.fullmatch(rule_id):
            raise TimeRuleError("rule_id must be a safe 1-64 character identifier")
        action_types_raw = raw.get("action_types", ["*"])
        if not isinstance(action_types_raw, list) or isinstance(action_types_raw, (str, bytes)):
            raise TimeRuleError("action_types must be a list")
        if not action_types_raw:
            raise TimeRuleError("action_types must not be empty")
        action_types: list[str] = []
        for item in action_types_raw:
            action_type = str(item).strip()
            if not SAFE_ACTION_TYPE.fullmatch(action_type):
                raise TimeRuleError("action_types contains an unsafe identifier")
            if action_type in action_types:
                raise TimeRuleError("action_types must be unique")
            action_types.append(action_type)

        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TimeRuleError("enabled must be a boolean")
        priority = raw.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise TimeRuleError("priority must be an integer")

        min_days = _number(raw.get("min_days", 0.1), "min_days", minimum=0.0)
        max_days = _number(raw.get("max_days", DEFAULT_MAX_DAYS), "max_days", minimum=0.0)
        if max_days < min_days:
            raise TimeRuleError("max_days must be >= min_days")
        values = {
            "base_days": _number(raw.get("base_days", 0.0), "base_days", minimum=0.0),
            "severity_factor": _number(raw.get("severity_factor", 0.0), "severity_factor", minimum=0.0),
            "workload_factor": _number(raw.get("workload_factor", 1.0), "workload_factor", minimum=0.0),
            "resource_factor": _number(raw.get("resource_factor", 0.0), "resource_factor", minimum=0.0),
            "distance_factor": _number(raw.get("distance_factor", 1.0), "distance_factor", minimum=0.0),
            "success_factor": _number(raw.get("success_factor", 1.0), "success_factor", minimum=0.0),
        }
        return cls(
            rule_id=rule_id,
            action_types=tuple(action_types),
            min_days=min_days,
            max_days=max_days,
            priority=priority,
            enabled=enabled,
            **values,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["action_types"] = list(self.action_types)
        return result


@dataclass(frozen=True)
class TimeEstimate:
    duration_days: float
    rule_id: str
    components: dict[str, float | bool | str]
    clamped: bool
    source: str = "deterministic"

    def public_metadata(self) -> dict[str, Any]:
        return {
            "duration_days": self.duration_days,
            "rule_id": self.rule_id,
            "components": dict(self.components),
            "clamped": self.clamped,
            "source": self.source,
        }


class TimeSimulator:
    """Pure deterministic rule engine with atomic configuration publication."""

    def __init__(self, rules: Iterable[dict[str, Any] | ActionTimeRule] | None = None) -> None:
        self._rules: tuple[ActionTimeRule, ...] = ()
        self._fingerprint = ""
        self.configure(list(rules or []))

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def rules(self) -> tuple[ActionTimeRule, ...]:
        return self._rules

    def configure(self, rules: Iterable[dict[str, Any] | ActionTimeRule]) -> None:
        parsed: list[ActionTimeRule] = []
        ids: set[str] = set()
        for raw in rules:
            rule = raw if isinstance(raw, ActionTimeRule) else ActionTimeRule.from_dict(raw)
            if rule.rule_id in ids:
                raise TimeRuleError(f"duplicate rule_id: {rule.rule_id}")
            ids.add(rule.rule_id)
            parsed.append(rule)
        parsed.sort(key=lambda item: (-item.priority, item.rule_id))
        canonical = [item.to_dict() for item in parsed]
        self._rules = tuple(parsed)
        self._fingerprint = hashlib.sha256(
            json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _select(self, action_type: str) -> ActionTimeRule:
        safe_type = str(action_type or "unknown").strip()
        candidates = [
            rule for rule in self._rules
            if rule.enabled and (safe_type in rule.action_types or "*" in rule.action_types)
        ]
        if candidates:
            candidates.sort(key=lambda rule: (
                0 if safe_type in rule.action_types else 1,
                -rule.priority,
                rule.rule_id,
            ))
            return candidates[0]
        return ActionTimeRule(rule_id="builtin.safe_fallback")

    def estimate(
        self,
        *,
        action_type: str,
        proposed_days: Any = 1.0,
        severity: Any = 5,
        workload_days: Any | None = None,
        resource_value: Any = 0.0,
        distance_days: Any = 0.0,
        success: bool = True,
    ) -> TimeEstimate:
        rule = self._select(action_type)
        workload, invalid_hint = _safe_hint(
            proposed_days if workload_days is None else workload_days,
            max(1.0, rule.min_days),
        )
        distance, invalid_distance = _safe_hint(distance_days, 0.0)
        resources, invalid_resources = _safe_hint(resource_value, 0.0)
        severity_value, invalid_severity = _safe_hint(severity, 5.0)
        severity_value = min(10.0, max(1.0, severity_value))
        severity_normalized = (severity_value - 1.0) / 9.0

        base = rule.base_days
        workload_component = rule.workload_factor * workload
        severity_component = rule.severity_factor * severity_normalized
        distance_component = rule.distance_factor * distance
        resource_discount = rule.resource_factor * math.log1p(resources)
        raw = max(0.0, base + workload_component + severity_component + distance_component - resource_discount)
        if not bool(success):
            raw *= rule.success_factor
        duration = min(rule.max_days, max(rule.min_days, raw))
        clamped = not math.isclose(duration, raw, rel_tol=0.0, abs_tol=1e-12)
        return TimeEstimate(
            duration_days=round(duration, 6),
            rule_id=rule.rule_id,
            components={
                "base_days": round(base, 6),
                "workload_days": round(workload_component, 6),
                "severity_days": round(severity_component, 6),
                "distance_days": round(distance_component, 6),
                "resource_discount_days": round(resource_discount, 6),
                "invalid_hint_replaced": invalid_hint,
                "invalid_distance_replaced": invalid_distance,
                "invalid_resources_replaced": invalid_resources,
                "invalid_severity_replaced": invalid_severity,
                "success_adjusted": not bool(success),
            },
            clamped=clamped,
            source="deterministic" if rule.rule_id != "builtin.safe_fallback" else "fallback",
        )
