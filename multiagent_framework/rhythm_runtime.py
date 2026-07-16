"""Deterministic runtime for DLC-authored pacing and issue rhythm."""
from __future__ import annotations

import copy
import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .causal_conditions import (
    CausalConditionContext,
    compare_condition_value,
    resolve_condition_value,
)
from .rhythm_contract import DAY, RhythmContract, RhythmRule, URGENCIES

ACTIVE_STATUSES = {"open", "monitoring", "overdue"}
FINAL_STATUSES = {"resolved", "closed", "cancelled"}
URGENCY_RANK = {name: index for index, name in enumerate(URGENCIES)}


class RhythmRuntimeError(ValueError):
    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(code)


@dataclass
class RhythmPollResult:
    activated: list[dict[str, Any]] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    queue_count: int = 0
    active_count: int = 0
    attention_available: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "activated": copy.deepcopy(self.activated),
            "transitions": copy.deepcopy(self.transitions),
            "feedback": copy.deepcopy(self.feedback),
            "activated_count": len(self.activated),
            "transition_count": len(self.transitions),
            "queue_count": self.queue_count,
            "active_count": self.active_count,
            "attention_available": self.attention_available,
        }


class RhythmRuntime:
    """Paces issue admission, event emission, escalation, and climax closure."""

    def __init__(self, contract: RhythmContract | None = None, *, generation: int = 0) -> None:
        self._lock = threading.RLock()
        self.contract = contract or RhythmContract()
        self.generation = max(0, int(generation))
        self.attention_available = float(self.contract.attention_budget)
        self.last_attention_at = 0.0
        self.quiet_until = 0.0
        self._last_admitted: dict[str, float] = {}
        self._last_event: dict[str, float] = {}
        self._queue: deque[str] = deque()
        self._issue_states: dict[str, dict[str, Any]] = {}
        self._feedback: deque[dict[str, Any]] = deque(maxlen=self.contract.recent_feedback_limit)
        self._suppressed_events: deque[dict[str, Any]] = deque(maxlen=self.contract.recent_feedback_limit)

    def configure(
        self,
        contract: RhythmContract,
        *,
        generation: int,
        sim_time: float = 0.0,
        preserve_state: bool = True,
    ) -> None:
        with self._lock:
            old_rules = {rule.rule_id for rule in self.contract.rules}
            new_rules = {rule.rule_id for rule in contract.rules}
            self.contract = contract
            self.generation = max(0, int(generation))
            now = max(0.0, float(sim_time))
            if preserve_state:
                self._recover(now)
                self.attention_available = min(float(contract.attention_budget), self.attention_available)
                self._last_admitted = {key: value for key, value in self._last_admitted.items() if key in new_rules}
                self._last_event = {key: value for key, value in self._last_event.items() if key in new_rules}
                self._issue_states = {
                    issue_id: state for issue_id, state in self._issue_states.items()
                    if not state.get("rule_id") or state.get("rule_id") in new_rules
                }
                self._queue = deque(issue_id for issue_id in self._queue if issue_id in self._issue_states)
            else:
                self.attention_available = float(contract.attention_budget)
                self.quiet_until = 0.0
                self._last_admitted.clear()
                self._last_event.clear()
                self._queue.clear()
                self._issue_states.clear()
            self.last_attention_at = now
            self._feedback = deque(self._feedback, maxlen=contract.recent_feedback_limit)
            self._suppressed_events = deque(self._suppressed_events, maxlen=contract.recent_feedback_limit)
            if old_rules != new_rules:
                self._feedback.append({
                    "kind": "configuration",
                    "sim_time": now,
                    "generation": self.generation,
                    "message": "Rhythm configuration activated.",
                })

    @staticmethod
    def _rule_map(contract: RhythmContract) -> dict[str, RhythmRule]:
        return {rule.rule_id: rule for rule in contract.rules}

    def _recover(self, sim_time: float) -> None:
        now = max(0.0, float(sim_time))
        if now <= self.last_attention_at:
            return
        elapsed_days = (now - self.last_attention_at) / DAY
        recovered = elapsed_days * self.contract.attention_recovery_per_day
        self.attention_available = min(
            float(self.contract.attention_budget),
            self.attention_available + recovered,
        )
        self.last_attention_at = now

    def _cost(self, rule: RhythmRule | None, urgency: str) -> float:
        if rule is not None and rule.attention_cost is not None:
            return float(rule.attention_cost)
        return float(self.contract.urgency_costs.get(urgency, self.contract.urgency_costs.get("normal", 0.0)))

    @staticmethod
    def _records(issue_manager: Any) -> list[dict[str, Any]]:
        getter = getattr(issue_manager, "rhythm_records", None)
        return list(getter()) if callable(getter) else []

    @staticmethod
    def _rhythm_meta(record: Mapping[str, Any]) -> Mapping[str, Any]:
        value = record.get("rhythm", {})
        return value if isinstance(value, Mapping) else {}

    def _can_admit(
        self,
        *,
        rule: RhythmRule | None,
        urgency: str,
        cost: float,
        sim_time: float,
        issue_manager: Any,
        exclude_issue_id: str = "",
    ) -> tuple[bool, str]:
        records = [
            record for record in self._records(issue_manager)
            if str(record.get("issue_id", "")) != exclude_issue_id
            and str(record.get("status", "")) in ACTIVE_STATUSES
        ]
        if len(records) >= self.contract.max_active_issues:
            return False, "active_issue_limit"
        quiet = sim_time < self.quiet_until
        if quiet and not (rule and rule.allow_during_quiet) and not (
            urgency == "critical" and self.contract.critical_bypasses_quiet
        ):
            return False, "quiet_period"
        if rule is not None:
            same_rule = sum(1 for record in records if self._rhythm_meta(record).get("rule_id") == rule.rule_id)
            if same_rule >= rule.max_active:
                return False, "rule_active_limit"
            if rule.mutual_exclusion_group:
                for record in records:
                    meta = self._rhythm_meta(record)
                    active_rule = self._rule_map(self.contract).get(str(meta.get("rule_id", "")))
                    if active_rule and active_rule.mutual_exclusion_group == rule.mutual_exclusion_group:
                        return False, "mutual_exclusion"
            admitted_at = self._last_admitted.get(rule.rule_id)
            if admitted_at is not None and sim_time < admitted_at + rule.cooldown_days * DAY:
                return False, "cooldown"
        if cost > self.attention_available + 1e-9:
            return False, "attention_budget"
        return True, ""

    def prepare_issue(
        self,
        *,
        issue_type_id: str,
        urgency: str,
        event_type: str,
        payload: Mapping[str, Any],
        sim_time: float,
        issue_manager: Any,
    ) -> dict[str, Any]:
        with self._lock:
            if not self.contract.enabled:
                return {"managed": False, "queued": False}
            self._recover(sim_time)
            rule = self.contract.rule_for_issue(issue_type_id, event_type)
            cost = self._cost(rule, urgency)
            admitted, reason = self._can_admit(
                rule=rule, urgency=urgency, cost=cost, sim_time=sim_time,
                issue_manager=issue_manager,
            )
            if not admitted and len(self._queue) >= self.contract.queue_limit:
                return {
                    "managed": True, "queued": False, "reject": True,
                    "reason": "queue_limit", "rule_id": rule.rule_id if rule else "",
                    "attention_cost": cost,
                }
            return {
                "managed": True,
                "queued": not admitted,
                "reason": reason,
                "rule_id": rule.rule_id if rule else "",
                "attention_cost": cost,
                "level": 0,
                "priority": rule.priority if rule else 0,
            }

    def record_issue(self, issue: Mapping[str, Any], *, sim_time: float) -> None:
        with self._lock:
            rhythm = self._rhythm_meta(issue)
            if not rhythm.get("managed"):
                return
            issue_id = str(issue.get("issue_id", ""))
            if not issue_id or issue_id in self._issue_states:
                return
            rule_id = str(rhythm.get("rule_id", ""))
            cost = float(rhythm.get("attention_cost", 0.0) or 0.0)
            queued = str(issue.get("status", "")) == "queued"
            self._issue_states[issue_id] = {
                "issue_id": issue_id,
                "rule_id": rule_id,
                "level": int(rhythm.get("level", 0) or 0),
                "attention_cost": cost,
                "queued_at": float(sim_time) if queued else None,
                "admitted_at": None if queued else float(sim_time),
                "last_transition_at": float(sim_time),
                "completed_at": None,
            }
            if queued:
                self._queue.append(issue_id)
                self._append_feedback(
                    "queued", issue_id, rule_id, sim_time,
                    f"Issue queued by rhythm director: {rhythm.get('queue_reason', 'pacing')}.",
                    {"reason": rhythm.get("queue_reason", "")},
                )
            else:
                self.attention_available = max(0.0, self.attention_available - cost)
                if rule_id:
                    self._last_admitted[rule_id] = float(sim_time)
                self._append_feedback(
                    "admitted", issue_id, rule_id, sim_time,
                    "Issue admitted to the active attention window.",
                    {"attention_cost": cost},
                )

    def allow_event(self, event_type: str, *, sim_time: float, issue_manager: Any) -> dict[str, Any]:
        with self._lock:
            if not self.contract.enabled:
                return {"allowed": True, "managed": False}
            rule = self.contract.rule_for_event(str(event_type))
            if rule is None:
                return {"allowed": True, "managed": False}
            self._recover(sim_time)
            reason = ""
            if sim_time < self.quiet_until and not rule.allow_during_quiet:
                reason = "quiet_period"
            elif rule.cooldown_days and rule.rule_id in self._last_event and sim_time < self._last_event[rule.rule_id] + rule.cooldown_days * DAY:
                reason = "cooldown"
            elif rule.mutual_exclusion_group:
                for record in self._records(issue_manager):
                    if str(record.get("status", "")) not in ACTIVE_STATUSES:
                        continue
                    active_rule = self._rule_map(self.contract).get(str(self._rhythm_meta(record).get("rule_id", "")))
                    if active_rule and active_rule.mutual_exclusion_group == rule.mutual_exclusion_group:
                        reason = "mutual_exclusion"
                        break
            allowed = not reason
            if allowed:
                self._last_event[rule.rule_id] = float(sim_time)
            else:
                row = {
                    "event_type": str(event_type), "rule_id": rule.rule_id,
                    "reason": reason, "sim_time": float(sim_time),
                }
                self._suppressed_events.append(row)
                self._append_feedback(
                    "event_suppressed", "", rule.rule_id, sim_time,
                    f"Event suppressed by rhythm director: {reason}.", row,
                )
            return {"allowed": allowed, "managed": True, "rule_id": rule.rule_id, "reason": reason}

    @staticmethod
    def _conditions_match(conditions: Sequence[Any], context: CausalConditionContext | None) -> bool:
        if not conditions:
            return True
        if context is None:
            return False
        return all(
            compare_condition_value(
                resolve_condition_value(condition, context),
                condition.operator,
                condition.value,
            )
            for condition in conditions
        )

    def _append_feedback(
        self,
        kind: str,
        issue_id: str,
        rule_id: str,
        sim_time: float,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "kind": kind,
            "issue_id": issue_id,
            "rule_id": rule_id,
            "sim_time": float(sim_time),
            "message": str(message),
            "details": copy.deepcopy(dict(details or {})),
        }
        self._feedback.append(row)
        return row

    def _set_quiet(self, sim_time: float, days: float) -> None:
        duration = max(float(self.contract.quiet_period_days), float(days))
        if duration > 0:
            self.quiet_until = max(self.quiet_until, float(sim_time) + duration * DAY)

    def _activate_queued(self, issue_manager: Any, sim_time: float, result: RhythmPollResult) -> None:
        rules = self._rule_map(self.contract)
        records = {str(item.get("issue_id", "")): item for item in self._records(issue_manager)}
        ordered = sorted(
            (issue_id for issue_id in self._queue if issue_id in records),
            key=lambda issue_id: (
                -int(self._rhythm_meta(records[issue_id]).get("priority", 0) or 0),
                -URGENCY_RANK.get(str(records[issue_id].get("urgency", "normal")), 1),
                float(records[issue_id].get("created_at", 0.0) or 0.0),
                issue_id,
            ),
        )
        remaining: deque[str] = deque()
        for issue_id in ordered:
            record = records[issue_id]
            if str(record.get("status", "")) != "queued":
                continue
            state = self._issue_states.get(issue_id)
            if state is None:
                continue
            rule = rules.get(str(state.get("rule_id", "")))
            cost = float(state.get("attention_cost", 0.0) or 0.0)
            admitted, reason = self._can_admit(
                rule=rule,
                urgency=str(record.get("urgency", "normal")),
                cost=cost,
                sim_time=sim_time,
                issue_manager=issue_manager,
                exclude_issue_id=issue_id,
            )
            if not admitted:
                updater = getattr(issue_manager, "rhythm_update_issue", None)
                if callable(updater):
                    updater(issue_id, sim_time=sim_time, queue_reason=reason)
                remaining.append(issue_id)
                continue
            updater = getattr(issue_manager, "rhythm_update_issue", None)
            if not callable(updater):
                remaining.append(issue_id)
                continue
            updated = updater(issue_id, sim_time=sim_time, status="open", queue_reason="", level=int(state.get("level", 0)))
            state["queued_at"] = None
            state["admitted_at"] = float(sim_time)
            state["last_transition_at"] = float(sim_time)
            self.attention_available = max(0.0, self.attention_available - cost)
            if rule is not None:
                self._last_admitted[rule.rule_id] = float(sim_time)
            feedback = self._append_feedback(
                "activated", issue_id, rule.rule_id if rule else "", sim_time,
                "Queued issue entered the active attention window.",
                {"attention_cost": cost},
            )
            result.activated.append(copy.deepcopy(updated))
            result.feedback.append(copy.deepcopy(feedback))
        self._queue = remaining

    def _progress_active(
        self,
        issue_manager: Any,
        sim_time: float,
        context: CausalConditionContext | None,
        result: RhythmPollResult,
    ) -> None:
        rules = self._rule_map(self.contract)
        records = self._records(issue_manager)
        by_id = {str(item.get("issue_id", "")): item for item in records}
        updater = getattr(issue_manager, "rhythm_update_issue", None)
        if not callable(updater):
            return
        for issue_id, state in list(self._issue_states.items()):
            record = by_id.get(issue_id)
            if record is None:
                continue
            status = str(record.get("status", ""))
            rule = rules.get(str(state.get("rule_id", "")))
            if status in FINAL_STATUSES:
                if state.get("completed_at") is None:
                    state["completed_at"] = float(sim_time)
                    if rule is not None:
                        self._set_quiet(sim_time, rule.quiet_period_days)
                continue
            if status not in ACTIVE_STATUSES or rule is None:
                continue
            level = int(state.get("level", 0) or 0)
            transition_raw = state.get("last_transition_at")
            if transition_raw is None:
                transition_raw = state.get("admitted_at")
            last_transition = float(sim_time if transition_raw is None else transition_raw)

            climax = rule.climax
            if (
                climax.enabled and level >= climax.level
                and sim_time >= last_transition + climax.after_days * DAY
                and self._conditions_match(climax.conditions, context)
            ):
                affected: list[str] = [issue_id]
                if climax.close_group and rule.mutual_exclusion_group:
                    affected = []
                    for candidate in records:
                        candidate_id = str(candidate.get("issue_id", ""))
                        if str(candidate.get("status", "")) not in ACTIVE_STATUSES:
                            continue
                        candidate_rule = rules.get(str(self._rhythm_meta(candidate).get("rule_id", "")))
                        if candidate_rule and candidate_rule.mutual_exclusion_group == rule.mutual_exclusion_group:
                            affected.append(candidate_id)
                for candidate_id in affected:
                    updated = updater(
                        candidate_id, sim_time=sim_time,
                        status=climax.issue_status, case_status=climax.case_status,
                        outcome={"kind": "rhythm_climax", "rule_id": rule.rule_id, "message": climax.feedback},
                    )
                    candidate_state = self._issue_states.get(candidate_id)
                    if candidate_state is not None:
                        candidate_state["completed_at"] = float(sim_time)
                        candidate_state["last_transition_at"] = float(sim_time)
                    result.transitions.append({
                        "kind": "climax", "rule_id": rule.rule_id,
                        "issue_id": candidate_id, "issue": copy.deepcopy(updated),
                    })
                self._set_quiet(sim_time, climax.quiet_period_days)
                feedback = self._append_feedback(
                    "climax", issue_id, rule.rule_id, sim_time,
                    climax.feedback or f"{rule.display_name} reached its climax and closed.",
                    {"affected_issue_ids": affected},
                )
                result.feedback.append(copy.deepcopy(feedback))
                continue

            deescalation = rule.deescalation
            if (
                deescalation.enabled and level > deescalation.target_level
                and sim_time >= last_transition + deescalation.interval_days * DAY
                and self._conditions_match(deescalation.conditions, context)
            ):
                new_level = max(deescalation.target_level, level - 1)
                urgency = rule.escalation.urgency_for_level(new_level, str(record.get("urgency", "normal")))
                updated = updater(
                    issue_id, sim_time=sim_time, status=deescalation.issue_status,
                    urgency=urgency, level=new_level,
                )
                state["level"] = new_level
                state["last_transition_at"] = float(sim_time)
                transition = {
                    "kind": "deescalation", "rule_id": rule.rule_id,
                    "issue_id": issue_id, "from_level": level, "to_level": new_level,
                    "issue": copy.deepcopy(updated),
                }
                result.transitions.append(transition)
                feedback = self._append_feedback(
                    "deescalation", issue_id, rule.rule_id, sim_time,
                    deescalation.feedback or f"{rule.display_name} de-escalated to level {new_level}.",
                    {"from_level": level, "to_level": new_level},
                )
                result.feedback.append(copy.deepcopy(feedback))
                continue

            escalation = rule.escalation
            admitted_raw = state.get("admitted_at")
            admitted_at = float(sim_time if admitted_raw is None else admitted_raw)
            due = (
                admitted_at + escalation.after_days * DAY
                if level == 0
                else last_transition + escalation.interval_days * DAY
            )
            if escalation.enabled and level < escalation.max_level and sim_time >= due:
                new_level = level + 1
                urgency = escalation.urgency_for_level(new_level, str(record.get("urgency", "normal")))
                updated = updater(issue_id, sim_time=sim_time, status="open", urgency=urgency, level=new_level)
                state["level"] = new_level
                state["last_transition_at"] = float(sim_time)
                transition = {
                    "kind": "escalation", "rule_id": rule.rule_id,
                    "issue_id": issue_id, "from_level": level, "to_level": new_level,
                    "issue": copy.deepcopy(updated),
                }
                result.transitions.append(transition)
                feedback = self._append_feedback(
                    "escalation", issue_id, rule.rule_id, sim_time,
                    escalation.feedback_for_level(new_level) or f"{rule.display_name} escalated to level {new_level}.",
                    {"from_level": level, "to_level": new_level},
                )
                result.feedback.append(copy.deepcopy(feedback))

    def poll(
        self,
        sim_time: float,
        *,
        issue_manager: Any,
        condition_context: CausalConditionContext | None = None,
    ) -> RhythmPollResult:
        with self._lock:
            result = RhythmPollResult()
            if not self.contract.enabled:
                return result
            self._recover(sim_time)
            self._progress_active(issue_manager, sim_time, condition_context, result)
            self._activate_queued(issue_manager, sim_time, result)
            records = self._records(issue_manager)
            result.queue_count = sum(str(item.get("status", "")) == "queued" for item in records)
            result.active_count = sum(str(item.get("status", "")) in ACTIVE_STATUSES for item in records)
            result.attention_available = round(self.attention_available, 6)
            return result

    def public_state(self, *, issue_manager: Any = None, sim_time: float = 0.0) -> dict[str, Any]:
        with self._lock:
            self._recover(sim_time)
            base = self.contract.public_state(generation=self.generation)
            records = self._records(issue_manager) if issue_manager is not None else []
            active = [item for item in records if str(item.get("status", "")) in ACTIVE_STATUSES]
            queued = [item for item in records if str(item.get("status", "")) == "queued"]
            active_groups: dict[str, int] = {}
            rules = self._rule_map(self.contract)
            for record in active:
                rule = rules.get(str(self._rhythm_meta(record).get("rule_id", "")))
                if rule and rule.mutual_exclusion_group:
                    active_groups[rule.mutual_exclusion_group] = active_groups.get(rule.mutual_exclusion_group, 0) + 1
            return {
                **base,
                "attention_available": round(self.attention_available, 6),
                "attention_used": round(max(0.0, self.contract.attention_budget - self.attention_available), 6),
                "quiet_until": self.quiet_until,
                "quiet_active": float(sim_time) < self.quiet_until,
                "active_issue_count": len(active),
                "queued_issue_count": len(queued),
                "active_groups": active_groups,
                "recent_feedback": copy.deepcopy(list(self._feedback)),
                "recent_suppressed_events": copy.deepcopy(list(self._suppressed_events)),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": "1.0",
                "generation": self.generation,
                "fingerprint": self.contract.fingerprint(),
                "attention_available": self.attention_available,
                "last_attention_at": self.last_attention_at,
                "quiet_until": self.quiet_until,
                "last_admitted": copy.deepcopy(self._last_admitted),
                "last_event": copy.deepcopy(self._last_event),
                "queue": list(self._queue),
                "issue_states": copy.deepcopy(self._issue_states),
                "feedback": copy.deepcopy(list(self._feedback)),
                "suppressed_events": copy.deepcopy(list(self._suppressed_events)),
            }

    def restore(self, value: Any, *, sim_time: float = 0.0) -> None:
        with self._lock:
            if value in (None, {}):
                self.last_attention_at = max(0.0, float(sim_time))
                return
            if not isinstance(value, Mapping):
                raise RhythmRuntimeError("rhythm.snapshot.invalid")
            fingerprint = str(value.get("fingerprint", ""))
            if fingerprint and fingerprint != self.contract.fingerprint():
                self.last_attention_at = max(0.0, float(sim_time))
                return
            try:
                available = float(value.get("attention_available", self.contract.attention_budget))
                last_attention = float(value.get("last_attention_at", sim_time))
                quiet_until = float(value.get("quiet_until", 0.0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise RhythmRuntimeError("rhythm.snapshot.invalid") from exc
            if not all(math.isfinite(item) for item in (available, last_attention, quiet_until)):
                raise RhythmRuntimeError("rhythm.snapshot.invalid")
            self.generation = max(0, int(value.get("generation", self.generation)))
            self.attention_available = min(max(0.0, available), self.contract.attention_budget)
            self.last_attention_at = max(0.0, last_attention)
            self.quiet_until = max(0.0, quiet_until)
            self._last_admitted = {str(key): float(item) for key, item in dict(value.get("last_admitted", {})).items()}
            self._last_event = {str(key): float(item) for key, item in dict(value.get("last_event", {})).items()}
            raw_states = value.get("issue_states", {})
            if not isinstance(raw_states, Mapping):
                raise RhythmRuntimeError("rhythm.snapshot.invalid")
            self._issue_states = copy.deepcopy(dict(raw_states))
            raw_queue = value.get("queue", [])
            if not isinstance(raw_queue, Sequence) or isinstance(raw_queue, (str, bytes, bytearray)):
                raise RhythmRuntimeError("rhythm.snapshot.invalid")
            self._queue = deque(str(item) for item in raw_queue if str(item) in self._issue_states)
            self._feedback = deque(
                copy.deepcopy(list(value.get("feedback", []))[-self.contract.recent_feedback_limit:]),
                maxlen=self.contract.recent_feedback_limit,
            )
            self._suppressed_events = deque(
                copy.deepcopy(list(value.get("suppressed_events", []))[-self.contract.recent_feedback_limit:]),
                maxlen=self.contract.recent_feedback_limit,
            )
            self._recover(max(0.0, float(sim_time)))
