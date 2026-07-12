"""Deterministic scale/soak harness for DLC, routing, world, and memory paths."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import tempfile
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence, TypeVar

from .court_graph import CourtGraph, Official
from .dlc_registry import DLCRegistry
from .memory_store import StructuredMemoryStore
from .router import RouterAgent
from .trajectory_evaluation import write_report_atomic
from .types import AgentAction
from .world_commands import WorldCommand, WorldCommandType
from .world_state import Entity, OngoingEvent, ResourceDefinition, WorldState


class ScaleEvaluationError(ValueError):
    """Raised when a scale configuration is malformed."""


@dataclass(frozen=True)
class ScaleConfig:
    dlc_count: int = 8
    agents_per_dlc: int = 25
    router_agent_count: int = 400
    router_action_count: int = 1200
    router_fanout: int = 20
    world_entity_count: int = 80
    world_command_count: int = 1200
    world_batch_size: int = 100
    memory_store_count: int = 80
    memory_records_per_store: int = 80
    memory_query_count: int = 240
    soak_cycles: int = 1
    soak_commands_per_cycle: int = 20
    soak_memory_records_per_cycle: int = 20
    thresholds_ms: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ScaleConfig":
        fields = cls.__dataclass_fields__
        unknown = sorted(set(raw) - set(fields))
        if unknown:
            raise ScaleEvaluationError("Unknown scale config fields: " + ", ".join(unknown))
        values: dict[str, Any] = {}
        for name in fields:
            if name == "thresholds_ms":
                thresholds = raw.get(name, {})
                if not isinstance(thresholds, Mapping):
                    raise ScaleEvaluationError("thresholds_ms must be an object")
                values[name] = {
                    str(key): _positive_float(value, f"thresholds_ms.{key}")
                    for key, value in thresholds.items()
                }
            elif name in raw:
                values[name] = _positive_int(raw[name], name)
        config = cls(**values)
        if config.router_fanout > config.router_agent_count:
            object.__setattr__(config, "router_fanout", config.router_agent_count)
        if config.soak_commands_per_cycle < 2:
            raise ScaleEvaluationError("soak_commands_per_cycle must be at least 2")
        return config


@dataclass(frozen=True)
class ScalePhaseResult:
    phase: str
    operations: int
    elapsed_ms: float
    operations_per_second: float
    peak_memory_mb: float
    passed: bool
    invariants: Mapping[str, Any] = field(default_factory=dict)
    threshold_ms: float | None = None
    invariants_passed: bool = True
    performance_passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["invariants"] = dict(self.invariants)
        return data


@dataclass(frozen=True)
class ScaleSuiteResult:
    suite_id: str
    passed: bool
    config: ScaleConfig
    total_elapsed_ms: float
    peak_memory_mb: float
    phases: tuple[ScalePhaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "passed": self.passed,
            "config": asdict(self.config),
            "total_elapsed_ms": self.total_elapsed_ms,
            "peak_memory_mb": self.peak_memory_mb,
            "phases": [phase.to_dict() for phase in self.phases],
        }


class _NoLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("deterministic scale routing unexpectedly called an LLM")


T = TypeVar("T")


class ScaleEvaluator:
    def __init__(self, config: ScaleConfig | None = None) -> None:
        self.config = config or ScaleConfig()

    async def run(self, *, workspace: str | Path | None = None) -> ScaleSuiteResult:
        started = time.perf_counter()
        owns_temp = workspace is None
        temp = tempfile.TemporaryDirectory(prefix="world-scale-") if owns_temp else None
        root = Path(temp.name if temp is not None else workspace)  # type: ignore[arg-type]
        root.mkdir(parents=True, exist_ok=True)
        tracemalloc.start()
        try:
            phases = [
                self._run_dlc_phase(root / "dlc"),
                await self._run_router_phase(root / "router"),
                self._run_world_phase(root / "world"),
                self._run_memory_phase(),
            ]
            if self.config.soak_cycles > 1:
                phases.append(self._run_soak_phase(root / "soak"))
        finally:
            tracemalloc.stop()
            if temp is not None:
                temp.cleanup()
        elapsed = (time.perf_counter() - started) * 1000.0
        return ScaleSuiteResult(
            suite_id="world-simulator-scale-v1",
            passed=all(phase.passed for phase in phases),
            config=self.config,
            total_elapsed_ms=round(elapsed, 3),
            peak_memory_mb=max((phase.peak_memory_mb for phase in phases), default=0.0),
            phases=tuple(phases),
        )

    def _run_dlc_phase(self, root: Path) -> ScalePhaseResult:
        root.mkdir(parents=True, exist_ok=True)
        registry = DLCRegistry(root, manifest_roots=[root / "dlcs"])
        config = self.config

        def workload() -> dict[str, Any]:
            total_agents = 0
            for dlc_index in range(config.dlc_count):
                dlc_id = f"scale_{dlc_index:03d}"
                dependency = [f"scale_{dlc_index - 1:03d}"] if dlc_index else []
                registry.create({
                    "id": dlc_id,
                    "name": f"Scale DLC {dlc_index}",
                    "version": "1.0.0",
                    "dependencies": dependency,
                })
                entity_id = f"entity_{dlc_index:03d}"
                resource_name = f"token_{dlc_index:03d}"
                agents = [
                    {
                        "id": f"agent_{dlc_index:03d}_{agent_index:04d}",
                        "name": f"Agent {dlc_index}-{agent_index}",
                        "location_id": entity_id,
                    }
                    for agent_index in range(config.agents_per_dlc)
                ]
                registry.save_content(dlc_id, {
                    "schema_version": "1.0",
                    "resources": [{
                        "name": resource_name,
                        "kind": "asset",
                        "unit": "unit",
                        "anchor_rate": 1.0 + dlc_index / 10.0,
                    }],
                    "entities": [{"id": entity_id, "type": "location", "name": entity_id}],
                    "inventory": [{"entity_id": entity_id, "item_name": resource_name, "quantity": 10}],
                    "entity_values": [],
                    "relations": [],
                    "agents": agents,
                    "initial_events": [],
                    "ongoing_events": [],
                    "personas": {},
                    "prompts": {},
                })
                registry.publish(dlc_id)
                registry.set_enabled(dlc_id, True)
                total_agents += len(agents)
            ordered = registry.ordered_active_packages()
            issues = sum(len(registry.validate(item.manifest.id)) for item in ordered)
            return {
                "packages": len(ordered),
                "agents": total_agents,
                "validation_issues": issues,
                "dependency_order": [item.manifest.id for item in ordered],
            }

        result, elapsed_ms, peak_mb = _measure(workload)
        expected_order = [f"scale_{index:03d}" for index in range(config.dlc_count)]
        invariants = {
            "package_count": result["packages"],
            "agent_count": result["agents"],
            "validation_issues": result["validation_issues"],
            "dependency_order_valid": result["dependency_order"] == expected_order,
        }
        valid = (
            invariants["package_count"] == config.dlc_count
            and invariants["agent_count"] == config.dlc_count * config.agents_per_dlc
            and invariants["validation_issues"] == 0
            and invariants["dependency_order_valid"]
        )
        return self._phase("dlc", config.dlc_count * config.agents_per_dlc, elapsed_ms, peak_mb, valid, invariants)

    async def _run_router_phase(self, root: Path) -> ScalePhaseResult:
        root.mkdir(parents=True, exist_ok=True)
        court = CourtGraph(str(root / "router.db"))
        config = self.config
        manager_count = math.ceil(config.router_agent_count / config.router_fanout)
        officials = [Official("emperor", "Emperor", 15, "capital", "player", "official", None, "steady")]
        for index in range(manager_count):
            officials.append(Official(
                f"manager_{index:04d}", f"Manager {index}", 10, "capital",
                "official", "official", "emperor", "steady",
            ))
        worker_ids: list[str] = []
        for index in range(config.router_agent_count):
            worker_id = f"worker_{index:06d}"
            worker_ids.append(worker_id)
            officials.append(Official(
                worker_id, f"Worker {index}", 3, f"location_{index % 50:03d}",
                "official", "official", f"manager_{index // config.router_fanout:04d}", "steady",
            ))
        court.bulk_load_officials(officials)
        llm = _NoLLM()
        router = RouterAgent(llm, court)
        actions = [
            AgentAction(
                agent_id=worker_ids[index % len(worker_ids)],
                action="report_message",
                content="deterministic scale report",
                original_intent="report to direct superior",
                metadata={
                    "intent_id": f"scale_intent_{index:08d}",
                    "chain_id": f"scale_chain_{index:08d}",
                    "hop_count": 0,
                },
            )
            for index in range(config.router_action_count)
        ]

        async def workload() -> dict[str, Any]:
            commands = await router.process_batch(actions)
            return {
                "commands": len(commands),
                "llm_calls": llm.calls,
                "unique_intents": len({command.intent_id for command in commands}),
                "chain_preserved": all(
                    command.chain_id == action.metadata["chain_id"]
                    for command, action in zip(commands, actions)
                ),
                "deterministic": all(
                    command.metadata.get("route_strategy") == "deterministic"
                    for command in commands
                ),
            }

        result, elapsed_ms, peak_mb = await _measure_async(workload)
        invariants = dict(result)
        valid = (
            result["commands"] == config.router_action_count
            and result["llm_calls"] == 0
            and result["unique_intents"] == config.router_action_count
            and result["chain_preserved"]
            and result["deterministic"]
        )
        return self._phase("router", config.router_action_count, elapsed_ms, peak_mb, valid, invariants)

    def _run_world_phase(self, root: Path) -> ScalePhaseResult:
        root.mkdir(parents=True, exist_ok=True)
        state = WorldState(str(root / "world.db"))
        config = self.config
        resource = "scale_token"
        anchor_rate = 2.5
        state.upsert_resource_definition(ResourceDefinition(
            name=resource,
            unit="token",
            kind="asset",
            default_quantity=0,
            min_quantity=0,
            anchor_rate=anchor_rate,
        ))
        entity_ids = [f"world_entity_{index:05d}" for index in range(config.world_entity_count)]
        for entity_id in entity_ids:
            state.upsert_entity(Entity(id=entity_id, type="location", name=entity_id))
            state.set_inventory(entity_id, resource, resource, 100, command_id=f"seed_{entity_id}")
        before = sum(state.get_inventory_valuation(entity_id)["base_value"] for entity_id in entity_ids)

        def workload() -> dict[str, Any]:
            commands = [
                WorldCommand(
                    command_id=f"scale_world_{index:08d}",
                    command_type=WorldCommandType.ADJUST_RESOURCE,
                    entity_id=entity_ids[index % len(entity_ids)],
                    chain_id=f"scale_chain_{index:08d}",
                    intent_id=f"scale_intent_{index:08d}",
                    payload={"resource_name": resource, "quantity_delta": 1},
                )
                for index in range(config.world_command_count)
            ]
            applied = 0
            batches_committed = 0
            for start in range(0, len(commands), config.world_batch_size):
                batch = state.command_bus.execute_batch(
                    commands[start:start + config.world_batch_size], atomic=True,
                )
                applied += batch.applied_count
                batches_committed += int(batch.ok and batch.committed)

            replay_commands = commands[: min(20, len(commands))]
            replay_batch = state.command_bus.execute_batch(replay_commands, atomic=True)
            replayed = replay_batch.replayed_count
            after = sum(state.get_inventory_valuation(entity_id)["base_value"] for entity_id in entity_ids)
            ledger_count = sum(len(state.get_inventory_ledger(entity_id, limit=1000)) for entity_id in entity_ids)
            expected_batches = math.ceil(len(commands) / config.world_batch_size)
            return {
                "applied": applied,
                "replayed": replayed,
                "batch_size": config.world_batch_size,
                "batches_committed": batches_committed,
                "expected_batches": expected_batches,
                "replay_batch_committed": replay_batch.ok and replay_batch.committed,
                "before_base_value": before,
                "after_base_value": after,
                "expected_after_base_value": before + config.world_command_count * anchor_rate,
                "ledger_count": ledger_count,
            }

        result, elapsed_ms, peak_mb = _measure(workload)
        expected_replays = min(20, config.world_command_count)
        conservation_error = abs(result["after_base_value"] - result["expected_after_base_value"])
        invariants = {
            **result,
            "conservation_error": conservation_error,
            "idempotent_replays": result["replayed"] == expected_replays,
        }
        valid = (
            result["applied"] == config.world_command_count
            and result["replayed"] == expected_replays
            and result["batches_committed"] == result["expected_batches"]
            and result["replay_batch_committed"]
            and conservation_error <= 1e-6
            and result["ledger_count"] == config.world_entity_count + config.world_command_count
        )
        return self._phase("world", config.world_command_count, elapsed_ms, peak_mb, valid, invariants)

    def _run_memory_phase(self) -> ScalePhaseResult:
        config = self.config

        def workload() -> dict[str, Any]:
            stores: list[StructuredMemoryStore] = []
            target_ids: list[str] = []
            for store_index in range(config.memory_store_count):
                store = StructuredMemoryStore(max_records=config.memory_records_per_store + 10)
                target_id = f"target_{store_index:05d}"
                target_ids.append(target_id)
                for record_index in range(config.memory_records_per_store):
                    is_target = record_index == config.memory_records_per_store // 2
                    store.remember(
                        kind="commitment" if is_target else "episodic",
                        content=(
                            f"critical target marker for agent {store_index}"
                            if is_target else f"routine archive note {record_index} for agent {store_index}"
                        ),
                        entities=[f"agent_{store_index:05d}"],
                        importance=1.0 if is_target else 0.2,
                        status="unresolved" if is_target else "active",
                        memory_id=target_id if is_target else f"mem_{store_index:05d}_{record_index:05d}",
                        created_at=float(record_index + 1),
                    )
                stores.append(store)
            hits = 0
            for query_index in range(config.memory_query_count):
                store_index = query_index % len(stores)
                results = stores[store_index].retrieve(
                    "critical target marker",
                    entities=[f"agent_{store_index:05d}"],
                    limit=5,
                    max_chars=1200,
                    now=float(config.memory_records_per_store + 2),
                )
                hits += int(any(item.record.memory_id == target_ids[store_index] for item in results))
            return {
                "records": sum(len(store) for store in stores),
                "queries": config.memory_query_count,
                "recall_hits": hits,
                "recall_rate": hits / config.memory_query_count,
            }

        result, elapsed_ms, peak_mb = _measure(workload)
        invariants = dict(result)
        valid = (
            result["records"] == config.memory_store_count * config.memory_records_per_store
            and result["recall_hits"] == config.memory_query_count
        )
        operations = config.memory_store_count * config.memory_records_per_store + config.memory_query_count
        return self._phase("memory", operations, elapsed_ms, peak_mb, valid, invariants)

    def _run_soak_phase(self, root: Path) -> ScalePhaseResult:
        root.mkdir(parents=True, exist_ok=True)
        config = self.config
        registry_root = root / "dlc"
        registry = DLCRegistry(registry_root, manifest_roots=[registry_root / "dlcs"])
        package = registry.create({
            "id": "soak_package",
            "name": "Soak Package",
            "version": "1.0.0",
        })
        base_content = {
            "schema_version": "1.0",
            "resources": [{
                "name": "soak_token", "kind": "asset", "unit": "token",
                "anchor_rate": 2.0,
            }],
            "entities": [{"id": "soak_place", "type": "location", "name": "Soak Place"}],
            "inventory": [{
                "entity_id": "soak_place", "item_name": "soak_token", "quantity": 10,
            }],
            "entity_values": [],
            "relations": [],
            "agents": [{"id": "soak_agent", "name": "Soak Agent", "location_id": "soak_place"}],
            "initial_events": [],
            "ongoing_events": [],
            "personas": {"soak_agent": "revision:0"},
            "prompts": {},
        }
        registry.save_content(package.manifest.id, base_content)
        registry.publish(package.manifest.id)
        registry.set_enabled(package.manifest.id, True)

        world = WorldState(str(root / "world.db"))
        resource = "soak_token"
        anchor_rate = 2.0
        world.upsert_resource_definition(ResourceDefinition(
            name=resource, unit="token", kind="asset", default_quantity=0,
            min_quantity=0, anchor_rate=anchor_rate,
        ))
        entity_ids = ("soak_a", "soak_b")
        for entity_id in entity_ids:
            world.upsert_entity(Entity(id=entity_id, type="location", name=entity_id))
            world.set_inventory(
                entity_id, resource, resource, 100, command_id=f"soak_seed_{entity_id}",
            )
        before = sum(
            world.get_inventory_valuation(entity_id)["base_value"]
            for entity_id in entity_ids
        )
        memory_limit = max(10, config.soak_memory_records_per_cycle * 2)
        memory = StructuredMemoryStore(max_records=memory_limit)
        commitment_id = "soak_commitment"
        memory.remember(
            kind="commitment", content="preserve soak invariant",
            entities=["soak_agent"], importance=1.0, status="unresolved",
            memory_id=commitment_id, created_at=0.0,
        )

        def workload() -> dict[str, Any]:
            reloads = 0
            ticks = 0
            matured_applied = 0
            accepted = 0
            rejected = 0
            committed_mixed_batches = 0
            for cycle in range(config.soak_cycles):
                content = registry.load_content("soak_package")
                content["personas"] = {"soak_agent": f"revision:{cycle + 1}"}
                registry.save_content("soak_package", content)
                registry.discover()
                active = registry.ordered_active_packages()
                loaded = registry.load_content("soak_package")
                if len(active) == 1 and loaded["personas"]["soak_agent"] == f"revision:{cycle + 1}":
                    reloads += 1

                event_id = f"soak_event_{cycle:06d}"
                world.add_ongoing_event(OngoingEvent(
                    event_id=event_id, entity_id=entity_ids[0],
                    event_type="resource", description="soak tick",
                    start_time=float(cycle), end_time=float(cycle + 1),
                    expected_result={
                        "inventory_deltas": [{"resource": resource, "delta": 1}],
                    },
                    source_action_id=f"soak_tick_{cycle:06d}",
                ), command_id=f"soak_event_create_{cycle:06d}")

                commands = [
                    WorldCommand(
                        command_id=f"soak_command_{cycle:06d}_{index:04d}",
                        command_type=WorldCommandType.ADJUST_RESOURCE,
                        entity_id=entity_ids[index % len(entity_ids)],
                        chain_id=f"soak_chain_{cycle:06d}",
                        intent_id=f"soak_intent_{cycle:06d}_{index:04d}",
                        sim_time=float(cycle),
                        payload={
                            "resource_name": resource,
                            "quantity_delta": -100000 if index == 0 else 1,
                        },
                    )
                    for index in range(config.soak_commands_per_cycle)
                ]
                batch = world.command_bus.execute_batch(commands, atomic=False)
                accepted += batch.applied_count
                rejected += batch.rejected_count
                committed_mixed_batches += int(batch.committed)
                matured = world.apply_matured_events(float(cycle + 1))
                matured_applied += sum(result.ok for result in matured)
                ticks += 1

                for index in range(config.soak_memory_records_per_cycle):
                    memory.remember(
                        kind="episodic",
                        content=f"soak cycle {cycle} record {index}",
                        entities=["soak_agent"], importance=0.1,
                        memory_id=f"soak_memory_{cycle:06d}_{index:04d}",
                        created_at=float(cycle * config.soak_memory_records_per_cycle + index + 1),
                    )
                memory.prune(now=float((cycle + 1) * config.soak_memory_records_per_cycle + 1))

            after = sum(
                world.get_inventory_valuation(entity_id)["base_value"]
                for entity_id in entity_ids
            )
            expected_accepted = config.soak_cycles * (config.soak_commands_per_cycle - 1)
            expected_after = before + (expected_accepted + config.soak_cycles) * anchor_rate
            return {
                "cycles_completed": config.soak_cycles,
                "dlc_reloads": reloads,
                "world_ticks": ticks,
                "matured_events_applied": matured_applied,
                "accepted_commands": accepted,
                "rejected_commands": rejected,
                "committed_mixed_batches": committed_mixed_batches,
                "expected_accepted_commands": expected_accepted,
                "before_base_value": before,
                "after_base_value": after,
                "expected_after_base_value": expected_after,
                "conservation_error": abs(after - expected_after),
                "memory_records": len(memory),
                "memory_limit": memory_limit,
                "memory_compacted": (
                    1 + config.soak_cycles * config.soak_memory_records_per_cycle - len(memory)
                ),
                "commitment_retained": any(
                    record.memory_id == commitment_id and record.status == "unresolved"
                    for record in memory.records
                ),
                "remaining_events": len(world.list_ongoing_events()),
            }

        result, elapsed_ms, peak_mb = _measure(workload)
        valid = (
            result["cycles_completed"] == config.soak_cycles
            and result["dlc_reloads"] == config.soak_cycles
            and result["world_ticks"] == config.soak_cycles
            and result["matured_events_applied"] == config.soak_cycles
            and result["accepted_commands"] == result["expected_accepted_commands"]
            and result["rejected_commands"] == config.soak_cycles
            and result["committed_mixed_batches"] == config.soak_cycles
            and result["conservation_error"] <= 1e-6
            and result["memory_records"] <= result["memory_limit"]
            and result["memory_compacted"] > 0
            and result["commitment_retained"]
            and result["remaining_events"] == 0
        )
        operations = config.soak_cycles * (
            config.soak_commands_per_cycle
            + config.soak_memory_records_per_cycle
            + 2
        )
        return self._phase("soak", operations, elapsed_ms, peak_mb, valid, result)

    def _phase(
        self,
        name: str,
        operations: int,
        elapsed_ms: float,
        peak_mb: float,
        invariants_valid: bool,
        invariants: Mapping[str, Any],
    ) -> ScalePhaseResult:
        threshold = self.config.thresholds_ms.get(name)
        performance_passed = threshold is None or elapsed_ms <= threshold
        passed = invariants_valid and performance_passed
        throughput = operations / max(elapsed_ms / 1000.0, 1e-9)
        return ScalePhaseResult(
            phase=name,
            operations=operations,
            elapsed_ms=round(elapsed_ms, 3),
            operations_per_second=round(throughput, 3),
            peak_memory_mb=round(peak_mb, 3),
            passed=passed,
            invariants=dict(invariants),
            threshold_ms=threshold,
            invariants_passed=invariants_valid,
            performance_passed=performance_passed,
        )


def _measure(function: Callable[[], T]) -> tuple[T, float, float]:
    tracemalloc.reset_peak()
    started = time.perf_counter()
    result = function()
    elapsed = (time.perf_counter() - started) * 1000.0
    _, peak = tracemalloc.get_traced_memory()
    return result, elapsed, peak / (1024 * 1024)


async def _measure_async(function: Callable[[], Awaitable[T]]) -> tuple[T, float, float]:
    tracemalloc.reset_peak()
    started = time.perf_counter()
    result = await function()
    elapsed = (time.perf_counter() - started) * 1000.0
    _, peak = tracemalloc.get_traced_memory()
    return result, elapsed, peak / (1024 * 1024)


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ScaleEvaluationError(f"{label} must be an integer") from exc
    if number <= 0:
        raise ScaleEvaluationError(f"{label} must be positive")
    return number


def _positive_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ScaleEvaluationError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ScaleEvaluationError(f"{label} must be a positive finite number")
    return number


def load_scale_config(path: str | Path) -> ScaleConfig:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaleEvaluationError(f"Cannot load scale config: {source}") from exc
    if not isinstance(raw, Mapping):
        raise ScaleEvaluationError("Scale config root must be an object")
    return ScaleConfig.from_mapping(raw)


async def _run_cli(args: argparse.Namespace) -> int:
    config = load_scale_config(args.config) if args.config else ScaleConfig()
    report = await ScaleEvaluator(config).run(workspace=args.workspace or None)
    payload = report.to_dict()
    if args.output:
        write_report_atomic(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic simulator scale evaluation")
    parser.add_argument("--config", default="")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_cli(args))
    except ScaleEvaluationError as exc:
        print(f"scale evaluation error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
