from __future__ import annotations

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from multiagent_framework.baseline_registry import (
    BaselineRegistry,
    BaselineRegistryError,
    main,
)
from multiagent_framework.operational_calibration import calibrate_operational_snapshots
from multiagent_framework.scale_calibration import calibrate_scale_reports
from multiagent_framework.scale_evaluation import ScaleConfig


def scale_report(offset: float = 0.0):
    phases = []
    for index, name in enumerate(("dlc", "router", "world", "memory", "soak"), 1):
        phases.append({
            "phase": name,
            "operations": 10,
            "elapsed_ms": 100 + index + offset,
            "operations_per_second": 100,
            "peak_memory_mb": 10 + index / 10,
            "passed": True,
            "invariants_passed": True,
            "performance_passed": True,
            "invariants": {"ok": True},
            "threshold_ms": None,
        })
    return {
        "suite_id": "world-simulator-scale-v1",
        "passed": True,
        "config": asdict(ScaleConfig(soak_cycles=2)),
        "total_elapsed_ms": 500 + offset,
        "peak_memory_mb": 11,
        "phases": phases,
    }


def scale_baseline(environment="ci-win", shift=0.0):
    return calibrate_scale_reports(
        [scale_report(shift + index) for index in range(3)],
        environment_id=environment,
    )


def operational_snapshot(index=0):
    return {
        "schema_version": "1.0",
        "status": "healthy",
        "collected_at": 1000 + index,
        "metrics": {
            "rates": {
                "llm_error_rate": 0.01,
                "llm_fallback_rate": 0.02,
                "dlc_reload_failure_rate": 0.01,
                "world_command_rejection_rate": 0.03,
                "agent_turn_error_rate": 0.01,
                "busy_agent_ratio": 0.2,
            },
            "llm": {"totals": {"calls": 100}},
            "dlc": {
                "metrics": {"attempts": 20, "max_duration_ms": 100 + index},
                "archive": {"issue_count": 0},
            },
            "world_commands": {"persisted_total": 200},
            "agent_turns": {"agent_turns": 80},
            "agents": {"total": 10},
        },
    }


def operational_baseline(environment="staging"):
    return calibrate_operational_snapshots(
        [operational_snapshot(index) for index in range(3)],
        environment_id=environment,
    )


def registry(tmp_path, key=None):
    return BaselineRegistry(
        tmp_path / "registry.db",
        key=key or BaselineRegistry.generate_key(),
    )


def test_approve_is_idempotent_and_activation_returns_verified_payload(tmp_path):
    store = registry(tmp_path)
    baseline = scale_baseline()
    first = store.approve(baseline, approval_ref="change-101")
    second = store.approve(baseline, approval_ref="change-duplicate")

    assert first["version"] == second["version"] == 1
    assert not first["active"]
    active = store.activate(1)
    assert active["active"]
    assert store.active_payload("scale", "ci-win") == baseline
    status = store.status()
    assert status["version_count"] == 1
    assert [event["action"] for event in status["recent_audit"]] == ["approve", "activate"]


def test_activate_new_version_and_rollback_to_previous_active(tmp_path):
    store = registry(tmp_path)
    v1 = store.approve(scale_baseline(shift=0), approval_ref="change-1")["version"]
    v2 = store.approve(scale_baseline(shift=20), approval_ref="change-2")["version"]
    store.activate(v1)
    store.activate(v2)

    rolled_back = store.rollback("scale", "ci-win")

    assert rolled_back["version"] == v1
    assert rolled_back["active"]
    assert store.status()["recent_audit"][-1]["action"] == "rollback"


def test_explicit_rollback_cannot_cross_environment_or_type(tmp_path):
    store = registry(tmp_path)
    scale_version = store.approve(scale_baseline("ci-win"), approval_ref="scale-1")["version"]
    ops_version = store.approve(operational_baseline("staging"), approval_ref="ops-1")["version"]
    store.activate(scale_version)
    store.activate(ops_version)

    with pytest.raises(BaselineRegistryError, match="rollback_target_mismatch"):
        store.rollback("scale", "ci-win", target_version=ops_version)
    assert store.active_payload("operational-alerts", "staging")["ready"]


def test_registry_rejects_unreviewed_tampered_sensitive_and_unsafe_approval(tmp_path):
    store = registry(tmp_path)
    unready = operational_baseline()
    unready["ready"] = False
    with pytest.raises(BaselineRegistryError, match="requires_review"):
        store.approve(unready, approval_ref="change-1")

    tampered = scale_baseline()
    tampered["recommended_scale_thresholds_ms"]["router"] += 1
    with pytest.raises(BaselineRegistryError, match="baseline_id_mismatch"):
        store.approve(tampered, approval_ref="change-1")

    sensitive = scale_baseline()
    sensitive["api_key"] = "sk-secret"
    # Re-signing cannot make forbidden content acceptable.
    core = {key: value for key, value in sensitive.items() if key not in {"baseline_id", "generated_at"}}
    import hashlib
    sensitive["baseline_id"] = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(BaselineRegistryError, match="forbidden_content"):
        store.approve(sensitive, approval_ref="change-1")

    with pytest.raises(BaselineRegistryError, match="approval_ref_invalid"):
        store.approve(scale_baseline(), approval_ref="../../ticket")


def test_payload_database_tampering_is_detected(tmp_path):
    store = registry(tmp_path)
    version = store.approve(scale_baseline(), approval_ref="change-1")["version"]
    store.activate(version)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE baselines SET payload_json='{}' WHERE version=?", (version,))

    with pytest.raises(BaselineRegistryError, match="payload_hash_invalid"):
        store.active_payload("scale", "ci-win")


def test_active_pointer_and_audit_tampering_are_detected(tmp_path):
    store = registry(tmp_path)
    version = store.approve(scale_baseline(), approval_ref="change-1")["version"]
    store.activate(version)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE active_baselines SET activated_at=0")
    with pytest.raises(BaselineRegistryError, match="active_signature_invalid"):
        store.active_payload("scale", "ci-win")

    other = registry(tmp_path / "other")
    version = other.approve(scale_baseline(), approval_ref="change-1")["version"]
    other.activate(version)
    with sqlite3.connect(other.database) as connection:
        connection.execute("UPDATE baseline_audit SET action='forged' WHERE sequence=1")
    with pytest.raises(BaselineRegistryError, match="audit_signature_invalid"):
        other.status()


def test_wrong_signing_key_cannot_trust_existing_registry(tmp_path):
    key = BaselineRegistry.generate_key()
    store = registry(tmp_path, key)
    store.approve(scale_baseline(), approval_ref="change-1")
    reopened = BaselineRegistry(tmp_path / "registry.db", key=BaselineRegistry.generate_key())

    with pytest.raises(BaselineRegistryError, match="baseline_signature_invalid"):
        reopened.status()


def test_concurrent_duplicate_approvals_create_one_version(tmp_path):
    key = BaselineRegistry.generate_key()
    database = tmp_path / "registry.db"
    BaselineRegistry(database, key=key)
    baseline = scale_baseline()

    def approve(_):
        return BaselineRegistry(database, key=key).approve(
            baseline, approval_ref="change-concurrent"
        )["version"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        versions = list(pool.map(approve, range(16)))

    assert set(versions) == {1}
    assert BaselineRegistry(database, key=key).status()["version_count"] == 1


def test_status_is_metadata_only_and_secret_free(tmp_path):
    store = registry(tmp_path)
    version = store.approve(scale_baseline(), approval_ref="change-1")["version"]
    store.activate(version)
    serialized = json.dumps(store.status()).lower()

    assert "recommended_scale_thresholds_ms" not in serialized
    assert "workload" not in serialized
    assert "payload" not in serialized
    assert "database" not in serialized
    assert "key" not in serialized.replace("key_id", "")


def test_cli_approve_activate_status_and_scale_gate_exit_codes(tmp_path, capsys):
    database = tmp_path / "registry.db"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(scale_baseline()), encoding="utf-8")

    assert main([
        "--database", str(database), "approve", "--baseline", str(baseline_path),
        "--approval-ref", "change-1"
    ]) == 0
    assert main(["--database", str(database), "activate", "--version", "1"]) == 0
    assert main(["--database", str(database), "status"]) == 0

    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(scale_report()), encoding="utf-8")
    output = tmp_path / "gate.json"
    assert main([
        "--database", str(database), "gate-scale", "--environment-id", "ci-win",
        "--candidate", str(candidate_path), "--output", str(output)
    ]) == 0
    assert output.exists()

    regressed = scale_report()
    regressed["phases"][0]["elapsed_ms"] = 1_000_000
    candidate_path.write_text(json.dumps(regressed), encoding="utf-8")
    assert main([
        "--database", str(database), "gate-scale", "--environment-id", "ci-win",
        "--candidate", str(candidate_path)
    ]) == 1
    assert "baseline registry error" not in capsys.readouterr().out


def test_cli_operational_gate_and_invalid_input(tmp_path):
    database = tmp_path / "registry.db"
    baseline_path = tmp_path / "ops-baseline.json"
    baseline_path.write_text(json.dumps(operational_baseline()), encoding="utf-8")
    assert main([
        "--database", str(database), "approve", "--baseline", str(baseline_path),
        "--approval-ref", "ops-change"
    ]) == 0
    assert main(["--database", str(database), "activate", "--version", "1"]) == 0

    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(operational_snapshot()), encoding="utf-8")
    assert main([
        "--database", str(database), "gate-operational", "--environment-id", "staging",
        "--snapshot", str(snapshot_path)
    ]) == 0

    snapshot = operational_snapshot()
    snapshot["metrics"]["rates"]["llm_error_rate"] = 0.9
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert main([
        "--database", str(database), "gate-operational", "--environment-id", "staging",
        "--snapshot", str(snapshot_path)
    ]) == 1

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    assert main([
        "--database", str(database), "approve", "--baseline", str(invalid),
        "--approval-ref", "bad"
    ]) == 2



def test_auto_generated_key_reopens_and_missing_active_is_safe(tmp_path):
    database = tmp_path / "registry.db"
    first = BaselineRegistry(database)
    version = first.approve(scale_baseline(), approval_ref="change-auto-key")["version"]
    first.activate(version)

    reopened = BaselineRegistry(database)
    assert reopened.key_id == first.key_id
    assert reopened.active_payload("scale", "ci-win")["baseline_id"] == scale_baseline()["baseline_id"]
    with pytest.raises(BaselineRegistryError, match="rollback_target_not_found"):
        reopened.rollback("scale", "ci-win")

    empty = BaselineRegistry(tmp_path / "empty.db")
    with pytest.raises(BaselineRegistryError, match="active_baseline_not_found"):
        empty.active_payload("scale", "ci-win")
    with pytest.raises(BaselineRegistryError, match="active_baseline_not_found"):
        empty.rollback("scale", "ci-win")


def test_explicit_rollback_and_independent_environment_pointers(tmp_path):
    store = registry(tmp_path)
    ci_v1 = store.approve(scale_baseline("ci-win", 0), approval_ref="ci-1")["version"]
    ci_v2 = store.approve(scale_baseline("ci-win", 30), approval_ref="ci-2")["version"]
    prod = store.approve(scale_baseline("prod", 10), approval_ref="prod-1")["version"]
    store.activate(ci_v1)
    store.activate(ci_v2)
    store.activate(prod)

    assert store.rollback("scale", "ci-win", target_version=ci_v1)["version"] == ci_v1
    assert store.active_payload("scale", "prod")["environment_id"] == "prod"
    active = {(item["environment_id"], item["version"]) for item in store.status()["versions"] if item["active"]}
    assert active == {("ci-win", ci_v1), ("prod", prod)}


def test_resigned_active_pointer_cannot_cross_environment(tmp_path):
    store = registry(tmp_path)
    ci = store.approve(scale_baseline("ci-win"), approval_ref="ci-1")["version"]
    prod = store.approve(scale_baseline("prod"), approval_ref="prod-1")["version"]
    store.activate(ci)
    with sqlite3.connect(store.database) as connection:
        row = connection.execute(
            "SELECT baseline_type, environment_id, activated_at FROM active_baselines "
            "WHERE baseline_type='scale' AND environment_id='ci-win'"
        ).fetchone()
        fields = {
            "baseline_type": row[0],
            "environment_id": row[1],
            "version": prod,
            "activated_at": row[2],
        }
        connection.execute(
            "UPDATE active_baselines SET version=?, signature=? "
            "WHERE baseline_type='scale' AND environment_id='ci-win'",
            (prod, store._sign("active", fields)),
        )

    with pytest.raises(BaselineRegistryError, match="active_baseline_identity_mismatch"):
        store.active_payload("scale", "ci-win")


def test_resigned_audit_still_requires_valid_semantics_and_linked_identity(tmp_path):
    store = registry(tmp_path)
    ci = store.approve(scale_baseline("ci-win"), approval_ref="ci-1")["version"]
    prod = store.approve(scale_baseline("prod"), approval_ref="prod-1")["version"]
    store.activate(ci)
    with sqlite3.connect(store.database) as connection:
        row = connection.execute("SELECT * FROM baseline_audit WHERE sequence=1").fetchone()
        fields = {
            "sequence": row[0],
            "action": "forged",
            "baseline_type": row[2],
            "environment_id": row[3],
            "target_version": row[4],
            "previous_version": row[5],
            "occurred_at": row[6],
        }
        connection.execute(
            "UPDATE baseline_audit SET action=?, signature=? WHERE sequence=1",
            ("forged", store._sign("audit", fields)),
        )
    with pytest.raises(BaselineRegistryError, match="audit_semantics_invalid"):
        store.status()

    other = registry(tmp_path / "linked")
    ci = other.approve(scale_baseline("ci-win"), approval_ref="ci-1")["version"]
    prod = other.approve(scale_baseline("prod"), approval_ref="prod-1")["version"]
    other.activate(ci)
    with sqlite3.connect(other.database) as connection:
        row = connection.execute(
            "SELECT * FROM baseline_audit WHERE action='activate'"
        ).fetchone()
        fields = {
            "sequence": row[0],
            "action": row[1],
            "baseline_type": row[2],
            "environment_id": row[3],
            "target_version": row[4],
            "previous_version": prod,
            "occurred_at": row[6],
        }
        connection.execute(
            "UPDATE baseline_audit SET previous_version=?, signature=? WHERE sequence=?",
            (prod, other._sign("audit", fields), row[0]),
        )
    with pytest.raises(BaselineRegistryError, match="audit_version_identity_mismatch"):
        other.status()


def test_sensitive_environment_references_are_rejected_after_resigning(tmp_path):
    store = registry(tmp_path)
    sensitive = scale_baseline()
    sensitive["notes"] = "read ${WORLD_SIM_API_KEY} during deployment"
    core = {
        key: value
        for key, value in sensitive.items()
        if key not in {"baseline_id", "generated_at"}
    }
    import hashlib
    sensitive["baseline_id"] = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    with pytest.raises(BaselineRegistryError, match="forbidden_content"):
        store.approve(sensitive, approval_ref="change-sensitive-ref")


def test_cli_invalid_gate_is_exit_2_and_rollback_writes_metadata(tmp_path, capsys):
    database = tmp_path / "registry.db"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(scale_baseline()), encoding="utf-8")
    assert main([
        "--database", str(database), "approve", "--baseline", str(baseline_path),
        "--approval-ref", "change-1",
    ]) == 0
    second = tmp_path / "baseline-2.json"
    second.write_text(json.dumps(scale_baseline(shift=30)), encoding="utf-8")
    assert main([
        "--database", str(database), "approve", "--baseline", str(second),
        "--approval-ref", "change-2",
    ]) == 0
    assert main(["--database", str(database), "activate", "--version", "1"]) == 0
    assert main(["--database", str(database), "activate", "--version", "2"]) == 0

    incompatible = scale_report()
    incompatible["suite_id"] = "different-suite"
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(incompatible), encoding="utf-8")
    assert main([
        "--database", str(database), "gate-scale", "--environment-id", "ci-win",
        "--candidate", str(candidate),
    ]) == 2
    output = capsys.readouterr().out.lower()
    assert "candidate_invalid" in output
    assert "different-suite" not in output
    assert str(candidate).lower() not in output

    rollback_output = tmp_path / "rollback.json"
    assert main([
        "--database", str(database), "rollback", "--baseline-type", "scale",
        "--environment-id", "ci-win", "--output", str(rollback_output),
    ]) == 0
    metadata = json.loads(rollback_output.read_text(encoding="utf-8"))
    assert metadata["version"] == 1
    assert metadata["active"] is True
    serialized = json.dumps(metadata).lower()
    assert "recommended_scale_thresholds_ms" not in serialized
    assert str(database).lower() not in serialized


def test_baseline_version_is_covered_by_signature(tmp_path):
    store = registry(tmp_path)
    store.approve(scale_baseline(), approval_ref="change-version")
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE baselines SET version=99 WHERE version=1")

    with pytest.raises(BaselineRegistryError, match="baseline_signature_invalid"):
        store.status()
