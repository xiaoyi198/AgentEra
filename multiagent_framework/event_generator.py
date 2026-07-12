"""Deterministic, restart-safe random world event scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .types import EventObject

GAME_SECONDS_PER_DAY = 86400.0
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class RandomEventGeneratorError(ValueError):
    """Raised when random-event templates or persisted state are invalid."""


@dataclass(frozen=True)
class RandomEventTemplate:
    template_id: str
    event_type: str
    target_agents: tuple[str, ...]
    initial_payload: dict[str, Any] = field(default_factory=dict)
    severity_min: int = 1
    severity_max: int = 10
    min_interval_days: float = 30.0
    max_interval_days: float = 90.0
    probability: float = 1.0
    max_occurrences: int = 0
    enabled: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RandomEventTemplate":
        if not isinstance(value, Mapping):
            raise RandomEventGeneratorError("random_event_template_invalid")
        targets = value.get("target_agents", [])
        if (
            not isinstance(targets, Sequence)
            or isinstance(targets, (str, bytes, bytearray))
        ):
            raise RandomEventGeneratorError("random_event_targets_invalid")
        payload = value.get("initial_payload", {})
        if not isinstance(payload, Mapping):
            raise RandomEventGeneratorError("random_event_payload_invalid")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise RandomEventGeneratorError("random_event_enabled_invalid")
        numeric_values = (
            value.get("severity_min", value.get("severity", 5)),
            value.get("severity_max", value.get("severity", 5)),
            value.get("min_interval_days", 30.0),
            value.get("max_interval_days", 90.0),
            value.get("probability", 1.0),
            value.get("max_occurrences", 0),
        )
        if any(isinstance(item, bool) for item in numeric_values):
            raise RandomEventGeneratorError("random_event_number_invalid")
        try:
            return cls(
                template_id=str(value.get("template_id", "")).strip(),
                event_type=str(value.get("event_type", "")).strip(),
                target_agents=tuple(str(item).strip() for item in targets),
                initial_payload=dict(payload),
                severity_min=int(numeric_values[0]),
                severity_max=int(numeric_values[1]),
                min_interval_days=float(numeric_values[2]),
                max_interval_days=float(numeric_values[3]),
                probability=float(numeric_values[4]),
                max_occurrences=int(numeric_values[5]),
                enabled=enabled,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise RandomEventGeneratorError("random_event_number_invalid") from exc

    def fingerprint(self) -> str:
        payload = {
            "template_id": self.template_id,
            "event_type": self.event_type,
            "target_agents": list(self.target_agents),
            "initial_payload": self.initial_payload,
            "severity_min": self.severity_min,
            "severity_max": self.severity_max,
            "min_interval_days": self.min_interval_days,
            "max_interval_days": self.max_interval_days,
            "probability": self.probability,
            "max_occurrences": self.max_occurrences,
            "enabled": self.enabled,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RandomEventPollResult:
    events: tuple[EventObject, ...]
    attempts: int


@dataclass
class _TemplateState:
    fingerprint: str
    next_due: float
    attempts: int = 0
    emitted: int = 0


class RandomEventGenerator:
    """Schedule DLC-defined events without relying on mutable process RNG state."""

    def __init__(self, templates: Sequence[Mapping[str, Any]] = (), *, sim_time: float = 0.0):
        self._templates: dict[str, RandomEventTemplate] = {}
        self._states: dict[str, _TemplateState] = {}
        self.configure(templates, sim_time=sim_time, preserve_state=False)

    @staticmethod
    def _unit(template_id: str, index: int, channel: str) -> float:
        digest = hashlib.sha256(
            f"world-simulator/random-event/v1\0{template_id}\0{index}\0{channel}".encode(
                "utf-8"
            )
        ).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64)

    @staticmethod
    def _validate(template: RandomEventTemplate) -> None:
        if not _SAFE_IDENTIFIER_RE.fullmatch(template.template_id):
            raise RandomEventGeneratorError("random_event_template_id_invalid")
        if not _SAFE_IDENTIFIER_RE.fullmatch(template.event_type):
            raise RandomEventGeneratorError("random_event_type_invalid")
        if not template.target_agents or len(set(template.target_agents)) != len(template.target_agents):
            raise RandomEventGeneratorError("random_event_targets_invalid")
        if not isinstance(template.initial_payload, dict):
            raise RandomEventGeneratorError("random_event_payload_invalid")
        if not 1 <= template.severity_min <= template.severity_max <= 10:
            raise RandomEventGeneratorError("random_event_severity_invalid")
        values = (
            template.min_interval_days, template.max_interval_days, template.probability
        )
        if not all(math.isfinite(item) for item in values):
            raise RandomEventGeneratorError("random_event_number_invalid")
        if (
            template.min_interval_days <= 0
            or template.max_interval_days < template.min_interval_days
            or not 0 <= template.probability <= 1
            or template.max_occurrences < 0
        ):
            raise RandomEventGeneratorError("random_event_schedule_invalid")

    def _interval_seconds(self, template: RandomEventTemplate, index: int) -> float:
        ratio = self._unit(template.template_id, index, "interval")
        days = template.min_interval_days + (
            template.max_interval_days - template.min_interval_days
        ) * ratio
        return days * GAME_SECONDS_PER_DAY

    def configure(
        self,
        templates: Sequence[Mapping[str, Any] | RandomEventTemplate],
        *,
        sim_time: float,
        preserve_state: bool = True,
    ) -> None:
        if not math.isfinite(float(sim_time)) or float(sim_time) < 0:
            raise RandomEventGeneratorError("random_event_sim_time_invalid")
        parsed: dict[str, RandomEventTemplate] = {}
        for raw in templates:
            template = raw if isinstance(raw, RandomEventTemplate) else RandomEventTemplate.from_mapping(raw)
            self._validate(template)
            if template.template_id in parsed:
                raise RandomEventGeneratorError("random_event_template_duplicate")
            parsed[template.template_id] = template
        states: dict[str, _TemplateState] = {}
        for template_id, template in parsed.items():
            fingerprint = template.fingerprint()
            previous = self._states.get(template_id)
            if preserve_state and previous is not None and previous.fingerprint == fingerprint:
                states[template_id] = previous
            else:
                states[template_id] = _TemplateState(
                    fingerprint=fingerprint,
                    next_due=float(sim_time) + self._interval_seconds(template, 0),
                )
        self._templates = parsed
        self._states = states

    def poll(self, sim_time: float, *, max_attempts: int = 100) -> RandomEventPollResult:
        now = float(sim_time)
        if not math.isfinite(now) or now < 0:
            raise RandomEventGeneratorError("random_event_sim_time_invalid")
        if isinstance(max_attempts, bool):
            raise RandomEventGeneratorError("random_event_budget_invalid")
        try:
            budget = int(max_attempts)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RandomEventGeneratorError("random_event_budget_invalid") from exc
        if budget <= 0:
            raise RandomEventGeneratorError("random_event_budget_invalid")

        events: list[EventObject] = []
        attempts = 0
        while attempts < budget:
            eligible: list[tuple[float, str]] = []
            for template_id, template in self._templates.items():
                state = self._states[template_id]
                capped = bool(
                    template.max_occurrences
                    and state.emitted >= template.max_occurrences
                )
                if template.enabled and not capped and state.next_due <= now:
                    eligible.append((state.next_due, template_id))
            if not eligible:
                break

            _, template_id = min(eligible)
            template = self._templates[template_id]
            state = self._states[template_id]
            occurrence_index = state.attempts
            scheduled_at = state.next_due
            state.attempts += 1
            attempts += 1
            should_emit = (
                self._unit(template_id, occurrence_index, "probability")
                < template.probability
            )
            if should_emit:
                severity_span = template.severity_max - template.severity_min + 1
                severity = template.severity_min + min(
                    severity_span - 1,
                    int(self._unit(template_id, occurrence_index, "severity") * severity_span),
                )
                state.emitted += 1
                payload = dict(template.initial_payload)
                payload.setdefault("random_event_template_id", template_id)
                payload.setdefault("occurrence", state.emitted)
                events.append(EventObject(
                    event_id=f"rnd_{template_id}_{state.attempts:06d}",
                    event_type=template.event_type,
                    severity=severity,
                    target_agents=list(template.target_agents),
                    initial_payload=payload,
                    generated_at=scheduled_at,
                ))
            state.next_due = scheduled_at + self._interval_seconds(
                template, state.attempts
            )
        return RandomEventPollResult(tuple(events), attempts)

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "templates": {
                template_id: {
                    "fingerprint": state.fingerprint,
                    "next_due": state.next_due,
                    "attempts": state.attempts,
                    "emitted": state.emitted,
                }
                for template_id, state in sorted(self._states.items())
            },
        }

    def restore(self, value: Mapping[str, Any], *, sim_time: float) -> None:
        if not math.isfinite(float(sim_time)) or float(sim_time) < 0:
            raise RandomEventGeneratorError("random_event_sim_time_invalid")
        saved = value.get("templates", {}) if isinstance(value, Mapping) else {}
        if not isinstance(saved, Mapping):
            saved = {}
        restored: dict[str, _TemplateState] = {}
        for template_id, template in self._templates.items():
            fingerprint = template.fingerprint()
            raw = saved.get(template_id)
            if isinstance(raw, Mapping) and str(raw.get("fingerprint", "")) == fingerprint:
                try:
                    state = _TemplateState(
                        fingerprint=fingerprint,
                        next_due=float(raw.get("next_due")),
                        attempts=int(raw.get("attempts", 0)),
                        emitted=int(raw.get("emitted", 0)),
                    )
                    if (
                        not math.isfinite(state.next_due)
                        or state.next_due < 0
                        or state.attempts < 0
                        or state.emitted < 0
                        or state.emitted > state.attempts
                        or (
                            template.max_occurrences
                            and state.emitted > template.max_occurrences
                        )
                    ):
                        raise ValueError
                    restored[template_id] = state
                    continue
                except (TypeError, ValueError, OverflowError):
                    pass
            restored[template_id] = _TemplateState(
                fingerprint=fingerprint,
                next_due=float(sim_time) + self._interval_seconds(template, 0),
            )
        self._states = restored

    def public_status(self) -> dict[str, Any]:
        enabled = [item for item in self._templates.values() if item.enabled]
        return {
            "template_count": len(self._templates),
            "enabled_count": len(enabled),
            "emitted_total": sum(state.emitted for state in self._states.values()),
            "next_due": min(
                (self._states[item.template_id].next_due for item in enabled),
                default=None,
            ),
        }
