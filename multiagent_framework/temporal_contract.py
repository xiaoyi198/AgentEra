"""DLC-authored calendars, seasons, recurring cycles, and automatic pause rules.

The module is deterministic and generation-bound.  It owns no wall-clock state: all
calculations use authoritative simulation seconds and can therefore be archived,
rolled back, and restored from saves without replaying old boundaries.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .causal_conditions import (
    CausalCondition,
    CausalConditionContext,
    CausalEventError,
    compare_condition_value,
    resolve_condition_value,
)
from .event_generator import GAME_SECONDS_PER_DAY
from .types import EventObject


MAX_MARKERS_PER_POLL = 2048
MAX_MONTHS = 64
MAX_SEASONS = 128
MAX_CYCLES = 256
MAX_BOUNDARY_EVENTS = 512
MAX_AUTO_PAUSES = 256
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BOUNDARY_TRIGGERS = {
    "day_start", "week_start", "month_start", "year_start",
    "season_start", "season_end", "cycle_start", "cycle_end",
}
CYCLE_KINDS = {"turn", "meeting", "task", "custom"}
_TRIGGER_ORDER = {
    "cycle_end": 0,
    "season_end": 1,
    "year_start": 2,
    "month_start": 3,
    "week_start": 4,
    "day_start": 5,
    "season_start": 6,
    "cycle_start": 7,
}


class TemporalContractError(ValueError):
    """Raised when a DLC time-system document or runtime snapshot is invalid."""

    def __init__(self, code: str, *, path: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(code)


def _mapping(value: Any, code: str, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TemporalContractError(code, path=path)
    return value


def _sequence(value: Any, code: str, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TemporalContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 512) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise TemporalContractError(code, path=path)
    return result


def _identifier(value: Any, code: str, path: str, *, required: bool = True) -> str:
    result = str(value or "").strip()
    if (required and not result) or (result and not _SAFE_ID.fullmatch(result)):
        raise TemporalContractError(code, path=path)
    return result


def _reference(value: Any, code: str, path: str) -> str:
    """Validate an authoritative DLC object id without forcing ASCII slugs."""
    result = _text(value, code, path, limit=128)
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise TemporalContractError(code, path=path)
    return result


def _integer(value: Any, code: str, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise TemporalContractError(code, path=path)
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalContractError(code, path=path) from exc
    if result < minimum or result > maximum:
        raise TemporalContractError(code, path=path)
    return result


def _number(value: Any, code: str, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise TemporalContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TemporalContractError(code, path=path) from exc
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise TemporalContractError(code, path=path)
    return result


def _default_months() -> list[dict[str, Any]]:
    lengths = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return [
        {"month_id": f"month_{index:02d}", "display_name": f"Month {index}", "days": days}
        for index, days in enumerate(lengths, 1)
    ]


def normalize_time_system_document(value: Any) -> dict[str, Any]:
    """Return a complete JSON-safe authoring document without mutating the input."""
    raw = dict(value) if isinstance(value, Mapping) else {}
    calendar_raw = raw.get("calendar", {})
    calendar = dict(calendar_raw) if isinstance(calendar_raw, Mapping) else {}
    months = calendar.get("months")
    if not isinstance(months, list) or not months:
        months = _default_months()
    weekdays = calendar.get("weekdays")
    if not isinstance(weekdays, list) or not weekdays:
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    normalized_calendar = {
        "calendar_id": str(calendar.get("calendar_id", "standard_calendar")),
        "display_name": str(calendar.get("display_name", "Standard Calendar")),
        "epoch_label": str(calendar.get("epoch_label", "Era")),
        "start_year": calendar.get("start_year", 1),
        "start_month_id": str(calendar.get("start_month_id", (months[0] or {}).get("month_id", "month_01"))),
        "start_day": calendar.get("start_day", 1),
        "weekdays": [str(item) for item in weekdays],
        "start_weekday": str(calendar.get("start_weekday", weekdays[0])),
        "months": [dict(item) if isinstance(item, Mapping) else item for item in months],
    }
    result = {
        "enabled": raw.get("enabled", False),
        "calendar": normalized_calendar,
        "seasons": [dict(item) if isinstance(item, Mapping) else item for item in raw.get("seasons", [])] if isinstance(raw.get("seasons", []), list) else raw.get("seasons"),
        "cycles": [dict(item) if isinstance(item, Mapping) else item for item in raw.get("cycles", [])] if isinstance(raw.get("cycles", []), list) else raw.get("cycles"),
        "boundary_events": [dict(item) if isinstance(item, Mapping) else item for item in raw.get("boundary_events", [])] if isinstance(raw.get("boundary_events", []), list) else raw.get("boundary_events"),
        "auto_pause_conditions": [dict(item) if isinstance(item, Mapping) else item for item in raw.get("auto_pause_conditions", [])] if isinstance(raw.get("auto_pause_conditions", []), list) else raw.get("auto_pause_conditions"),
    }
    return result


@dataclass(frozen=True)
class CalendarMonth:
    month_id: str
    display_name: str
    days: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "CalendarMonth":
        return cls(
            month_id=_identifier(value.get("month_id"), "time_system.month_id_invalid", path + ".month_id"),
            display_name=_text(value.get("display_name", value.get("month_id")), "time_system.month_name_invalid", path + ".display_name"),
            days=_integer(value.get("days"), "time_system.month_days_invalid", path + ".days", 1, 3660),
        )


@dataclass(frozen=True)
class CalendarDefinition:
    calendar_id: str
    display_name: str
    epoch_label: str
    start_year: int
    start_month_id: str
    start_day: int
    weekdays: tuple[str, ...]
    start_weekday: str
    months: tuple[CalendarMonth, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str = "time_system.calendar") -> "CalendarDefinition":
        month_values = _sequence(value.get("months", []), "time_system.months_invalid", path + ".months")
        weekday_values = _sequence(value.get("weekdays", []), "time_system.weekdays_invalid", path + ".weekdays")
        if not 1 <= len(month_values) <= MAX_MONTHS or not 1 <= len(weekday_values) <= 32:
            raise TemporalContractError("time_system.calendar_size_invalid", path=path)
        months = tuple(CalendarMonth.from_mapping(_mapping(item, "time_system.month_invalid", f"{path}.months[{index}]"), f"{path}.months[{index}]") for index, item in enumerate(month_values))
        weekdays = tuple(_text(item, "time_system.weekday_invalid", f"{path}.weekdays[{index}]", limit=64) for index, item in enumerate(weekday_values))
        if len({item.month_id for item in months}) != len(months):
            raise TemporalContractError("time_system.month_duplicate", path=path + ".months")
        if len(set(weekdays)) != len(weekdays):
            raise TemporalContractError("time_system.weekday_duplicate", path=path + ".weekdays")
        start_month_id = _identifier(value.get("start_month_id"), "time_system.start_month_invalid", path + ".start_month_id")
        month = next((item for item in months if item.month_id == start_month_id), None)
        if month is None:
            raise TemporalContractError("time_system.start_month_missing", path=path + ".start_month_id")
        start_day = _integer(value.get("start_day", 1), "time_system.start_day_invalid", path + ".start_day", 1, month.days)
        start_weekday = _text(value.get("start_weekday"), "time_system.start_weekday_invalid", path + ".start_weekday", limit=64)
        if start_weekday not in weekdays:
            raise TemporalContractError("time_system.start_weekday_missing", path=path + ".start_weekday")
        return cls(
            calendar_id=_identifier(value.get("calendar_id"), "time_system.calendar_id_invalid", path + ".calendar_id"),
            display_name=_text(value.get("display_name"), "time_system.calendar_name_invalid", path + ".display_name"),
            epoch_label=_text(value.get("epoch_label", "Era"), "time_system.epoch_label_invalid", path + ".epoch_label", required=False, limit=128),
            start_year=_integer(value.get("start_year", 1), "time_system.start_year_invalid", path + ".start_year", -1000000, 1000000),
            start_month_id=start_month_id,
            start_day=start_day,
            weekdays=weekdays,
            start_weekday=start_weekday,
            months=months,
        )

    @property
    def year_days(self) -> int:
        return sum(item.days for item in self.months)

    @property
    def start_ordinal(self) -> int:
        total = 0
        for month in self.months:
            if month.month_id == self.start_month_id:
                return total + self.start_day - 1
            total += month.days
        return 0

    def date_for_day(self, day_index: int) -> dict[str, Any]:
        absolute = self.start_ordinal + int(day_index)
        year_offset, ordinal_zero = divmod(absolute, self.year_days)
        day_of_year = ordinal_zero + 1
        remaining = ordinal_zero
        month_index = 0
        for index, month in enumerate(self.months):
            month_index = index
            if remaining < month.days:
                break
            remaining -= month.days
        month = self.months[month_index]
        weekday_start = self.weekdays.index(self.start_weekday)
        weekday_index = (weekday_start + int(day_index)) % len(self.weekdays)
        return {
            "calendar_id": self.calendar_id,
            "display_name": self.display_name,
            "epoch_label": self.epoch_label,
            "year": self.start_year + year_offset,
            "month_id": month.month_id,
            "month_name": month.display_name,
            "month_index": month_index,
            "day": remaining + 1,
            "day_of_year": day_of_year,
            "year_days": self.year_days,
            "weekday": self.weekdays[weekday_index],
            "weekday_index": weekday_index,
            "day_index": int(day_index),
        }


@dataclass(frozen=True)
class SeasonDefinition:
    season_id: str
    display_name: str
    start_day: int
    end_day: int
    description: str = ""
    color: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str, year_days: int) -> "SeasonDefinition":
        return cls(
            season_id=_identifier(value.get("season_id"), "time_system.season_id_invalid", path + ".season_id"),
            display_name=_text(value.get("display_name", value.get("season_id")), "time_system.season_name_invalid", path + ".display_name"),
            start_day=_integer(value.get("start_day"), "time_system.season_start_invalid", path + ".start_day", 1, year_days),
            end_day=_integer(value.get("end_day"), "time_system.season_end_invalid", path + ".end_day", 1, year_days),
            description=_text(value.get("description", ""), "time_system.description_invalid", path + ".description", required=False, limit=4000),
            color=_text(value.get("color", ""), "time_system.color_invalid", path + ".color", required=False, limit=64),
        )

    def contains(self, day_of_year: int) -> bool:
        if self.start_day <= self.end_day:
            return self.start_day <= day_of_year <= self.end_day
        return day_of_year >= self.start_day or day_of_year <= self.end_day


@dataclass(frozen=True)
class CycleDefinition:
    cycle_id: str
    display_name: str
    kind: str
    period_days: int
    duration_days: int
    offset_days: int = 0
    description: str = ""
    color: str = ""
    enabled: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "CycleDefinition":
        kind = str(value.get("kind", "custom")).strip()
        if kind not in CYCLE_KINDS:
            raise TemporalContractError("time_system.cycle_kind_invalid", path=path + ".kind")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TemporalContractError("time_system.enabled_invalid", path=path + ".enabled")
        period = _integer(value.get("period_days"), "time_system.cycle_period_invalid", path + ".period_days", 1, 1000000)
        duration = _integer(value.get("duration_days", period), "time_system.cycle_duration_invalid", path + ".duration_days", 1, period)
        return cls(
            cycle_id=_identifier(value.get("cycle_id"), "time_system.cycle_id_invalid", path + ".cycle_id"),
            display_name=_text(value.get("display_name", value.get("cycle_id")), "time_system.cycle_name_invalid", path + ".display_name"),
            kind=kind,
            period_days=period,
            duration_days=duration,
            offset_days=_integer(value.get("offset_days", 0), "time_system.cycle_offset_invalid", path + ".offset_days", 0, 1000000),
            description=_text(value.get("description", ""), "time_system.description_invalid", path + ".description", required=False, limit=4000),
            color=_text(value.get("color", ""), "time_system.color_invalid", path + ".color", required=False, limit=64),
            enabled=enabled,
        )


@dataclass(frozen=True)
class BoundaryEventDefinition:
    boundary_event_id: str
    trigger: str
    event_type: str
    severity: int
    target_agents: tuple[str, ...] = ()
    cycle_id: str = ""
    season_id: str = ""
    month_id: str = ""
    day: int | None = None
    issue_type_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "BoundaryEventDefinition":
        trigger = str(value.get("trigger", "day_start")).strip()
        if trigger not in BOUNDARY_TRIGGERS:
            raise TemporalContractError("time_system.trigger_invalid", path=path + ".trigger")
        targets = _sequence(value.get("target_agents", []), "time_system.targets_invalid", path + ".target_agents")
        payload = _mapping(value.get("payload", {}), "time_system.payload_invalid", path + ".payload")
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool) or len(targets) > 256 or len(payload) > 128:
            raise TemporalContractError("time_system.boundary_event_invalid", path=path)
        raw_day = value.get("day")
        day = None if raw_day in (None, "") else _integer(raw_day, "time_system.day_filter_invalid", path + ".day", 1, 3660)
        return cls(
            boundary_event_id=_identifier(value.get("boundary_event_id"), "time_system.boundary_event_id_invalid", path + ".boundary_event_id"),
            trigger=trigger,
            cycle_id=_identifier(value.get("cycle_id", ""), "time_system.reference_invalid", path + ".cycle_id", required=False),
            season_id=_identifier(value.get("season_id", ""), "time_system.reference_invalid", path + ".season_id", required=False),
            month_id=_identifier(value.get("month_id", ""), "time_system.reference_invalid", path + ".month_id", required=False),
            day=day,
            event_type=_identifier(value.get("event_type"), "time_system.event_type_invalid", path + ".event_type"),
            severity=_integer(value.get("severity", 5), "time_system.severity_invalid", path + ".severity", 1, 10),
            target_agents=tuple(_reference(item, "time_system.target_invalid", f"{path}.target_agents[{index}]") for index, item in enumerate(targets)),
            issue_type_id=_identifier(value.get("issue_type_id", ""), "time_system.reference_invalid", path + ".issue_type_id", required=False),
            payload=copy.deepcopy(dict(payload)),
            enabled=enabled,
        )


@dataclass(frozen=True)
class AutoPauseDefinition:
    pause_id: str
    display_name: str
    trigger: str
    reason: str
    mode: str = "all"
    conditions: tuple[CausalCondition, ...] = ()
    cycle_id: str = ""
    season_id: str = ""
    month_id: str = ""
    day: int | None = None
    cooldown_days: float = 0.0
    once: bool = False
    edge_triggered: bool = True
    enabled: bool = True

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], path: str) -> "AutoPauseDefinition":
        trigger = str(value.get("trigger", "condition")).strip()
        if trigger != "condition" and trigger not in BOUNDARY_TRIGGERS:
            raise TemporalContractError("time_system.trigger_invalid", path=path + ".trigger")
        mode = str(value.get("mode", "all")).strip()
        if mode not in {"all", "any"}:
            raise TemporalContractError("time_system.pause_mode_invalid", path=path + ".mode")
        condition_values = _sequence(value.get("conditions", []), "time_system.pause_conditions_invalid", path + ".conditions")
        if trigger == "condition" and not condition_values:
            raise TemporalContractError("time_system.pause_conditions_required", path=path + ".conditions")
        conditions_list: list[CausalCondition] = []
        for index, condition_value in enumerate(condition_values):
            condition_path = f"{path}.conditions[{index}]"
            try:
                conditions_list.append(
                    CausalCondition.from_mapping(
                        _mapping(condition_value, "causal_condition_invalid", condition_path)
                    )
                )
            except CausalEventError as exc:
                raise TemporalContractError(str(exc), path=condition_path) from exc
        conditions = tuple(conditions_list)
        enabled = value.get("enabled", True)
        once = value.get("once", False)
        edge_triggered = value.get("edge_triggered", True)
        if not all(isinstance(item, bool) for item in (enabled, once, edge_triggered)):
            raise TemporalContractError("time_system.enabled_invalid", path=path)
        raw_day = value.get("day")
        return cls(
            pause_id=_identifier(value.get("pause_id"), "time_system.pause_id_invalid", path + ".pause_id"),
            display_name=_text(value.get("display_name", value.get("pause_id")), "time_system.pause_name_invalid", path + ".display_name"),
            trigger=trigger,
            reason=_text(value.get("reason"), "time_system.pause_reason_invalid", path + ".reason", limit=2000),
            mode=mode,
            conditions=conditions,
            cycle_id=_identifier(value.get("cycle_id", ""), "time_system.reference_invalid", path + ".cycle_id", required=False),
            season_id=_identifier(value.get("season_id", ""), "time_system.reference_invalid", path + ".season_id", required=False),
            month_id=_identifier(value.get("month_id", ""), "time_system.reference_invalid", path + ".month_id", required=False),
            day=None if raw_day in (None, "") else _integer(raw_day, "time_system.day_filter_invalid", path + ".day", 1, 3660),
            cooldown_days=_number(value.get("cooldown_days", 0), "time_system.cooldown_invalid", path + ".cooldown_days", 0, 1000000),
            once=once,
            edge_triggered=edge_triggered,
            enabled=enabled,
        )


@dataclass(frozen=True)
class TemporalContract:
    enabled: bool = False
    calendar: CalendarDefinition | None = None
    seasons: tuple[SeasonDefinition, ...] = ()
    cycles: tuple[CycleDefinition, ...] = ()
    boundary_events: tuple[BoundaryEventDefinition, ...] = ()
    auto_pause_conditions: tuple[AutoPauseDefinition, ...] = ()

    @classmethod
    def from_mapping(cls, value: Any) -> "TemporalContract":
        document = normalize_time_system_document(value)
        enabled = document.get("enabled", False)
        if not isinstance(enabled, bool):
            raise TemporalContractError("time_system.enabled_invalid", path="time_system.enabled")
        calendar = CalendarDefinition.from_mapping(_mapping(document["calendar"], "time_system.calendar_invalid", "time_system.calendar"))
        season_values = _sequence(document["seasons"], "time_system.seasons_invalid", "time_system.seasons")
        cycle_values = _sequence(document["cycles"], "time_system.cycles_invalid", "time_system.cycles")
        event_values = _sequence(document["boundary_events"], "time_system.boundary_events_invalid", "time_system.boundary_events")
        pause_values = _sequence(document["auto_pause_conditions"], "time_system.auto_pauses_invalid", "time_system.auto_pause_conditions")
        if len(season_values) > MAX_SEASONS or len(cycle_values) > MAX_CYCLES or len(event_values) > MAX_BOUNDARY_EVENTS or len(pause_values) > MAX_AUTO_PAUSES:
            raise TemporalContractError("time_system.size_limit", path="time_system")
        seasons = tuple(SeasonDefinition.from_mapping(_mapping(item, "time_system.season_invalid", f"time_system.seasons[{index}]"), f"time_system.seasons[{index}]", calendar.year_days) for index, item in enumerate(season_values))
        cycles = tuple(CycleDefinition.from_mapping(_mapping(item, "time_system.cycle_invalid", f"time_system.cycles[{index}]"), f"time_system.cycles[{index}]") for index, item in enumerate(cycle_values))
        events = tuple(BoundaryEventDefinition.from_mapping(_mapping(item, "time_system.boundary_event_invalid", f"time_system.boundary_events[{index}]"), f"time_system.boundary_events[{index}]") for index, item in enumerate(event_values))
        pauses = tuple(AutoPauseDefinition.from_mapping(_mapping(item, "time_system.auto_pause_invalid", f"time_system.auto_pause_conditions[{index}]"), f"time_system.auto_pause_conditions[{index}]") for index, item in enumerate(pause_values))
        for values, attr, code, path in (
            (seasons, "season_id", "time_system.season_duplicate", "time_system.seasons"),
            (cycles, "cycle_id", "time_system.cycle_duplicate", "time_system.cycles"),
            (events, "boundary_event_id", "time_system.boundary_event_duplicate", "time_system.boundary_events"),
            (pauses, "pause_id", "time_system.pause_duplicate", "time_system.auto_pause_conditions"),
        ):
            ids = [getattr(item, attr) for item in values]
            if len(ids) != len(set(ids)):
                raise TemporalContractError(code, path=path)
        for day in range(1, calendar.year_days + 1):
            matches = [item.season_id for item in seasons if item.contains(day)]
            if len(matches) > 1:
                raise TemporalContractError("time_system.season_overlap", path="time_system.seasons")
        season_ids = {item.season_id for item in seasons}
        cycle_ids = {item.cycle_id for item in cycles}
        month_ids = {item.month_id for item in calendar.months}
        for item, path in [*((item, f"time_system.boundary_events[{index}]") for index, item in enumerate(events)), *((item, f"time_system.auto_pause_conditions[{index}]") for index, item in enumerate(pauses))]:
            if item.cycle_id and item.cycle_id not in cycle_ids:
                raise TemporalContractError("time_system.cycle_reference_missing", path=path + ".cycle_id")
            if item.season_id and item.season_id not in season_ids:
                raise TemporalContractError("time_system.season_reference_missing", path=path + ".season_id")
            if item.month_id and item.month_id not in month_ids:
                raise TemporalContractError("time_system.month_reference_missing", path=path + ".month_id")
            if item.day is not None:
                month = next((month for month in calendar.months if month.month_id == item.month_id), None)
                maximum = month.days if month is not None else calendar.year_days
                if item.day > maximum:
                    raise TemporalContractError("time_system.day_filter_invalid", path=path + ".day")
        return cls(enabled=enabled, calendar=calendar, seasons=seasons, cycles=cycles, boundary_events=events, auto_pause_conditions=pauses)

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.calendar is not None)

    def to_dict(self) -> dict[str, Any]:
        calendar = self.calendar
        return {
            "enabled": self.enabled,
            "calendar": {
                **({key: value for key, value in asdict(calendar).items() if key != "months"} if calendar else {}),
                "weekdays": list(calendar.weekdays) if calendar else [],
                "months": [asdict(item) for item in calendar.months] if calendar else [],
            },
            "seasons": [asdict(item) for item in self.seasons],
            "cycles": [asdict(item) for item in self.cycles],
            "boundary_events": [{**asdict(item), "target_agents": list(item.target_agents)} for item in self.boundary_events],
            "auto_pause_conditions": [{**asdict(item), "conditions": [asdict(condition) for condition in item.conditions]} for item in self.auto_pause_conditions],
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def season_for_date(self, date: Mapping[str, Any]) -> SeasonDefinition | None:
        day = int(date.get("day_of_year", 1))
        return next((item for item in self.seasons if item.contains(day)), None)

    def public_state(self, sim_time: float, *, generation: int = 0, receipts: Mapping[str, Any] | None = None, recent_pauses: Sequence[Mapping[str, Any]] = (), auto_pause_reason: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.calendar is None:
            return {"configured": False, "enabled": False, "generation": generation, "fingerprint": self.fingerprint(), "calendar": None, "current_date": None, "current_season": None, "cycles": [], "auto_pause_conditions": [], "recent_auto_pauses": [], "auto_pause": auto_pause_reason}
        day_float = max(0.0, float(sim_time)) / GAME_SECONDS_PER_DAY
        day_index = math.floor(day_float)
        date = self.calendar.date_for_day(day_index)
        date["day_fraction"] = day_float - day_index
        season = self.season_for_date(date)
        cycle_states = [self._cycle_state(item, day_float) for item in self.cycles if item.enabled]
        receipt_map = receipts or {}
        pauses = []
        for item in self.auto_pause_conditions:
            receipt = receipt_map.get(item.pause_id, {}) if isinstance(receipt_map, Mapping) else {}
            pauses.append({
                "pause_id": item.pause_id,
                "display_name": item.display_name,
                "trigger": item.trigger,
                "reason": item.reason,
                "cooldown_days": item.cooldown_days,
                "once": item.once,
                "edge_triggered": item.edge_triggered,
                "enabled": item.enabled,
                "fired_count": int(receipt.get("fired_count", 0) or 0) if isinstance(receipt, Mapping) else 0,
                "last_fired_at": receipt.get("last_fired_at") if isinstance(receipt, Mapping) else None,
            })
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "generation": generation,
            "fingerprint": self.fingerprint(),
            "calendar": {**self.to_dict()["calendar"], "year_days": self.calendar.year_days},
            "current_date": date,
            "current_season": asdict(season) if season else None,
            "cycles": cycle_states,
            "auto_pause_conditions": pauses,
            "recent_auto_pauses": [copy.deepcopy(dict(item)) for item in recent_pauses][-20:],
            "auto_pause": copy.deepcopy(dict(auto_pause_reason)) if isinstance(auto_pause_reason, Mapping) else None,
        }

    @staticmethod
    def _cycle_state(cycle: CycleDefinition, day_float: float) -> dict[str, Any]:
        if day_float < cycle.offset_days:
            occurrence = -1
            start = float(cycle.offset_days)
            end = start + cycle.duration_days
            active = False
            progress = 0.0
            next_boundary = start
            next_trigger = "cycle_start"
        else:
            occurrence = math.floor((day_float - cycle.offset_days) / cycle.period_days)
            start = float(cycle.offset_days + occurrence * cycle.period_days)
            end = start + cycle.duration_days
            active = day_float < end
            progress = min(1.0, max(0.0, (day_float - start) / cycle.duration_days)) if active else 1.0
            next_start = start + cycle.period_days
            if active and end <= next_start:
                next_boundary, next_trigger = end, "cycle_end"
            else:
                next_boundary, next_trigger = next_start, "cycle_start"
        return {
            **asdict(cycle),
            "occurrence": occurrence,
            "active": active,
            "start_day_index": start,
            "end_day_index": end,
            "progress": progress,
            "next_boundary_day_index": next_boundary,
            "next_boundary_sim_time": next_boundary * GAME_SECONDS_PER_DAY,
            "next_boundary_trigger": next_trigger,
        }


def validate_time_system_document(value: Any) -> None:
    TemporalContract.from_mapping(value)


@dataclass(frozen=True)
class TemporalPollResult:
    markers: tuple[dict[str, Any], ...] = ()
    events: tuple[EventObject, ...] = ()
    event_metadata: tuple[dict[str, Any], ...] = ()
    pause_requests: tuple[dict[str, Any], ...] = ()
    truncated: bool = False


class TemporalRuntime:
    """Incrementally emits deterministic time boundaries and pause requests."""

    def __init__(self, contract: TemporalContract | None = None, *, generation: int = 0) -> None:
        self.contract = contract or TemporalContract.from_mapping({})
        self.generation = int(generation)
        self.last_polled_time: float | None = None
        self._pause_receipts: dict[str, dict[str, Any]] = {}
        self._recent_pauses: list[dict[str, Any]] = []

    def configure(self, contract: TemporalContract, *, generation: int, sim_time: float = 0.0, preserve_state: bool = True) -> None:
        previous = self._pause_receipts if preserve_state else {}
        self.contract = contract
        self.generation = int(generation)
        self._pause_receipts = {
            item.pause_id: copy.deepcopy(previous[item.pause_id])
            for item in contract.auto_pause_conditions if item.pause_id in previous
        }
        self._recent_pauses = self._recent_pauses[-50:] if preserve_state else []
        self.last_polled_time = max(0.0, float(sim_time)) if preserve_state else None

    @property
    def pause_receipts(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._pause_receipts)

    def public_state(self, sim_time: float, *, auto_pause_reason: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.contract.public_state(sim_time, generation=self.generation, receipts=self._pause_receipts, recent_pauses=self._recent_pauses, auto_pause_reason=auto_pause_reason)

    def poll(self, sim_time: float, *, condition_context: CausalConditionContext | None = None, max_markers: int = MAX_MARKERS_PER_POLL) -> TemporalPollResult:
        now = max(0.0, float(sim_time))
        if not math.isfinite(now):
            raise TemporalContractError("time_system.sim_time_invalid", path="sim_time")
        if self.last_polled_time is not None and now < self.last_polled_time:
            # A stale caller must not rewind the boundary cursor and cause old markers
            # to be emitted again on the next forward poll. Save loading restores the
            # cursor explicitly through ``restore`` instead.
            return TemporalPollResult()
        if not self.contract.configured or self.contract.calendar is None:
            self.last_polled_time = now
            return TemporalPollResult()
        markers, truncated, processed_time = self._collect_markers(now, max_markers=max_markers)
        events: list[EventObject] = []
        metadata: list[dict[str, Any]] = []
        for marker in markers:
            for definition in self.contract.boundary_events:
                if not definition.enabled or not self._matches(definition, marker):
                    continue
                marker_identity = json.dumps(marker, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(
                    f"{self.generation}|{definition.boundary_event_id}|{marker_identity}".encode("utf-8")
                ).hexdigest()[:20]
                payload = copy.deepcopy(definition.payload)
                payload.update({
                    "temporal_boundary": copy.deepcopy(marker),
                    "boundary_event_id": definition.boundary_event_id,
                    "source_kind": "system",
                    "source_id": "time_system",
                })
                if definition.issue_type_id:
                    payload["issue_type_id"] = definition.issue_type_id
                event = EventObject(
                    event_id=f"evt_time_{digest}",
                    event_type=definition.event_type,
                    severity=definition.severity,
                    target_agents=list(definition.target_agents),
                    initial_payload=payload,
                    generated_at=float(marker["sim_time"]),
                )
                events.append(event)
                metadata.append({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "severity": event.severity,
                    "target_count": len(event.target_agents),
                    "generated_at": event.generated_at,
                    "source": "time_system",
                    "boundary_event_id": definition.boundary_event_id,
                    "trigger": marker["trigger"],
                    "issue_type_id": definition.issue_type_id,
                    "marker": copy.deepcopy(marker),
                })
        context = condition_context or CausalConditionContext(sim_time=now)
        context = CausalConditionContext(
            world_state=context.world_state,
            actor_manager=context.actor_manager,
            objective_manager=context.objective_manager,
            event_history=context.event_history,
            current_event=context.current_event,
            sim_time=now,
            knowledge_provider=context.knowledge_provider,
            issue_manager=context.issue_manager,
            action_catalog=context.action_catalog,
            derived_metric_provider=context.derived_metric_provider,
        )
        requests: list[dict[str, Any]] = []
        for definition in self.contract.auto_pause_conditions:
            if not definition.enabled:
                continue
            if definition.trigger == "condition":
                truth = self._conditions_match(definition, context)
                receipt = self._pause_receipts.setdefault(definition.pause_id, {"last_fired_at": None, "fired_count": 0, "latch": False})
                prior = bool(receipt.get("latch", False))
                receipt["latch"] = truth
                if truth and (not definition.edge_triggered or not prior) and self._can_fire(definition, receipt, now):
                    requests.append(self._fire_pause(definition, receipt, now, None))
            else:
                for marker in markers:
                    if not self._matches(definition, marker) or not self._conditions_match(definition, context):
                        continue
                    receipt = self._pause_receipts.setdefault(definition.pause_id, {"last_fired_at": None, "fired_count": 0, "latch": False})
                    fired_at = float(marker["sim_time"])
                    if self._can_fire(definition, receipt, fired_at):
                        requests.append(self._fire_pause(definition, receipt, fired_at, marker))
        self.last_polled_time = processed_time
        return TemporalPollResult(tuple(markers), tuple(events), tuple(metadata), tuple(requests), truncated)

    def _collect_markers(self, now: float, *, max_markers: int) -> tuple[list[dict[str, Any]], bool, float]:
        calendar = self.contract.calendar
        assert calendar is not None
        current_day = math.floor(now / GAME_SECONDS_PER_DAY)
        if self.last_polled_time is None:
            start_day = 0
        else:
            start_day = math.floor(self.last_polled_time / GAME_SECONDS_PER_DAY) + 1
        markers: list[dict[str, Any]] = []
        marker_limit = max(1, int(max_markers))
        processed_time = now if start_day > current_day else (self.last_polled_time or 0.0)
        for day_index in range(start_day, current_day + 1):
            boundary_time = float(day_index * GAME_SECONDS_PER_DAY)
            day_markers = self._markers_for_day(day_index, boundary_time)
            if markers and len(markers) + len(day_markers) > marker_limit:
                return markers, True, processed_time
            # A day is the smallest resumable unit because snapshots persist a time
            # cursor rather than a marker offset. Keep dense days atomic so truncation
            # can never silently discard the tail of that day's boundaries. The schema
            # caps one day well below MAX_MARKERS_PER_POLL in normal operation.
            markers.extend(day_markers)
            processed_time = boundary_time
            if len(markers) >= marker_limit and day_index < current_day:
                return markers, True, processed_time
        return markers, False, now

    def _markers_for_day(self, day_index: int, sim_time: float) -> list[dict[str, Any]]:
        calendar = self.contract.calendar
        assert calendar is not None
        date = calendar.date_for_day(day_index)
        previous_date = calendar.date_for_day(day_index - 1) if day_index > 0 else None
        current_season = self.contract.season_for_date(date)
        previous_season = self.contract.season_for_date(previous_date) if previous_date else None
        values: list[dict[str, Any]] = []
        def add(trigger: str, **extra: Any) -> None:
            values.append({"trigger": trigger, "sim_time": sim_time, "day_index": day_index, "date": copy.deepcopy(date), **extra})
        for cycle in self.contract.cycles:
            if not cycle.enabled:
                continue
            if day_index >= cycle.offset_days + cycle.duration_days and (day_index - cycle.offset_days - cycle.duration_days) % cycle.period_days == 0:
                occurrence = (day_index - cycle.offset_days - cycle.duration_days) // cycle.period_days
                add("cycle_end", cycle_id=cycle.cycle_id, cycle_kind=cycle.kind, occurrence=occurrence)
        if previous_season is not None and (current_season is None or current_season.season_id != previous_season.season_id):
            add("season_end", season_id=previous_season.season_id)
        if date["day_of_year"] == 1:
            add("year_start")
        if date["day"] == 1:
            add("month_start", month_id=date["month_id"])
        if date["weekday_index"] == 0:
            add("week_start")
        add("day_start")
        if current_season is not None and (previous_season is None or previous_season.season_id != current_season.season_id):
            add("season_start", season_id=current_season.season_id)
        for cycle in self.contract.cycles:
            if cycle.enabled and day_index >= cycle.offset_days and (day_index - cycle.offset_days) % cycle.period_days == 0:
                occurrence = (day_index - cycle.offset_days) // cycle.period_days
                add("cycle_start", cycle_id=cycle.cycle_id, cycle_kind=cycle.kind, occurrence=occurrence)
        values.sort(key=lambda item: (_TRIGGER_ORDER[item["trigger"]], str(item.get("cycle_id", item.get("season_id", ""))), int(item.get("occurrence", -1))))
        return values

    @staticmethod
    def _matches(definition: Any, marker: Mapping[str, Any]) -> bool:
        if definition.trigger != marker.get("trigger"):
            return False
        date = marker.get("date", {})
        return not (
            (definition.cycle_id and definition.cycle_id != marker.get("cycle_id"))
            or (definition.season_id and definition.season_id != marker.get("season_id"))
            or (definition.month_id and definition.month_id != date.get("month_id"))
            or (definition.day is not None and definition.day not in {date.get("day"), date.get("day_of_year")})
        )

    @staticmethod
    def _conditions_match(definition: AutoPauseDefinition, context: CausalConditionContext) -> bool:
        if not definition.conditions:
            return True
        results = [compare_condition_value(resolve_condition_value(condition, context), condition.operator, condition.value) for condition in definition.conditions]
        return all(results) if definition.mode == "all" else any(results)

    @staticmethod
    def _can_fire(definition: AutoPauseDefinition, receipt: Mapping[str, Any], fired_at: float) -> bool:
        if definition.once and int(receipt.get("fired_count", 0) or 0) > 0:
            return False
        previous = receipt.get("last_fired_at")
        if previous is None:
            return True
        return fired_at - float(previous) >= definition.cooldown_days * GAME_SECONDS_PER_DAY - 1e-9

    def _fire_pause(self, definition: AutoPauseDefinition, receipt: dict[str, Any], fired_at: float, marker: Mapping[str, Any] | None) -> dict[str, Any]:
        receipt["last_fired_at"] = fired_at
        receipt["fired_count"] = int(receipt.get("fired_count", 0) or 0) + 1
        request = {
            "pause_id": definition.pause_id,
            "display_name": definition.display_name,
            "reason": definition.reason,
            "trigger": definition.trigger,
            "fired_at": fired_at,
            "fired_count": receipt["fired_count"],
            "marker": copy.deepcopy(dict(marker)) if marker is not None else None,
        }
        self._recent_pauses.append(copy.deepcopy(request))
        self._recent_pauses = self._recent_pauses[-50:]
        return request

    def snapshot(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "fingerprint": self.contract.fingerprint(),
            "last_polled_time": self.last_polled_time,
            "pause_receipts": copy.deepcopy(self._pause_receipts),
            "recent_pauses": copy.deepcopy(self._recent_pauses[-50:]),
        }

    def restore(self, value: Any, *, sim_time: float) -> None:
        raw = _mapping(value, "time_system.snapshot_invalid", "time_system_runtime")
        last = raw.get("last_polled_time")
        if last is not None:
            last = _number(last, "time_system.snapshot_invalid", "time_system_runtime.last_polled_time", 0, max(float(sim_time), 0.0))
        receipt_values = _mapping(raw.get("pause_receipts", {}), "time_system.snapshot_invalid", "time_system_runtime.pause_receipts")
        known = {item.pause_id for item in self.contract.auto_pause_conditions}
        receipts: dict[str, dict[str, Any]] = {}
        for pause_id, receipt_value in receipt_values.items():
            if pause_id not in known:
                continue
            receipt = _mapping(receipt_value, "time_system.snapshot_invalid", f"time_system_runtime.pause_receipts.{pause_id}")
            fired_count = _integer(receipt.get("fired_count", 0), "time_system.snapshot_invalid", f"time_system_runtime.pause_receipts.{pause_id}.fired_count", 0, 1000000000)
            last_fired = receipt.get("last_fired_at")
            if last_fired is not None:
                last_fired = _number(last_fired, "time_system.snapshot_invalid", f"time_system_runtime.pause_receipts.{pause_id}.last_fired_at", 0, max(float(sim_time), 0.0))
            receipts[pause_id] = {"last_fired_at": last_fired, "fired_count": fired_count, "latch": bool(receipt.get("latch", False))}
        recent_values = _sequence(raw.get("recent_pauses", []), "time_system.snapshot_invalid", "time_system_runtime.recent_pauses")
        recent = [copy.deepcopy(dict(_mapping(item, "time_system.snapshot_invalid", "time_system_runtime.recent_pauses"))) for item in recent_values[-50:]]
        self.last_polled_time = last if last is not None else max(0.0, float(sim_time))
        self._pause_receipts = receipts
        self._recent_pauses = recent
