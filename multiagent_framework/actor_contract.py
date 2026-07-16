"""Generation-bound generic Actor definitions and mutable runtime state."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
import json
import math
import re
import threading
from typing import Any, Iterable, Mapping, Sequence

ACTOR_ID_PATTERN = re.compile(r"^[^\s/\\\x00-\x1f]{1,128}$")
ACTOR_TYPES = {"person", "organization", "institution", "group", "creature", "system", "other"}
GOAL_STATUSES = {"inactive", "active", "blocked", "completed", "failed", "abandoned"}
MEMBERSHIP_STATUSES = {"active", "inactive", "suspended", "former"}

class ActorContractError(ValueError):
    def __init__(self, code: str, message: str | None = None, *, path: str = "") -> None:
        super().__init__(message or code)
        self.code, self.path = code, path

@dataclass(frozen=True)
class ActorValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"

def normalize_actor_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("DLC content field 'actors' must be a list")
    return [copy.deepcopy(item) if isinstance(item, dict) else item for item in value]

def _mapping(value: Any, code: str, path: str, limit: int = 128) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or len(value) > limit:
        raise ActorContractError(code, path=path)
    return value

def _sequence(value: Any, code: str, path: str, limit: int = 128) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) > limit:
        raise ActorContractError(code, path=path)
    return value

def _text(value: Any, code: str, path: str, required: bool = True, limit: int = 512) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise ActorContractError(code, path=path)
    return result

def _id(value: Any, code: str, path: str, required: bool = True) -> str:
    result = _text(value, code, path, required, 128)
    if result and not ACTOR_ID_PATTERN.fullmatch(result):
        raise ActorContractError(code, path=path)
    return result

def _number(value: Any, code: str, path: str) -> float:
    if isinstance(value, bool):
        raise ActorContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActorContractError(code, path=path) from exc
    if not math.isfinite(result):
        raise ActorContractError(code, path=path)
    return result

def _bounded(value: Any, code: str, path: str, minimum: float, maximum: float) -> float:
    result = _number(value, code, path)
    if not minimum <= result <= maximum:
        raise ActorContractError(code, path=path)
    return result

def _scalar(value: Any, code: str, path: str) -> Any:
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise ActorContractError(code, path=path)
    if isinstance(value, float) and not math.isfinite(value):
        raise ActorContractError(code, path=path)
    if isinstance(value, str) and len(value) > 512:
        raise ActorContractError(code, path=path)
    return copy.deepcopy(value)

def _safe_json(value: Any, code: str, path: str, depth: int = 0) -> Any:
    if depth > 3:
        raise ActorContractError(code, path=path)
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ActorContractError(code, path=path)
        return {_id(key, code, f"{path}.{key}"): _safe_json(item, code, f"{path}.{key}", depth + 1) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 64:
            raise ActorContractError(code, path=path)
        return [_safe_json(item, code, f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]
    return _scalar(value, code, path)

def _local_ids(items: Sequence[Any], field_name: str, code: str, path: str) -> None:
    seen: set[str] = set()
    for index, item in enumerate(items):
        raw = _mapping(item, code, f"{path}[{index}]")
        item_id = _id(raw.get(field_name), code, f"{path}[{index}].{field_name}")
        if item_id in seen:
            raise ActorContractError(code, path=f"{path}[{index}].{field_name}")
        seen.add(item_id)

def _tags(value: Any, path: str) -> list[str]:
    result = [_id(item, "actor.trait.invalid_tag", f"{path}[{index}]") for index, item in enumerate(_sequence(value, "actor.trait.invalid_tags", path, 32))]
    if len(result) != len(set(result)):
        raise ActorContractError("actor.trait.duplicate_tag", path=path)
    return result

def _trait(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    trait_id = _id(raw.get("trait_id"), "actor.trait.invalid_id", f"{path}.trait_id")
    return {"trait_id": trait_id, "display_name": _text(raw.get("display_name", trait_id), "actor.trait.invalid_name", f"{path}.display_name", limit=128), "description": _text(raw.get("description", ""), "actor.trait.invalid_description", f"{path}.description", False, 1000), "intensity": _bounded(raw.get("intensity", 1), "actor.trait.invalid_intensity", f"{path}.intensity", 0, 1), "tags": _tags(raw.get("tags", []), f"{path}.tags")}

def _need(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    minimum = _number(raw.get("minimum", 0), "actor.need.invalid_minimum", f"{path}.minimum")
    maximum = _number(raw.get("maximum", 100), "actor.need.invalid_maximum", f"{path}.maximum")
    current = _number(raw.get("current", minimum), "actor.need.invalid_current", f"{path}.current")
    target = _number(raw.get("target", maximum), "actor.need.invalid_target", f"{path}.target")
    if maximum < minimum or not minimum <= current <= maximum or not minimum <= target <= maximum:
        raise ActorContractError("actor.need.invalid_range", path=path)
    need_id = _id(raw.get("need_id"), "actor.need.invalid_id", f"{path}.need_id")
    return {"need_id": need_id, "display_name": _text(raw.get("display_name", need_id), "actor.need.invalid_name", f"{path}.display_name", limit=128), "description": _text(raw.get("description", ""), "actor.need.invalid_description", f"{path}.description", False, 1000), "current": current, "target": target, "minimum": minimum, "maximum": maximum, "priority": _bounded(raw.get("priority", 50), "actor.need.invalid_priority", f"{path}.priority", 0, 100)}

def _goal(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    status = _text(raw.get("status", "active"), "actor.goal.invalid_status", f"{path}.status", limit=32)
    if status not in GOAL_STATUSES:
        raise ActorContractError("actor.goal.invalid_status", path=f"{path}.status")
    target = _number(raw.get("target", 100), "actor.goal.invalid_target", f"{path}.target")
    progress = _number(raw.get("progress", 0), "actor.goal.invalid_progress", f"{path}.progress")
    if target <= 0 or not 0 <= progress <= target:
        raise ActorContractError("actor.goal.invalid_progress", path=path)
    goal_id = _id(raw.get("goal_id"), "actor.goal.invalid_id", f"{path}.goal_id")
    return {"goal_id": goal_id, "display_name": _text(raw.get("display_name", goal_id), "actor.goal.invalid_name", f"{path}.display_name", limit=128), "description": _text(raw.get("description", ""), "actor.goal.invalid_description", f"{path}.description", False, 1000), "status": status, "priority": _bounded(raw.get("priority", 50), "actor.goal.invalid_priority", f"{path}.priority", 0, 100), "progress": progress, "target": target}

def _identity(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    identity_id = _id(raw.get("identity_id"), "actor.identity.invalid_id", f"{path}.identity_id")
    public = raw.get("public", True)
    if not isinstance(public, bool):
        raise ActorContractError("actor.identity.invalid_public", path=f"{path}.public")
    return {"identity_id": identity_id, "display_name": _text(raw.get("display_name", identity_id), "actor.identity.invalid_name", f"{path}.display_name", limit=128), "kind": _id(raw.get("kind", "role"), "actor.identity.invalid_kind", f"{path}.kind"), "public": public, "description": _text(raw.get("description", ""), "actor.identity.invalid_description", f"{path}.description", False, 1000)}

def _membership(raw: Mapping[str, Any], path: str) -> dict[str, Any]:
    status = _text(raw.get("status", "active"), "actor.membership.invalid_status", f"{path}.status", limit=32)
    if status not in MEMBERSHIP_STATUSES:
        raise ActorContractError("actor.membership.invalid_status", path=f"{path}.status")
    primary = raw.get("primary", False)
    if not isinstance(primary, bool):
        raise ActorContractError("actor.membership.invalid_primary", path=f"{path}.primary")
    rank = raw.get("rank")
    return {"organization_id": _id(raw.get("organization_id"), "actor.membership.invalid_organization", f"{path}.organization_id"), "role": _id(raw.get("role", "member"), "actor.membership.invalid_role", f"{path}.role"), "status": status, "rank": None if rank is None else _number(rank, "actor.membership.invalid_rank", f"{path}.rank"), "primary": primary, "description": _text(raw.get("description", ""), "actor.membership.invalid_description", f"{path}.description", False, 1000)}

@dataclass(frozen=True)
class ActorDefinition:
    actor_id: str
    display_name: str
    description: str = ""
    actor_type: str = "person"
    entity_id: str = ""
    enabled: bool = True
    attributes: dict[str, float] = field(default_factory=dict)
    traits: tuple[dict[str, Any], ...] = ()
    needs: tuple[dict[str, Any], ...] = ()
    goals: tuple[dict[str, Any], ...] = ()
    capability_ids: tuple[str, ...] = ()
    identities: tuple[dict[str, Any], ...] = ()
    memberships: tuple[dict[str, Any], ...] = ()
    states: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Mapping[str, Any], path: str = "actors[0]") -> "ActorDefinition":
        actor_type = _id(raw.get("actor_type", "person"), "actor.invalid_type", f"{path}.actor_type")
        if actor_type not in ACTOR_TYPES:
            raise ActorContractError("actor.invalid_type", path=f"{path}.actor_type")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ActorContractError("actor.invalid_enabled", path=f"{path}.enabled")
        attributes = {_id(key, "actor.attribute.invalid_id", f"{path}.attributes.{key}"): _number(value, "actor.attribute.invalid_value", f"{path}.attributes.{key}") for key, value in _mapping(raw.get("attributes", {}), "actor.invalid_attributes", f"{path}.attributes", 64).items()}
        component_specs = (("traits", "trait_id", _trait), ("needs", "need_id", _need), ("goals", "goal_id", _goal), ("identities", "identity_id", _identity), ("memberships", "organization_id", _membership))
        components: dict[str, tuple[dict[str, Any], ...]] = {}
        for name, key, parser in component_specs:
            items = _sequence(raw.get(name, []), f"actor.invalid_{name}", f"{path}.{name}", 64)
            duplicate_code = (
                "actor.membership.duplicate_organization"
                if name == "memberships"
                else f"actor.{name[:-1]}.duplicate_id"
            )
            _local_ids(items, key, duplicate_code, f"{path}.{name}")
            components[name] = tuple(parser(_mapping(item, f"actor.{name}.invalid", f"{path}.{name}[{index}]"), f"{path}.{name}[{index}]") for index, item in enumerate(items))
        capabilities = tuple(_id(item, "actor.capability.invalid_id", f"{path}.capability_ids[{index}]") for index, item in enumerate(_sequence(raw.get("capability_ids", []), "actor.invalid_capabilities", f"{path}.capability_ids", 64)))
        if len(capabilities) != len(set(capabilities)):
            raise ActorContractError("actor.capability.duplicate_id", path=f"{path}.capability_ids")
        states = {_id(key, "actor.state.invalid_id", f"{path}.states.{key}"): _scalar(value, "actor.state.invalid_value", f"{path}.states.{key}") for key, value in _mapping(raw.get("states", {}), "actor.invalid_states", f"{path}.states", 64).items()}
        metadata = _safe_json(raw.get("metadata", {}), "actor.invalid_metadata", f"{path}.metadata")
        if not isinstance(metadata, dict):
            raise ActorContractError("actor.invalid_metadata", path=f"{path}.metadata")
        return cls(actor_id=_id(raw.get("actor_id", raw.get("id")), "actor.invalid_id", f"{path}.actor_id"), display_name=_text(raw.get("display_name", raw.get("name", raw.get("actor_id", raw.get("id")))), "actor.invalid_name", f"{path}.display_name", limit=128), description=_text(raw.get("description", ""), "actor.invalid_description", f"{path}.description", False, 2000), actor_type=actor_type, entity_id=_id(raw.get("entity_id", ""), "actor.invalid_entity", f"{path}.entity_id", False), enabled=enabled, attributes=attributes, traits=components["traits"], needs=components["needs"], goals=components["goals"], capability_ids=capabilities, identities=components["identities"], memberships=components["memberships"], states=states, metadata=metadata)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def legacy_actor_from_agent(raw: Mapping[str, Any]) -> dict[str, Any]:
    actor_id = str(raw.get("actor_id") or raw.get("id") or "").strip()
    personality = str(raw.get("personality_seed", "")).strip()
    role = str(raw.get("role", "official")).strip() or "official"
    result = {"actor_id": actor_id, "display_name": str(raw.get("name") or actor_id), "description": str(raw.get("description", "")), "actor_type": "institution" if raw.get("agent_type") == "institution" else "person", "entity_id": str(raw.get("location_id", "")), "enabled": bool(raw.get("enabled", True)), "attributes": {"rank": raw.get("rank", 1)}, "traits": [], "needs": [], "goals": [], "capability_ids": [], "identities": [{"identity_id": role, "display_name": role, "kind": "role", "public": True}], "memberships": [], "states": {"legacy_projection": True}, "metadata": {"legacy_agent_id": str(raw.get("id", ""))}}
    if personality:
        result["traits"] = [{"trait_id": personality, "display_name": personality, "intensity": 1, "tags": ["personality"]}]
    return result

def effective_actor_documents(actors: Sequence[Any], agents: Sequence[Any]) -> list[dict[str, Any]]:
    result = [copy.deepcopy(item) for item in actors if isinstance(item, dict)]
    known = {str(item.get("actor_id", item.get("id", ""))).strip() for item in result}
    for agent in agents:
        if isinstance(agent, Mapping):
            explicit_actor_id = str(agent.get("actor_id") or "").strip()
            actor_id = explicit_actor_id or str(agent.get("id") or "").strip()
            if not explicit_actor_id and actor_id and actor_id not in known:
                result.append(legacy_actor_from_agent(agent))
                known.add(actor_id)
    return result

def validate_actor_definitions(actors: Sequence[Any], *, agents: Sequence[Any] = (), entity_ids: Iterable[str] = (), capability_ids: Iterable[str] = ()) -> list[ActorValidationIssue]:
    issues: list[ActorValidationIssue] = []
    entities, capabilities, parsed = {str(item) for item in entity_ids}, {str(item) for item in capability_ids}, {}
    for index, raw in enumerate(actors):
        path = f"actors[{index}]"
        if not isinstance(raw, Mapping):
            issues.append(ActorValidationIssue("actor.invalid_definition", "Actor definition must be an object", path)); continue
        try:
            actor = ActorDefinition.parse(raw, path)
        except ActorContractError as exc:
            issues.append(ActorValidationIssue(exc.code, str(exc), exc.path or path)); continue
        if actor.actor_id in parsed:
            issues.append(ActorValidationIssue("actors.duplicate", f"Duplicate actor_id: {actor.actor_id}", f"{path}.actor_id")); continue
        parsed[actor.actor_id] = actor
        if actor.entity_id and actor.entity_id not in entities:
            issues.append(ActorValidationIssue("actor.missing_entity", f"Unknown entity: {actor.entity_id}", f"{path}.entity_id"))
        for position, capability_id in enumerate(actor.capability_ids):
            if capability_id not in capabilities:
                issues.append(ActorValidationIssue("actor.missing_capability", f"Unknown capability: {capability_id}", f"{path}.capability_ids[{position}]"))
        for position, membership in enumerate(actor.memberships):
            if membership["organization_id"] not in entities:
                issues.append(ActorValidationIssue("actor.membership.missing_organization", f"Unknown organization entity: {membership['organization_id']}", f"{path}.memberships[{position}].organization_id"))
    for index, raw in enumerate(agents):
        if isinstance(raw, Mapping):
            agent_id = str(raw.get("id", "")).strip()
            actor_id = str(raw.get("actor_id") or agent_id).strip()
            if actor_id and actor_id not in parsed:
                issues.append(ActorValidationIssue("agent.missing_actor", f"Unknown actor: {actor_id}", f"agents[{index}].actor_id"))
    return issues

class ActorManager:
    def __init__(self) -> None:
        self._lock, self._generation = threading.RLock(), 0
        self._definitions: dict[str, ActorDefinition] = {}
        self._dynamic_definitions: dict[str, ActorDefinition] = {}
        self._lifecycle_tombstones: set[str] = set()
        self._runtime: dict[str, dict[str, Any]] = {}
        # SQLite gameplay effects are authoritative; this is a read-only overlay.
        self._effect_overrides: dict[str, dict[str, Any]] = {}

    @property
    def generation(self) -> int: return self._generation
    @property
    def configured(self) -> bool:
        return any(
            actor_id not in self._lifecycle_tombstones
            for actor_id in set(self._definitions) | set(self._dynamic_definitions)
        )

    def _definition(self, actor_id: str) -> ActorDefinition | None:
        if actor_id in self._lifecycle_tombstones:
            return None
        return self._definitions.get(actor_id) or self._dynamic_definitions.get(actor_id)

    @staticmethod
    def _initial(actor: ActorDefinition) -> dict[str, Any]:
        return {"attributes": copy.deepcopy(actor.attributes), "needs": {item["need_id"]: item["current"] for item in actor.needs}, "goals": {item["goal_id"]: {"status": item["status"], "progress": item["progress"]} for item in actor.goals}, "states": copy.deepcopy(actor.states), "version": 0}

    def configure(self, definitions: Sequence[ActorDefinition | Mapping[str, Any]], *, generation: int, preserve_state: bool = True) -> None:
        parsed: dict[str, ActorDefinition] = {}
        for index, item in enumerate(definitions):
            actor = item if isinstance(item, ActorDefinition) else ActorDefinition.parse(item, f"actors[{index}]")
            if actor.actor_id in parsed: raise ActorContractError("actors.duplicate", path=f"actors[{index}].actor_id")
            parsed[actor.actor_id] = actor
        with self._lock:
            previous = self._runtime if preserve_state else {}
            runtime: dict[str, dict[str, Any]] = {}
            combined = {
                actor_id: actor
                for actor_id, actor in {**parsed, **{
                    dynamic_id: actor for dynamic_id, actor in self._dynamic_definitions.items()
                    if dynamic_id not in parsed
                }}.items()
                if actor_id not in self._lifecycle_tombstones
            }
            for actor_id, actor in combined.items():
                base = self._initial(actor)
                current = previous.get(actor_id, {})
                if isinstance(current, Mapping):
                    self._merge_runtime(actor, base, current, path=f"actor_runtime.{actor_id}")
                runtime[actor_id] = base
            self._definitions = parsed
            self._dynamic_definitions = {
                actor_id: actor for actor_id, actor in self._dynamic_definitions.items()
                if actor_id not in parsed
            }
            self._runtime = runtime
            self._generation = max(0, int(generation))

    @staticmethod
    def _merge_runtime(
        actor: ActorDefinition,
        base: dict[str, Any],
        raw: Mapping[str, Any],
        *,
        path: str,
    ) -> None:
        attributes = raw.get("attributes", {})
        if isinstance(attributes, Mapping):
            for key, value in attributes.items():
                if key in base["attributes"]:
                    base["attributes"][key] = _number(
                        value, "actor.runtime.invalid_attribute", f"{path}.attributes.{key}"
                    )
        needs = {item["need_id"]: item for item in actor.needs}
        raw_needs = raw.get("needs", {})
        if isinstance(raw_needs, Mapping):
            for key, value in raw_needs.items():
                definition = needs.get(key)
                if definition is None:
                    continue
                number = _number(value, "actor.runtime.invalid_need", f"{path}.needs.{key}")
                if definition["minimum"] <= number <= definition["maximum"]:
                    base["needs"][key] = number
        goals = {item["goal_id"]: item for item in actor.goals}
        raw_goals = raw.get("goals", {})
        if isinstance(raw_goals, Mapping):
            for key, value in raw_goals.items():
                definition = goals.get(key)
                if definition is None or not isinstance(value, Mapping):
                    continue
                status = str(value.get("status", definition["status"]))
                progress = _number(
                    value.get("progress", definition["progress"]),
                    "actor.runtime.invalid_goal",
                    f"{path}.goals.{key}",
                )
                if status in GOAL_STATUSES and 0 <= progress <= definition["target"]:
                    base["goals"][key] = {"status": status, "progress": progress}
        states = raw.get("states", {})
        if isinstance(states, Mapping):
            for key, value in states.items():
                if key in base["states"]:
                    base["states"][key] = _scalar(
                        value, "actor.runtime.invalid_state", f"{path}.states.{key}"
                    )
        try:
            base["version"] = max(0, int(raw.get("version", 0)))
        except (TypeError, ValueError, OverflowError):
            base["version"] = 0

    def set_lifecycle_definitions(self, definitions: Mapping[str, Any] | None) -> None:
        """Project active SQLite-authoritative lifecycle actors into this generation."""
        parsed: dict[str, ActorDefinition] = {}
        tombstones: set[str] = set()
        if isinstance(definitions, Mapping):
            for raw_actor_id, raw in definitions.items():
                if not isinstance(raw, Mapping):
                    continue
                actor_id = str(raw.get("actor_id") or raw_actor_id).strip()
                if not actor_id:
                    continue
                if raw.get("retired") or raw.get("active") is False:
                    tombstones.add(actor_id)
                    continue
                authored = copy.deepcopy(dict(raw))
                authored["actor_id"] = actor_id
                actor = ActorDefinition.parse(
                    authored, f"lifecycle.dynamic_actors.{actor_id}"
                )
                if actor_id not in self._definitions:
                    parsed[actor_id] = actor
        with self._lock:
            previous = self._runtime
            runtime: dict[str, dict[str, Any]] = {}
            combined = {
                actor_id: actor
                for actor_id, actor in {**self._definitions, **parsed}.items()
                if actor_id not in tombstones
            }
            for actor_id, actor in combined.items():
                base = self._initial(actor)
                current = previous.get(actor_id, {})
                if isinstance(current, Mapping):
                    self._merge_runtime(
                        actor, base, current, path=f"actor_runtime.{actor_id}"
                    )
                runtime[actor_id] = base
            self._dynamic_definitions = parsed
            self._lifecycle_tombstones = tombstones
            self._runtime = runtime
            valid_actor_ids = set(combined)
            self._effect_overrides = {
                actor_id: raw for actor_id, raw in self._effect_overrides.items()
                if actor_id in valid_actor_ids
            }

    def set_effect_overrides(self, overrides: Mapping[str, Any] | None) -> None:
        """Replace the projected membership overlay from GameplayEffectRuntime."""
        normalized: dict[str, dict[str, Any]] = {}
        if isinstance(overrides, Mapping):
            for raw_actor_id, raw in overrides.items():
                actor_id = str(raw_actor_id)
                if self._definition(actor_id) is not None and isinstance(raw, Mapping):
                    normalized[actor_id] = copy.deepcopy(dict(raw))
        with self._lock:
            self._effect_overrides = normalized

    def get(self, actor_id: str) -> dict[str, Any] | None:
        with self._lock:
            actor = self._definition(str(actor_id))
            if actor is None:
                return None
            result = actor.to_dict()
            result["runtime"] = copy.deepcopy(
                self._runtime.get(actor.actor_id, self._initial(actor))
            )
            override = self._effect_overrides.get(actor.actor_id, {})
            memberships = override.get("memberships", {}) if isinstance(override, Mapping) else {}
            if isinstance(memberships, Mapping):
                merged = {
                    str(item.get("organization_id", "")): copy.deepcopy(item)
                    for item in result.get("memberships", [])
                    if isinstance(item, Mapping) and item.get("organization_id")
                }
                for organization_id, item in memberships.items():
                    if not isinstance(item, Mapping):
                        continue
                    if item.get("removed"):
                        merged.pop(str(organization_id), None)
                    else:
                        merged[str(organization_id)] = copy.deepcopy(dict(item))
                result["memberships"] = [merged[key] for key in sorted(merged)]
            result["generation"] = self._generation
            return result

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            actor_ids = sorted(
                (set(self._definitions) | set(self._dynamic_definitions))
                - self._lifecycle_tombstones
            )
        return [actor for actor_id in actor_ids if (actor := self.get(actor_id)) is not None]

    def public_state(self) -> dict[str, Any]:
        actors = self.list()
        return {"configured": bool(actors), "generation": self._generation, "actor_count": len(actors), "actors": actors}

    def snapshot(self) -> dict[str, Any]:
        with self._lock: return {"schema_version": 1, "generation": self._generation, "actors": copy.deepcopy(self._runtime)}

    def restore(self, snapshot: Mapping[str, Any] | None) -> None:
        if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("actors", {}), Mapping): return
        with self._lock:
            for actor_id, raw in snapshot["actors"].items():
                actor = self._definition(str(actor_id))
                if actor is None or not isinstance(raw, Mapping):
                    continue
                base = self._initial(actor)
                self._merge_runtime(actor, base, raw, path=f"actor_runtime.{actor_id}")
                self._runtime[str(actor_id)] = base

    def prompt_values(self, actor_id: str) -> dict[str, str]:
        actor = self.get(actor_id)
        keys = ("actor", "actor_id", "actor_type", "actor_entity_id", "actor_attributes", "actor_traits", "actor_needs", "actor_goals", "actor_capabilities", "actor_identities", "actor_memberships", "actor_states")
        if actor is None: return {key: "" for key in keys}
        runtime = actor.pop("runtime"); actor.pop("generation", None)
        dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        summary = {"actor_id": actor["actor_id"], "display_name": actor["display_name"], "actor_type": actor["actor_type"], "entity_id": actor["entity_id"], "attributes": runtime["attributes"], "traits": actor["traits"], "needs": [{**item, "current": runtime["needs"].get(item["need_id"], item["current"])} for item in actor["needs"]], "goals": [{**item, **runtime["goals"].get(item["goal_id"], {})} for item in actor["goals"]], "capability_ids": actor["capability_ids"], "identities": actor["identities"], "memberships": actor["memberships"], "states": runtime["states"]}
        return {"actor": dump(summary), "actor_id": actor["actor_id"], "actor_type": actor["actor_type"], "actor_entity_id": actor["entity_id"], "actor_attributes": dump(summary["attributes"]), "actor_traits": dump(summary["traits"]), "actor_needs": dump(summary["needs"]), "actor_goals": dump(summary["goals"]), "actor_capabilities": dump(summary["capability_ids"]), "actor_identities": dump(summary["identities"]), "actor_memberships": dump(summary["memberships"]), "actor_states": dump(summary["states"])}
