"""Transactional world-command bus with idempotent receipts.

All authoritative world mutations should converge here.  The bus opens one
SQLite transaction per command, delegates deterministic mutation logic to
WorldState, and records an immutable receipt only after the mutation commits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from .world_state import WorldState

logger = logging.getLogger(__name__)


class WorldCommandType(str, Enum):
    UPDATE_STATUS = "update_status"
    ADJUST_RESOURCE = "adjust_resource"
    SET_RESOURCE = "set_resource"
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    APPLY_MATURED_EVENT = "apply_matured_event"
    CREATE_ENTITY = "create_entity"
    MOVE_ENTITY = "move_entity"
    TRANSFORM_ENTITY = "transform_entity"
    REMOVE_ENTITY = "remove_entity"
    MODIFY_RELATIONSHIP = "modify_relationship"
    SET_AGENT_ENABLED = "set_agent_enabled"
    MODIFY_MEMBERSHIP = "modify_membership"
    CREATE_ISSUE = "create_issue"
    SET_WORLD_TRUTH = "set_world_truth"
    SET_ACTOR_BELIEF = "set_actor_belief"
    REVEAL_INFORMATION = "reveal_information"
    PROPAGATE_KNOWLEDGE = "propagate_knowledge"
    START_INVESTIGATION = "start_investigation"
    ADVANCE_INVESTIGATION = "advance_investigation"
    ADVANCE_OBJECTIVE = "advance_objective"
    UNLOCK_ACTION = "unlock_action"
    SWITCH_PHASE = "switch_phase"


@dataclass(slots=True)
class WorldCommand:
    command_type: WorldCommandType
    payload: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=lambda: f"wcmd_{uuid.uuid4().hex}")
    entity_id: str = ""
    expected_version: int | None = None
    chain_id: str = ""
    intent_id: str = ""
    sim_time: float = 0.0
    schema_version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "command_type": self.command_type.value,
            "entity_id": self.entity_id,
            "expected_version": self.expected_version,
            "chain_id": self.chain_id,
            "intent_id": self.intent_id,
            "sim_time": self.sim_time,
            "schema_version": self.schema_version,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    def payload_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True)
class WorldCommandResult:
    command_id: str
    command_type: str
    ok: bool
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error: str = ""
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def require_ok(self) -> dict[str, Any]:
        if not self.ok:
            raise WorldCommandError(
                self.error_code or "command_rejected",
                self.error or "world command rejected",
                self.result,
            )
        return self.result


@dataclass(slots=True)
class WorldCommandBatchResult:
    ok: bool
    atomic: bool
    committed: bool
    results: list[WorldCommandResult] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return sum(result.ok and not result.replayed for result in self.results)

    @property
    def replayed_count(self) -> int:
        return sum(result.replayed for result in self.results)

    @property
    def rejected_count(self) -> int:
        return sum(not result.ok for result in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "atomic": self.atomic,
            "committed": self.committed,
            "applied_count": self.applied_count,
            "replayed_count": self.replayed_count,
            "rejected_count": self.rejected_count,
            "results": [result.to_dict() for result in self.results],
        }


class WorldCommandError(RuntimeError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class WorldCommandBus:
    """Execute deterministic world mutations in one SQLite transaction."""

    def __init__(self, world_state: "WorldState") -> None:
        self.world_state = world_state
        self._metrics_lock = threading.Lock()
        self._runtime_metrics = {
            "execute_calls": 0,
            "batch_calls": 0,
            "batch_commands": 0,
            "replays": 0,
            "idempotency_conflicts": 0,
            "crashes": 0,
        }

    def _record_runtime_metric(self, name: str, amount: int = 1) -> None:
        with self._metrics_lock:
            self._runtime_metrics[name] = self._runtime_metrics.get(name, 0) + amount

    def _record_replay(self, result: WorldCommandResult) -> None:
        self._record_runtime_metric("replays")
        if result.status == "idempotency_conflict":
            self._record_runtime_metric("idempotency_conflicts")

    def execute(self, command: WorldCommand) -> WorldCommandResult:
        self._record_runtime_metric("execute_calls")
        if not command.command_id:
            command.command_id = f"wcmd_{uuid.uuid4().hex}"
        payload_hash = command.payload_hash()

        with self.world_state._write_lock:
            conn = self.world_state._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT * FROM world_commands WHERE command_id = ?",
                    (command.command_id,),
                ).fetchone()
                if existing is not None:
                    result = self._result_from_receipt(existing, payload_hash)
                    self._record_replay(result)
                    conn.rollback()
                    return result

                reduced = self.world_state._reduce_world_command(conn, command)
                result = WorldCommandResult(
                    command_id=command.command_id,
                    command_type=command.command_type.value,
                    ok=True,
                    status="applied",
                    result=reduced,
                )
                self._insert_receipt(conn, command, payload_hash, result)
                conn.commit()
                return result
            except WorldCommandError as exc:
                conn.rollback()
                result = WorldCommandResult(
                    command_id=command.command_id,
                    command_type=command.command_type.value,
                    ok=False,
                    status="rejected",
                    result=dict(exc.details),
                    error_code=exc.code,
                    error=str(exc),
                )
                self._store_rejection(command, payload_hash, result)
                return result
            except Exception:
                conn.rollback()
                self._record_runtime_metric("crashes")
                logger.exception(
                    "World command crashed: id=%s type=%s",
                    command.command_id, command.command_type.value,
                )
                raise
            finally:
                conn.close()

    def execute_batch(
        self,
        commands: list[WorldCommand],
        *,
        atomic: bool = True,
    ) -> WorldCommandBatchResult:
        """Execute many commands with one connection and one outer transaction.

        In atomic mode, the first rejection rolls back every newly-applied command
        in the batch. Existing successful receipts remain valid replays. In
        non-atomic mode, each command is isolated by a SQLite savepoint so a
        rejection does not discard successful siblings. Rejected commands retain
        immutable audit receipts in both modes.
        """
        items = list(commands)
        self._record_runtime_metric("batch_calls")
        self._record_runtime_metric("batch_commands", len(items))
        if not items:
            return WorldCommandBatchResult(ok=True, atomic=atomic, committed=True)
        payload_hashes: list[str] = []
        for command in items:
            if not command.command_id:
                command.command_id = f"wcmd_{uuid.uuid4().hex}"
            payload_hashes.append(command.payload_hash())

        with self.world_state._write_lock:
            conn = self.world_state._conn()
            results: list[WorldCommandResult] = []
            newly_applied: set[int] = set()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for index, (command, payload_hash) in enumerate(zip(items, payload_hashes)):
                    existing = conn.execute(
                        "SELECT * FROM world_commands WHERE command_id = ?",
                        (command.command_id,),
                    ).fetchone()
                    if existing is not None:
                        result = self._result_from_receipt(existing, payload_hash)
                        self._record_replay(result)
                        results.append(result)
                        if atomic and not result.ok:
                            conn.rollback()
                            return self._aborted_batch(
                                items, results, newly_applied, index, atomic=True,
                            )
                        continue

                    savepoint = f"world_batch_{index}"
                    if not atomic:
                        conn.execute(f"SAVEPOINT {savepoint}")
                    try:
                        reduced = self.world_state._reduce_world_command(conn, command)
                        result = WorldCommandResult(
                            command_id=command.command_id,
                            command_type=command.command_type.value,
                            ok=True,
                            status="applied",
                            result=reduced,
                        )
                        self._insert_receipt(conn, command, payload_hash, result)
                        newly_applied.add(index)
                        if not atomic:
                            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                    except WorldCommandError as exc:
                        result = WorldCommandResult(
                            command_id=command.command_id,
                            command_type=command.command_type.value,
                            ok=False,
                            status="rejected",
                            result=dict(exc.details),
                            error_code=exc.code,
                            error=str(exc),
                        )
                        if atomic:
                            conn.rollback()
                            self._store_rejection(command, payload_hash, result)
                            results.append(result)
                            return self._aborted_batch(
                                items, results, newly_applied, index, atomic=True,
                            )
                        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                        self._insert_receipt(conn, command, payload_hash, result)
                    results.append(result)

                conn.commit()
                return WorldCommandBatchResult(
                    ok=all(result.ok for result in results),
                    atomic=atomic,
                    committed=True,
                    results=results,
                )
            except Exception:
                conn.rollback()
                self._record_runtime_metric("crashes")
                logger.exception(
                    "World command batch crashed: size=%s atomic=%s",
                    len(items), atomic,
                )
                raise
            finally:
                conn.close()

    @staticmethod
    def _aborted_batch(
        commands: list[WorldCommand],
        results: list[WorldCommandResult],
        newly_applied: set[int],
        failure_index: int,
        *,
        atomic: bool,
    ) -> WorldCommandBatchResult:
        normalized: list[WorldCommandResult] = []
        for index, result in enumerate(results):
            if index in newly_applied:
                normalized.append(WorldCommandResult(
                    command_id=result.command_id,
                    command_type=result.command_type,
                    ok=False,
                    status="rolled_back",
                    result=result.result,
                    error_code="batch_rolled_back",
                    error=f"atomic batch rolled back after command {failure_index} failed",
                ))
            else:
                normalized.append(result)
        for command in commands[len(results):]:
            normalized.append(WorldCommandResult(
                command_id=command.command_id,
                command_type=command.command_type.value,
                ok=False,
                status="not_executed",
                error_code="batch_aborted",
                error=f"atomic batch stopped after command {failure_index} failed",
            ))
        return WorldCommandBatchResult(
            ok=False, atomic=atomic, committed=False, results=normalized,
        )

    def execute_or_raise(self, command: WorldCommand) -> dict[str, Any]:
        return self.execute(command).require_ok()

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return aggregate receipt and runtime counters without payloads or errors."""
        conn = self.world_state._conn()
        try:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM world_commands GROUP BY status"
            ).fetchall()
            type_rows = conn.execute(
                "SELECT command_type, COUNT(*) AS count FROM world_commands "
                "GROUP BY command_type"
            ).fetchall()
            error_rows = conn.execute(
                "SELECT error_code, COUNT(*) AS count FROM world_commands "
                "WHERE error_code IS NOT NULL AND error_code != '' GROUP BY error_code"
            ).fetchall()
        finally:
            conn.close()

        by_status = {str(row["status"]): int(row["count"]) for row in status_rows}
        by_type = {str(row["command_type"]): int(row["count"]) for row in type_rows}
        by_error_code = {
            str(row["error_code"]): int(row["count"]) for row in error_rows
        }
        with self._metrics_lock:
            runtime = dict(self._runtime_metrics)
        return {
            "persisted_total": sum(by_status.values()),
            "by_status": by_status,
            "by_type": by_type,
            "by_error_code": by_error_code,
            "runtime": runtime,
        }

    def _store_rejection(
        self,
        command: WorldCommand,
        payload_hash: str,
        result: WorldCommandResult,
    ) -> None:
        conn = self.world_state._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT payload_hash FROM world_commands WHERE command_id = ?",
                (command.command_id,),
            ).fetchone()
            if existing is None:
                self._insert_receipt(conn, command, payload_hash, result)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("Failed to persist rejected world command %s", command.command_id)
        finally:
            conn.close()

    @staticmethod
    def _insert_receipt(
        conn: "sqlite3.Connection",
        command: WorldCommand,
        payload_hash: str,
        result: WorldCommandResult,
    ) -> None:
        conn.execute(
            """INSERT INTO world_commands
               (command_id, command_type, entity_id, payload_hash, payload_json,
                status, result_json, error_code, error_message, chain_id,
                intent_id, schema_version, sim_time, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                command.command_id,
                command.command_type.value,
                command.entity_id,
                payload_hash,
                json.dumps(command.canonical_payload(), ensure_ascii=False, default=str),
                result.status,
                json.dumps(result.result, ensure_ascii=False, default=str),
                result.error_code,
                result.error,
                command.chain_id,
                command.intent_id,
                command.schema_version,
                float(command.sim_time),
                time.time(),
            ),
        )

    @staticmethod
    def _result_from_receipt(row: Any, payload_hash: str) -> WorldCommandResult:
        if row["payload_hash"] != payload_hash:
            return WorldCommandResult(
                command_id=row["command_id"],
                command_type=row["command_type"],
                ok=False,
                status="idempotency_conflict",
                error_code="idempotency_conflict",
                error="command_id was already used with a different payload",
                replayed=True,
            )
        try:
            payload = json.loads(row["result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        return WorldCommandResult(
            command_id=row["command_id"],
            command_type=row["command_type"],
            ok=row["status"] == "applied",
            status=row["status"],
            result=payload,
            error_code=row["error_code"] or "",
            error=row["error_message"] or "",
            replayed=True,
        )
