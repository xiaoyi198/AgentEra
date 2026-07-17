"""Deterministic, bounded runtime for player-facing outcome feedback."""
from __future__ import annotations

from collections import deque
import copy
import hashlib
import json
import math
import threading
from typing import Any, Mapping, Sequence

from .causal_conditions import CausalConditionContext, compare_condition_value, resolve_condition_value
from .outcome_feedback_contract import DAY, OutcomeFeedbackContract, OutcomeFeedbackRule

_SAFE_CHANGE_KEYS = {
    "kind", "entity_id", "entity_name", "resource_name", "item_name", "metric_id",
    "display_name", "field", "before", "after", "delta", "direction", "unit",
    "old_status", "new_status", "quantity", "quantity_delta", "base_value_delta",
    "summary", "reason", "source_kind", "source_id",
}
_SAFE_DETAIL_KEYS = {"decision_id", "action_id", "issue_id", "case_id", "event_type", "transition_id", "rule_id"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _safe_text(value: Any, limit: int) -> str:
    result = str(value or "").strip()
    result = "".join(char for char in result if ord(char) >= 32 or char in "\n\t")
    return result[:limit]


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value[:1000] if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return _safe_text(value, 1000)


def _sanitize_mapping(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _safe_scalar(item) for key, item in value.items() if str(key) in allowed}


def _sanitize_changes(values: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    return [row for row in (_sanitize_mapping(item, _SAFE_CHANGE_KEYS) for item in values[:max(0, limit)]) if row]


class OutcomeFeedbackRuntime:
    def __init__(self) -> None:
        self.contract = OutcomeFeedbackContract()
        self.generation = 0
        self._recent: deque[dict[str, Any]] = deque(maxlen=self.contract.recent_limit)
        self._pending: list[dict[str, Any]] = []
        self._receipts: deque[str] = deque(maxlen=self.contract.receipt_limit)
        self._receipt_set: set[str] = set()
        self._last_deliveries: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def configure(
        self, contract: OutcomeFeedbackContract, *, generation: int,
        sim_time: float = 0.0, preserve_state: bool = True,
    ) -> None:
        with self._lock:
            old_pending = list(self._pending) if preserve_state else []
            old_recent = list(self._recent) if preserve_state else []
            old_receipts = list(self._receipts) if preserve_state else []
            rule_map = {rule.rule_id: rule for rule in contract.rules if rule.enabled}
            self.contract = contract
            self.generation = int(generation)
            self._recent = deque(old_recent[-contract.recent_limit:], maxlen=contract.recent_limit)
            self._receipts = deque(old_receipts[-contract.receipt_limit:], maxlen=contract.receipt_limit)
            self._receipt_set = set(self._receipts)
            self._pending = []
            for row in old_pending:
                rule = rule_map.get(str(row.get("rule_id", "")))
                if not rule or str(row.get("rule_fingerprint", "")) != rule.fingerprint():
                    continue
                candidate = copy.deepcopy(row)
                candidate["generation"] = self.generation
                self._pending.append(candidate)
            self._pending.sort(key=lambda row: (_finite(row.get("due_at")), -int(row.get("priority", 0)), str(row.get("feedback_id", ""))))
            self._pending = self._pending[:contract.pending_limit]
            self._last_deliveries = []

    @staticmethod
    def _rule_matches(rule: OutcomeFeedbackRule, source: Mapping[str, Any], metric_changes: Sequence[Mapping[str, Any]]) -> bool:
        source_kind = str(source.get("kind", ""))
        if not rule.enabled or source_kind not in rule.source_kinds:
            return False
        if rule.action_ids and str(source.get("action_id", source.get("id", ""))) not in rule.action_ids:
            return False
        if rule.issue_type_ids and str(source.get("issue_type_id", "")) not in rule.issue_type_ids:
            return False
        if rule.event_types and str(source.get("event_type", "")) not in rule.event_types:
            return False
        if rule.metric_ids:
            changed_ids = {str(item.get("metric_id", "")) for item in metric_changes}
            if not changed_ids.intersection(rule.metric_ids):
                return False
        return True

    @staticmethod
    def _conditions_match(rule: OutcomeFeedbackRule, context: CausalConditionContext | None) -> bool:
        if not rule.conditions:
            return True
        if context is None:
            return False
        return all(
            compare_condition_value(resolve_condition_value(condition, context), condition.operator, condition.value)
            for condition in rule.conditions
        )

    def _remember_receipt(self, receipt: str) -> bool:
        if receipt in self._receipt_set:
            return False
        if len(self._receipts) == self._receipts.maxlen and self._receipts:
            expired = self._receipts[0]
            self._receipt_set.discard(expired)
        self._receipts.append(receipt)
        self._receipt_set.add(receipt)
        return True

    @staticmethod
    def _feedback_id(receipt: str) -> str:
        return "feedback_" + hashlib.sha256(receipt.encode("utf-8")).hexdigest()[:24]

    def _build_record(
        self, *, receipt: str, kind: str, source: Mapping[str, Any], sim_time: float,
        title: str, summary: str, reason: str = "", reveal_text: str = "",
        rule: OutcomeFeedbackRule | None = None, changes: Sequence[Mapping[str, Any]] = (),
        metric_changes: Sequence[Mapping[str, Any]] = (), details: Mapping[str, Any] | None = None,
        due_at: float | None = None,
    ) -> dict[str, Any]:
        source_public = {
            "kind": _safe_text(source.get("kind", ""), 64),
            "id": _safe_text(source.get("id", ""), 128),
            "display_name": _safe_text(source.get("display_name", ""), 240),
        }
        return {
            "feedback_id": self._feedback_id(receipt),
            "kind": kind,
            "generation": self.generation,
            "rule_id": rule.rule_id if rule else "",
            "source": source_public,
            "title": _safe_text(title, 240),
            "summary": _safe_text(summary, 4000),
            "reason": _safe_text(reason, 2000),
            "reveal_text": _safe_text(reveal_text, 4000),
            "changes": copy.deepcopy(list(changes)),
            "metric_changes": copy.deepcopy(list(metric_changes)),
            "details": _sanitize_mapping(details or {}, _SAFE_DETAIL_KEYS),
            "created_at": float(sim_time),
            "due_at": float(due_at if due_at is not None else sim_time),
            "delivered_at": None,
        }

    def _deliver(self, record: Mapping[str, Any], sim_time: float) -> dict[str, Any]:
        row = copy.deepcopy(dict(record))
        row["delivered_at"] = float(sim_time)
        row.pop("rule_fingerprint", None)
        row.pop("priority", None)
        self._recent.append(row)
        self._last_deliveries.append(copy.deepcopy(row))
        return row

    def record(
        self, *, source_kind: str, source_id: str, receipt_id: str, sim_time: float,
        display_name: str = "", action_id: str = "", issue_type_id: str = "",
        event_type: str = "", summary: str = "", reason: str = "",
        changes: Sequence[Mapping[str, Any]] = (), metric_changes: Sequence[Mapping[str, Any]] = (),
        decisions: Sequence[Mapping[str, Any]] = (), details: Mapping[str, Any] | None = None,
        condition_context: CausalConditionContext | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if not self.contract.enabled or source_kind not in {"action", "decision", "case_closed", "causal_event", "lifecycle", "rhythm"}:
                return {"delivered": [], "queued": [], "replayed": False}
            base_receipt = _safe_text(receipt_id, 256) or f"{source_kind}:{source_id}:{sim_time:.6f}"
            source = {
                "kind": source_kind, "id": _safe_text(source_id, 128),
                "display_name": _safe_text(display_name, 240), "action_id": _safe_text(action_id, 128),
                "issue_type_id": _safe_text(issue_type_id, 128), "event_type": _safe_text(event_type, 128),
            }
            command_changes = _sanitize_changes(changes, self.contract.immediate.max_changes)
            metric_rows = _sanitize_changes(metric_changes, self.contract.immediate.max_metric_changes)
            delivered: list[dict[str, Any]] = []
            queued: list[dict[str, Any]] = []

            if source_kind in {"action", "decision"} and self.contract.immediate.enabled:
                receipt = base_receipt + ":builtin:immediate"
                if self._remember_receipt(receipt):
                    record = self._build_record(
                        receipt=receipt, kind="immediate", source=source, sim_time=sim_time,
                        title=display_name or "Immediate outcome",
                        summary=summary or "The action completed and its visible effects are listed below.",
                        reason=reason or display_name,
                        changes=command_changes if self.contract.immediate.include_command_changes else (),
                        metric_changes=metric_rows if self.contract.immediate.include_metric_changes else (),
                        details=details,
                    )
                    delivered.append(self._deliver(record, sim_time))

            if source_kind == "case_closed" and self.contract.case_summaries.enabled:
                receipt = base_receipt + ":builtin:case_summary"
                if self._remember_receipt(receipt):
                    safe_details = dict(details or {})
                    if self.contract.case_summaries.include_decisions:
                        safe_details["decision_count"] = len(decisions) if isinstance(decisions, Sequence) else 0
                    record = self._build_record(
                        receipt=receipt, kind="case_summary", source=source, sim_time=sim_time,
                        title=display_name or "Case closed",
                        summary=summary or "The case has been closed.", reason=reason,
                        changes=command_changes if self.contract.case_summaries.include_changes else (),
                        metric_changes=metric_rows if self.contract.case_summaries.include_metric_changes else (),
                        details=safe_details,
                    )
                    delivered.append(self._deliver(record, sim_time))

            for rule in self.contract.rules:
                if not self._rule_matches(rule, source, metric_rows):
                    continue
                receipt = f"{base_receipt}:rule:{rule.rule_id}:{rule.delivery}"
                if receipt in self._receipt_set:
                    continue
                kind = {"immediate": "immediate", "delayed": "delayed_report", "causal_reveal": "causal_reveal"}[rule.delivery]
                due_at = float(sim_time) + rule.delay_days * DAY
                record = self._build_record(
                    receipt=receipt, kind=kind, source=source, sim_time=sim_time,
                    title=rule.title or rule.display_name,
                    summary=rule.summary or summary,
                    reason=rule.reason or reason,
                    reveal_text=rule.reveal_text,
                    rule=rule, changes=command_changes, metric_changes=metric_rows,
                    details=details, due_at=due_at,
                )
                if rule.delivery == "immediate" and self._conditions_match(rule, condition_context):
                    self._remember_receipt(receipt)
                    delivered.append(self._deliver(record, sim_time))
                    continue
                if len(self._pending) >= self.contract.pending_limit:
                    continue
                self._remember_receipt(receipt)
                record["rule_fingerprint"] = rule.fingerprint()
                record["priority"] = rule.priority
                self._pending.append(record)
                queued.append(copy.deepcopy({key: value for key, value in record.items() if key not in {"rule_fingerprint", "priority"}}))
            self._pending.sort(key=lambda row: (_finite(row.get("due_at")), -int(row.get("priority", 0)), str(row.get("feedback_id", ""))))
            return {"delivered": delivered, "queued": queued, "replayed": not delivered and not queued and any(base_receipt in item for item in self._receipt_set)}

    def poll(self, *, sim_time: float, condition_context: CausalConditionContext | None = None) -> dict[str, Any]:
        with self._lock:
            self._last_deliveries = []
            rule_map = {rule.rule_id: rule for rule in self.contract.rules if rule.enabled}
            retained: list[dict[str, Any]] = []
            for record in self._pending:
                if _finite(record.get("due_at")) > float(sim_time):
                    retained.append(record)
                    continue
                rule = rule_map.get(str(record.get("rule_id", "")))
                if not rule or str(record.get("rule_fingerprint", "")) != rule.fingerprint():
                    continue
                if not self._conditions_match(rule, condition_context):
                    retained.append(record)
                    continue
                self._deliver(record, sim_time)
            self._pending = retained[:self.contract.pending_limit]
            return {"delivered": copy.deepcopy(self._last_deliveries), "pending_count": len(self._pending)}

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            state = self.contract.public_state(generation=self.generation)
            state.update({
                "recent": copy.deepcopy(list(self._recent)),
                "pending": [
                    {
                        "feedback_id": row.get("feedback_id", ""), "kind": row.get("kind", ""),
                        "rule_id": row.get("rule_id", ""), "source": copy.deepcopy(row.get("source", {})),
                        "title": row.get("title", ""), "created_at": row.get("created_at", 0.0),
                        "due_at": row.get("due_at", 0.0),
                    }
                    for row in self._pending
                ],
                "recent_count": len(self._recent), "pending_count": len(self._pending),
            })
            return state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "generation": self.generation,
                "recent": copy.deepcopy(list(self._recent)),
                "pending": copy.deepcopy(self._pending),
                "receipts": list(self._receipts),
            }

    def restore(self, value: Any, *, sim_time: float = 0.0) -> None:
        with self._lock:
            if not isinstance(value, Mapping):
                return
            recent = value.get("recent", [])
            pending = value.get("pending", [])
            receipts = value.get("receipts", [])
            self._recent = deque(
                [copy.deepcopy(dict(item)) for item in recent if isinstance(item, Mapping)][-self.contract.recent_limit:],
                maxlen=self.contract.recent_limit,
            )
            valid_rules = {rule.rule_id: rule for rule in self.contract.rules if rule.enabled}
            self._pending = []
            for item in pending if isinstance(pending, Sequence) else []:
                if not isinstance(item, Mapping):
                    continue
                row = copy.deepcopy(dict(item))
                rule = valid_rules.get(str(row.get("rule_id", "")))
                if rule and str(row.get("rule_fingerprint", "")) == rule.fingerprint():
                    self._pending.append(row)
            self._pending.sort(key=lambda row: (_finite(row.get("due_at")), -int(row.get("priority", 0)), str(row.get("feedback_id", ""))))
            self._pending = self._pending[:self.contract.pending_limit]
            normalized_receipts = [_safe_text(item, 512) for item in receipts if _safe_text(item, 512)] if isinstance(receipts, Sequence) else []
            self._receipts = deque(normalized_receipts[-self.contract.receipt_limit:], maxlen=self.contract.receipt_limit)
            self._receipt_set = set(self._receipts)
            self._last_deliveries = []
