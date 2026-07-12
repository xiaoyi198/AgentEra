"""Signed, transactional registry for approved scale and operational baselines."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .operational_calibration import (
    OperationalCalibrationError,
    compare_operational_snapshot,
)
from .scale_calibration import ScaleCalibrationError, compare_scale_report
from .trajectory_evaluation import write_report_atomic


class BaselineRegistryError(ValueError):
    """Raised when an approved baseline registry cannot be trusted or updated."""


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_REFERENCE = re.compile(
    r"(?:\$\{[A-Za-z_][A-Za-z0-9_]*(?:KEY|SECRET|TOKEN|CREDENTIAL)[A-Za-z0-9_]*\}"
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:API_KEY|SECRET|TOKEN|CREDENTIALS?)\b)",
    re.IGNORECASE,
)
_FORBIDDEN_KEYS = {
    "api_key", "authorization", "provider_url", "base_url", "secret", "token",
    "credential", "credentials", "prompt", "prompts", "message", "messages",
    "memories", "error", "errors", "raw_error", "archive_path",
    "source_path", "input_path", "config_path", "command_id", "chain_id", "trace_id",
}


def _canonical_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_bytes(value: Any) -> bytes:
    return _canonical_text(value).encode("utf-8")


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if (
                key in _FORBIDDEN_KEYS
                or key.endswith("_api_key")
                or key.endswith("_secret")
                or key.endswith("_credential")
                or key.endswith("_token")
                or key.endswith("_path")
                or key.endswith("_env")
            ):
                return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive(item) for item in value)
    elif isinstance(value, str):
        stripped = value.strip()
        if (
            "://" in stripped
            or re.match(r"^[A-Za-z]:[\\/]", stripped)
            or stripped.startswith("/")
            or _SENSITIVE_REFERENCE.search(stripped)
        ):
            return True
    return False


def _baseline_type(payload: Mapping[str, Any]) -> str:
    if payload.get("baseline_type") == "operational-alerts":
        return "operational-alerts"
    if "recommended_scale_thresholds_ms" in payload:
        return "scale"
    raise BaselineRegistryError("unsupported_baseline_type")


def _validate_baseline(payload: Mapping[str, Any]) -> tuple[str, str, str, str]:
    if payload.get("schema_version") != "1.0":
        raise BaselineRegistryError("unsupported_baseline_schema")
    kind = _baseline_type(payload)
    environment_id = str(payload.get("environment_id", ""))
    baseline_id = str(payload.get("baseline_id", ""))
    if not _ID.fullmatch(environment_id):
        raise BaselineRegistryError("baseline_environment_invalid")
    if not _SHA256.fullmatch(baseline_id):
        raise BaselineRegistryError("baseline_id_invalid")
    if kind == "operational-alerts" and payload.get("ready") is not True:
        raise BaselineRegistryError("baseline_requires_review")
    if _contains_sensitive(payload):
        raise BaselineRegistryError("baseline_contains_forbidden_content")
    core = {
        str(key): value
        for key, value in payload.items()
        if key not in {"baseline_id", "generated_at"}
    }
    expected = hashlib.sha256(_canonical_bytes(core)).hexdigest()
    if not hmac.compare_digest(expected, baseline_id):
        raise BaselineRegistryError("baseline_id_mismatch")
    return kind, environment_id, baseline_id, _canonical_text(dict(payload))


def _load_json(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineRegistryError(f"cannot_load_{label}") from exc
    if not isinstance(value, dict):
        raise BaselineRegistryError(f"{label}_root_invalid")
    return value


class BaselineRegistry:
    """Store approved baselines, active pointers, and signed audit history in SQLite."""

    def __init__(
        self,
        database: str | Path,
        *,
        key: bytes | str | None = None,
        key_env: str = "WORLD_SIM_BASELINE_REGISTRY_KEY",
    ) -> None:
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._master_key = self._resolve_key(key, key_env)
        self._signing_key = hmac.new(
            self._master_key,
            b"world-simulator/baseline-registry/v1",
            hashlib.sha256,
        ).digest()
        self.key_id = hashlib.sha256(self._signing_key).hexdigest()[:16]
        self._initialize()

    @staticmethod
    def generate_key() -> str:
        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    def _resolve_key(self, explicit: bytes | str | None, key_env: str) -> bytes:
        source = explicit if explicit is not None else os.environ.get(key_env)
        if source is not None:
            return self._decode_key(source)
        key_path = self.database.with_suffix(self.database.suffix + ".key")
        if key_path.exists():
            try:
                return self._decode_key(key_path.read_bytes().strip())
            except (OSError, ValueError) as exc:
                raise BaselineRegistryError("registry_key_invalid") from exc
        raw = os.urandom(32)
        encoded = base64.urlsafe_b64encode(raw)
        try:
            with key_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            try:
                return self._decode_key(key_path.read_bytes().strip())
            except (OSError, ValueError) as exc:
                raise BaselineRegistryError("registry_key_invalid") from exc
        except OSError as exc:
            raise BaselineRegistryError("registry_key_write_failed") from exc
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return raw

    @staticmethod
    def _decode_key(value: bytes | str) -> bytes:
        raw = value.encode("ascii") if isinstance(value, str) else bytes(value)
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except Exception as exc:
            raise BaselineRegistryError("registry_key_invalid") from exc
        if len(decoded) != 32:
            raise BaselineRegistryError("registry_key_must_be_32_bytes_base64")
        return decoded

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS baselines (
                    version INTEGER PRIMARY KEY AUTOINCREMENT,
                    baseline_type TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    baseline_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    approval_ref TEXT NOT NULL,
                    approved_at REAL NOT NULL,
                    signature TEXT NOT NULL,
                    UNIQUE(baseline_type, environment_id, baseline_id)
                );
                CREATE TABLE IF NOT EXISTS active_baselines (
                    baseline_type TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    version INTEGER NOT NULL REFERENCES baselines(version),
                    activated_at REAL NOT NULL,
                    signature TEXT NOT NULL,
                    PRIMARY KEY(baseline_type, environment_id)
                );
                CREATE TABLE IF NOT EXISTS baseline_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    baseline_type TEXT NOT NULL,
                    environment_id TEXT NOT NULL,
                    target_version INTEGER NOT NULL,
                    previous_version INTEGER,
                    occurred_at REAL NOT NULL,
                    signature TEXT NOT NULL
                );
                """
            )

    def _sign(self, domain: str, value: Mapping[str, Any]) -> str:
        message = domain.encode("ascii") + b"\0" + _canonical_bytes(value)
        return hmac.new(self._signing_key, message, hashlib.sha256).hexdigest()

    def _verify_signature(self, domain: str, value: Mapping[str, Any], signature: str) -> None:
        expected = self._sign(domain, value)
        if not hmac.compare_digest(expected, str(signature)):
            raise BaselineRegistryError(f"{domain}_signature_invalid")

    @staticmethod
    def _safe_ref(value: str) -> str:
        if not _ID.fullmatch(value):
            raise BaselineRegistryError("approval_ref_invalid")
        return value

    def _baseline_signed_fields(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "version": int(row["version"]),
            "baseline_type": row["baseline_type"],
            "environment_id": row["environment_id"],
            "baseline_id": row["baseline_id"],
            "payload_hash": row["payload_hash"],
            "approval_ref": row["approval_ref"],
            "approved_at": row["approved_at"],
        }

    def _verify_baseline_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        fields = self._baseline_signed_fields(row)
        self._verify_signature("baseline", fields, str(row["signature"]))
        payload_text = str(row["payload_json"])
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(payload_hash, str(row["payload_hash"])):
            raise BaselineRegistryError("baseline_payload_hash_invalid")
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise BaselineRegistryError("baseline_payload_invalid") from exc
        if not isinstance(payload, dict):
            raise BaselineRegistryError("baseline_payload_invalid")
        kind, environment_id, baseline_id, canonical = _validate_baseline(payload)
        if canonical != payload_text:
            raise BaselineRegistryError("baseline_payload_not_canonical")
        if (
            kind != row["baseline_type"]
            or environment_id != row["environment_id"]
            or baseline_id != row["baseline_id"]
        ):
            raise BaselineRegistryError("baseline_metadata_mismatch")
        return payload

    def approve(self, payload: Mapping[str, Any], *, approval_ref: str) -> dict[str, Any]:
        kind, environment_id, baseline_id, payload_text = _validate_baseline(payload)
        approval_ref = self._safe_ref(approval_ref)
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        approved_at = time.time()
        fields = {
            "baseline_type": kind,
            "environment_id": environment_id,
            "baseline_id": baseline_id,
            "payload_hash": payload_hash,
            "approval_ref": approval_ref,
            "approved_at": approved_at,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM baselines WHERE baseline_type=? AND environment_id=? AND baseline_id=?",
                (kind, environment_id, baseline_id),
            ).fetchone()
            if existing is not None:
                self._verify_baseline_row(existing)
                return self._metadata(existing, connection=connection)
            cursor = connection.execute(
                """INSERT INTO baselines(
                    baseline_type, environment_id, baseline_id, payload_json, payload_hash,
                    approval_ref, approved_at, signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind, environment_id, baseline_id, payload_text, payload_hash,
                    approval_ref, approved_at, "",
                ),
            )
            version = int(cursor.lastrowid)
            fields["version"] = version
            connection.execute(
                "UPDATE baselines SET signature=? WHERE version=?",
                (self._sign("baseline", fields), version),
            )
            row = connection.execute(
                "SELECT * FROM baselines WHERE version=?", (version,)
            ).fetchone()
            self._audit(
                connection, action="approve", kind=kind, environment_id=environment_id,
                target_version=version, previous_version=None, occurred_at=approved_at,
            )
            return self._metadata(row, connection=connection)

    def _active_row(
        self, connection: sqlite3.Connection, kind: str, environment_id: str
    ) -> sqlite3.Row | None:
        row = connection.execute(
            "SELECT * FROM active_baselines WHERE baseline_type=? AND environment_id=?",
            (kind, environment_id),
        ).fetchone()
        if row is not None:
            fields = {
                "baseline_type": row["baseline_type"],
                "environment_id": row["environment_id"],
                "version": row["version"],
                "activated_at": row["activated_at"],
            }
            self._verify_signature("active", fields, row["signature"])
            target = connection.execute(
                "SELECT baseline_type, environment_id FROM baselines WHERE version=?",
                (int(row["version"]),),
            ).fetchone()
            if target is None:
                raise BaselineRegistryError("active_baseline_missing")
            if (
                str(target["baseline_type"]) != str(row["baseline_type"])
                or str(target["environment_id"]) != str(row["environment_id"])
            ):
                raise BaselineRegistryError("active_baseline_identity_mismatch")
        return row

    def _metadata(
        self, row: Mapping[str, Any], *, connection: sqlite3.Connection
    ) -> dict[str, Any]:
        active = self._active_row(
            connection, str(row["baseline_type"]), str(row["environment_id"])
        )
        return {
            "version": int(row["version"]),
            "baseline_type": str(row["baseline_type"]),
            "environment_id": str(row["environment_id"]),
            "baseline_id": str(row["baseline_id"]),
            "approval_ref": str(row["approval_ref"]),
            "approved_at": float(row["approved_at"]),
            "active": active is not None and int(active["version"]) == int(row["version"]),
        }

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        action: str,
        kind: str,
        environment_id: str,
        target_version: int,
        previous_version: int | None,
        occurred_at: float,
    ) -> None:
        fields = {
            "action": action,
            "baseline_type": kind,
            "environment_id": environment_id,
            "target_version": target_version,
            "previous_version": previous_version,
            "occurred_at": occurred_at,
        }
        cursor = connection.execute(
            """INSERT INTO baseline_audit(
                action, baseline_type, environment_id, target_version,
                previous_version, occurred_at, signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                action, kind, environment_id, target_version,
                previous_version, occurred_at, "",
            ),
        )
        sequence = int(cursor.lastrowid)
        fields["sequence"] = sequence
        connection.execute(
            "UPDATE baseline_audit SET signature=? WHERE sequence=?",
            (self._sign("audit", fields), sequence),
        )

    def activate(self, version: int, *, action: str = "activate") -> dict[str, Any]:
        if action not in {"activate", "rollback"}:
            raise BaselineRegistryError("activation_action_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM baselines WHERE version=?", (int(version),)
            ).fetchone()
            if row is None:
                raise BaselineRegistryError("baseline_version_not_found")
            self._verify_baseline_row(row)
            kind = str(row["baseline_type"])
            environment_id = str(row["environment_id"])
            current = self._active_row(connection, kind, environment_id)
            previous_version = int(current["version"]) if current is not None else None
            if previous_version == int(version):
                return self._metadata(row, connection=connection)
            activated_at = time.time()
            fields = {
                "baseline_type": kind,
                "environment_id": environment_id,
                "version": int(version),
                "activated_at": activated_at,
            }
            connection.execute(
                """INSERT INTO active_baselines(
                    baseline_type, environment_id, version, activated_at, signature
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(baseline_type, environment_id) DO UPDATE SET
                    version=excluded.version,
                    activated_at=excluded.activated_at,
                    signature=excluded.signature""",
                (
                    kind, environment_id, int(version), activated_at,
                    self._sign("active", fields),
                ),
            )
            self._audit(
                connection,
                action=action,
                kind=kind,
                environment_id=environment_id,
                target_version=int(version),
                previous_version=previous_version,
                occurred_at=activated_at,
            )
            return self._metadata(row, connection=connection)

    def rollback(
        self,
        baseline_type: str,
        environment_id: str,
        *,
        target_version: int | None = None,
    ) -> dict[str, Any]:
        if baseline_type not in {"scale", "operational-alerts"} or not _ID.fullmatch(environment_id):
            raise BaselineRegistryError("baseline_identity_invalid")
        if target_version is None:
            with self._connect() as connection:
                current = self._active_row(connection, baseline_type, environment_id)
                if current is None:
                    raise BaselineRegistryError("active_baseline_not_found")
                event = connection.execute(
                    """SELECT * FROM baseline_audit
                    WHERE baseline_type=? AND environment_id=? AND target_version=?
                      AND previous_version IS NOT NULL
                    ORDER BY sequence DESC LIMIT 1""",
                    (baseline_type, environment_id, int(current["version"])),
                ).fetchone()
                if event is None:
                    raise BaselineRegistryError("rollback_target_not_found")
                verified_event = self._verify_audit_row(event, connection=connection)
                if verified_event["action"] not in {"activate", "rollback"}:
                    raise BaselineRegistryError("rollback_audit_invalid")
                target_version = int(verified_event["previous_version"])
        with self._connect() as connection:
            row = connection.execute(
                "SELECT baseline_type, environment_id FROM baselines WHERE version=?",
                (int(target_version),),
            ).fetchone()
            if row is None or row["baseline_type"] != baseline_type or row["environment_id"] != environment_id:
                raise BaselineRegistryError("rollback_target_mismatch")
        return self.activate(int(target_version), action="rollback")

    def active_payload(self, baseline_type: str, environment_id: str) -> dict[str, Any]:
        if baseline_type not in {"scale", "operational-alerts"} or not _ID.fullmatch(environment_id):
            raise BaselineRegistryError("baseline_identity_invalid")
        with self._connect() as connection:
            active = self._active_row(connection, baseline_type, environment_id)
            if active is None:
                raise BaselineRegistryError("active_baseline_not_found")
            row = connection.execute(
                "SELECT * FROM baselines WHERE version=?", (int(active["version"]),)
            ).fetchone()
            if row is None:
                raise BaselineRegistryError("active_baseline_missing")
            return self._verify_baseline_row(row)

    def _verify_audit_row(
        self, row: Mapping[str, Any], *, connection: sqlite3.Connection
    ) -> dict[str, Any]:
        fields = {
            "sequence": int(row["sequence"]),
            "action": str(row["action"]),
            "baseline_type": str(row["baseline_type"]),
            "environment_id": str(row["environment_id"]),
            "target_version": int(row["target_version"]),
            "previous_version": (
                int(row["previous_version"]) if row["previous_version"] is not None else None
            ),
            "occurred_at": float(row["occurred_at"]),
        }
        self._verify_signature("audit", fields, str(row["signature"]))
        if (
            fields["action"] not in {"approve", "activate", "rollback"}
            or fields["baseline_type"] not in {"scale", "operational-alerts"}
            or not _ID.fullmatch(fields["environment_id"])
            or (fields["action"] == "approve" and fields["previous_version"] is not None)
        ):
            raise BaselineRegistryError("audit_semantics_invalid")
        versions = [fields["target_version"]]
        if fields["previous_version"] is not None:
            versions.append(fields["previous_version"])
        placeholders = ",".join("?" for _ in versions)
        linked = connection.execute(
            f"SELECT version, baseline_type, environment_id FROM baselines "
            f"WHERE version IN ({placeholders})",
            versions,
        ).fetchall()
        by_version = {int(item["version"]): item for item in linked}
        for version in versions:
            linked_row = by_version.get(version)
            if linked_row is None or (
                str(linked_row["baseline_type"]) != fields["baseline_type"]
                or str(linked_row["environment_id"]) != fields["environment_id"]
            ):
                raise BaselineRegistryError("audit_version_identity_mismatch")
        return fields

    def status(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM baselines ORDER BY version").fetchall()
            versions = []
            for row in rows:
                self._verify_baseline_row(row)
                versions.append(self._metadata(row, connection=connection))
            active_rows = connection.execute(
                "SELECT baseline_type, environment_id FROM active_baselines "
                "ORDER BY baseline_type, environment_id"
            ).fetchall()
            for active in active_rows:
                self._active_row(
                    connection, str(active["baseline_type"]), str(active["environment_id"])
                )
            audit_rows = connection.execute(
                "SELECT * FROM baseline_audit ORDER BY sequence"
            ).fetchall()
            audit = [
                self._verify_audit_row(row, connection=connection) for row in audit_rows
            ]
            return {
                "schema_version": "1.0",
                "key_id": self.key_id,
                "version_count": len(versions),
                "active_count": len(active_rows),
                "audit_event_count": len(audit),
                "versions": versions,
                "recent_audit": audit[-50:],
            }


def _registry(args: argparse.Namespace) -> BaselineRegistry:
    return BaselineRegistry(args.database)


def _emit(value: Mapping[str, Any], output: str = "") -> None:
    if output:
        write_report_atomic(output, dict(value))
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _run_approve(args: argparse.Namespace) -> int:
    metadata = _registry(args).approve(
        _load_json(args.baseline, "baseline"), approval_ref=args.approval_ref
    )
    _emit(metadata, args.output)
    return 0


def _run_activate(args: argparse.Namespace) -> int:
    metadata = _registry(args).activate(args.version)
    _emit(metadata, args.output)
    return 0


def _run_rollback(args: argparse.Namespace) -> int:
    metadata = _registry(args).rollback(
        args.baseline_type,
        args.environment_id,
        target_version=args.target_version,
    )
    _emit(metadata, args.output)
    return 0


def _run_status(args: argparse.Namespace) -> int:
    _emit(_registry(args).status(), args.output)
    return 0


def _run_gate_scale(args: argparse.Namespace) -> int:
    registry = _registry(args)
    baseline = registry.active_payload("scale", args.environment_id)
    try:
        result = compare_scale_report(baseline, _load_json(args.candidate, "candidate"))
    except ScaleCalibrationError as exc:
        raise BaselineRegistryError("candidate_invalid") from exc
    _emit(result, args.output)
    return 0 if result["passed"] else 1


def _run_gate_operational(args: argparse.Namespace) -> int:
    registry = _registry(args)
    baseline = registry.active_payload("operational-alerts", args.environment_id)
    try:
        result = compare_operational_snapshot(
            baseline, _load_json(args.snapshot, "snapshot")
        )
    except OperationalCalibrationError as exc:
        raise BaselineRegistryError("candidate_invalid") from exc
    _emit(result, args.output)
    return 0 if result["passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage signed approved simulator baselines")
    parser.add_argument("--database", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    approve = subparsers.add_parser("approve")
    approve.add_argument("--baseline", required=True)
    approve.add_argument("--approval-ref", required=True)
    approve.add_argument("--output", default="")
    approve.set_defaults(handler=_run_approve)

    activate = subparsers.add_parser("activate")
    activate.add_argument("--version", required=True, type=int)
    activate.add_argument("--output", default="")
    activate.set_defaults(handler=_run_activate)

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--baseline-type", choices=("scale", "operational-alerts"), required=True)
    rollback.add_argument("--environment-id", required=True)
    rollback.add_argument("--target-version", type=int)
    rollback.add_argument("--output", default="")
    rollback.set_defaults(handler=_run_rollback)

    status = subparsers.add_parser("status")
    status.add_argument("--output", default="")
    status.set_defaults(handler=_run_status)

    gate_scale = subparsers.add_parser("gate-scale")
    gate_scale.add_argument("--environment-id", required=True)
    gate_scale.add_argument("--candidate", required=True)
    gate_scale.add_argument("--output", default="")
    gate_scale.set_defaults(handler=_run_gate_scale)

    gate_ops = subparsers.add_parser("gate-operational")
    gate_ops.add_argument("--environment-id", required=True)
    gate_ops.add_argument("--snapshot", required=True)
    gate_ops.add_argument("--output", default="")
    gate_ops.set_defaults(handler=_run_gate_operational)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except BaselineRegistryError as exc:
        print(f"baseline registry error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
