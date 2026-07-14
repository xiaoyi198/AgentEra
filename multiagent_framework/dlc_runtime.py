"""Runtime materialization of normalized DLC bundle content."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .agent_runtime import AgentConfig, BaseAgent, InstitutionAgent, OfficialAgent
from .court_graph import CourtGraph, Official
from .causal_templates import expand_causal_rule_document
from .dlc_registry import (
    DLCManifest, DLCPackage, DLCRegistry, DLCRegistryError,
)
from .generation_archive import (
    GenerationArchiveError, GenerationArchiveStore,
)
from .prompt_registry import (
    PromptRegistry as FrameworkPromptRegistry,
    PromptRegistryError,
    PromptRenderResult,
)
from .types import EventObject
from .gameplay_contract import GameplayContract
from .world_state import (
    Entity,
    EntityValue,
    InventoryItem,
    OngoingEvent,
    ResourceDefinition,
    WorldState,
)

_DEFAULT_OFFICIAL_TEMPLATE = (
    "You are {name}, located at {location}, rank {rank}.\n"
    "{personality}\nYour direct superior is {superior}.\n"
    "Use tools to inspect the world before making decisions, then submit concrete actions."
)

_AGENT_PROMPT_VARIABLES = {
    "id", "name", "rank", "rank_title", "location", "location_id",
    "personality", "personality_seed", "superior", "superior_id",
    "role", "agent_type",
}


@dataclass
class DLCLoadReport:
    package_id: str
    resources: int = 0
    entities: int = 0
    inventory: int = 0
    entity_values: int = 0
    relations: int = 0
    ongoing_events: int = 0
    officials: int = 0
    agents: int = 0
    initial_events: int = 0
    random_events: int = 0
    causal_event_rules: int = 0
    time_rules: int = 0
    actions: int = 0
    issue_types: int = 0
    objectives: int = 0
    endings: int = 0
    seeded_world: bool = False
    model_profiles: dict[str, str] = field(default_factory=dict)
    prompt_hashes: dict[str, str] = field(default_factory=dict)
    gameplay_configured: bool = False
    player_identity_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DLCReloadPlan:
    current_generation: int
    target_generation: int
    package_ids: list[str] = field(default_factory=list)
    package_hashes: dict[str, str] = field(default_factory=dict)
    added_agents: list[str] = field(default_factory=list)
    removed_agents: list[str] = field(default_factory=list)
    changed_agents: list[str] = field(default_factory=list)
    unchanged_agents: list[str] = field(default_factory=list)
    changed_runtime_tasks: list[str] = field(default_factory=list)
    runtime_model_profiles: dict[str, str] = field(default_factory=dict)
    gameplay_changed: bool = False
    world_changes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["safe"] = self.safe
        return result


@dataclass
class _DLCMaterializedSnapshot:
    package_ids: tuple[str, ...]
    package_hashes: dict[str, str]
    agents: dict[str, BaseAgent]
    officials: list[Official]
    official_ids: set[str]
    prompts: dict[str, "PromptRegistry"]
    model_profiles: dict[str, str]
    agent_packages: dict[str, str]
    agent_fingerprints: dict[str, str]
    resources: list[ResourceDefinition]
    entities: list[Entity]
    inventory: list[InventoryItem]
    entity_values: list[EntityValue]
    relations: list[dict[str, Any]]
    ongoing_events: list[OngoingEvent]
    random_event_templates: list[dict[str, Any]]
    causal_event_rules: list[dict[str, Any]]
    time_rules: list[dict[str, Any]]
    action_definitions: list[dict[str, Any]]
    issue_definitions: list[dict[str, Any]]
    objective_definitions: list[dict[str, Any]]
    ending_definitions: list[dict[str, Any]]
    primary_package_id: str
    gameplay_contract: GameplayContract
    global_prompts: dict[str, str]
    global_prompt_hashes: dict[str, str]
    global_clients: dict[str, Any]
    global_model_profiles: dict[str, str]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class PromptRegistry(FrameworkPromptRegistry):
    """DLC prompt registry with persona-aware Agent prompt rendering."""

    def __init__(self, content: dict[str, Any], package: DLCPackage) -> None:
        prompt_sources = dict(content.get("prompts", {}))
        prompt_sources.setdefault("official_template", {
            "version": "framework-default-1",
            "template": _DEFAULT_OFFICIAL_TEMPLATE,
            "max_chars": 12_000,
        })
        try:
            super().__init__(prompt_sources)
        except (PromptRegistryError, TypeError, ValueError) as exc:
            raise DLCRegistryError(
                f"Invalid prompt registry in DLC {package.manifest.id}: {exc}"
            ) from exc
        self.personas = dict(content.get("personas", {}))
        self.metadata = dict(package.manifest.metadata)
        self.render_reports: dict[str, PromptRenderResult] = {}

    def build_agent_prompt(self, official: Official) -> str:
        rank_titles = self.metadata.get("rank_titles", {})
        rank_title = rank_titles.get(str(official.rank), f"rank {official.rank}")
        values = {
            "id": official.id,
            "name": official.name,
            "rank": official.rank,
            "rank_title": rank_title,
            "location": official.location_id,
            "location_id": official.location_id,
            "personality": self.personas.get(official.personality_seed, ""),
            "personality_seed": official.personality_seed,
            "superior": official.superior_id or "none",
            "superior_id": official.superior_id or "",
            "role": official.role,
            "agent_type": official.agent_type,
        }
        prompt_name = official.id if self.has(official.id) else "official_template"
        unsupported = [
            item for item in self.lint(prompt_name, allowed_variables=_AGENT_PROMPT_VARIABLES)
            if item.severity == "error"
        ]
        if unsupported:
            raise DLCRegistryError(
                f"Invalid official prompt template for {official.id}: "
                + "; ".join(item.message for item in unsupported)
            )
        try:
            report = self.render(prompt_name, values, strict=True)
        except PromptRegistryError as exc:
            raise DLCRegistryError(
                f"Invalid official prompt template for {official.id}: {exc}"
            ) from exc
        self.render_reports[official.id] = report
        prompt = report.text
        if official.extra_prompt:
            prompt += "\n\n## Additional traits\n\n" + official.extra_prompt
        return prompt


class ModelBindingResolver:
    """Resolve Agent model profiles without exposing or mutating provider secrets."""

    def __init__(self, llm_client: Any, bindings: dict[str, Any] | None = None) -> None:
        self.llm_client = llm_client
        raw = dict(bindings or {})
        self.task_bindings = dict(raw.get("tasks", {})) if isinstance(raw.get("tasks", {}), dict) else {}
        self.agent_bindings = dict(raw.get("agents", {})) if isinstance(raw.get("agents", {}), dict) else {}
        for task_name, profile in raw.items():
            if task_name not in {"tasks", "agents"} and isinstance(profile, str):
                self.task_bindings.setdefault(str(task_name), profile)

    def resolve(
        self,
        agent_id: str,
        agent_source: dict[str, Any] | None = None,
        *,
        task_type: str = "agent.turn",
    ) -> str:
        source = agent_source or {}
        explicit = source.get("model_profile")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        agent_profile = self.agent_bindings.get(agent_id)
        if isinstance(agent_profile, str) and agent_profile.strip():
            return agent_profile.strip()
        task_profile = self.task_bindings.get(task_type)
        if isinstance(task_profile, str) and task_profile.strip():
            return task_profile.strip()
        return ""

    def bind(self, agent_id: str, profile_name: str = "") -> Any:
        client = self.llm_client
        if profile_name:
            known = self._known_profiles(client)
            if known is not None and profile_name not in known:
                raise DLCRegistryError(
                    f"Agent {agent_id} references unknown model profile {profile_name!r}; "
                    f"available profiles: {', '.join(sorted(known)) or 'none'}"
                )
            if hasattr(client, "with_profile"):
                try:
                    client = client.with_profile(profile_name)
                except Exception as exc:
                    raise DLCRegistryError(
                        f"Cannot bind Agent {agent_id} to model profile {profile_name!r}: {exc}"
                    ) from exc
            elif hasattr(client, "for_agent"):
                try:
                    return client.for_agent(agent_id, profile_override=profile_name)
                except TypeError as exc:
                    raise DLCRegistryError(
                        f"LLM client does not support explicit model bindings for Agent {agent_id}"
                    ) from exc
            else:
                raise DLCRegistryError(
                    f"LLM client does not support explicit model bindings for Agent {agent_id}"
                )
        if hasattr(client, "for_agent"):
            return client.for_agent(agent_id)
        return client

    @staticmethod
    def _known_profiles(client: Any) -> set[str] | None:
        gateway = getattr(client, "gateway", None)
        if gateway is not None:
            names = getattr(gateway, "profile_names", None)
            if names is not None:
                return set(names)
            config = getattr(gateway, "config", None)
            models = getattr(config, "models", None)
            if isinstance(models, dict):
                return set(models)
        names = getattr(client, "profile_names", None)
        if names is not None:
            return set(names)
        return None


class AgentFactory:
    def __init__(
        self, llm_client: Any, agent_defaults: dict[str, Any] | None = None
    ) -> None:
        self.llm_client = llm_client
        self.agent_defaults = dict(agent_defaults or {})
        self.last_bindings: dict[str, str] = {}

    def create(
        self,
        officials: list[Official],
        prompts: PromptRegistry,
        *,
        package: DLCPackage,
        agent_sources: list[dict[str, Any]] | None = None,
        excluded_agent_ids: set[str] | frozenset[str] | None = None,
        legacy_role_player: bool = True,
    ) -> list[BaseAgent]:
        agents: list[BaseAgent] = []
        sources = {str(item.get("id", "")): item for item in (agent_sources or [])}
        excluded = set(excluded_agent_ids or ())
        base_client = self.llm_client
        if hasattr(base_client, "for_dlc"):
            base_client = base_client.for_dlc(package.manifest.id)
        resolver = ModelBindingResolver(base_client, package.manifest.model_bindings)
        self.last_bindings = {}
        for official in officials:
            if (
                official.id in excluded
                or (legacy_role_player and official.role == "player")
                or not official.enabled
            ):
                continue
            config = AgentConfig(
                agent_id=official.id,
                agent_type=official.agent_type,
                display_name=official.name,
                system_prompt=prompts.build_agent_prompt(official),
                personality_seed=official.personality_seed,
                **self.agent_defaults,
            )
            profile_name = resolver.resolve(official.id, sources.get(official.id))
            bound_llm = resolver.bind(official.id, profile_name)
            if profile_name:
                self.last_bindings[official.id] = profile_name
            agent_cls = InstitutionAgent if official.agent_type == "institution" else OfficialAgent
            agents.append(agent_cls(config, bound_llm))
        return agents


class DLCBundleLoader:
    """Convert normalized registry content into runtime objects and register them."""

    def __init__(
        self,
        registry: DLCRegistry,
        world_state: WorldState,
        court: CourtGraph,
        orchestrator: Any,
        llm_client: Any,
        *,
        generation_archive: GenerationArchiveStore | None = None,
        generation_history_limit: int = 20,
        agent_defaults: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self.world_state = world_state
        self.court = court
        self.orchestrator = orchestrator
        self.agent_factory = AgentFactory(llm_client, agent_defaults=agent_defaults)
        self.prompt_registries: dict[str, PromptRegistry] = {}
        self.generation: int = 0
        self._active_snapshot: _DLCMaterializedSnapshot | None = None
        self._managed_agent_ids: set[str] = set()
        self._managed_official_ids: set[str] = set()
        self._archived_agent_memory: dict[str, dict[str, Any]] = {}
        self._generation_history: dict[int, _DLCMaterializedSnapshot] = {}
        self._generation_order: list[int] = []
        self._generation_history_limit = max(1, int(generation_history_limit))
        self._archive_issues: list[dict[str, Any]] = []
        self._generation_archive = generation_archive
        if self._generation_archive is None:
            try:
                runtime_parent = Path(str(self.world_state._db_path)).resolve().parent
                self._generation_archive = GenerationArchiveStore(
                    runtime_parent / "dlc_generations",
                    history_limit=self._generation_history_limit,
                )
            except (GenerationArchiveError, OSError, ValueError):
                self._generation_archive = None
                self._archive_issues.append({"code": "archive_init_failed"})
        self._reload_lock = asyncio.Lock()
        self.reload_events: list[dict[str, Any]] = []
        self._reload_metrics: dict[str, Any] = {
            "attempts": 0,
            "successes": 0,
            "rollback_attempts": 0,
            "rollback_successes": 0,
            "rollback_failures": 0,
            "reload_in_progress": False,
            "current_waiting_inflight_agents": 0,
            "failures": 0,
            "unsafe_rejections": 0,
            "anchor_migration_rejections": 0,
            "resource_kind_migration_rejections": 0,
            "last_duration_ms": 0.0,
            "total_duration_ms": 0.0,
            "max_duration_ms": 0.0,
            "last_memory_migration_ms": 0.0,
            "total_memory_migration_ms": 0.0,
            "last_waiting_inflight_agents": 0,
            "max_waiting_inflight_agents": 0,
            "world_rows_added_total": {
                "resources": 0,
                "entities": 0,
                "inventory": 0,
                "entity_values": 0,
                "ongoing_events": 0,
            },
        }
        self._restore_generation_archive()

    @staticmethod
    def _resource(item: dict[str, Any]) -> ResourceDefinition:
        kind = item.get("kind", "asset")
        return ResourceDefinition(
            name=str(item["name"]),
            unit=str(item.get("unit", "")),
            kind=str(kind),
            default_quantity=float(item.get("default_quantity", item.get("default", 0.0))),
            min_quantity=item.get("min_quantity", item.get("min", 0.0)),
            max_quantity=item.get("max_quantity", item.get("max")),
            anchor_rate=(float(item.get("anchor_rate", 1.0)) if kind == "asset" else None),
            metadata=dict(item.get("metadata", {})),
        )

    @staticmethod
    def _official(item: dict[str, Any]) -> Official:
        return Official(
            id=str(item["id"]),
            name=str(item.get("name", item["id"])),
            rank=int(item.get("rank", 1)),
            location_id=str(item.get("location_id", "")),
            role=str(item.get("role", "official")),
            agent_type=str(item.get("agent_type", "official")),
            superior_id=item.get("superior_id"),
            personality_seed=str(item.get("personality_seed", "")),
            enabled=bool(item.get("enabled", True)),
            extra_prompt=str(item.get("extra_prompt", "")),
        )

    def load_package(self, package: DLCPackage, *, seed_world: bool = True) -> DLCLoadReport:
        issues = self.registry.validate(package.manifest.id)
        errors = [item for item in issues if item.severity == "error"]
        if errors:
            raise DLCRegistryError(
                f"Cannot load invalid DLC {package.manifest.id}: "
                + "; ".join(item.message for item in errors[:5])
            )
        content = self.registry.load_content(package.manifest.id)
        primary = self.registry.primary_package()
        package_gameplay = (
            GameplayContract.from_content(
                content["gameplay"], content["agents"],
                package_id=package.manifest.id, package_name=package.manifest.name
            )
            if primary is not None and primary.manifest.id == package.manifest.id
            else GameplayContract(
                package_id=package.manifest.id, package_name=package.manifest.name
            )
        )

        # Build prompts and model-bound Agents before mutating world state. Invalid
        # prompt/model bindings therefore fail the package load without partial seeding.
        # Directly player-controlled Agent records remain addressable court/actor nodes,
        # but do not receive an autonomous LLM runtime.
        officials = [self._official(item) for item in content["agents"]]
        prompts = PromptRegistry(content, package)
        agents = self.agent_factory.create(
            officials,
            prompts,
            package=package,
            agent_sources=content["agents"],
            excluded_agent_ids=package_gameplay.direct_agent_ids,
            legacy_role_player=package_gameplay.source != "explicit",
        )

        resources = [self._resource(item) for item in content["resources"]]
        resource_rates = {
            item.name: item.anchor_rate for item in resources if item.kind == "asset"
        }
        entities = [Entity(
            id=str(item["id"]), type=str(item.get("type", "location")),
            name=str(item.get("name", item["id"])),
            description=str(item.get("description", "")),
            current_status=str(item.get("current_status", "")),
            updated_at=float(item.get("updated_at", 0.0)),
            metadata=dict(item.get("metadata", {})), version=int(item.get("version", 0)),
        ) for item in content["entities"]]
        inventory = [InventoryItem(
            entity_id=str(item["entity_id"]),
            category=str(item.get("category", item.get("item_name", item.get("resource_name", "")))),
            item_name=str(item.get("item_name", item.get("resource_name", ""))),
            quantity=float(item.get("quantity", 0.0)),
            description=str(item.get("description", "")),
            version=int(item.get("version", 0)),
            anchor_rate=float(resource_rates.get(
                str(item.get("item_name", item.get("resource_name", ""))), 1.0
            )),
        ) for item in content["inventory"]]
        values = [EntityValue(
            entity_id=str(item["entity_id"]), name=str(item["name"]),
            kind=str(item.get("kind", "metric")), value=float(item.get("value", 0.0)),
            description=str(item.get("description", "")), version=int(item.get("version", 0)),
        ) for item in content["entity_values"]]
        ongoing = [OngoingEvent(
            event_id=str(item["event_id"]), entity_id=str(item["entity_id"]),
            event_type=str(item.get("event_type", "state_change")),
            description=str(item.get("description", "")),
            start_time=float(item.get("start_time", 0.0)), end_time=float(item.get("end_time", 0.0)),
            expected_result=dict(item.get("expected_result", {})),
            source_action_id=str(item.get("source_action_id", "")),
        ) for item in content["ongoing_events"]]

        if seed_world:
            self.world_state.bulk_load(
                entities, inventory, list(content["relations"]), ongoing, values, resources
            )

        self.court.bulk_load_officials(officials)
        self.prompt_registries[package.manifest.id] = prompts
        for agent in agents:
            self.orchestrator.register_agent(agent)

        events = [EventObject(
            event_id=str(item.get("event_id", "")),
            event_type=str(item.get("event_type", "")),
            severity=int(item.get("severity", 5)),
            target_agents=list(item.get("target_agents", [])),
            initial_payload=dict(item.get("initial_payload", {})),
            generated_at=float(item.get("generated_at", 0.0)),
        ) for item in content["initial_events"]]
        if seed_world:
            for event in events:
                self.orchestrator.inject_event(event)

        return DLCLoadReport(
            package_id=package.manifest.id,
            resources=len(resources), entities=len(entities), inventory=len(inventory),
            entity_values=len(values), relations=len(content["relations"]),
            ongoing_events=len(ongoing), officials=len(officials), agents=len(agents),
            initial_events=len(events) if seed_world else 0,
            random_events=len(content["random_events"]),
            causal_event_rules=len(content["causal_event_rules"]),
            time_rules=len(content["time_rules"]),
            actions=len(content["actions"]),
            issue_types=len(content["issue_types"]),
            objectives=len(content["objectives"]),
            endings=len(content["endings"]),
            seeded_world=seed_world,
            model_profiles=dict(self.agent_factory.last_bindings),
            prompt_hashes={
                agent_id: report.content_hash
                for agent_id, report in prompts.render_reports.items()
            },
            gameplay_configured=package_gameplay.configured,
            player_identity_id=(
                package_gameplay.player.identity_id if package_gameplay.player else ""
            ),
        )

    def _materialize_active(self) -> _DLCMaterializedSnapshot:
        packages = self.registry.ordered_active_packages()
        agents: dict[str, BaseAgent] = {}
        officials: list[Official] = []
        prompts_by_package: dict[str, PromptRegistry] = {}
        model_profiles: dict[str, str] = {}
        agent_packages: dict[str, str] = {}
        agent_fingerprints: dict[str, str] = {}
        package_hashes: dict[str, str] = {}
        resources: list[ResourceDefinition] = []
        entities: list[Entity] = []
        inventory: list[InventoryItem] = []
        entity_values: list[EntityValue] = []
        relations: list[dict[str, Any]] = []
        ongoing_events: list[OngoingEvent] = []
        random_event_templates: list[dict[str, Any]] = []
        causal_event_rules: list[dict[str, Any]] = []
        causal_rule_ids: set[str] = set()
        time_rules: list[dict[str, Any]] = []
        action_definitions: list[dict[str, Any]] = []
        action_ids: set[str] = set()
        issue_definitions: list[dict[str, Any]] = []
        issue_type_ids: set[str] = set()
        objective_definitions: list[dict[str, Any]] = []
        ending_definitions: list[dict[str, Any]] = []
        global_prompts: dict[str, str] = {}
        global_prompt_hashes: dict[str, str] = {}
        global_clients: dict[str, Any] = {}
        global_model_profiles: dict[str, str] = {}
        primary = self.registry.primary_package()
        primary_package_id = primary.manifest.id if primary is not None else ""
        gameplay_contract = GameplayContract(
            package_id=primary_package_id,
            package_name=(primary.manifest.name if primary is not None else ""),
        )

        for package in packages:
            issues = self.registry.validate(package.manifest.id)
            errors = [item for item in issues if item.severity == "error"]
            if errors:
                raise DLCRegistryError(
                    f"Cannot prepare invalid DLC {package.manifest.id}: "
                    + "; ".join(item.message for item in errors[:5])
                )
            content = self.registry.load_content(package.manifest.id)
            package_gameplay = GameplayContract(
                package_id=package.manifest.id, package_name=package.manifest.name
            )
            if package.manifest.id == primary_package_id:
                package_gameplay = GameplayContract.from_content(
                    content["gameplay"],
                    content["agents"],
                    package_id=package.manifest.id,
                    package_name=package.manifest.name,
                )
                gameplay_contract = package_gameplay
            package_hashes[package.manifest.id] = _stable_hash({
                "manifest": {
                    "id": package.manifest.id,
                    "version": package.manifest.version,
                    "dependencies": package.manifest.dependencies,
                    "model_bindings": package.manifest.model_bindings,
                },
                "content": content,
            })
            package_officials = [self._official(item) for item in content["agents"]]
            prompt_registry = PromptRegistry(content, package)
            package_agents = self.agent_factory.create(
                package_officials,
                prompt_registry,
                package=package,
                agent_sources=content["agents"],
                excluded_agent_ids=package_gameplay.direct_agent_ids,
                legacy_role_player=package_gameplay.source != "explicit",
            )
            package_bindings = dict(self.agent_factory.last_bindings)
            for agent in package_agents:
                if agent.agent_id in agents:
                    raise DLCRegistryError(f"Duplicate active Agent id: {agent.agent_id}")
                agents[agent.agent_id] = agent
                profile = package_bindings.get(agent.agent_id, "")
                model_profiles[agent.agent_id] = profile
                agent_packages[agent.agent_id] = package.manifest.id
                agent_fingerprints[agent.agent_id] = _stable_hash({
                    "agent_id": agent.agent_id,
                    "agent_type": agent.agent_type,
                    "display_name": agent.config.display_name,
                    "personality_seed": agent.config.personality_seed,
                    "system_prompt_hash": _stable_hash(agent.config.system_prompt),
                    "model_profile": profile,
                    "package_id": package.manifest.id,
                })
            prompts_by_package[package.manifest.id] = prompt_registry
            if package.manifest.id == primary_package_id:
                task_specs = {
                    "router.ambiguous": ("router", "router"),
                    "action.simulation": ("action_agent", "action_agent"),
                    "memory.compress": ("memory_compressor", "memory_compressor"),
                    "tianji.chat": ("tianji", "tianji"),
                }
                gateway = getattr(self.orchestrator, "model_gateway", None)
                for task_type, (runtime_id, prompt_name) in task_specs.items():
                    prompt_text = prompt_registry.get(prompt_name, "")
                    global_prompts[runtime_id] = prompt_text
                    global_prompt_hashes[runtime_id] = _stable_hash(prompt_text)
                    if gateway is not None and hasattr(gateway, "for_task"):
                        base_client = gateway.for_task(
                            task_type, agent_id=runtime_id
                        )
                        if hasattr(base_client, "for_dlc"):
                            base_client = base_client.for_dlc(package.manifest.id)
                        resolver = ModelBindingResolver(
                            base_client, package.manifest.model_bindings
                        )
                        profile = resolver.resolve(runtime_id, task_type=task_type)
                        global_clients[runtime_id] = resolver.bind(runtime_id, profile)
                        global_model_profiles[runtime_id] = profile
            officials.extend(package_officials)
            package_resources = [self._resource(item) for item in content["resources"]]
            rates = {
                item.name: item.anchor_rate for item in package_resources if item.kind == "asset"
            }
            resources.extend(package_resources)
            entities.extend(Entity(
                id=str(item["id"]), type=str(item.get("type", "location")),
                name=str(item.get("name", item["id"])),
                description=str(item.get("description", "")),
                current_status=str(item.get("current_status", "")),
                updated_at=float(item.get("updated_at", 0.0)),
                metadata=dict(item.get("metadata", {})), version=int(item.get("version", 0)),
            ) for item in content["entities"])
            inventory.extend(InventoryItem(
                entity_id=str(item["entity_id"]),
                category=str(item.get("category", item.get("item_name", item.get("resource_name", "")))),
                item_name=str(item.get("item_name", item.get("resource_name", ""))),
                quantity=float(item.get("quantity", 0.0)),
                description=str(item.get("description", "")),
                version=int(item.get("version", 0)),
                anchor_rate=float(rates.get(
                    str(item.get("item_name", item.get("resource_name", ""))), 1.0
                )),
            ) for item in content["inventory"])
            entity_values.extend(EntityValue(
                entity_id=str(item["entity_id"]), name=str(item["name"]),
                kind=str(item.get("kind", "metric")), value=float(item.get("value", 0.0)),
                description=str(item.get("description", "")), version=int(item.get("version", 0)),
            ) for item in content["entity_values"])
            relations.extend(dict(item) for item in content["relations"])
            random_event_templates.extend(copy.deepcopy(content["random_events"]))
            for causal_rule in expand_causal_rule_document(content):
                rule_id = str(causal_rule.get("rule_id", ""))
                if rule_id in causal_rule_ids:
                    raise DLCRegistryError(f"Duplicate active causal event rule id: {rule_id}")
                causal_rule_ids.add(rule_id)
                causal_event_rules.append(copy.deepcopy(causal_rule))
            time_rules.extend(copy.deepcopy(content["time_rules"]))
            for action in content["actions"]:
                action_id = str(action.get("action_id", ""))
                if action_id in action_ids:
                    raise DLCRegistryError(f"Duplicate active action id: {action_id}")
                action_ids.add(action_id)
                action_definitions.append(copy.deepcopy(action))
            for issue_definition in content["issue_types"]:
                issue_type_id = str(issue_definition.get("issue_type_id", ""))
                if issue_type_id in issue_type_ids:
                    raise DLCRegistryError(f"Duplicate active issue type id: {issue_type_id}")
                issue_type_ids.add(issue_type_id)
                issue_definitions.append(copy.deepcopy(issue_definition))
            # The primary DLC owns the campaign loop. Dependency DLCs may
            # contribute entities/actions/issues without competing for final endings.
            if package.manifest.id == primary_package_id:
                objective_definitions = copy.deepcopy(content["objectives"])
                ending_definitions = copy.deepcopy(content["endings"])
            ongoing_events.extend(OngoingEvent(
                event_id=str(item["event_id"]), entity_id=str(item["entity_id"]),
                event_type=str(item.get("event_type", "state_change")),
                description=str(item.get("description", "")),
                start_time=float(item.get("start_time", 0.0)),
                end_time=float(item.get("end_time", 0.0)),
                expected_result=dict(item.get("expected_result", {})),
                source_action_id=str(item.get("source_action_id", "")),
            ) for item in content["ongoing_events"])

        return _DLCMaterializedSnapshot(
            package_ids=tuple(package.manifest.id for package in packages),
            package_hashes=package_hashes,
            agents=agents,
            officials=officials,
            official_ids={official.id for official in officials},
            prompts=prompts_by_package,
            model_profiles=model_profiles,
            agent_packages=agent_packages,
            agent_fingerprints=agent_fingerprints,
            resources=resources,
            entities=entities,
            inventory=inventory,
            entity_values=entity_values,
            relations=relations,
            ongoing_events=ongoing_events,
            random_event_templates=random_event_templates,
            causal_event_rules=causal_event_rules,
            time_rules=time_rules,
            action_definitions=action_definitions,
            issue_definitions=issue_definitions,
            objective_definitions=objective_definitions,
            ending_definitions=ending_definitions,
            primary_package_id=primary_package_id,
            gameplay_contract=gameplay_contract,
            global_prompts=global_prompts,
            global_prompt_hashes=global_prompt_hashes,
            global_clients=global_clients,
            global_model_profiles=global_model_profiles,
        )

    def _build_reload_plan(self, candidate: _DLCMaterializedSnapshot) -> DLCReloadPlan:
        current = self._active_snapshot
        old_fingerprints = current.agent_fingerprints if current else {}
        new_fingerprints = candidate.agent_fingerprints
        old_ids = set(old_fingerprints)
        new_ids = set(new_fingerprints)
        shared = old_ids & new_ids
        changed = sorted(
            agent_id for agent_id in shared
            if old_fingerprints[agent_id] != new_fingerprints[agent_id]
        )
        unchanged = sorted(shared - set(changed))
        existing_entities = {item.id for item in self.world_state.get_all_entities()}
        existing_resources = {
            item.name: item for item in self.world_state.list_resource_definitions()
        }
        errors: list[str] = []
        for definition in candidate.resources:
            existing = existing_resources.get(definition.name)
            if existing is None:
                continue
            if existing.kind != definition.kind:
                errors.append(f"resource_kind_change:{definition.name}")
                continue
            if definition.kind == "asset":
                old_rate = float(existing.anchor_rate or 1.0)
                new_rate = float(definition.anchor_rate or 1.0)
                if abs(old_rate - new_rate) > 1e-12:
                    errors.append(f"anchor_rate_change:{definition.name}")
        old_global_hashes = current.global_prompt_hashes if current else {}
        old_global_profiles = current.global_model_profiles if current else {}
        runtime_ids = set(old_global_hashes) | set(candidate.global_prompt_hashes)
        changed_runtime_tasks = sorted(
            runtime_id for runtime_id in runtime_ids
            if old_global_hashes.get(runtime_id) != candidate.global_prompt_hashes.get(runtime_id)
            or old_global_profiles.get(runtime_id) != candidate.global_model_profiles.get(runtime_id)
        )
        warnings: list[str] = []
        if candidate.inventory:
            warnings.append("existing_inventory_quantities_preserved")
        if candidate.entity_values:
            warnings.append("existing_entity_values_preserved")
        if candidate.ongoing_events:
            warnings.append("existing_ongoing_event_progress_preserved")
        warnings.append("initial_events_not_replayed")
        return DLCReloadPlan(
            current_generation=self.generation,
            target_generation=self.generation + 1,
            package_ids=list(candidate.package_ids),
            package_hashes=dict(candidate.package_hashes),
            added_agents=sorted(new_ids - old_ids),
            removed_agents=sorted(old_ids - new_ids),
            changed_agents=changed,
            unchanged_agents=unchanged,
            changed_runtime_tasks=changed_runtime_tasks,
            runtime_model_profiles=dict(candidate.global_model_profiles),
            gameplay_changed=(
                current is None
                or current.gameplay_contract.to_dict()
                != candidate.gameplay_contract.to_dict()
            ),
            world_changes={
                "resources_added": sum(1 for item in candidate.resources if item.name not in existing_resources),
                "entities_added": sum(1 for item in candidate.entities if item.id not in existing_entities),
                "inventory_seed_candidates": len(candidate.inventory),
                "entity_value_seed_candidates": len(candidate.entity_values),
                "relations_upserted": len(candidate.relations),
                "ongoing_event_candidates": len(candidate.ongoing_events),
                "random_event_templates": len(candidate.random_event_templates),
                "causal_event_rules": len(candidate.causal_event_rules),
                "time_rules": len(candidate.time_rules),
                "actions": len(candidate.action_definitions),
                "issue_types": len(candidate.issue_definitions),
                "objectives": len(candidate.objective_definitions),
                "endings": len(candidate.ending_definitions),
            },
            warnings=warnings,
            errors=sorted(set(errors)),
        )

    @staticmethod
    def _prompt_registry_blueprint(registry: PromptRegistry) -> dict[str, Any]:
        return {
            "definitions": registry.export_definitions(),
            "personas": copy.deepcopy(registry.personas),
            "metadata": copy.deepcopy(registry.metadata),
        }

    def _snapshot_to_archive_blueprint(
        self, snapshot: _DLCMaterializedSnapshot
    ) -> dict[str, Any]:
        return {
            "package_ids": list(snapshot.package_ids),
            "package_hashes": dict(snapshot.package_hashes),
            "agents": [
                {
                    "class": type(agent).__name__,
                    "config": asdict(agent.config),
                    "model_profile": snapshot.model_profiles.get(agent_id, ""),
                    "package_id": snapshot.agent_packages.get(agent_id, ""),
                }
                for agent_id, agent in sorted(snapshot.agents.items())
            ],
            "officials": [asdict(item) for item in snapshot.officials],
            "prompts": {
                package_id: self._prompt_registry_blueprint(registry)
                for package_id, registry in sorted(snapshot.prompts.items())
            },
            "model_profiles": dict(snapshot.model_profiles),
            "agent_packages": dict(snapshot.agent_packages),
            "agent_fingerprints": dict(snapshot.agent_fingerprints),
            "resources": [asdict(item) for item in snapshot.resources],
            "entities": [asdict(item) for item in snapshot.entities],
            "inventory": [asdict(item) for item in snapshot.inventory],
            "entity_values": [asdict(item) for item in snapshot.entity_values],
            "relations": copy.deepcopy(snapshot.relations),
            "ongoing_events": [asdict(item) for item in snapshot.ongoing_events],
            "random_event_templates": copy.deepcopy(snapshot.random_event_templates),
            "causal_event_rules": copy.deepcopy(snapshot.causal_event_rules),
            "time_rules": copy.deepcopy(snapshot.time_rules),
            "action_definitions": copy.deepcopy(snapshot.action_definitions),
            "issue_definitions": copy.deepcopy(snapshot.issue_definitions),
            "objective_definitions": copy.deepcopy(snapshot.objective_definitions),
            "ending_definitions": copy.deepcopy(snapshot.ending_definitions),
            "primary_package_id": snapshot.primary_package_id,
            "gameplay_contract": snapshot.gameplay_contract.to_dict(),
            "global_prompts": dict(snapshot.global_prompts),
            "global_prompt_hashes": dict(snapshot.global_prompt_hashes),
            "global_model_profiles": dict(snapshot.global_model_profiles),
        }

    @staticmethod
    def _restore_prompt_registry(
        package_id: str, blueprint: dict[str, Any]
    ) -> PromptRegistry:
        metadata = dict(blueprint.get("metadata", {}))
        manifest = DLCManifest(
            id=package_id,
            name=package_id,
            enabled=True,
            status="published",
            metadata=metadata,
        )
        package = DLCPackage(
            manifest=manifest,
            root=Path("."),
            manifest_path=Path("manifest.json"),
        )
        return PromptRegistry({
            "prompts": dict(blueprint.get("definitions", {})),
            "personas": dict(blueprint.get("personas", {})),
        }, package)

    def _restore_global_client(
        self, runtime_id: str, profile: str, package_id: str = ""
    ) -> Any:
        task_types = {
            "router": "router.ambiguous",
            "action_agent": "action.simulation",
            "memory_compressor": "memory.compress",
            "tianji": "tianji.chat",
        }
        gateway = getattr(self.orchestrator, "model_gateway", None)
        if gateway is not None and hasattr(gateway, "for_task"):
            base_client = gateway.for_task(
                task_types.get(runtime_id, runtime_id), agent_id=runtime_id
            )
            if package_id and hasattr(base_client, "for_dlc"):
                base_client = base_client.for_dlc(package_id)
        else:
            base_client = self.agent_factory.llm_client
        return ModelBindingResolver(base_client).bind(runtime_id, profile)

    def _snapshot_from_archive_blueprint(
        self, blueprint: dict[str, Any]
    ) -> _DLCMaterializedSnapshot:
        prompts = {
            str(package_id): self._restore_prompt_registry(
                str(package_id), dict(prompt_blueprint)
            )
            for package_id, prompt_blueprint in dict(
                blueprint.get("prompts", {})
            ).items()
        }
        model_profiles = {
            str(key): str(value)
            for key, value in dict(blueprint.get("model_profiles", {})).items()
        }
        agent_packages = {
            str(key): str(value)
            for key, value in dict(blueprint.get("agent_packages", {})).items()
        }
        package_ids = tuple(str(item) for item in blueprint.get("package_ids", []))
        primary_package_id = str(blueprint.get("primary_package_id", ""))
        if not primary_package_id and len(package_ids) == 1:
            primary_package_id = package_ids[0]
        agents: dict[str, BaseAgent] = {}
        classes = {
            "OfficialAgent": OfficialAgent,
            "InstitutionAgent": InstitutionAgent,
        }
        for raw_agent in list(blueprint.get("agents", [])):
            if not isinstance(raw_agent, dict):
                raise DLCRegistryError("Archived Agent blueprint is invalid")
            agent_class = classes.get(str(raw_agent.get("class", "")))
            if agent_class is None:
                raise DLCRegistryError("Archived Agent class is unsupported")
            config = AgentConfig(**dict(raw_agent.get("config", {})))
            profile = str(raw_agent.get(
                "model_profile", model_profiles.get(config.agent_id, "")
            ))
            package_id = str(
                raw_agent.get(
                    "package_id",
                    agent_packages.get(config.agent_id, primary_package_id),
                )
            )
            if package_id:
                agent_packages[config.agent_id] = package_id
            base_client = self.agent_factory.llm_client
            if package_id and hasattr(base_client, "for_dlc"):
                base_client = base_client.for_dlc(package_id)
            client = ModelBindingResolver(base_client).bind(config.agent_id, profile)
            agents[config.agent_id] = agent_class(config, client)

        global_prompts = {
            str(key): str(value)
            for key, value in dict(blueprint.get("global_prompts", {})).items()
        }
        global_profiles = {
            str(key): str(value)
            for key, value in dict(
                blueprint.get("global_model_profiles", {})
            ).items()
        }
        global_clients = {
            runtime_id: self._restore_global_client(
                runtime_id,
                global_profiles.get(runtime_id, ""),
                primary_package_id,
            )
            for runtime_id in global_prompts
        }
        officials = [
            Official(**dict(item)) for item in blueprint.get("officials", [])
        ]
        archived_gameplay = blueprint.get("gameplay_contract")
        gameplay_contract = (
            GameplayContract.from_dict(archived_gameplay)
            if isinstance(archived_gameplay, dict)
            else GameplayContract.from_content(
                {},
                [asdict(item) for item in officials],
                package_id=primary_package_id,
                package_name=primary_package_id,
            )
        )
        return _DLCMaterializedSnapshot(
            package_ids=package_ids,
            package_hashes={
                str(key): str(value)
                for key, value in dict(
                    blueprint.get("package_hashes", {})
                ).items()
            },
            agents=agents,
            officials=officials,
            official_ids={item.id for item in officials},
            prompts=prompts,
            model_profiles=model_profiles,
            agent_packages=agent_packages,
            agent_fingerprints={
                str(key): str(value)
                for key, value in dict(
                    blueprint.get("agent_fingerprints", {})
                ).items()
            },
            resources=[
                ResourceDefinition(**dict(item))
                for item in blueprint.get("resources", [])
            ],
            entities=[
                Entity(**dict(item)) for item in blueprint.get("entities", [])
            ],
            inventory=[
                InventoryItem(**dict(item))
                for item in blueprint.get("inventory", [])
            ],
            entity_values=[
                EntityValue(**dict(item))
                for item in blueprint.get("entity_values", [])
            ],
            relations=[
                dict(item) for item in blueprint.get("relations", [])
            ],
            ongoing_events=[
                OngoingEvent(**dict(item))
                for item in blueprint.get("ongoing_events", [])
            ],
            random_event_templates=[
                dict(item) for item in blueprint.get("random_event_templates", [])
            ],
            causal_event_rules=[
                dict(item) for item in blueprint.get("causal_event_rules", [])
            ],
            time_rules=[dict(item) for item in blueprint.get("time_rules", [])],
            action_definitions=[dict(item) for item in blueprint.get("action_definitions", [])],
            issue_definitions=[dict(item) for item in blueprint.get("issue_definitions", [])],
            objective_definitions=[dict(item) for item in blueprint.get("objective_definitions", [])],
            ending_definitions=[dict(item) for item in blueprint.get("ending_definitions", [])],
            primary_package_id=str(blueprint.get("primary_package_id", "")),
            gameplay_contract=gameplay_contract,
            global_prompts=global_prompts,
            global_prompt_hashes={
                runtime_id: str(
                    dict(blueprint.get("global_prompt_hashes", {})).get(
                        runtime_id, _stable_hash(prompt)
                    )
                )
                for runtime_id, prompt in global_prompts.items()
            },
            global_clients=global_clients,
            global_model_profiles=global_profiles,
        )

    def _restore_generation_archive(self) -> None:
        if self._generation_archive is None:
            return
        loaded = self._generation_archive.load_all()
        self._archive_issues.extend(loaded.issues)
        for record in loaded.records:
            try:
                snapshot = self._snapshot_from_archive_blueprint(record.blueprint)
            except (DLCRegistryError, TypeError, ValueError, KeyError):
                self._archive_issues.append({
                    "generation": record.generation,
                    "code": "archive_blueprint_restore_failed",
                })
                continue
            self._generation_history[record.generation] = snapshot
            self._generation_order.append(record.generation)
        if self._generation_order:
            self.generation = max(self._generation_order)

    def _archive_public_status(self) -> dict[str, Any]:
        if self._generation_archive is None:
            return {
                "enabled": False,
                "persisted_generations": 0,
                "issue_count": len(self._archive_issues),
                "issues": list(self._archive_issues[-20:]),
            }
        status = self._generation_archive.public_status()
        combined = list(self._archive_issues)
        for issue in status.get("issues", []):
            if issue not in combined:
                combined.append(issue)
        status["issue_count"] = len(combined)
        status["issues"] = combined[-20:]
        return status

    @staticmethod
    def _clone_snapshot_blueprint(
        snapshot: _DLCMaterializedSnapshot,
    ) -> _DLCMaterializedSnapshot:
        agents = {
            agent_id: type(agent)(copy.deepcopy(agent.config), agent.llm)
            for agent_id, agent in snapshot.agents.items()
        }
        return _DLCMaterializedSnapshot(
            package_ids=tuple(snapshot.package_ids),
            package_hashes=dict(snapshot.package_hashes),
            agents=agents,
            officials=copy.deepcopy(snapshot.officials),
            official_ids=set(snapshot.official_ids),
            prompts=dict(snapshot.prompts),
            model_profiles=dict(snapshot.model_profiles),
            agent_packages=dict(snapshot.agent_packages),
            agent_fingerprints=dict(snapshot.agent_fingerprints),
            resources=copy.deepcopy(snapshot.resources),
            entities=copy.deepcopy(snapshot.entities),
            inventory=copy.deepcopy(snapshot.inventory),
            entity_values=copy.deepcopy(snapshot.entity_values),
            relations=copy.deepcopy(snapshot.relations),
            ongoing_events=copy.deepcopy(snapshot.ongoing_events),
            random_event_templates=copy.deepcopy(snapshot.random_event_templates),
            causal_event_rules=copy.deepcopy(snapshot.causal_event_rules),
            time_rules=copy.deepcopy(snapshot.time_rules),
            action_definitions=copy.deepcopy(snapshot.action_definitions),
            issue_definitions=copy.deepcopy(snapshot.issue_definitions),
            objective_definitions=copy.deepcopy(snapshot.objective_definitions),
            ending_definitions=copy.deepcopy(snapshot.ending_definitions),
            primary_package_id=snapshot.primary_package_id,
            gameplay_contract=copy.deepcopy(snapshot.gameplay_contract),
            global_prompts=dict(snapshot.global_prompts),
            global_prompt_hashes=dict(snapshot.global_prompt_hashes),
            global_clients=dict(snapshot.global_clients),
            global_model_profiles=dict(snapshot.global_model_profiles),
        )

    def _remember_generation(
        self, generation: int, snapshot: _DLCMaterializedSnapshot
    ) -> None:
        # History stores configuration blueprints only. Active/retired Agent memory
        # remains in the live map or the explicit memory archive, not in every generation.
        self._generation_history[generation] = self._clone_snapshot_blueprint(snapshot)
        if generation in self._generation_order:
            self._generation_order.remove(generation)
        self._generation_order.append(generation)
        while len(self._generation_order) > self._generation_history_limit:
            expired = self._generation_order.pop(0)
            self._generation_history.pop(expired, None)
        if self._generation_archive is not None:
            try:
                self._generation_archive.save(
                    generation, self._snapshot_to_archive_blueprint(snapshot)
                )
            except (GenerationArchiveError, OSError, TypeError, ValueError):
                self._archive_issues.append({
                    "generation": generation,
                    "code": "archive_write_failed",
                })
                del self._archive_issues[:-100]

    def _public_generation_history(self) -> list[dict[str, Any]]:
        return [
            {
                "generation": generation,
                "package_ids": list(self._generation_history[generation].package_ids),
                "package_hashes": dict(
                    self._generation_history[generation].package_hashes
                ),
                "active": generation == self.generation,
            }
            for generation in self._generation_order
            if generation in self._generation_history
        ]

    def preview_rollback(self, source_generation: int) -> dict[str, Any]:
        snapshot = self._generation_history.get(int(source_generation))
        if snapshot is None:
            raise DLCRegistryError("DLC runtime generation is not available")
        candidate = self._clone_snapshot_blueprint(snapshot)
        plan = self._build_reload_plan(candidate)
        result = plan.to_dict()
        result["activation_kind"] = "rollback"
        result["rollback_source_generation"] = int(source_generation)
        result["target_generation"] = self.generation + 1
        warnings = list(result.get("warnings", []))
        for warning in (
            "dynamic_world_state_preserved",
            "world_additions_are_not_deleted",
            "rollback_creates_new_monotonic_generation",
        ):
            if warning not in warnings:
                warnings.append(warning)
        result["warnings"] = warnings
        return result

    async def rollback_active(self, source_generation: int) -> dict[str, Any]:
        source_generation = int(source_generation)
        snapshot = self._generation_history.get(source_generation)
        if snapshot is None:
            async with self._reload_lock:
                metrics = self._reload_metrics
                metrics["attempts"] += 1
                metrics["failures"] += 1
                metrics["rollback_attempts"] += 1
                metrics["rollback_failures"] += 1
                self._append_reload_event({
                    "event": "dlc_runtime_rollback_failed",
                    "activation_kind": "rollback",
                    "generation": self.generation,
                    "source_generation": source_generation,
                    "failure_code": "generation_not_available",
                    "duration_ms": 0.0,
                    "memory_migration_ms": 0.0,
                    "waiting_inflight_agents": 0,
                    "occurred_at": time.time(),
                })
            raise DLCRegistryError("DLC runtime generation is not available")
        candidate = self._clone_snapshot_blueprint(snapshot)
        return await self.reload_active(
            candidate_override=candidate,
            activation_kind="rollback",
            source_generation=int(source_generation),
        )

    @staticmethod
    def _world_rows_added(world_result: dict[str, int]) -> dict[str, int]:
        return {
            "resources": int(world_result.get("resources_added", 0)),
            "entities": int(world_result.get("entities_added", 0)),
            "inventory": int(world_result.get("inventory_added", 0)),
            "entity_values": int(world_result.get("entity_values_added", 0)),
            "ongoing_events": int(world_result.get("ongoing_events_added", 0)),
        }

    @staticmethod
    def _failure_code(plan: DLCReloadPlan | None) -> str:
        errors = plan.errors if plan is not None else []
        if any(item.startswith("anchor_rate_change:") for item in errors):
            return "anchor_migration_required"
        if any(item.startswith("resource_kind_change:") for item in errors):
            return "resource_kind_migration_required"
        if plan is not None and not plan.safe:
            return "explicit_migration_required"
        return "activation_failed"

    def _metrics_snapshot(self) -> dict[str, Any]:
        metrics = dict(self._reload_metrics)
        metrics["world_rows_added_total"] = dict(
            self._reload_metrics["world_rows_added_total"]
        )
        return metrics

    def _append_reload_event(self, event: dict[str, Any]) -> None:
        self.reload_events.append(event)
        del self.reload_events[:-100]

    def _prepare_runtime_candidate(
        self, candidate: _DLCMaterializedSnapshot, generation: int
    ) -> Any:
        """Resolve a complete immutable binding set without publishing it."""
        if not hasattr(self.orchestrator, "prepare_runtime_bindings"):
            return None
        snapshot = self.orchestrator.prepare_runtime_bindings(
            generation,
            prompts=candidate.global_prompts,
            clients=candidate.global_clients,
            model_profiles=candidate.global_model_profiles,
        )
        # Seal the resolved values into the generation blueprint. This makes rollback
        # restore the exact Prompt/client pair even when a DLC omitted an override.
        candidate.global_prompts = {
            task_id: binding.prompt for task_id, binding in snapshot.bindings.items()
        }
        candidate.global_prompt_hashes = {
            task_id: _stable_hash(binding.prompt)
            for task_id, binding in snapshot.bindings.items()
        }
        candidate.global_clients = {
            task_id: binding.llm for task_id, binding in snapshot.bindings.items()
        }
        candidate.global_model_profiles = {
            task_id: binding.model_profile
            for task_id, binding in snapshot.bindings.items()
        }
        return snapshot

    def preview_reload(self) -> dict[str, Any]:
        self.registry.discover()
        candidate = self._materialize_active()
        self._prepare_runtime_candidate(candidate, self.generation + 1)
        return self._build_reload_plan(candidate).to_dict()

    async def reload_active(
        self,
        *,
        candidate_override: _DLCMaterializedSnapshot | None = None,
        activation_kind: str = "reload",
        source_generation: int | None = None,
    ) -> dict[str, Any]:
        async with self._reload_lock:
            started = time.perf_counter()
            plan: DLCReloadPlan | None = None
            memory_migration_ms = 0.0
            waiting_inflight_agents = 0
            self._reload_metrics["attempts"] += 1
            if activation_kind == "rollback":
                self._reload_metrics["rollback_attempts"] += 1
            self._reload_metrics["reload_in_progress"] = True
            self._reload_metrics["current_waiting_inflight_agents"] = 0
            try:
                if candidate_override is None:
                    self.registry.discover()
                    candidate = self._materialize_active()
                else:
                    candidate = candidate_override
                plan = self._build_reload_plan(candidate)
                if not plan.safe:
                    raise DLCRegistryError(
                        "DLC runtime reload requires an explicit world migration"
                    )
                target_generation = self.generation + 1
                runtime_bindings_snapshot = self._prepare_runtime_candidate(
                    candidate, target_generation
                )

                current_agents = {
                    agent_id: self.orchestrator.get_agent(agent_id)
                    if hasattr(self.orchestrator, "get_agent")
                    else self.orchestrator.agents.get(agent_id)
                    for agent_id in self._managed_agent_ids
                }
                waiting_inflight_agents = sum(
                    1
                    for agent in current_agents.values()
                    if agent is not None
                    and getattr(agent, "_turn_lock", None) is not None
                    and agent._turn_lock.locked()
                )
                self._reload_metrics[
                    "current_waiting_inflight_agents"
                ] = waiting_inflight_agents
                migration_started = time.perf_counter()
                live_snapshots: dict[str, dict[str, Any]] = {}
                try:
                    snapshot_agent_ids = sorted(
                        agent_id
                        for agent_id in (set(candidate.agents) | set(plan.removed_agents))
                        if current_agents.get(agent_id) is not None
                    )
                    snapshot_values = await asyncio.gather(*(
                        current_agents[agent_id].snapshot_memory()
                        for agent_id in snapshot_agent_ids
                    ))
                    live_snapshots = dict(zip(snapshot_agent_ids, snapshot_values))

                    for agent_id, candidate_agent in candidate.agents.items():
                        memory = live_snapshots.get(agent_id)
                        if memory is None and agent_id in self._archived_agent_memory:
                            memory = self._archived_agent_memory[agent_id]
                        if memory is not None:
                            candidate_agent.deserialize_memory(memory)

                    for agent_id in plan.removed_agents:
                        memory = live_snapshots.get(agent_id)
                        if memory is not None:
                            self._archived_agent_memory[agent_id] = memory
                finally:
                    memory_migration_ms = (
                        time.perf_counter() - migration_started
                    ) * 1000.0

                world_result = self.world_state.apply_dlc_schema_update(
                    candidate.entities,
                    candidate.inventory,
                    candidate.relations,
                    candidate.ongoing_events,
                    candidate.entity_values,
                    candidate.resources,
                    officials=candidate.officials,
                    replace_official_ids=self._managed_official_ids,
                )
                if hasattr(self.court, "refresh_cache"):
                    self.court.refresh_cache()
                if runtime_bindings_snapshot is None:
                    # Compatibility path for lightweight external orchestrators. The
                    # production Orchestrator uses one immutable snapshot publication.
                    router = getattr(self.orchestrator, "router", None)
                    if router is not None:
                        if candidate.global_prompts.get("router"):
                            router._system_prompt = candidate.global_prompts["router"]
                        if candidate.global_clients.get("router") is not None:
                            router.llm = candidate.global_clients["router"]
                    action_agent = getattr(self.orchestrator, "action_agent", None)
                    if action_agent is not None:
                        if candidate.global_prompts.get("action_agent"):
                            action_agent._prompt_template = candidate.global_prompts["action_agent"]
                        if candidate.global_clients.get("action_agent") is not None:
                            action_agent.llm = candidate.global_clients["action_agent"]
                    compressor = getattr(self.orchestrator, "_memory_compressor", None)
                    if compressor is not None:
                        if candidate.global_prompts.get("memory_compressor"):
                            compressor._prompt_template = candidate.global_prompts["memory_compressor"]
                        if candidate.global_clients.get("memory_compressor") is not None:
                            compressor._llm = candidate.global_clients["memory_compressor"]
                    tianji = getattr(self.orchestrator, "tianji_agent", None)
                    if tianji is not None:
                        prompt = candidate.global_prompts.get("tianji", "")
                        if prompt:
                            tianji._system_prompt = prompt
                            if getattr(tianji, "messages", None):
                                tianji.messages[0] = {"role": "system", "content": prompt}
                        if candidate.global_clients.get("tianji") is not None:
                            tianji.llm = candidate.global_clients["tianji"]

                if hasattr(self.orchestrator, "activate_agent_snapshot"):
                    self.orchestrator.activate_agent_snapshot(
                        candidate.agents,
                        replace_agent_ids=self._managed_agent_ids,
                    )
                else:
                    merged = {
                        agent_id: agent
                        for agent_id, agent in self.orchestrator.agents.items()
                        if agent_id not in self._managed_agent_ids
                    }
                    for agent in candidate.agents.values():
                        self.orchestrator.register_agent(agent)
                    merged.update(candidate.agents)
                    self.orchestrator.agents = merged

                if runtime_bindings_snapshot is not None:
                    self.orchestrator.activate_runtime_bindings(runtime_bindings_snapshot)
                if hasattr(self.orchestrator, "configure_random_events"):
                    self.orchestrator.configure_random_events(
                        candidate.random_event_templates, preserve_state=True
                    )
                if hasattr(self.orchestrator, "configure_causal_events"):
                    self.orchestrator.configure_causal_events(candidate.causal_event_rules)
                if hasattr(self.orchestrator, "configure_time_rules"):
                    self.orchestrator.configure_time_rules(candidate.time_rules)
                if hasattr(self.orchestrator, "configure_gameplay_contract"):
                    self.orchestrator.configure_gameplay_contract(
                        candidate.gameplay_contract, generation=target_generation
                    )
                if hasattr(self.orchestrator, "configure_action_catalog"):
                    self.orchestrator.configure_action_catalog(
                        candidate.action_definitions, generation=target_generation,
                        gameplay=candidate.gameplay_contract,
                    )
                if hasattr(self.orchestrator, "configure_issue_catalog"):
                    self.orchestrator.configure_issue_catalog(
                        candidate.issue_definitions, generation=target_generation,
                        gameplay=candidate.gameplay_contract,
                    )
                if hasattr(self.orchestrator, "configure_objective_catalog"):
                    self.orchestrator.configure_objective_catalog(
                        candidate.objective_definitions, candidate.ending_definitions,
                        generation=target_generation, gameplay=candidate.gameplay_contract,
                    )

                self.prompt_registries = dict(candidate.prompts)
                self._managed_agent_ids = set(candidate.agents)
                self._managed_official_ids = set(candidate.official_ids)
                self._active_snapshot = candidate
                self.generation = target_generation
                self._remember_generation(self.generation, candidate)

                duration_ms = (time.perf_counter() - started) * 1000.0
                rows_added = self._world_rows_added(world_result)
                metrics = self._reload_metrics
                metrics["successes"] += 1
                if activation_kind == "rollback":
                    metrics["rollback_successes"] += 1
                metrics["last_duration_ms"] = duration_ms
                metrics["total_duration_ms"] += duration_ms
                metrics["max_duration_ms"] = max(
                    metrics["max_duration_ms"], duration_ms
                )
                metrics["last_memory_migration_ms"] = memory_migration_ms
                metrics["total_memory_migration_ms"] += memory_migration_ms
                metrics["last_waiting_inflight_agents"] = waiting_inflight_agents
                metrics["max_waiting_inflight_agents"] = max(
                    metrics["max_waiting_inflight_agents"], waiting_inflight_agents
                )
                for key, count in rows_added.items():
                    metrics["world_rows_added_total"][key] += count

                event = {
                    "event": (
                        "dlc_runtime_rolled_back"
                        if activation_kind == "rollback"
                        else "dlc_runtime_reloaded"
                    ),
                    "activation_kind": activation_kind,
                    "generation": self.generation,
                    "package_ids": list(candidate.package_ids),
                    "source_generation": source_generation,
                    "added_agents": len(plan.added_agents),
                    "removed_agents": len(plan.removed_agents),
                    "changed_agents": len(plan.changed_agents),
                    "world_changes": dict(world_result),
                    "world_rows_added": rows_added,
                    "duration_ms": round(duration_ms, 3),
                    "memory_migration_ms": round(memory_migration_ms, 3),
                    "waiting_inflight_agents": waiting_inflight_agents,
                    "occurred_at": time.time(),
                }
                self._append_reload_event(event)
                result = plan.to_dict()
                result["generation"] = self.generation
                result["activation_kind"] = activation_kind
                result["source_generation"] = source_generation
                result["world_applied"] = world_result
                result["retired_memory_archives"] = len(
                    self._archived_agent_memory
                )
                result["operation"] = {
                    "duration_ms": round(duration_ms, 3),
                    "memory_migration_ms": round(memory_migration_ms, 3),
                    "waiting_inflight_agents": waiting_inflight_agents,
                    "world_rows_added": rows_added,
                }
                return result
            except Exception:
                duration_ms = (time.perf_counter() - started) * 1000.0
                failure_code = self._failure_code(plan)
                metrics = self._reload_metrics
                metrics["failures"] += 1
                if activation_kind == "rollback":
                    metrics["rollback_failures"] += 1
                metrics["last_duration_ms"] = duration_ms
                metrics["total_duration_ms"] += duration_ms
                metrics["max_duration_ms"] = max(
                    metrics["max_duration_ms"], duration_ms
                )
                metrics["last_memory_migration_ms"] = memory_migration_ms
                metrics["total_memory_migration_ms"] += memory_migration_ms
                metrics["last_waiting_inflight_agents"] = waiting_inflight_agents
                metrics["max_waiting_inflight_agents"] = max(
                    metrics["max_waiting_inflight_agents"], waiting_inflight_agents
                )
                if plan is not None and not plan.safe:
                    metrics["unsafe_rejections"] += 1
                plan_errors = plan.errors if plan is not None else []
                if any(
                    item.startswith("anchor_rate_change:") for item in plan_errors
                ):
                    metrics["anchor_migration_rejections"] += 1
                if any(
                    item.startswith("resource_kind_change:") for item in plan_errors
                ):
                    metrics["resource_kind_migration_rejections"] += 1
                self._append_reload_event({
                    "event": (
                        "dlc_runtime_rollback_failed"
                        if activation_kind == "rollback"
                        else "dlc_runtime_reload_failed"
                    ),
                    "activation_kind": activation_kind,
                    "generation": self.generation,
                    "source_generation": source_generation,
                    "failure_code": failure_code,
                    "duration_ms": round(duration_ms, 3),
                    "memory_migration_ms": round(memory_migration_ms, 3),
                    "waiting_inflight_agents": waiting_inflight_agents,
                    "occurred_at": time.time(),
                })
                raise
            finally:
                self._reload_metrics["reload_in_progress"] = False
                self._reload_metrics["current_waiting_inflight_agents"] = 0

    def model_policy_targets(self) -> dict[str, set[str]]:
        """Return active package/Agent identifiers for internal policy validation."""
        snapshot = self._active_snapshot
        if snapshot is None:
            return {}
        result = {package_id: set() for package_id in snapshot.package_ids}
        for agent_id, package_id in snapshot.agent_packages.items():
            result.setdefault(package_id, set()).add(agent_id)
        return result

    def public_runtime(self) -> dict[str, Any]:
        snapshot = self._active_snapshot
        return {
            "generation": self.generation,
            "package_ids": list(snapshot.package_ids) if snapshot else [],
            "agent_count": len(self._managed_agent_ids),
            "official_count": len(self._managed_official_ids),
            "retired_memory_archives": len(self._archived_agent_memory),
            "available_generations": self._public_generation_history(),
            "generation_archive": self._archive_public_status(),
            "gameplay": (
                self.orchestrator.gameplay_public_state()
                if hasattr(self.orchestrator, "gameplay_public_state")
                else {
                    **(snapshot.gameplay_contract.to_dict() if snapshot else GameplayContract().to_dict()),
                    "generation": self.generation,
                }
            ),
            "actions": (
                self.orchestrator.actions_public_state()
                if hasattr(self.orchestrator, "actions_public_state")
                else {"configured": bool(snapshot and snapshot.action_definitions), "generation": self.generation, "actions": copy.deepcopy(snapshot.action_definitions) if snapshot else []}
            ),
            "issues": (
                self.orchestrator.issues_public_state()
                if hasattr(self.orchestrator, "issues_public_state")
                else {"configured": bool(snapshot and snapshot.issue_definitions), "generation": self.generation, "issue_types": copy.deepcopy(snapshot.issue_definitions) if snapshot else []}
            ),
            "objectives": (
                self.orchestrator.objectives_public_state()
                if hasattr(self.orchestrator, "objectives_public_state")
                else {
                    "configured": bool(snapshot and (snapshot.objective_definitions or snapshot.ending_definitions)),
                    "sandbox": not bool(snapshot and (snapshot.objective_definitions or snapshot.ending_definitions)),
                    "generation": self.generation,
                    "objectives": copy.deepcopy(snapshot.objective_definitions) if snapshot else [],
                    "endings": copy.deepcopy(snapshot.ending_definitions) if snapshot else [],
                    "current_ending": None,
                }
            ),
            "causal_events": (
                self.orchestrator.causal_event_status()
                if hasattr(self.orchestrator, "causal_event_status")
                else {"rule_count": len(snapshot.causal_event_rules) if snapshot else 0}
            ),
            "time_rules": (
                self.orchestrator.time_rule_status()
                if hasattr(self.orchestrator, "time_rule_status")
                else {"rule_count": len(snapshot.time_rules) if snapshot else 0}
            ),
            "runtime_bindings": (
                self.orchestrator.runtime_bindings_public_state()
                if hasattr(self.orchestrator, "runtime_bindings_public_state")
                else {"generation": self.generation, "tasks": {}}
            ),
            "metrics": self._metrics_snapshot(),
            "recent_events": list(self.reload_events[-20:]),
        }

    def load_active(self, *, seed_world: bool = True) -> list[DLCLoadReport]:
        candidate = self._materialize_active()
        # Configure causality before initial-event injection so authored opening
        # events participate in the same deterministic chain semantics.
        if hasattr(self.orchestrator, "configure_causal_events"):
            self.orchestrator.configure_causal_events(candidate.causal_event_rules)
        reports = [
            self.load_package(package, seed_world=seed_world)
            for package in self.registry.ordered_active_packages()
        ]
        self._active_snapshot = candidate
        self._managed_agent_ids = set(candidate.agents)
        self._managed_official_ids = set(candidate.official_ids)
        # A restart publishes the registry's current content as a new generation.
        # Previously archived generations remain rollback targets and numbering never
        # moves backwards or overwrites the last pre-restart blueprint.
        self.generation = max(1, self.generation + 1)
        if (
            hasattr(self.orchestrator, "prepare_runtime_bindings")
            and hasattr(self.orchestrator, "activate_runtime_bindings")
        ):
            runtime_bindings_snapshot = self._prepare_runtime_candidate(
                candidate, self.generation
            )
            self.orchestrator.activate_runtime_bindings(runtime_bindings_snapshot)
        if hasattr(self.orchestrator, "configure_random_events"):
            self.orchestrator.configure_random_events(
                candidate.random_event_templates, preserve_state=False
            )
        if hasattr(self.orchestrator, "configure_time_rules"):
            self.orchestrator.configure_time_rules(candidate.time_rules)
        if hasattr(self.orchestrator, "configure_gameplay_contract"):
            self.orchestrator.configure_gameplay_contract(
                candidate.gameplay_contract, generation=self.generation
            )
        if hasattr(self.orchestrator, "configure_action_catalog"):
            self.orchestrator.configure_action_catalog(
                candidate.action_definitions, generation=self.generation,
                gameplay=candidate.gameplay_contract,
            )
        if hasattr(self.orchestrator, "configure_issue_catalog"):
            self.orchestrator.configure_issue_catalog(
                candidate.issue_definitions, generation=self.generation,
                gameplay=candidate.gameplay_contract,
            )
        if hasattr(self.orchestrator, "configure_objective_catalog"):
            self.orchestrator.configure_objective_catalog(
                candidate.objective_definitions, candidate.ending_definitions,
                generation=self.generation, gameplay=candidate.gameplay_contract,
            )
        self._remember_generation(self.generation, candidate)
        return reports
