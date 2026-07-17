"""Isolated allowlisted runtime tools for credentialed trajectory evaluation.

Each evaluation opens a disposable WorldState database and a fresh structured-memory
store from a bounded declarative fixture.  The module never accepts import paths,
production database locations, provider configuration, or arbitrary tool handlers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any, AsyncIterator, Mapping

from .memory_store import StructuredMemoryStore
from .trajectory_evaluation import LiveToolExecutor
from .world_commands import WorldCommand, WorldCommandType
from .world_state import Entity, ResourceDefinition, WorldState


class TrajectoryRuntimeError(ValueError):
    """Raised when a runtime fixture crosses the bounded evaluation contract."""


_ID_RE = re.compile(r"^[\w.:-]{1,96}$", re.UNICODE)
_ALLOWED_TOOLS = frozenset({
    "recall_memory",
    "inspect_memory",
    "inspect_inventory",
    "apply_world_command",
    "inspect_world",
    "submit_report",
    "submit_memory_summary",
    "submit_advice",
    "route_intent",
})
_MAX_ITEMS = 32
_MAX_TEXT = 1200


@dataclass(frozen=True)
class _TransferScope:
    scope: str
    source_entity: str
    target_entity: str
    resource_id: str


@dataclass(frozen=True)
class _WorldSnapshot:
    topic: str
    entity_id: str
    resource_id: str
    daily_net_change: float
    emergency_floor: float
    external_purchase_available: bool


class IsolatedTrajectoryRuntimeFactory:
    """Open one disposable runtime session for every scenario execution."""

    @asynccontextmanager
    async def __call__(
        self,
        scenario: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> AsyncIterator[LiveToolExecutor]:
        fixture = execution.get("runtime_fixture")
        if not isinstance(fixture, Mapping):
            raise TrajectoryRuntimeError("runtime_fixture must be an object")
        with TemporaryDirectory(prefix="agentera-trajectory-") as temp_dir:
            session = _TrajectoryRuntimeSession.open(
                fixture,
                database_path=Path(temp_dir) / "world.sqlite3",
            )
            yield session.execute


class _TrajectoryRuntimeSession:
    def __init__(
        self,
        *,
        world: WorldState,
        memory: StructuredMemoryStore,
        generation: int,
        transfer_scopes: tuple[_TransferScope, ...],
        snapshots: tuple[_WorldSnapshot, ...],
        retrieval: Mapping[str, Any],
    ) -> None:
        self.world = world
        self.memory = memory
        self.generation = generation
        self.transfer_scopes = transfer_scopes
        self.snapshots = snapshots
        self.retrieval = dict(retrieval)
        self.results: dict[str, list[dict[str, Any]]] = {}
        self.command_counter = 0

    @classmethod
    def open(
        cls,
        fixture: Mapping[str, Any],
        *,
        database_path: Path,
    ) -> "_TrajectoryRuntimeSession":
        _allow_keys(fixture, {"schema_version", "world", "memory"}, "runtime_fixture")
        if str(fixture.get("schema_version", "")) != "1.0":
            raise TrajectoryRuntimeError("Unsupported runtime_fixture schema_version")

        world_fixture = _mapping(fixture.get("world", {}), "runtime_fixture.world")
        memory_fixture = _mapping(fixture.get("memory", {}), "runtime_fixture.memory")
        _allow_keys(
            world_fixture,
            {"generation", "entities", "resources", "inventory", "transfer_scopes", "snapshots"},
            "runtime_fixture.world",
        )
        _allow_keys(memory_fixture, {"records", "retrieval"}, "runtime_fixture.memory")

        world = WorldState(str(database_path))
        resources = _bounded_list(world_fixture.get("resources", []), "world.resources")
        for index, raw in enumerate(resources):
            item = _mapping(raw, f"world.resources[{index}]")
            _allow_keys(
                item,
                {"name", "unit", "kind", "default_quantity", "min_quantity", "max_quantity", "anchor_rate", "metadata"},
                f"world.resources[{index}]",
            )
            kind = _text(item.get("kind", "asset"), f"world.resources[{index}].kind", maximum=16)
            if kind not in {"asset", "metric", "parameter"}:
                raise TrajectoryRuntimeError("world resource kind is invalid")
            definition = ResourceDefinition(
                name=_identifier(item.get("name"), f"world.resources[{index}].name"),
                unit=_text(item.get("unit", ""), f"world.resources[{index}].unit", maximum=64, required=False),
                kind=kind,
                default_quantity=_finite(item.get("default_quantity", 0), f"world.resources[{index}].default_quantity"),
                min_quantity=_optional_finite(item.get("min_quantity", 0), f"world.resources[{index}].min_quantity"),
                max_quantity=_optional_finite(item.get("max_quantity"), f"world.resources[{index}].max_quantity"),
                anchor_rate=_optional_finite(item.get("anchor_rate", 1), f"world.resources[{index}].anchor_rate"),
                metadata=_scalar_mapping(item.get("metadata", {}), f"world.resources[{index}].metadata"),
            )
            world.upsert_resource_definition(definition)

        entities = _bounded_list(world_fixture.get("entities", []), "world.entities")
        for index, raw in enumerate(entities):
            item = _mapping(raw, f"world.entities[{index}]")
            _allow_keys(
                item,
                {"id", "type", "name", "description", "current_status", "metadata"},
                f"world.entities[{index}]",
            )
            world.upsert_entity(Entity(
                id=_identifier(item.get("id"), f"world.entities[{index}].id"),
                type=_identifier(item.get("type"), f"world.entities[{index}].type"),
                name=_text(item.get("name"), f"world.entities[{index}].name"),
                description=_text(item.get("description", ""), f"world.entities[{index}].description", required=False),
                current_status=_text(item.get("current_status", ""), f"world.entities[{index}].current_status", required=False),
                metadata=_scalar_mapping(item.get("metadata", {}), f"world.entities[{index}].metadata"),
            ))

        inventory = _bounded_list(world_fixture.get("inventory", []), "world.inventory")
        for index, raw in enumerate(inventory):
            item = _mapping(raw, f"world.inventory[{index}]")
            _allow_keys(item, {"entity_id", "resource_id", "quantity", "description"}, f"world.inventory[{index}]")
            entity_id = _identifier(item.get("entity_id"), f"world.inventory[{index}].entity_id")
            resource_id = _identifier(item.get("resource_id"), f"world.inventory[{index}].resource_id")
            world.set_inventory(
                entity_id,
                resource_id,
                resource_id,
                _finite(item.get("quantity", 0), f"world.inventory[{index}].quantity"),
                _text(item.get("description", ""), f"world.inventory[{index}].description", required=False),
                command_id=f"fixture-seed-{index}",
                reason="trajectory fixture seed",
            )

        transfer_scopes: list[_TransferScope] = []
        for index, raw in enumerate(_bounded_list(world_fixture.get("transfer_scopes", []), "world.transfer_scopes")):
            item = _mapping(raw, f"world.transfer_scopes[{index}]")
            _allow_keys(item, {"scope", "source_entity", "target_entity", "resource_id"}, f"world.transfer_scopes[{index}]")
            transfer_scopes.append(_TransferScope(
                scope=_identifier(item.get("scope"), f"world.transfer_scopes[{index}].scope"),
                source_entity=_identifier(item.get("source_entity"), f"world.transfer_scopes[{index}].source_entity"),
                target_entity=_identifier(item.get("target_entity"), f"world.transfer_scopes[{index}].target_entity"),
                resource_id=_identifier(item.get("resource_id"), f"world.transfer_scopes[{index}].resource_id"),
            ))

        snapshots: list[_WorldSnapshot] = []
        for index, raw in enumerate(_bounded_list(world_fixture.get("snapshots", []), "world.snapshots")):
            item = _mapping(raw, f"world.snapshots[{index}]")
            _allow_keys(
                item,
                {"topic", "entity_id", "resource_id", "daily_net_change", "emergency_floor", "external_purchase_available"},
                f"world.snapshots[{index}]",
            )
            snapshots.append(_WorldSnapshot(
                topic=_identifier(item.get("topic"), f"world.snapshots[{index}].topic"),
                entity_id=_identifier(item.get("entity_id"), f"world.snapshots[{index}].entity_id"),
                resource_id=_identifier(item.get("resource_id"), f"world.snapshots[{index}].resource_id"),
                daily_net_change=_finite(item.get("daily_net_change", 0), f"world.snapshots[{index}].daily_net_change"),
                emergency_floor=_finite(item.get("emergency_floor", 0), f"world.snapshots[{index}].emergency_floor"),
                external_purchase_available=_boolean(item.get("external_purchase_available", False), f"world.snapshots[{index}].external_purchase_available"),
            ))

        memory = StructuredMemoryStore(max_records=max(10, len(memory_fixture.get("records", [])) + 10))
        records = _bounded_list(memory_fixture.get("records", []), "memory.records")
        for index, raw in enumerate(records):
            item = _mapping(raw, f"memory.records[{index}]")
            _allow_keys(
                item,
                {"memory_id", "kind", "content", "entities", "importance", "created_at", "expires_at", "source_chain_id", "status", "metadata"},
                f"memory.records[{index}]",
            )
            raw_entities = _bounded_list(item.get("entities", []), f"memory.records[{index}].entities")
            memory.remember(
                memory_id=_identifier(item.get("memory_id"), f"memory.records[{index}].memory_id"),
                kind=_text(item.get("kind", "episodic"), f"memory.records[{index}].kind", maximum=32),
                content=_text(item.get("content"), f"memory.records[{index}].content"),
                entities=tuple(_identifier(value, f"memory.records[{index}].entities") for value in raw_entities),
                importance=_finite(item.get("importance", 0.5), f"memory.records[{index}].importance"),
                created_at=_optional_finite(item.get("created_at"), f"memory.records[{index}].created_at"),
                expires_at=_optional_finite(item.get("expires_at"), f"memory.records[{index}].expires_at"),
                source_chain_id=_text(item.get("source_chain_id", ""), f"memory.records[{index}].source_chain_id", maximum=96, required=False),
                status=_text(item.get("status", "active"), f"memory.records[{index}].status", maximum=32),
                metadata=_scalar_mapping(item.get("metadata", {}), f"memory.records[{index}].metadata"),
            )

        retrieval = _mapping(memory_fixture.get("retrieval", {}), "memory.retrieval")
        _allow_keys(
            retrieval,
            {"limit", "max_chars", "min_query_overlap", "unrelated_commitment_limit", "duplicate_overlap_threshold", "kind_diversity_bonus"},
            "memory.retrieval",
        )
        normalized_retrieval = {
            "limit": _bounded_int(retrieval.get("limit", 8), "memory.retrieval.limit", 1, 16),
            "max_chars": _bounded_int(retrieval.get("max_chars", 2400), "memory.retrieval.max_chars", 64, 8000),
            "min_query_overlap": _finite(retrieval.get("min_query_overlap", 0.08), "memory.retrieval.min_query_overlap"),
            "unrelated_commitment_limit": _bounded_int(retrieval.get("unrelated_commitment_limit", 1), "memory.retrieval.unrelated_commitment_limit", 0, 8),
            "duplicate_overlap_threshold": _finite(retrieval.get("duplicate_overlap_threshold", 0.85), "memory.retrieval.duplicate_overlap_threshold"),
            "kind_diversity_bonus": _finite(retrieval.get("kind_diversity_bonus", 0.6), "memory.retrieval.kind_diversity_bonus"),
        }
        generation = _bounded_int(world_fixture.get("generation", 1), "world.generation", 0, 2_147_483_647)
        return cls(
            world=world,
            memory=memory,
            generation=generation,
            transfer_scopes=tuple(transfer_scopes),
            snapshots=tuple(snapshots),
            retrieval=normalized_retrieval,
        )

    async def execute(
        self,
        _scenario: Mapping[str, Any],
        name: str,
        arguments: dict[str, Any],
        _tool_call: dict[str, Any],
    ) -> dict[str, Any]:
        if name not in _ALLOWED_TOOLS:
            return _rejected("tool_not_allowlisted")
        handler = getattr(self, f"_tool_{name}")
        try:
            result = handler(dict(arguments))
        except (KeyError, TypeError, ValueError):
            result = _rejected("invalid_tool_arguments")
        self.results.setdefault(name, []).append(dict(result))
        return result

    def _tool_recall_memory(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = _text(arguments.get("query"), "recall_memory.query", maximum=256)
        results = self.memory.retrieve(query, **self.retrieval)
        records = [self._memory_result(item.record, evidence_field="evidence") for item in results]
        ids = [item["id"] for item in records]
        status = records[0]["state"] if records else ""
        return {
            "ok": True,
            "accepted": True,
            "memory_ids": ids,
            "records": records,
            "_evaluation_evidence": {
                "memory_recalled": ids,
                "commitment_status": status,
            },
        }

    def _tool_inspect_memory(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        scope = _identifier(arguments.get("scope"), "inspect_memory.scope")
        if scope != "unresolved_commitments":
            return _rejected("unsupported_memory_scope")
        results = self.memory.retrieve(
            "unresolved commitment",
            kinds=("commitment",),
            **self.retrieval,
        )
        selected = [item for item in results if item.record.status == "unresolved"]
        records = [self._memory_result(item.record, evidence_field="summary") for item in selected]
        ids = [item["id"] for item in records]
        return {
            "ok": True,
            "accepted": True,
            "memory_ids": ids,
            "records": records,
            "_evaluation_evidence": {"memory_recalled": ids},
        }

    @staticmethod
    def _memory_result(record: Any, *, evidence_field: str) -> dict[str, Any]:
        state = "open" if record.status == "unresolved" else record.status
        return {
            "id": record.memory_id,
            "kind": record.kind,
            "state": state,
            evidence_field: record.content,
        }

    def _tool_inspect_inventory(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        scope_name = _identifier(arguments.get("scope"), "inspect_inventory.scope")
        scope = next((item for item in self.transfer_scopes if item.scope == scope_name), None)
        if scope is None:
            return _rejected("unknown_inventory_scope")
        item = self._inventory_item(scope.source_entity, scope.resource_id)
        definition = self.world.get_resource_definition(scope.resource_id)
        if item is None or definition is None or definition.kind != "asset":
            return _rejected("inventory_scope_unavailable")
        return {
            "ok": True,
            "accepted": True,
            "snapshot_generation": self.generation,
            "source_entity": scope.source_entity,
            "target_entity": scope.target_entity,
            "resource_id": scope.resource_id,
            "available_quantity": item.quantity,
            "unit_base_value": item.anchor_rate,
            "source_base_value_total": item.base_value,
        }

    def _tool_apply_world_command(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        inspected = self._last("inspect_inventory")
        if inspected is None or not inspected.get("ok"):
            return _rejected("inventory_evidence_required")
        if _text(arguments.get("command_type"), "command_type", maximum=64) != "transfer_resource":
            return _rejected("unsupported_world_command")
        source = _identifier(arguments.get("source_entity"), "source_entity")
        target = _identifier(arguments.get("target_entity"), "target_entity")
        resource = _identifier(arguments.get("resource_id"), "resource_id")
        quantity = _finite(arguments.get("quantity"), "quantity")
        unit_value = _finite(arguments.get("unit_base_value"), "unit_base_value")
        total_value = _finite(arguments.get("total_base_value"), "total_base_value")
        generation = _bounded_int(arguments.get("evidence_generation"), "evidence_generation", 0, 2_147_483_647)
        if quantity <= 0:
            return _rejected("invalid_transfer_quantity")
        if (
            source != inspected.get("source_entity")
            or target != inspected.get("target_entity")
            or resource != inspected.get("resource_id")
            or generation != inspected.get("snapshot_generation")
            or not _same(unit_value, inspected.get("unit_base_value"))
            or not _same(total_value, quantity * unit_value)
            or quantity > float(inspected.get("available_quantity", 0))
        ):
            return _rejected("world_command_contract_mismatch")

        before = float(self.world.get_economic_invariants()["inventory"]["total_base_value"])
        self.command_counter += 1
        transfer_id = f"trajectory-transfer-{self.command_counter}"
        batch = self.world.command_bus.execute_batch([
            WorldCommand(
                command_type=WorldCommandType.ADJUST_RESOURCE,
                command_id=f"{transfer_id}-debit",
                entity_id=source,
                payload={"resource_name": resource, "quantity_delta": -quantity, "reason": "trajectory transfer"},
                metadata={
                    "source": "trajectory_runtime",
                    "transfer_id": transfer_id,
                    "transfer_side": "debit",
                    "anchored_base_value": total_value,
                },
            ),
            WorldCommand(
                command_type=WorldCommandType.ADJUST_RESOURCE,
                command_id=f"{transfer_id}-credit",
                entity_id=target,
                payload={"resource_name": resource, "quantity_delta": quantity, "reason": "trajectory transfer"},
                metadata={
                    "source": "trajectory_runtime",
                    "transfer_id": transfer_id,
                    "transfer_side": "credit",
                    "anchored_base_value": total_value,
                },
            ),
        ], atomic=True)
        if not batch.ok or not batch.committed:
            return _rejected("world_command_rejected")
        after = float(self.world.get_economic_invariants()["inventory"]["total_base_value"])
        return {
            "ok": True,
            "accepted": True,
            "value_conserved": _same(before, after),
            "source_quantity": self._inventory_item(source, resource).quantity,
            "target_quantity": self._inventory_item(target, resource).quantity,
            "_evaluation_evidence": {
                "resource": {
                    "before_base_value": before,
                    "after_base_value": after,
                    "external_delta": 0,
                    "tolerance": 1e-6,
                }
            },
        }

    def _tool_inspect_world(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        topic = _identifier(arguments.get("topic"), "inspect_world.topic")
        snapshot = next((item for item in self.snapshots if item.topic == topic), None)
        if snapshot is None:
            return _rejected("unknown_world_topic")
        inventory = self._inventory_item(snapshot.entity_id, snapshot.resource_id)
        if inventory is None:
            return _rejected("world_snapshot_unavailable")
        if snapshot.daily_net_change < 0:
            reserve_days = inventory.quantity / abs(snapshot.daily_net_change)
        else:
            reserve_days = 0.0
        return {
            "ok": True,
            "accepted": True,
            "snapshot_generation": self.generation,
            f"{snapshot.topic}_quantity": inventory.quantity,
            "daily_net_change": snapshot.daily_net_change,
            "reserve_days": reserve_days,
            "emergency_floor": snapshot.emergency_floor,
            "external_purchase_available": snapshot.external_purchase_available,
        }

    def _tool_submit_report(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        recalled = self._last("recall_memory")
        memory_id = _identifier(arguments.get("commitment_id"), "submit_report.commitment_id")
        status = _text(arguments.get("status"), "submit_report.status", maximum=32)
        _text(arguments.get("summary"), "submit_report.summary")
        record = next(
            (
                item for item in (recalled or {}).get("records", [])
                if item.get("id") == memory_id
            ),
            None,
        )
        expected_status = "unresolved" if record and record.get("state") == "open" else (
            str(record.get("state", "")) if record else ""
        )
        valid = bool(record and status == expected_status)
        if not valid:
            return _rejected("report_not_grounded")
        return {
            "ok": True,
            "accepted": True,
            "_evaluation_evidence": {"commitment_status": status},
        }

    def _tool_submit_memory_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        inspected = self._last("inspect_memory")
        preserved_raw = _bounded_list(arguments.get("preserved_ids"), "preserved_ids")
        preserved = [_identifier(item, "preserved_ids") for item in preserved_raw]
        commitment_id = _identifier(arguments.get("commitment_id"), "commitment_id")
        status = _text(arguments.get("status"), "status", maximum=32)
        _text(arguments.get("summary"), "summary")
        known = set(inspected.get("memory_ids", [])) if inspected else set()
        if status != "retained" or commitment_id not in known or commitment_id not in preserved:
            return _rejected("memory_summary_not_grounded")
        return {
            "ok": True,
            "accepted": True,
            "preserved_ids": preserved,
            "_evaluation_evidence": {
                "memory_recalled": sorted(known),
                "commitment_status": status,
            },
        }

    def _tool_submit_advice(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        inspected = self._last("inspect_world")
        _text(arguments.get("summary"), "summary", maximum=160)
        actions = _bounded_list(arguments.get("actions"), "actions")
        if len(actions) != 2:
            return _rejected("advice_action_count_invalid")
        for action in actions:
            _text(action, "actions", maximum=100)
        generation = _bounded_int(arguments.get("evidence_generation"), "evidence_generation", 0, 2_147_483_647)
        reserve_days = _finite(arguments.get("reserve_days"), "reserve_days")
        grounded = bool(
            inspected
            and generation == inspected.get("snapshot_generation")
            and _same(reserve_days, inspected.get("reserve_days"))
        )
        if not grounded:
            return _rejected("advice_not_grounded")
        return {"ok": True, "accepted": True, "grounded": True}

    def _tool_route_intent(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        target = _identifier(arguments.get("target"), "route_intent.target")
        reason_code = _identifier(arguments.get("reason_code"), "route_intent.reason_code")
        return {
            "ok": True,
            "accepted": True,
            "target": target,
            "reason_code": reason_code,
            "_evaluation_evidence": {"route_target": target},
        }

    def _last(self, tool: str) -> dict[str, Any] | None:
        values = self.results.get(tool, [])
        return values[-1] if values else None

    def _inventory_item(self, entity_id: str, resource_id: str) -> Any | None:
        return next(
            (item for item in self.world.get_inventory(entity_id) if item.item_name == resource_id),
            None,
        )


def _rejected(code: str) -> dict[str, Any]:
    return {"ok": False, "accepted": False, "error_code": code, "error": "runtime tool rejected"}


def _same(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TrajectoryRuntimeError(f"{label} must be an object")
    if len(value) > _MAX_ITEMS:
        raise TrajectoryRuntimeError(f"{label} has too many fields")
    return value


def _allow_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    if set(value) - allowed:
        raise TrajectoryRuntimeError(f"{label} contains unsupported fields")


def _bounded_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise TrajectoryRuntimeError(f"{label} must be an array")
    if len(value) > _MAX_ITEMS:
        raise TrajectoryRuntimeError(f"{label} has too many items")
    return value


def _text(value: Any, label: str, *, maximum: int = _MAX_TEXT, required: bool = True) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise TrajectoryRuntimeError(f"{label} is required")
    if len(text) > maximum:
        raise TrajectoryRuntimeError(f"{label} is too long")
    return text


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label, maximum=96)
    if not _ID_RE.fullmatch(text):
        raise TrajectoryRuntimeError(f"{label} is not a safe identifier")
    return text


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryRuntimeError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise TrajectoryRuntimeError(f"{label} must be finite")
    return number


def _optional_finite(value: Any, label: str) -> float | None:
    return None if value is None else _finite(value, label)


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TrajectoryRuntimeError(f"{label} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise TrajectoryRuntimeError(f"{label} must be an integer")
    if not minimum <= number <= maximum:
        raise TrajectoryRuntimeError(f"{label} is out of range")
    return number


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise TrajectoryRuntimeError(f"{label} must be boolean")
    return value


def _scalar_mapping(value: Any, label: str) -> dict[str, Any]:
    raw = _mapping(value, label)
    result: dict[str, Any] = {}
    for key, item in raw.items():
        safe_key = _identifier(key, f"{label}.key")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise TrajectoryRuntimeError(f"{label} values must be scalar")
        if isinstance(item, str) and len(item) > 256:
            raise TrajectoryRuntimeError(f"{label} value is too long")
        if isinstance(item, float) and not math.isfinite(item):
            raise TrajectoryRuntimeError(f"{label} value must be finite")
        result[safe_key] = item
    return result
