"""Structured long-term Agent memory with deterministic relevance retrieval."""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

MEMORY_KINDS = {"episodic", "semantic", "relationship", "commitment"}
MEMORY_STATUSES = {"active", "unresolved", "resolved", "failed", "superseded"}


@dataclass
class MemoryRecord:
    memory_id: str
    kind: str
    content: str
    entities: tuple[str, ...] = ()
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = 0.0
    expires_at: float | None = None
    source_chain_id: str = ""
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in MEMORY_KINDS:
            raise ValueError(f"Unsupported memory kind: {self.kind}")
        if self.status not in MEMORY_STATUSES:
            raise ValueError(f"Unsupported memory status: {self.status}")
        self.content = str(self.content).strip()
        if not self.content:
            raise ValueError("Memory content cannot be empty")
        self.entities = tuple(dict.fromkeys(
            str(item).strip() for item in self.entities if str(item).strip()
        ))
        self.importance = min(1.0, max(0.0, float(self.importance)))
        self.created_at = float(self.created_at)
        self.last_accessed_at = float(self.last_accessed_at or 0.0)
        self.expires_at = None if self.expires_at is None else float(self.expires_at)
        self.source_chain_id = str(self.source_chain_id or "")
        self.metadata = dict(self.metadata or {})

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryRecord":
        return cls(
            memory_id=str(data.get("memory_id", "") or f"mem_{uuid.uuid4().hex[:12]}"),
            kind=str(data.get("kind", "episodic")),
            content=str(data.get("content", "")),
            entities=tuple(data.get("entities", ())),
            importance=float(data.get("importance", 0.5)),
            created_at=float(data.get("created_at", time.time())),
            last_accessed_at=float(data.get("last_accessed_at", 0.0)),
            expires_at=data.get("expires_at"),
            source_chain_id=str(data.get("source_chain_id", "")),
            status=str(data.get("status", "active")),
            metadata=dict(data.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entities"] = list(self.entities)
        return data


@dataclass(frozen=True)
class MemorySearchResult:
    record: MemoryRecord
    score: float


class StructuredMemoryStore:
    """Bounded deterministic memory store without requiring a vector database."""

    def __init__(self, *, max_records: int = 500, recency_half_life_s: float = 604800.0) -> None:
        self.max_records = max(10, int(max_records))
        self.recency_half_life_s = max(1.0, float(recency_half_life_s))
        self._records: dict[str, MemoryRecord] = {}

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[MemoryRecord, ...]:
        return tuple(self._records.values())

    def remember(
        self,
        *,
        kind: str,
        content: str,
        entities: Iterable[str] = (),
        importance: float = 0.5,
        created_at: float | None = None,
        expires_at: float | None = None,
        source_chain_id: str = "",
        status: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        memory_id: str = "",
    ) -> MemoryRecord:
        metadata_dict = dict(metadata or {})
        dedupe_key = str(metadata_dict.get("dedupe_key", "") or "")
        if dedupe_key:
            for record in self._records.values():
                if (
                    record.kind == kind
                    and record.status in {"active", "unresolved"}
                    and record.metadata.get("dedupe_key") == dedupe_key
                ):
                    record.importance = max(record.importance, min(1.0, float(importance)))
                    record.entities = tuple(dict.fromkeys((*record.entities, *tuple(entities))))
                    record.metadata.update(metadata_dict)
                    return record
        record = MemoryRecord(
            memory_id=memory_id or f"mem_{uuid.uuid4().hex[:12]}",
            kind=kind,
            content=content,
            entities=tuple(entities),
            importance=importance,
            created_at=time.time() if created_at is None else created_at,
            expires_at=expires_at,
            source_chain_id=source_chain_id,
            status=status or ("unresolved" if kind == "commitment" else "active"),
            metadata=metadata_dict,
        )
        self._records[record.memory_id] = record
        self.prune(now=record.created_at)
        return record

    def add_commitment(
        self,
        content: str,
        *,
        intent_id: str,
        entities: Iterable[str] = (),
        source_chain_id: str = "",
        importance: float = 0.85,
    ) -> MemoryRecord:
        return self.remember(
            kind="commitment",
            content=content,
            entities=entities,
            importance=importance,
            source_chain_id=source_chain_id,
            status="unresolved",
            metadata={"intent_id": intent_id, "dedupe_key": f"intent:{intent_id}"},
        )

    def resolve_commitment(
        self,
        intent_id: str,
        *,
        success: bool | None = None,
        resolution: str = "",
        resolved_at: float | None = None,
    ) -> MemoryRecord | None:
        if not intent_id:
            return None
        for record in reversed(tuple(self._records.values())):
            if (
                record.kind == "commitment"
                and record.metadata.get("intent_id") == intent_id
                and record.status == "unresolved"
            ):
                record.status = "failed" if success is False else "resolved"
                record.last_accessed_at = resolved_at or time.time()
                record.metadata["resolution"] = resolution
                record.metadata["success"] = success
                record.metadata["resolved_at"] = record.last_accessed_at
                return record
        return None

    def ingest_payload(self, payload: Mapping[str, Any], *, agent_id: str = "") -> None:
        chain_id = str(payload.get("chain_id", "") or "")
        entities = self._payload_entities(payload, agent_id=agent_id)
        if payload.get("type") == "action_result":
            intent_id = str(payload.get("intent_id", "") or "")
            success = payload.get("success")
            resolution = str(payload.get("message", "") or "")
            self.resolve_commitment(
                intent_id,
                success=success if isinstance(success, bool) else None,
                resolution=resolution,
            )
            self.remember(
                kind="episodic",
                content="Action result: " + self._brief_mapping(payload, (
                    "action_type", "target_location", "success", "message", "changes", "intent_id"
                )),
                entities=entities,
                importance=0.9 if success is False else 0.72,
                source_chain_id=chain_id,
                metadata={"event_type": "action_result", "intent_id": intent_id},
            )
            return

        description = str(payload.get("description", "") or "").strip()
        if description:
            severity = payload.get("severity", 5)
            try:
                importance = min(1.0, max(0.35, float(severity) / 10.0))
            except (TypeError, ValueError):
                importance = 0.55
            self.remember(
                kind="episodic",
                content=description,
                entities=entities,
                importance=importance,
                source_chain_id=chain_id,
                metadata={"event_type": str(payload.get("event_type", "event"))},
            )

        message = str(payload.get("message", "") or "").strip()
        if message:
            sender = str(payload.get("from", payload.get("sender", "unknown")) or "unknown")
            self.remember(
                kind="relationship",
                content=f"Message from {sender}: {message}",
                entities=tuple(dict.fromkeys((sender, *entities))),
                importance=0.58,
                source_chain_id=chain_id,
                metadata={"sender": sender},
            )

        facts = payload.get("memory_facts", [])
        if isinstance(facts, list):
            for fact in facts:
                if isinstance(fact, str) and fact.strip():
                    self.remember(
                        kind="semantic",
                        content=fact,
                        entities=entities,
                        importance=0.7,
                        source_chain_id=chain_id,
                    )
                elif isinstance(fact, Mapping) and str(fact.get("content", "")).strip():
                    self.remember(
                        kind="semantic",
                        content=str(fact["content"]),
                        entities=fact.get("entities", entities),
                        importance=float(fact.get("importance", 0.7)),
                        source_chain_id=chain_id,
                        metadata=dict(fact.get("metadata", {})),
                    )

    def ingest_feedback(self, feedback: Mapping[str, Any], *, agent_id: str = "") -> None:
        intent_id = str(feedback.get("intent_id", "") or "")
        result = feedback.get("result")
        result_map = result if isinstance(result, Mapping) else {}
        phase = str(feedback.get("phase", "") or "")
        success_raw = feedback.get("success", result_map.get("success"))
        success = success_raw if isinstance(success_raw, bool) else None
        final = (
            phase in {"execution", "world_execution", "completed", "failed"}
            or "success" in feedback
            or "success" in result_map
            or str(feedback.get("status", "")) in {"completed", "failed", "rejected"}
        )
        if final:
            self.resolve_commitment(
                intent_id,
                success=success,
                resolution=self._brief_mapping(feedback, ("phase", "status", "result", "error")),
            )
        self.remember(
            kind="episodic",
            content="Execution feedback: " + self._brief_mapping(
                feedback, ("phase", "status", "intent_id", "result", "error")
            ),
            entities=self._payload_entities(feedback, agent_id=agent_id),
            importance=0.75 if final else 0.5,
            source_chain_id=str(feedback.get("chain_id", "") or ""),
            metadata={"event_type": "execution_feedback", "intent_id": intent_id, "final": final},
        )

    def retrieve(
        self,
        query: str = "",
        *,
        entities: Iterable[str] = (),
        kinds: Iterable[str] = (),
        limit: int = 8,
        max_chars: int = 2400,
        now: float | None = None,
    ) -> list[MemorySearchResult]:
        now_value = time.time() if now is None else float(now)
        query_tokens = self._tokens(query)
        entity_set = {str(item).casefold() for item in entities if str(item).strip()}
        kind_set = {str(item) for item in kinds}
        scored: list[MemorySearchResult] = []
        for record in self._records.values():
            if record.expires_at is not None and record.expires_at <= now_value:
                continue
            if kind_set and record.kind not in kind_set:
                continue
            age = max(0.0, now_value - record.created_at)
            recency = math.pow(0.5, age / self.recency_half_life_s)
            record_tokens = self._tokens(record.content)
            overlap = len(query_tokens & record_tokens) / max(1, len(query_tokens))
            record_entities = {item.casefold() for item in record.entities}
            entity_overlap = len(entity_set & record_entities)
            kind_bonus = {
                "commitment": 0.8,
                "semantic": 0.5,
                "relationship": 0.35,
                "episodic": 0.2,
            }[record.kind]
            unresolved_bonus = 2.5 if (
                record.kind == "commitment" and record.status == "unresolved"
            ) else 0.0
            resolved_penalty = -0.5 if record.status in {"resolved", "failed", "superseded"} else 0.0
            score = (
                record.importance * 4.0
                + recency * 1.5
                + overlap * 4.0
                + entity_overlap * 2.5
                + kind_bonus
                + unresolved_bonus
                + resolved_penalty
            )
            scored.append(MemorySearchResult(record=record, score=score))

        scored.sort(key=lambda item: (
            -item.score,
            -item.record.created_at,
            item.record.memory_id,
        ))
        selected: list[MemorySearchResult] = []
        used_chars = 0
        for item in scored:
            line_size = len(item.record.content) + 48
            if selected and used_chars + line_size > max_chars:
                continue
            if line_size > max_chars and not selected:
                continue
            selected.append(item)
            used_chars += line_size
            item.record.last_accessed_at = now_value
            if len(selected) >= max(1, int(limit)):
                break
        return selected

    def format_context(
        self,
        query: str = "",
        *,
        entities: Iterable[str] = (),
        limit: int = 8,
        max_chars: int = 2400,
        now: float | None = None,
    ) -> str:
        results = self.retrieve(
            query,
            entities=entities,
            limit=limit,
            max_chars=max_chars,
            now=now,
        )
        if not results:
            return ""
        lines = ["## Relevant long-term memory"]
        for item in results:
            record = item.record
            labels = [record.kind, record.status]
            if record.entities:
                labels.append("entities=" + ",".join(record.entities))
            lines.append(f"- [{' | '.join(labels)}] {record.content}")
        return "\n".join(lines)

    def prune(self, *, now: float | None = None) -> None:
        now_value = time.time() if now is None else float(now)
        expired = [
            memory_id for memory_id, record in self._records.items()
            if record.expires_at is not None and record.expires_at <= now_value
        ]
        for memory_id in expired:
            self._records.pop(memory_id, None)
        if len(self._records) <= self.max_records:
            return
        ranked = sorted(
            self._records.values(),
            key=lambda record: (
                record.kind == "commitment" and record.status == "unresolved",
                record.importance,
                record.last_accessed_at,
                record.created_at,
            ),
        )
        remove_count = len(self._records) - self.max_records
        for record in ranked:
            if remove_count <= 0:
                break
            if record.kind == "commitment" and record.status == "unresolved":
                continue
            self._records.pop(record.memory_id, None)
            remove_count -= 1

    def serialize(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self._records.values()]

    @classmethod
    def deserialize(
        cls,
        data: Any,
        *,
        max_records: int = 500,
    ) -> "StructuredMemoryStore":
        store = cls(max_records=max_records)
        records = data.get("records", []) if isinstance(data, Mapping) else data
        if not isinstance(records, list):
            return store
        for raw in records:
            if not isinstance(raw, Mapping):
                continue
            try:
                record = MemoryRecord.from_dict(raw)
            except (TypeError, ValueError):
                continue
            store._records[record.memory_id] = record
        store.prune()
        return store

    @staticmethod
    def _tokens(text: str) -> set[str]:
        lowered = str(text).casefold()
        tokens = set(re.findall(r"[a-z0-9_]+", lowered))
        for sequence in re.findall(r"[\u3400-\u9fff]+", lowered):
            if len(sequence) == 1:
                tokens.add(sequence)
            else:
                tokens.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
        return tokens

    @staticmethod
    def _payload_entities(payload: Mapping[str, Any], *, agent_id: str = "") -> tuple[str, ...]:
        values: list[str] = [agent_id] if agent_id else []
        for key in (
            "from", "sender", "entity_id", "location_id", "target_location",
            "target_agent", "executor_id", "source_agent_id",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                values.append(str(value))
        for key in ("target_agents", "entities"):
            value = payload.get(key, [])
            if isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value if str(item).strip())
        return tuple(dict.fromkeys(item for item in values if item))

    @staticmethod
    def _brief_mapping(payload: Mapping[str, Any], keys: Iterable[str]) -> str:
        parts: list[str] = []
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            rendered = str(value)
            if len(rendered) > 500:
                rendered = rendered[:497] + "..."
            parts.append(f"{key}={rendered}")
        return "; ".join(parts) or "no details"
