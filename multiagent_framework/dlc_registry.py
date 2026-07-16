"""DLC package discovery, validation, and authoring registry.

The registry owns the on-disk package boundary, prevents entrypoint path
traversal, normalizes legacy DLCs, and performs atomic JSON writes.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .prompt_registry import PromptRegistry as FrameworkPromptRegistry, PromptRegistryError
from .causal_events import CAUSAL_METADATA_FIELDS, CausalEventError, CausalEventRule
from .causal_templates import CausalTemplateError, expand_causal_rule_document
from .time_simulator import ActionTimeRule, TimeRuleError
from .world_state import RESOURCE_TOKENS
from .relationship_contract import (
    RelationshipCatalog, validate_relationship_definitions,
)
from .gameplay_contract import GameplayContract, normalize_gameplay_document, validate_gameplay_document
from .action_contract import normalize_action_document, validate_action_definitions
from .actor_contract import (
    effective_actor_documents, normalize_actor_document, validate_actor_definitions,
)
from .capability_contract import normalize_capability_document, validate_capability_definitions
from .issue_contract import normalize_issue_document, validate_issue_definitions
from .objective_contract import (
    normalize_ending_document, normalize_objective_document,
    validate_objective_definitions,
)
from .derived_metrics import (
    DerivedMetricCatalog, DerivedMetricContractError,
    normalize_derived_metric_document,
)
from .ui_manifest import validate_ui_manifest_document
from .temporal_contract import (
    TemporalContract, TemporalContractError, normalize_time_system_document,
)
from .lifecycle_contract import (
    LifecycleContract, LifecycleContractError, normalize_lifecycle_document,
)
from .rhythm_contract import (
    RhythmContract, RhythmContractError, normalize_rhythm_document,
)

DLC_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
RANDOM_EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DLC_FORMATS = {"bundle-v1", "legacy-v1"}
DLC_STATUSES = {"draft", "published"}
CURRENT_CONTENT_SCHEMA_VERSION = "1.0"
RESOURCE_KINDS = {"asset", "metric", "parameter"}
AGENT_PROMPT_VARIABLES = {
    "id", "name", "rank", "rank_title", "location", "location_id",
    "personality", "personality_seed", "superior", "superior_id",
    "role", "agent_type",
    "actor", "actor_id", "actor_type", "actor_entity_id", "actor_attributes",
    "actor_traits", "actor_needs", "actor_goals", "actor_capabilities",
    "actor_identities", "actor_memberships", "actor_states",
}


_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*))?(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def _parse_semver(value: str, *, partial: bool = False) -> tuple[int, int, int, tuple[Any, ...]]:
    text = str(value).strip()
    match = _SEMVER_PATTERN.fullmatch(text)
    if not match:
        raise DLCRegistryError(f"Invalid semantic version: {value!r}")
    if not partial and (match.group("minor") is None or match.group("patch") is None):
        raise DLCRegistryError(f"Semantic version must use major.minor.patch: {value!r}")
    prerelease: tuple[Any, ...] = ()
    if match.group("pre"):
        prerelease = tuple(
            int(part) if part.isdigit() else part
            for part in match.group("pre").split(".")
        )
    return (
        int(match.group("major")),
        int(match.group("minor") or 0),
        int(match.group("patch") or 0),
        prerelease,
    )


def _compare_semver(left: tuple[int, int, int, tuple[Any, ...]], right: tuple[int, int, int, tuple[Any, ...]]) -> int:
    if left[:3] != right[:3]:
        return -1 if left[:3] < right[:3] else 1
    left_pre, right_pre = left[3], right[3]
    if not left_pre and not right_pre:
        return 0
    if not left_pre:
        return 1
    if not right_pre:
        return -1
    for lpart, rpart in zip(left_pre, right_pre):
        if lpart == rpart:
            continue
        if isinstance(lpart, int) and isinstance(rpart, str):
            return -1
        if isinstance(lpart, str) and isinstance(rpart, int):
            return 1
        return -1 if lpart < rpart else 1
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _constraint_clause(version: tuple[int, int, int, tuple[Any, ...]], clause: str) -> bool:
    clause = clause.strip()
    if not clause or clause in {"*", "x", "X"}:
        return True
    wildcard = re.fullmatch(r"(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?", clause)
    if wildcard and any(part in {"x", "X", "*"} for part in wildcard.groups()[1:] if part is not None):
        major = int(wildcard.group(1))
        minor = wildcard.group(2)
        patch = wildcard.group(3)
        return version[0] == major and (minor in {None, "x", "X", "*"} or version[1] == int(minor)) and (patch in {None, "x", "X", "*"} or version[2] == int(patch))
    operator = "="
    target_text = clause
    for candidate in (">=", "<=", ">", "<", "^", "~", "="):
        if clause.startswith(candidate):
            operator, target_text = candidate, clause[len(candidate):].strip()
            break
    target = _parse_semver(target_text, partial=operator in {"^", "~"})
    comparison = _compare_semver(version, target)
    if operator == "^":
        if target[0] > 0:
            upper = (target[0] + 1, 0, 0, ())
        elif target[1] > 0:
            upper = (0, target[1] + 1, 0, ())
        else:
            upper = (0, 0, target[2] + 1, ())
        return comparison >= 0 and _compare_semver(version, upper) < 0
    if operator == "~":
        upper = (target[0], target[1] + 1, 0, ())
        return comparison >= 0 and _compare_semver(version, upper) < 0
    return {"=": comparison == 0, ">=": comparison >= 0, "<=": comparison <= 0, ">": comparison > 0, "<": comparison < 0}[operator]


def version_satisfies(version: str, constraint: str) -> bool:
    """Return whether a strict SemVer version satisfies a compact range expression."""
    parsed = _parse_semver(version)
    expression = str(constraint).strip()
    if not expression:
        raise DLCRegistryError("Dependency version constraint cannot be empty")
    alternatives = [item.strip() for item in expression.split("||")]
    for alternative in alternatives:
        clauses = [item for item in re.split(r"(?:\s*,\s*|\s+)", alternative) if item]
        if clauses and all(_constraint_clause(parsed, clause) for clause in clauses):
            return True
    return False


class DLCRegistryError(ValueError):
    """Raised when a DLC operation violates registry invariants."""


@dataclass(frozen=True)
class DLCValidationIssue:
    code: str
    message: str
    path: str = ""
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class DLCManifest:
    id: str
    name: str
    version: str = "0.1.0"
    schema_version: str = "1.0"
    description: str = ""
    enabled: bool = False
    status: str = "draft"
    format: str = "bundle-v1"
    priority: int = 0
    primary: bool = False
    dependencies: list[str] = field(default_factory=list)
    dependency_constraints: dict[str, str] = field(default_factory=dict)
    entrypoints: dict[str, str] = field(default_factory=dict)
    model_bindings: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DLCManifest":
        known = set(cls.__dataclass_fields__)
        return cls(**{key: data[key] for key in known if key in data})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DLCPackage:
    manifest: DLCManifest
    root: Path
    manifest_path: Path

    def summary(self) -> dict[str, Any]:
        data = self.manifest.to_dict()
        data["root"] = str(self.root)
        return data


class DLCRegistry:
    """Discover and safely edit DLC packages below explicitly allowed roots."""

    def __init__(
        self,
        project_root: str | Path = ".",
        packages_root: str | Path = "dlcs",
        manifest_roots: Iterable[str | Path] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        package_path = Path(packages_root)
        if not package_path.is_absolute():
            package_path = self.project_root / package_path
        self.packages_root = package_path.resolve()
        roots = list(manifest_roots or [self.project_root, self.packages_root])
        self.manifest_roots = [
            (Path(root) if Path(root).is_absolute() else self.project_root / root).resolve()
            for root in roots
        ]
        self.packages_root.mkdir(parents=True, exist_ok=True)
        self._packages: dict[str, DLCPackage] = {}
        self.discover()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DLCRegistryError(f"Cannot read JSON {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise DLCRegistryError(f"JSON root must be an object: {path}")
        return data

    @staticmethod
    def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _safe_entrypoint(self, package: DLCPackage, relative: str) -> Path:
        if not relative or Path(relative).is_absolute():
            raise DLCRegistryError("DLC entrypoint must be a non-empty relative path")
        resolved = (package.root / relative).resolve()
        if not self._inside(resolved, package.root):
            raise DLCRegistryError(f"DLC entrypoint escapes package root: {relative}")
        return resolved

    def discover(self) -> list[DLCPackage]:
        candidates: set[Path] = set()
        for root in self.manifest_roots:
            if not root.exists():
                continue
            direct = root / "dlc.json"
            if direct.is_file():
                candidates.add(direct.resolve())
            for path in root.glob("*/dlc.json"):
                if path.is_file():
                    candidates.add(path.resolve())

        packages: dict[str, DLCPackage] = {}
        for manifest_path in sorted(candidates, key=str):
            manifest = DLCManifest.from_dict(self._read_json(manifest_path))
            package = DLCPackage(manifest, manifest_path.parent.resolve(), manifest_path)
            previous = packages.get(manifest.id)
            if previous and previous.manifest_path != manifest_path:
                raise DLCRegistryError(
                    f"Duplicate DLC id {manifest.id!r}: {previous.root} and {package.root}"
                )
            packages[manifest.id] = package
        self._packages = packages
        return self.list_packages()

    def list_packages(self) -> list[DLCPackage]:
        return sorted(
            self._packages.values(),
            key=lambda pkg: (-pkg.manifest.priority, pkg.manifest.id),
        )

    def get(self, dlc_id: str) -> DLCPackage:
        try:
            return self._packages[dlc_id]
        except KeyError as exc:
            raise DLCRegistryError(f"Unknown DLC: {dlc_id}") from exc

    def primary_package(self) -> DLCPackage | None:
        active = [
            pkg for pkg in self.list_packages()
            if pkg.manifest.enabled and pkg.manifest.status == "published"
        ]
        return next((pkg for pkg in active if pkg.manifest.primary), active[0] if active else None)

    def _dependency_cycle(self, start_id: str) -> list[str]:
        visited: set[str] = set()
        stack: list[str] = []
        positions: dict[str, int] = {}

        def visit(dlc_id: str) -> list[str]:
            if dlc_id in positions:
                return stack[positions[dlc_id]:] + [dlc_id]
            if dlc_id in visited or dlc_id not in self._packages:
                return []
            visited.add(dlc_id)
            positions[dlc_id] = len(stack)
            stack.append(dlc_id)
            dependencies = self._packages[dlc_id].manifest.dependencies
            if isinstance(dependencies, list):
                for dependency in dependencies:
                    if isinstance(dependency, str):
                        cycle = visit(dependency)
                        if cycle:
                            return cycle
            stack.pop()
            positions.pop(dlc_id, None)
            return []

        return visit(start_id)

    @staticmethod
    def _dependency_constraint(manifest: DLCManifest, dependency: str) -> str:
        constraints = manifest.dependency_constraints
        return constraints.get(dependency, "*") if isinstance(constraints, dict) else "*"

    def ordered_active_packages(self) -> list[DLCPackage]:
        active = {
            pkg.manifest.id: pkg for pkg in self.list_packages()
            if pkg.manifest.enabled and pkg.manifest.status == "published"
        }
        ordered: list[DLCPackage] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(dlc_id: str) -> None:
            if dlc_id in permanent:
                return
            if dlc_id in temporary:
                raise DLCRegistryError(f"DLC dependency cycle includes {dlc_id}")
            temporary.add(dlc_id)
            package = active[dlc_id]
            if not isinstance(package.manifest.dependencies, list):
                raise DLCRegistryError(f"Active DLC {dlc_id} has invalid dependencies")
            for dependency in package.manifest.dependencies:
                if not isinstance(dependency, str) or dependency not in active:
                    raise DLCRegistryError(
                        f"Active DLC {dlc_id} requires inactive or missing DLC {dependency}"
                    )
                constraint = self._dependency_constraint(package.manifest, dependency)
                if not version_satisfies(active[dependency].manifest.version, constraint):
                    raise DLCRegistryError(
                        f"Active DLC {dlc_id} requires {dependency} {constraint}, "
                        f"but active version is {active[dependency].manifest.version}"
                    )
                visit(dependency)
            temporary.remove(dlc_id)
            permanent.add(dlc_id)
            ordered.append(package)

        for package in self.list_packages():
            if package.manifest.id in active:
                visit(package.manifest.id)
        return ordered

    def load_content(self, dlc_id: str) -> dict[str, Any]:
        package = self.get(dlc_id)
        if package.manifest.format == "legacy-v1":
            return self._load_legacy_content(package)
        entry = package.manifest.entrypoints.get("content", "content.json")
        path = self._safe_entrypoint(package, entry)
        content = self._read_json(path)
        normalized = self._normalize_content(content)
        if "schema_version" not in content:
            # Missing schema markers belong to the pre-1.0 bundle shape. Keep
            # that fact visible so validation and Studio migration can act on it.
            normalized["schema_version"] = "0.9"
        return normalized

    @staticmethod
    def _normalize_content(content: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(content)
        normalized.setdefault("schema_version", "1.0")
        for key in (
            "resources", "entities", "inventory", "entity_values", "relations",
            "relationship_types", "actors", "agents", "initial_events", "ongoing_events", "random_events",
            "causal_condition_groups", "causal_rule_templates", "causal_event_rules", "time_rules", "actions", "capabilities", "issue_types", "derived_metrics", "objectives", "endings",
        ):
            value = normalized.setdefault(key, [])
            if not isinstance(value, list):
                raise DLCRegistryError(f"DLC content field {key!r} must be a list")
        for key in ("personas", "prompts", "ui_manifest", "time_system", "lifecycle", "rhythm"):
            value = normalized.setdefault(key, {})
            if not isinstance(value, dict):
                raise DLCRegistryError(f"DLC content field {key!r} must be an object")
        try:
            normalized["gameplay"] = normalize_gameplay_document(
                normalized.get("gameplay", {})
            )
            normalized["actors"] = normalize_actor_document(normalized.get("actors", []))
            normalized["actions"] = normalize_action_document(normalized.get("actions", []))
            normalized["capabilities"] = normalize_capability_document(normalized.get("capabilities", []))
            normalized["issue_types"] = normalize_issue_document(normalized.get("issue_types", []))
            normalized["derived_metrics"] = normalize_derived_metric_document(normalized.get("derived_metrics", []))
            normalized["time_system"] = normalize_time_system_document(normalized.get("time_system", {}))
            normalized["lifecycle"] = normalize_lifecycle_document(normalized.get("lifecycle", {}))
            normalized["rhythm"] = normalize_rhythm_document(normalized.get("rhythm", {}))
            normalized["objectives"] = normalize_objective_document(normalized.get("objectives", []))
            normalized["endings"] = normalize_ending_document(normalized.get("endings", []))
        except TypeError as exc:
            raise DLCRegistryError(str(exc)) from exc
        return normalized
    def normalize_content_document(self, content: dict[str, Any]) -> dict[str, Any]:
        """Normalize an in-memory draft without writing it to the package."""

        return self._normalize_content(content)

    def _load_legacy_content(self, package: DLCPackage) -> dict[str, Any]:
        entries = package.manifest.entrypoints

        def read_optional(name: str, fallback: str) -> dict[str, Any]:
            relative = entries.get(name, fallback)
            path = self._safe_entrypoint(package, relative)
            return self._read_json(path) if path.is_file() else {}

        officials = read_optional("agents", "config/officials.json")
        world = read_optional("world", "config/world_state.json")
        events = read_optional("events", "config/events.json")
        personas = read_optional("personas", "config/personas.json")
        prompts: dict[str, str] = {}
        prompts_path = self._safe_entrypoint(package, entries.get("prompts", "prompts"))
        if prompts_path.is_dir():
            for prompt_path in sorted(prompts_path.glob("*.txt")):
                prompts[prompt_path.stem] = prompt_path.read_text(encoding="utf-8")

        used_resources = {
            str(item.get("item_name", item.get("name", "")))
            for item in world.get("inventory", [])
        }
        used_resources.update(
            str(item.get("name", "")) for item in world.get("entity_values", [])
        )
        resources = []
        for token in RESOURCE_TOKENS:
            if token["name"] not in used_resources:
                continue
            resources.append({
                "name": token["name"],
                "unit": token.get("unit", ""),
                "kind": token.get("kind", "asset"),
                "default_quantity": token.get("default", 0.0),
                "min_quantity": token.get("min"),
                "max_quantity": token.get("max"),
                "anchor_rate": token.get("anchor_rate"),
                "metadata": {"legacy_type": token.get("type", "")},
            })
        known = {item["name"] for item in resources}
        for name in sorted(used_resources - known - {""}):
            resources.append({"name": name, "kind": "asset", "anchor_rate": 1.0})

        resource_kinds = {item["name"]: item.get("kind", "asset") for item in resources}
        inventory: list[dict[str, Any]] = []
        entity_values = list(world.get("entity_values", []))
        for item in world.get("inventory", []):
            name = str(item.get("item_name", item.get("resource_name", "")))
            kind = resource_kinds.get(name, "asset")
            if kind == "asset":
                inventory.append(item)
            else:
                entity_values.append({
                    "entity_id": item.get("entity_id", ""),
                    "name": name,
                    "kind": kind,
                    "value": item.get("quantity", 0.0),
                    "description": item.get("description", ""),
                    "version": item.get("version", 0),
                })

        return self._normalize_content({
            "schema_version": "1.0",
            "resources": resources,
            "entities": world.get("entities", []),
            "inventory": inventory,
            "entity_values": entity_values,
            "relations": world.get("relations", []),
            "agents": officials.get("officials", officials.get("agents", [])),
            "initial_events": events.get("initial_events", []),
            "random_events": events.get("random_events", []),
            "causal_event_rules": events.get("causal_event_rules", []),
            "time_rules": events.get("time_rules", []),
            "issue_types": events.get("issue_types", []),
            "objectives": events.get("objectives", []),
            "endings": events.get("endings", []),
            "ongoing_events": world.get("ongoing_events", []),
            "personas": personas,
            "prompts": prompts,
        })

    @staticmethod
    def _duplicates(items: list[dict[str, Any]], key: str) -> set[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for item in items:
            value = str(item.get(key, ""))
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        return duplicates

    def validate(self, dlc_id: str) -> list[DLCValidationIssue]:
        package = self.get(dlc_id)
        manifest = package.manifest
        issues: list[DLCValidationIssue] = []

        def add(
            code: str, message: str, path: str = "", severity: str = "error",
        ) -> None:
            issues.append(DLCValidationIssue(code, message, path, severity))

        if not DLC_ID_PATTERN.fullmatch(manifest.id):
            add("manifest.invalid_id", "DLC id must match ^[a-z][a-z0-9_-]{1,63}$", "manifest.id")
        if package.root.name != manifest.id:
            add("manifest.directory_mismatch", "Package directory must equal manifest id", "manifest.id")
        if not manifest.name.strip():
            add("manifest.missing_name", "DLC name is required", "manifest.name")
        try:
            _parse_semver(manifest.version)
        except DLCRegistryError as exc:
            add("manifest.invalid_version", str(exc), "manifest.version")
        if manifest.format not in DLC_FORMATS:
            add("manifest.invalid_format", f"Unsupported format: {manifest.format}", "manifest.format")
        if manifest.status not in DLC_STATUSES:
            add("manifest.invalid_status", f"Unsupported status: {manifest.status}", "manifest.status")
        dependency_ids: set[str] = set()
        if not isinstance(manifest.dependencies, list):
            add("manifest.invalid_dependencies", "dependencies must be a list", "manifest.dependencies")
        else:
            for index, dependency in enumerate(manifest.dependencies):
                path = f"manifest.dependencies[{index}]"
                if not isinstance(dependency, str) or not dependency.strip():
                    add("dependency.invalid_id", "Dependency id must be a non-empty string", path)
                    continue
                if dependency in dependency_ids:
                    add("dependency.duplicate", f"Duplicate dependency: {dependency}", path)
                dependency_ids.add(dependency)
                if dependency == manifest.id:
                    add("dependency.self", "A DLC cannot depend on itself", path)
                elif dependency not in self._packages:
                    add("dependency.missing", f"Missing dependency: {dependency}", path)
        constraints = manifest.dependency_constraints
        if not isinstance(constraints, dict):
            add(
                "manifest.invalid_dependency_constraints",
                "dependency_constraints must be an object",
                "manifest.dependency_constraints",
            )
            constraints = {}
        for dependency, constraint in constraints.items():
            path = f"manifest.dependency_constraints.{dependency}"
            if dependency not in dependency_ids:
                add(
                    "dependency.constraint_without_dependency",
                    f"Version constraint declared for undeclared dependency: {dependency}",
                    path,
                )
                continue
            if not isinstance(constraint, str) or not constraint.strip():
                add("dependency.invalid_constraint", "Version constraint must be a non-empty string", path)
                continue
            target = self._packages.get(dependency)
            try:
                satisfied = target is None or version_satisfies(target.manifest.version, constraint)
            except DLCRegistryError as exc:
                add("dependency.invalid_constraint", str(exc), path)
                continue
            if target is not None and not satisfied:
                add(
                    "dependency.version_mismatch",
                    f"Dependency {dependency} requires {constraint}, installed version is {target.manifest.version}",
                    path,
                )
        cycle = self._dependency_cycle(dlc_id)
        if cycle:
            add(
                "dependency.cycle",
                "DLC dependency cycle: " + " -> ".join(cycle),
                "manifest.dependencies",
            )
        for name, relative in manifest.entrypoints.items():
            try:
                self._safe_entrypoint(package, relative)
            except DLCRegistryError as exc:
                add("entrypoint.unsafe", str(exc), f"manifest.entrypoints.{name}")

        bindings = manifest.model_bindings
        if not isinstance(bindings, dict):
            add(
                "manifest.invalid_model_bindings",
                "model_bindings must be an object",
                "manifest.model_bindings",
            )
            bindings = {}
        for section in ("tasks", "agents"):
            mapping = bindings.get(section, {})
            if not isinstance(mapping, dict):
                add(
                    "manifest.invalid_model_binding_section",
                    f"model_bindings.{section} must be an object",
                    f"manifest.model_bindings.{section}",
                )
                continue
            for binding_key, profile in mapping.items():
                if not str(binding_key).strip() or not isinstance(profile, str) or not profile.strip():
                    add(
                        "manifest.invalid_model_binding",
                        "Model binding keys and profile names must be non-empty strings",
                        f"manifest.model_bindings.{section}.{binding_key}",
                    )
        for binding_key, profile in bindings.items():
            if binding_key in {"tasks", "agents"}:
                continue
            if not isinstance(profile, str) or not profile.strip():
                add(
                    "manifest.invalid_model_binding",
                    "Legacy flat model bindings must map task names to profile strings",
                    f"manifest.model_bindings.{binding_key}",
                )

        try:
            content = self.load_content(dlc_id)
        except DLCRegistryError as exc:
            add("content.unreadable", str(exc), "content")
            return issues

        content_schema = str(content.get("schema_version", ""))
        if content_schema != CURRENT_CONTENT_SCHEMA_VERSION:
            add(
                "content.migration_required",
                f"Content schema {content_schema or '<missing>'} must be migrated to {CURRENT_CONTENT_SCHEMA_VERSION}",
                "content.schema_version",
            )

        entities = content["entities"]
        agents = content["agents"]
        actors = effective_actor_documents(content["actors"], agents)
        resources = content["resources"]
        for collection, key in ((entities, "id"), (agents, "id"), (resources, "name")):
            label = "resources" if key == "name" else ("entities" if collection is entities else "agents")
            for duplicate in self._duplicates(collection, key):
                add(f"{label}.duplicate", f"Duplicate {key}: {duplicate}", label)

        entity_ids = {str(item.get("id", "")) for item in entities if item.get("id")}
        agent_ids = {str(item.get("id", "")) for item in agents if item.get("id")}
        actor_ids = {str(item.get("actor_id", item.get("id", ""))) for item in actors if item.get("actor_id", item.get("id"))}
        actor_map = {str(item.get("actor_id", item.get("id", ""))): item for item in actors if item.get("actor_id", item.get("id"))}
        actor_knowledge_holder_ids = actor_ids | agent_ids
        objective_ids = {str(item.get("objective_id", "")) for item in content["objectives"] if item.get("objective_id")}
        relationship_type_ids = RelationshipCatalog(content["relationship_types"]).ids
        resource_map = {str(item.get("name", "")): item for item in resources if item.get("name")}
        derived_metric_catalog: DerivedMetricCatalog | None = None
        try:
            derived_metric_catalog = DerivedMetricCatalog(content["derived_metrics"])
        except DerivedMetricContractError as exc:
            add(exc.code, exc.code, exc.path or "derived_metrics")
        derived_metric_ids = {
            str(item.get("metric_id", item.get("id", "")))
            for item in content["derived_metrics"]
            if isinstance(item, dict) and item.get("metric_id", item.get("id"))
        }
        if derived_metric_catalog is not None:
            for definition in (
                derived_metric_catalog.get(metric_id)
                for metric_id in sorted(derived_metric_ids)
            ):
                if definition is None:
                    continue
                for reference_index, reference in enumerate(definition.references):
                    reference_path = f"derived_metrics.{definition.metric_id}.formula.references[{reference_index}]"
                    source = reference.get("source", "")
                    entity_id = reference.get("entity_id", "")
                    name = reference.get("name", "")
                    if source in {"entity_count", "anchored_inventory", "metric", "parameter", "ongoing_event"}:
                        if entity_id and entity_id not in entity_ids:
                            add("derived_metric.missing_entity", f"Unknown derived-metric entity: {entity_id}", reference_path + ".entity_id")
                    if source == "actor_attribute" and entity_id and entity_id not in actor_ids:
                        add("derived_metric.missing_actor", f"Unknown derived-metric actor: {entity_id}", reference_path + ".entity_id")
                    if source in {"anchored_inventory", "metric", "parameter"}:
                        resource = resource_map.get(name)
                        expected_kind = "asset" if source == "anchored_inventory" else source
                        if resource is None:
                            add("derived_metric.missing_resource", f"Unknown derived-metric resource: {name}", reference_path + ".name")
                        elif resource.get("kind") != expected_kind:
                            add("derived_metric.invalid_resource_kind", f"Derived metric requires resource kind {expected_kind}", reference_path + ".source")
                    if source == "relation" and name and name not in relationship_type_ids:
                        add("derived_metric.missing_relationship_type", f"Unknown derived-metric relationship type: {name}", reference_path + ".name")
        for gameplay_issue in validate_gameplay_document(
            content["gameplay"], entity_ids=entity_ids, agent_ids=agent_ids
        ):
            add(gameplay_issue.code, gameplay_issue.message, gameplay_issue.path)
        gameplay_contract = GameplayContract.from_content(
            content["gameplay"], agents, package_id=manifest.id,
            package_name=manifest.name, derive_legacy=True,
        )
        action_resources = {str(item.get("name", "")): item for item in RESOURCE_TOKENS}
        action_resources.update(resource_map)
        for action_issue in validate_action_definitions(
            content["actions"], entity_ids=entity_ids, agent_ids=agent_ids,
            resources=action_resources, gameplay=gameplay_contract,
        ):
            add(action_issue.code, action_issue.message, action_issue.path)
        action_ids = {str(item.get("action_id", "")) for item in content["actions"] if item.get("action_id")}
        for capability_issue in validate_capability_definitions(
            content["capabilities"], action_ids=action_ids,
            entity_ids=entity_ids, agent_ids=agent_ids,
        ):
            add(
                capability_issue.code, capability_issue.message,
                capability_issue.path, capability_issue.severity,
            )
        capability_ids = {str(item.get("capability_id", "")) for item in content["capabilities"] if item.get("capability_id")}
        for actor_issue in validate_actor_definitions(
            actors, agents=agents, entity_ids=entity_ids, capability_ids=capability_ids,
        ):
            add(actor_issue.code, actor_issue.message, actor_issue.path, actor_issue.severity)
        for issue in validate_issue_definitions(
            content["issue_types"], action_ids=action_ids, entity_ids=entity_ids,
            agent_ids=agent_ids, gameplay=gameplay_contract,
        ):
            add(issue.code, issue.message, issue.path)
        issue_type_ids = {str(item.get("issue_type_id", "")) for item in content["issue_types"] if item.get("issue_type_id")}
        temporal_contract: TemporalContract | None = None
        try:
            temporal_contract = TemporalContract.from_mapping(content.get("time_system", {}))
        except TemporalContractError as exc:
            add(exc.code, exc.code, exc.path or "time_system")
        if temporal_contract is not None:
            for index, definition in enumerate(temporal_contract.boundary_events):
                path = f"time_system.boundary_events[{index}]"
                for target in definition.target_agents:
                    if target not in agent_ids:
                        add("time_system.missing_agent", f"Unknown target agent: {target}", path + ".target_agents")
                if definition.issue_type_id and definition.issue_type_id not in issue_type_ids:
                    add("time_system.missing_issue_type", f"Unknown issue type: {definition.issue_type_id}", path + ".issue_type_id")
            for pause_index, pause in enumerate(temporal_contract.auto_pause_conditions):
                for condition_index, condition in enumerate(pause.conditions):
                    path = f"time_system.auto_pause_conditions[{pause_index}].conditions[{condition_index}]"
                    if condition.kind in {"anchored_inventory", "metric", "parameter", "entity_field", "entity_tag", "entity_state", "ongoing_event", "world_truth", "actor_belief", "player_knowledge", "knowledge_confidence", "knowledge_freshness", "knowledge_secrecy"} and condition.entity_id and condition.entity_id not in entity_ids:
                        add("time_system.missing_entity", f"Unknown entity: {condition.entity_id}", path + ".entity_id")
                    if condition.kind in {"anchored_inventory", "metric", "parameter"}:
                        resource = resource_map.get(condition.name)
                        expected_kind = "asset" if condition.kind == "anchored_inventory" else condition.kind
                        if resource is None:
                            add("time_system.missing_resource", f"Unknown resource: {condition.name}", path + ".name")
                        elif resource.get("kind") != expected_kind:
                            add("time_system.invalid_resource_kind", f"Condition requires resource kind {expected_kind}", path + ".kind")
                    if condition.kind in {"actor_attribute", "actor_state", "actor_trait", "actor_membership"} and condition.entity_id not in actor_ids:
                        add("time_system.missing_actor", f"Unknown actor: {condition.entity_id}", path + ".entity_id")
                    if condition.kind == "relationship":
                        for field_name in ("entity_id", "target_entity_id"):
                            entity_id = getattr(condition, field_name)
                            if entity_id and entity_id not in entity_ids:
                                add("time_system.missing_entity", f"Unknown relationship endpoint: {entity_id}", path + "." + field_name)
                        if condition.name and condition.name not in relationship_type_ids:
                            add("time_system.missing_relationship_type", f"Unknown relationship type: {condition.name}", path + ".name")
                    if condition.kind == "objective_progress" and (condition.entity_id or condition.name) not in objective_ids:
                        add("time_system.missing_objective", f"Unknown objective: {condition.entity_id or condition.name}", path + ".entity_id")
                    if condition.kind == "derived_metric" and (condition.name or condition.entity_id) not in derived_metric_ids:
                        add("time_system.missing_derived_metric", f"Unknown derived metric: {condition.name or condition.entity_id}", path + ".name")
        rhythm_contract: RhythmContract | None = None
        try:
            rhythm_contract = RhythmContract.from_mapping(content.get("rhythm", {}))
        except RhythmContractError as exc:
            add(exc.code, exc.code, exc.path or "rhythm")
        if rhythm_contract is not None:
            def validate_rhythm_condition(condition: Any, path: str) -> None:
                if condition.kind in {
                    "anchored_inventory", "metric", "parameter", "entity_field",
                    "entity_tag", "entity_state", "ongoing_event", "world_truth",
                    "actor_belief", "player_knowledge", "knowledge_confidence",
                    "knowledge_freshness", "knowledge_secrecy",
                } and condition.entity_id and condition.entity_id not in entity_ids:
                    add("rhythm.missing_entity", f"Unknown entity: {condition.entity_id}", path + ".entity_id")
                if condition.kind in {"anchored_inventory", "metric", "parameter"}:
                    resource = resource_map.get(condition.name)
                    expected_kind = "asset" if condition.kind == "anchored_inventory" else condition.kind
                    if resource is None:
                        add("rhythm.missing_resource", f"Unknown resource: {condition.name}", path + ".name")
                    elif resource.get("kind") != expected_kind:
                        add("rhythm.invalid_resource_kind", f"Condition requires resource kind {expected_kind}", path + ".kind")
                if condition.kind in {"actor_attribute", "actor_state", "actor_trait", "actor_membership"} and condition.entity_id not in actor_ids:
                    add("rhythm.missing_actor", f"Unknown actor: {condition.entity_id}", path + ".entity_id")
                if condition.kind == "relationship":
                    for field_name in ("entity_id", "target_entity_id"):
                        entity_id = getattr(condition, field_name)
                        if entity_id and entity_id not in entity_ids:
                            add("rhythm.missing_entity", f"Unknown relationship endpoint: {entity_id}", path + "." + field_name)
                    if condition.name and condition.name not in relationship_type_ids:
                        add("rhythm.missing_relationship_type", f"Unknown relationship type: {condition.name}", path + ".name")
                if condition.kind == "objective_progress" and (condition.entity_id or condition.name) not in objective_ids:
                    add("rhythm.missing_objective", f"Unknown objective: {condition.entity_id or condition.name}", path + ".entity_id")
                if condition.kind == "derived_metric" and (condition.name or condition.entity_id) not in derived_metric_ids:
                    add("rhythm.missing_derived_metric", f"Unknown derived metric: {condition.name or condition.entity_id}", path + ".name")

            for rule_index, rule in enumerate(rhythm_contract.rules):
                rule_path = f"rhythm.rules[{rule_index}]"
                for issue_index, issue_type_id in enumerate(rule.issue_type_ids):
                    if issue_type_id not in issue_type_ids:
                        add("rhythm.missing_issue_type", f"Unknown issue type: {issue_type_id}", f"{rule_path}.issue_type_ids[{issue_index}]")
                for section_name, transition in (("deescalation", rule.deescalation), ("climax", rule.climax)):
                    for condition_index, condition in enumerate(transition.conditions):
                        validate_rhythm_condition(condition, f"{rule_path}.{section_name}.conditions[{condition_index}]")

        lifecycle_contract: LifecycleContract | None = None
        try:
            lifecycle_contract = LifecycleContract.from_mapping(content.get("lifecycle", {}))
        except LifecycleContractError as exc:
            add(exc.code, exc.code, exc.path or "lifecycle")
        if lifecycle_contract is not None:
            cycle_ids = {item.cycle_id for item in temporal_contract.cycles} if temporal_contract else set()
            season_ids = {item.season_id for item in temporal_contract.seasons} if temporal_contract else set()
            month_ids = (
                {item.month_id for item in temporal_contract.calendar.months}
                if temporal_contract and temporal_contract.calendar else set()
            )

            def lifecycle_dynamic_reference(value: Any) -> bool:
                text = str(value or "")
                return "{" in text and "}" in text

            def validate_lifecycle_condition(condition: Any, path: str) -> None:
                if condition.kind in {
                    "anchored_inventory", "metric", "parameter", "entity_field",
                    "entity_tag", "entity_state", "ongoing_event", "world_truth",
                    "actor_belief", "player_knowledge", "knowledge_confidence",
                    "knowledge_freshness", "knowledge_secrecy",
                } and condition.entity_id and condition.entity_id not in entity_ids:
                    add("lifecycle.missing_entity", f"Unknown entity: {condition.entity_id}", path + ".entity_id")
                if condition.kind in {"anchored_inventory", "metric", "parameter"}:
                    resource = resource_map.get(condition.name)
                    expected_kind = "asset" if condition.kind == "anchored_inventory" else condition.kind
                    if resource is None:
                        add("lifecycle.missing_resource", f"Unknown resource: {condition.name}", path + ".name")
                    elif resource.get("kind") != expected_kind:
                        add("lifecycle.invalid_resource_kind", f"Condition requires resource kind {expected_kind}", path + ".kind")
                if condition.kind in {"actor_attribute", "actor_state", "actor_trait", "actor_membership"}:
                    if condition.entity_id not in actor_ids and not lifecycle_dynamic_reference(condition.entity_id):
                        add("lifecycle.missing_actor", f"Unknown actor: {condition.entity_id}", path + ".entity_id")
                if condition.kind == "relationship":
                    for field_name in ("entity_id", "target_entity_id"):
                        endpoint = getattr(condition, field_name)
                        if endpoint and endpoint not in entity_ids and not lifecycle_dynamic_reference(endpoint):
                            add("lifecycle.missing_entity", f"Unknown relationship endpoint: {endpoint}", path + "." + field_name)
                    if condition.name and condition.name not in relationship_type_ids:
                        add("lifecycle.missing_relationship_type", f"Unknown relationship type: {condition.name}", path + ".name")
                if condition.kind == "objective_progress" and (condition.entity_id or condition.name) not in objective_ids:
                    add("lifecycle.missing_objective", f"Unknown objective: {condition.entity_id or condition.name}", path + ".entity_id")
                if condition.kind == "derived_metric" and (condition.name or condition.entity_id) not in derived_metric_ids:
                    add("lifecycle.missing_derived_metric", f"Unknown derived metric: {condition.name or condition.entity_id}", path + ".name")
                if condition.kind == "aggregate_metric":
                    if condition.entity_id and condition.field != "actor_attribute" and condition.entity_id not in entity_ids:
                        add("lifecycle.missing_entity", f"Unknown aggregate entity: {condition.entity_id}", path + ".entity_id")
                    if condition.field == "actor_attribute" and condition.entity_id and condition.entity_id not in actor_ids and not lifecycle_dynamic_reference(condition.entity_id):
                        add("lifecycle.missing_actor", f"Unknown aggregate actor: {condition.entity_id}", path + ".entity_id")
                    if condition.field in {"anchored_inventory", "metric", "parameter"}:
                        resource = resource_map.get(condition.name)
                        expected_kind = "asset" if condition.field == "anchored_inventory" else condition.field
                        if resource is None:
                            add("lifecycle.missing_resource", f"Unknown aggregate resource: {condition.name}", path + ".name")
                        elif resource.get("kind") != expected_kind:
                            add("lifecycle.invalid_resource_kind", f"Aggregate requires resource kind {expected_kind}", path + ".field")
                    if condition.field == "relation" and condition.name and condition.name not in relationship_type_ids:
                        add("lifecycle.missing_relationship_type", f"Unknown aggregate relationship type: {condition.name}", path + ".name")

            for template_index, template in enumerate(lifecycle_contract.actor_templates):
                path = f"lifecycle.actor_templates[{template_index}]"
                source_agent_id = str(template.agent.get("source_agent_id", ""))
                if source_agent_id and source_agent_id not in agent_ids:
                    add("lifecycle.missing_source_agent", f"Unknown static source agent: {source_agent_id}", path + ".agent.source_agent_id")
                actor_entity_id = str(template.actor.get("entity_id", ""))
                if actor_entity_id and not template.entity and actor_entity_id not in entity_ids and not lifecycle_dynamic_reference(actor_entity_id):
                    add("lifecycle.missing_entity", f"Unknown Actor entity: {actor_entity_id}", path + ".actor.entity_id")
                location_id = str(template.entity.get("location_id", ""))
                if location_id and location_id not in entity_ids and not lifecycle_dynamic_reference(location_id):
                    add("lifecycle.missing_entity", f"Unknown template location: {location_id}", path + ".entity.location_id")
                memberships = template.actor.get("memberships", [])
                if isinstance(memberships, list):
                    for membership_index, membership in enumerate(memberships):
                        if not isinstance(membership, Mapping):
                            continue
                        organization_id = str(membership.get("organization_id", ""))
                        if organization_id and organization_id not in entity_ids and not lifecycle_dynamic_reference(organization_id):
                            add("lifecycle.missing_organization", f"Unknown organization: {organization_id}", f"{path}.actor.memberships[{membership_index}].organization_id")

            for position_index, position in enumerate(lifecycle_contract.positions):
                path = f"lifecycle.positions[{position_index}]"
                if position.organization_id not in entity_ids:
                    add("lifecycle.missing_organization", f"Unknown organization: {position.organization_id}", path + ".organization_id")
                for field_name, values in (("incumbent_actor_ids", position.incumbent_actor_ids), ("candidate_actor_ids", position.candidate_actor_ids)):
                    for actor_index, actor_id in enumerate(values):
                        if actor_id not in actor_ids and not lifecycle_dynamic_reference(actor_id):
                            add("lifecycle.missing_actor", f"Unknown initial Actor: {actor_id}", f"{path}.{field_name}[{actor_index}]")

            for transition_index, transition in enumerate(lifecycle_contract.transitions):
                path = f"lifecycle.transitions[{transition_index}]"
                if transition.cycle_id and transition.cycle_id not in cycle_ids:
                    add("lifecycle.missing_cycle", f"Unknown time cycle: {transition.cycle_id}", path + ".cycle_id")
                if transition.season_id and transition.season_id not in season_ids:
                    add("lifecycle.missing_season", f"Unknown season: {transition.season_id}", path + ".season_id")
                if transition.month_id and transition.month_id not in month_ids:
                    add("lifecycle.missing_month", f"Unknown calendar month: {transition.month_id}", path + ".month_id")
                for condition_index, condition in enumerate(transition.conditions):
                    validate_lifecycle_condition(condition, f"{path}.conditions[{condition_index}]")
                for operation_index, operation in enumerate(transition.operations):
                    operation_path = f"{path}.operations[{operation_index}]"
                    data = operation.data
                    if operation.operation == "transform_entity":
                        entity_id = str(data.get("entity_id", ""))
                        if entity_id not in entity_ids and not lifecycle_dynamic_reference(entity_id):
                            add("lifecycle.missing_entity", f"Unknown entity: {entity_id}", operation_path + ".entity_id")
                    elif operation.operation == "split_organization":
                        organization_id = str(data.get("source_organization_id", ""))
                        if organization_id not in entity_ids and not lifecycle_dynamic_reference(organization_id):
                            add("lifecycle.missing_organization", f"Unknown source organization: {organization_id}", operation_path + ".source_organization_id")
                    elif operation.operation == "merge_organizations":
                        source_ids = data.get("source_organization_ids", [])
                        if isinstance(source_ids, list):
                            for source_index, organization_id in enumerate(source_ids):
                                if str(organization_id) not in entity_ids and not lifecycle_dynamic_reference(organization_id):
                                    add("lifecycle.missing_organization", f"Unknown source organization: {organization_id}", f"{operation_path}.source_organization_ids[{source_index}]")

        for objective_issue in validate_objective_definitions(
            content["objectives"], content["endings"], entity_ids=entity_ids,
            resources=action_resources, action_ids=action_ids,
            issue_type_ids=issue_type_ids, derived_metric_ids=derived_metric_ids,
            gameplay=gameplay_contract,
        ):
            add(objective_issue.code, objective_issue.message, objective_issue.path, objective_issue.severity)
        for ui_issue in validate_ui_manifest_document(content["ui_manifest"]):
            add(ui_issue.code, ui_issue.message, ui_issue.path, ui_issue.severity)

        agent_bindings = bindings.get("agents", {}) if isinstance(bindings.get("agents", {}), dict) else {}
        for bound_agent in agent_bindings:
            if str(bound_agent) not in agent_ids:
                add(
                    "manifest.model_binding_unknown_agent",
                    f"Model binding references unknown agent: {bound_agent}",
                    f"manifest.model_bindings.agents.{bound_agent}",
                )

        for index, entity in enumerate(entities):
            if not str(entity.get("id", "")).strip():
                add("entity.missing_id", "Entity id is required", f"entities[{index}].id")
            if not str(entity.get("type", "")).strip():
                add("entity.missing_type", "Entity type is required", f"entities[{index}].type")

        for index, resource in enumerate(resources):
            path = f"resources[{index}]"
            name = str(resource.get("name", ""))
            kind = str(resource.get("kind", "asset"))
            if not name:
                add("resource.missing_name", "Resource name is required", f"{path}.name")
            if kind not in RESOURCE_KINDS:
                add("resource.invalid_kind", f"Invalid resource kind: {kind}", f"{path}.kind")
            if kind == "asset":
                rate = resource.get("anchor_rate")
                if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                    add("resource.invalid_anchor_rate", "Asset anchor_rate must be finite and positive", f"{path}.anchor_rate")
            for field_name in ("default_quantity", "min_quantity", "max_quantity"):
                value = resource.get(field_name)
                if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                    add("resource.invalid_number", f"{field_name} must be finite", f"{path}.{field_name}")

        for index, item in enumerate(content["inventory"]):
            path = f"inventory[{index}]"
            entity_id = str(item.get("entity_id", ""))
            resource_name = str(item.get("item_name", item.get("resource_name", "")))
            if entity_id not in entity_ids:
                add("inventory.missing_entity", f"Unknown entity: {entity_id}", f"{path}.entity_id")
            resource = resource_map.get(resource_name)
            if resource is None:
                add("inventory.missing_resource", f"Unknown resource: {resource_name}", f"{path}.item_name")
            elif resource.get("kind", "asset") != "asset":
                add("inventory.non_asset", f"Inventory resource must be an asset: {resource_name}", f"{path}.item_name")
            elif "anchor_rate" in item:
                item_rate = item.get("anchor_rate")
                resource_rate = resource.get("anchor_rate")
                if (
                    not isinstance(item_rate, (int, float))
                    or isinstance(item_rate, bool)
                    or not math.isfinite(item_rate)
                    or float(item_rate) != float(resource_rate)
                ):
                    add(
                        "inventory.anchor_mismatch",
                        f"Inventory anchor_rate must match resource catalog: {resource_name}",
                        f"{path}.anchor_rate",
                    )

        for index, item in enumerate(content["entity_values"]):
            path = f"entity_values[{index}]"
            entity_id = str(item.get("entity_id", ""))
            name = str(item.get("name", ""))
            if entity_id not in entity_ids:
                add("value.missing_entity", f"Unknown entity: {entity_id}", f"{path}.entity_id")
            resource = resource_map.get(name)
            if resource is None:
                add("value.missing_resource", f"Unknown resource: {name}", f"{path}.name")
            elif resource.get("kind") not in {"metric", "parameter"}:
                add("value.invalid_kind", f"Entity value must reference metric/parameter: {name}", f"{path}.name")
            if item.get("kind") and resource and item.get("kind") != resource.get("kind"):
                add("value.kind_mismatch", f"Value kind differs from resource definition: {name}", f"{path}.kind")
        for issue in validate_relationship_definitions(
            content["relationship_types"], content["relations"], entity_ids=entity_ids
        ):
            code = "relation.invalid_type" if issue.code == "relation.unknown_type" else issue.code
            add(code, issue.message, issue.path, issue.severity)

        for index, agent in enumerate(agents):
            path = f"agents[{index}]"
            agent_id = str(agent.get("id", ""))
            if not agent_id:
                add("agent.missing_id", "Agent id is required", f"{path}.id")
            location_id = str(agent.get("location_id", ""))
            if location_id and location_id not in entity_ids:
                add("agent.missing_location", f"Unknown location entity: {location_id}", f"{path}.location_id")
            superior_id = agent.get("superior_id")
            if superior_id and str(superior_id) not in agent_ids:
                add("agent.missing_superior", f"Unknown superior agent: {superior_id}", f"{path}.superior_id")
            model_profile = agent.get("model_profile")
            if model_profile is not None and (
                not isinstance(model_profile, str) or not model_profile.strip()
            ):
                add(
                    "agent.invalid_model_profile",
                    "model_profile must be a non-empty string",
                    f"{path}.model_profile",
                )

        for duplicate in self._duplicates([item for item in content["time_rules"] if isinstance(item, dict)], "rule_id"):
            add("time_rules.duplicate", f"Duplicate rule_id: {duplicate}", "time_rules")
        for index, rule in enumerate(content["time_rules"]):
            path = f"time_rules[{index}]"
            if not isinstance(rule, dict):
                add("time_rule.invalid_rule", "Time rule must be an object", path)
                continue
            try:
                ActionTimeRule.from_dict(rule)
            except TimeRuleError as exc:
                add("time_rule.invalid_rule", str(exc), path)

        for duplicate in self._duplicates([item for item in content["causal_event_rules"] if isinstance(item, dict)], "rule_id"):
            add("causal_events.duplicate", f"Duplicate rule_id: {duplicate}", "causal_event_rules")
        try:
            expanded_causal_rules = expand_causal_rule_document(content)
        except CausalTemplateError as exc:
            add("causal_authoring.invalid", str(exc), exc.path or "causal_event_rules")
            expanded_causal_rules = []
        for index, raw_rule in enumerate(expanded_causal_rules):
            path = f"causal_event_rules[{index}]"
            try:
                rule = CausalEventRule.from_mapping(raw_rule)
            except CausalEventError as exc:
                add("causal_event.invalid_rule", str(exc), path)
                continue
            for target in rule.target_agents:
                if target not in agent_ids:
                    add("causal_event.missing_agent", f"Unknown target agent: {target}", f"{path}.target_agents")
            for condition_index, condition in enumerate(rule.conditions):
                condition_path = f"{path}.conditions[{condition_index}]"
                entity_kinds = {
                    "anchored_inventory", "metric", "parameter",
                    "entity_field", "entity_tag", "entity_state",
                }
                if condition.kind in entity_kinds and condition.entity_id not in entity_ids:
                    add("causal_event.missing_entity", f"Unknown entity: {condition.entity_id}", f"{condition_path}.entity_id")
                optional_entity_kinds = {
                    "ongoing_event", "world_truth", "actor_belief", "player_knowledge",
                    "knowledge_confidence", "knowledge_freshness", "knowledge_secrecy",
                }
                if condition.kind in optional_entity_kinds and condition.entity_id and condition.entity_id not in entity_ids:
                    add("causal_event.missing_entity", f"Unknown entity: {condition.entity_id}", f"{condition_path}.entity_id")
                if condition.kind in {"anchored_inventory", "metric", "parameter"}:
                    resource = resource_map.get(condition.name)
                    if resource is None:
                        add("causal_event.missing_resource", f"Unknown resource: {condition.name}", f"{condition_path}.name")
                    elif condition.kind == "anchored_inventory" and resource.get("kind") != "asset":
                        add("causal_event.invalid_resource_kind", "anchored_inventory condition requires an asset", f"{condition_path}.kind")
                    elif condition.kind in {"metric", "parameter"} and resource.get("kind") != condition.kind:
                        add("causal_event.invalid_resource_kind", f"Condition requires resource kind {condition.kind}", f"{condition_path}.kind")
                actor_component_kinds = {
                    "actor_attribute", "actor_state", "actor_trait", "actor_membership",
                }
                if condition.kind in actor_component_kinds:
                    if condition.entity_id not in actor_ids:
                        add("causal_event.missing_actor", f"Unknown actor: {condition.entity_id}", f"{condition_path}.entity_id")
                    else:
                        actor = actor_map[condition.entity_id]
                        runtime_refs = {
                            "actor_attribute": set(actor.get("attributes", {})),
                            "actor_state": set(actor.get("states", {})),
                            "actor_trait": {str(item.get("trait_id", "")) for item in actor.get("traits", [])},
                            "actor_membership": {str(item.get("organization_id", "")) for item in actor.get("memberships", [])},
                        }
                        if condition.name not in runtime_refs[condition.kind]:
                            add("causal_event.missing_actor_component", f"Unknown actor component: {condition.name}", f"{condition_path}.name")
                if condition.kind == "actor_belief" and condition.target_entity_id not in actor_knowledge_holder_ids:
                    add(
                        "causal_event.missing_knowledge_holder",
                        f"Unknown Actor knowledge holder: {condition.target_entity_id}",
                        f"{condition_path}.target_entity_id",
                    )
                if condition.kind in {"knowledge_confidence", "knowledge_freshness", "knowledge_secrecy"}:
                    layer = str(condition.filters.get("layer", "player_knowledge"))
                    holder_id = str(condition.target_entity_id or condition.filters.get("holder_id", ""))
                    if layer == "actor_belief" and holder_id not in actor_knowledge_holder_ids:
                        add(
                            "causal_event.missing_knowledge_holder",
                            f"Unknown Actor knowledge holder: {holder_id}",
                            f"{condition_path}.target_entity_id",
                        )
                if condition.kind == "relationship":
                    for field_name, entity_id in (("entity_id", condition.entity_id), ("target_entity_id", condition.target_entity_id)):
                        if entity_id not in entity_ids:
                            add("causal_event.missing_entity", f"Unknown relationship endpoint: {entity_id}", f"{condition_path}.{field_name}")
                    if condition.name not in relationship_type_ids:
                        add("causal_event.missing_relationship_type", f"Unknown relationship type: {condition.name}", f"{condition_path}.name")
                if condition.kind == "objective_progress":
                    objective_id = condition.entity_id or condition.name
                    if objective_id not in objective_ids:
                        add("causal_event.missing_objective", f"Unknown objective: {objective_id}", f"{condition_path}.entity_id")
                if condition.kind == "derived_metric":
                    metric_id = condition.name or condition.entity_id
                    if metric_id not in derived_metric_ids:
                        add("causal_event.missing_derived_metric", f"Unknown derived metric: {metric_id}", f"{condition_path}.name")
                if condition.kind == "aggregate_metric":
                    if condition.entity_id and condition.entity_id not in entity_ids and condition.field != "actor_attribute":
                        add("causal_event.missing_entity", f"Unknown aggregate entity: {condition.entity_id}", f"{condition_path}.entity_id")
                    if condition.field == "actor_attribute" and condition.entity_id and condition.entity_id not in actor_ids:
                        add("causal_event.missing_actor", f"Unknown aggregate actor: {condition.entity_id}", f"{condition_path}.entity_id")
                    if condition.field in {"anchored_inventory", "metric", "parameter"}:
                        resource = resource_map.get(condition.name)
                        expected_kind = "asset" if condition.field == "anchored_inventory" else condition.field
                        if resource is None:
                            add("causal_event.missing_resource", f"Unknown aggregate resource: {condition.name}", f"{condition_path}.name")
                        elif resource.get("kind") != expected_kind:
                            add("causal_event.invalid_resource_kind", f"Aggregate requires resource kind {expected_kind}", f"{condition_path}.field")
                    if condition.field == "relation" and condition.name and condition.name not in relationship_type_ids:
                        add("causal_event.missing_relationship_type", f"Unknown aggregate relationship type: {condition.name}", f"{condition_path}.name")
            available_effect_entities = set(entity_ids)
            created_effect_entities: set[str] = set()
            available_investigations: set[str] = set()
            for effect_index, effect in enumerate(rule.world_effects):
                effect_path = f"{path}.world_effects[{effect_index}]"
                effect_type = str(effect.get("effect_type", ""))

                def require_effect_entity(field_name: str, *, optional: bool = False) -> str:
                    entity_id = str(effect.get(field_name, ""))
                    if optional and not entity_id:
                        return entity_id
                    if entity_id not in available_effect_entities:
                        add(
                            "causal_event.effect_missing_entity",
                            f"Unknown world-effect entity: {entity_id}",
                            f"{effect_path}.{field_name}",
                        )
                    return entity_id

                def require_actor_knowledge_holder(holder_id: str, field_name: str) -> None:
                    if holder_id not in actor_knowledge_holder_ids:
                        add(
                            "causal_event.effect_missing_knowledge_holder",
                            f"Unknown Actor knowledge holder: {holder_id}",
                            f"{effect_path}.{field_name}",
                        )

                if effect_type == "create_entity":
                    created_id = str(effect.get("entity_id", ""))
                    if created_id in available_effect_entities or created_id in created_effect_entities:
                        add(
                            "causal_event.effect_duplicate_entity",
                            f"World-effect entity already exists or is created twice: {created_id}",
                            f"{effect_path}.entity_id",
                        )
                    else:
                        created_effect_entities.add(created_id)
                        available_effect_entities.add(created_id)
                    for resource_name in effect.get("resources", {}):
                        if str(resource_name) not in resource_map:
                            add(
                                "causal_event.effect_missing_resource",
                                f"Unknown create-entity resource: {resource_name}",
                                f"{effect_path}.resources.{resource_name}",
                            )
                elif effect_type == "transfer_anchored_value":
                    require_effect_entity("from_entity_id")
                    require_effect_entity("to_entity_id")
                elif effect_type in {
                    "adjust_resource", "set_resource", "update_status",
                    "create_ongoing_event", "move_entity", "transform_entity", "remove_entity",
                }:
                    referenced_id = require_effect_entity("entity_id")
                    if effect_type == "remove_entity" and referenced_id in available_effect_entities:
                        available_effect_entities.remove(referenced_id)
                elif effect_type in {"enable_agent", "disable_agent"}:
                    agent_id = str(effect.get("agent_id", ""))
                    actor_id = str(effect.get("actor_id", ""))
                    if agent_id not in agent_ids:
                        add(
                            "causal_event.effect_missing_agent",
                            f"Unknown world-effect agent: {agent_id}",
                            f"{effect_path}.agent_id",
                        )
                    if actor_id and actor_id not in actor_ids:
                        add(
                            "causal_event.effect_missing_actor",
                            f"Unknown world-effect actor: {actor_id}",
                            f"{effect_path}.actor_id",
                        )
                elif effect_type == "modify_relationship":
                    require_effect_entity("parent_id")
                    require_effect_entity("child_id")
                    rel_type = str(effect.get("rel_type", ""))
                    if rel_type not in relationship_type_ids:
                        add(
                            "causal_event.effect_missing_relationship_type",
                            f"Unknown world-effect relationship type: {rel_type}",
                            f"{effect_path}.rel_type",
                        )
                elif effect_type == "modify_membership":
                    actor_id = str(effect.get("actor_id", ""))
                    if actor_id not in actor_ids:
                        add(
                            "causal_event.effect_missing_actor",
                            f"Unknown world-effect actor: {actor_id}",
                            f"{effect_path}.actor_id",
                        )
                    require_effect_entity("organization_id")
                elif effect_type == "create_issue":
                    issue_type_id = str(effect.get("issue_type_id", ""))
                    if issue_type_id not in issue_type_ids:
                        add(
                            "causal_event.effect_missing_issue_type",
                            f"Unknown world-effect issue type: {issue_type_id}",
                            f"{effect_path}.issue_type_id",
                        )
                    source = effect.get("source", {})
                    if isinstance(source, Mapping) and str(source.get("kind", "")) == "entity":
                        source_id = str(source.get("id", ""))
                        if source_id not in available_effect_entities:
                            add(
                                "causal_event.effect_missing_entity",
                                f"Unknown world-effect issue source entity: {source_id}",
                                f"{effect_path}.source.id",
                            )
                elif effect_type in {"declare_world_truth", "set_actor_belief", "reveal_information"}:
                    require_effect_entity("entity_id", optional=True)
                    if effect_type == "set_actor_belief":
                        require_actor_knowledge_holder(str(effect.get("holder_id", "")), "holder_id")
                elif effect_type == "propagate_knowledge":
                    require_effect_entity("entity_id", optional=True)
                    source_layer = str(effect.get("source_layer", ""))
                    target_layer = str(effect.get("target_layer", ""))
                    if source_layer == "actor_belief":
                        require_actor_knowledge_holder(str(effect.get("source_holder_id", "")), "source_holder_id")
                    if target_layer == "actor_belief":
                        require_actor_knowledge_holder(str(effect.get("target_holder_id", "")), "target_holder_id")
                elif effect_type == "start_investigation":
                    require_effect_entity("entity_id", optional=True)
                    investigation_id = str(effect.get("investigation_id", ""))
                    if investigation_id in available_investigations:
                        add(
                            "causal_event.effect_duplicate_investigation",
                            f"Investigation is started twice in one rule: {investigation_id}",
                            f"{effect_path}.investigation_id",
                        )
                    available_investigations.add(investigation_id)
                    if str(effect.get("investigator_layer", "")) == "actor_belief":
                        require_actor_knowledge_holder(str(effect.get("investigator_id", "")), "investigator_id")
                    result_policy = effect.get("result_policy", {})
                    if isinstance(result_policy, Mapping) and result_policy:
                        if str(result_policy.get("source_layer", "")) == "actor_belief":
                            require_actor_knowledge_holder(
                                str(result_policy.get("source_holder_id", "")),
                                "result_policy.source_holder_id",
                            )
                        if str(result_policy.get("target_layer", effect.get("investigator_layer", ""))) == "actor_belief":
                            require_actor_knowledge_holder(
                                str(result_policy.get("target_holder_id", effect.get("investigator_id", ""))),
                                "result_policy.target_holder_id",
                            )
                elif effect_type == "advance_objective":
                    objective_id = str(effect.get("objective_id", ""))
                    if objective_id not in objective_ids:
                        add(
                            "causal_event.effect_missing_objective",
                            f"Unknown world-effect objective: {objective_id}",
                            f"{effect_path}.objective_id",
                        )
                elif effect_type == "unlock_action":
                    action_id = str(effect.get("action_id", ""))
                    if action_id not in action_ids:
                        add(
                            "causal_event.effect_missing_action",
                            f"Unknown world-effect action: {action_id}",
                            f"{effect_path}.action_id",
                        )
                    scope_id = str(effect.get("scope_id", ""))
                    if scope_id and scope_id not in available_effect_entities and scope_id not in actor_ids and scope_id not in agent_ids:
                        add(
                            "causal_event.effect_missing_scope",
                            f"Unknown world-effect action scope: {scope_id}",
                            f"{effect_path}.scope_id",
                        )

                resource_fields: tuple[str, ...] = ()
                if effect_type in {"adjust_resource", "set_resource"}:
                    resource_fields = ("resource_name",)
                elif effect_type == "transfer_anchored_value":
                    resource_fields = ("from_resource", "to_resource")
                for field_name in resource_fields:
                    resource_name = str(effect.get(field_name, ""))
                    resource = resource_map.get(resource_name)
                    if resource is None:
                        add("causal_event.effect_missing_resource", f"Unknown world-effect resource: {resource_name}", f"{effect_path}.{field_name}")
                    elif effect_type == "transfer_anchored_value" and resource.get("kind") != "asset":
                        add("causal_event.effect_invalid_resource_kind", "Anchored transfers require asset resources", f"{effect_path}.{field_name}")
                if effect_type == "create_ongoing_event":
                    for delta_index, delta in enumerate(effect.get("expected_result", {}).get("inventory_deltas", [])):
                        resource_name = str(delta.get("resource", ""))
                        resource = resource_map.get(resource_name)
                        delta_path = f"{effect_path}.expected_result.inventory_deltas[{delta_index}].resource"
                        if resource is None:
                            add("causal_event.effect_missing_resource", f"Unknown ongoing-result resource: {resource_name}", delta_path)
                        elif resource.get("kind") != "asset":
                            add("causal_event.effect_invalid_resource_kind", "Ongoing inventory deltas require asset resources", delta_path)

        for duplicate in self._duplicates([item for item in content["random_events"] if isinstance(item, dict)], "template_id"):
            add("random_events.duplicate", f"Duplicate template_id: {duplicate}", "random_events")

        reserved_payload_fields = {"random_event_template_id", "occurrence"} | set(CAUSAL_METADATA_FIELDS)
        for index, template in enumerate(content["random_events"]):
            path = f"random_events[{index}]"
            if not isinstance(template, dict):
                add("random_event.invalid_template", "Random event template must be an object", path)
                continue
            template_id = template.get("template_id")
            event_type = template.get("event_type")
            if not isinstance(template_id, str) or not RANDOM_EVENT_ID_PATTERN.fullmatch(template_id.strip()):
                add(
                    "random_event.invalid_template_id",
                    "template_id must use 1-64 letters, numbers, dot, underscore, or hyphen",
                    f"{path}.template_id",
                )
            if not isinstance(event_type, str) or not RANDOM_EVENT_ID_PATTERN.fullmatch(event_type.strip()):
                add(
                    "random_event.invalid_type",
                    "event_type must use 1-64 letters, numbers, dot, underscore, or hyphen",
                    f"{path}.event_type",
                )
            targets = template.get("target_agents", [])
            if not isinstance(targets, list) or not targets:
                add("random_event.invalid_targets", "target_agents must be a non-empty list", f"{path}.target_agents")
            else:
                normalized_targets = [str(target).strip() for target in targets]
                if any(not target for target in normalized_targets) or len(set(normalized_targets)) != len(normalized_targets):
                    add("random_event.invalid_targets", "target_agents must contain unique non-empty Agent ids", f"{path}.target_agents")
                for target in normalized_targets:
                    if target and target not in agent_ids:
                        add("random_event.missing_agent", f"Unknown target agent: {target}", f"{path}.target_agents")
            payload = template.get("initial_payload", {})
            if not isinstance(payload, dict):
                add("random_event.invalid_payload", "initial_payload must be an object", f"{path}.initial_payload")
            else:
                for field_name in sorted(reserved_payload_fields & set(payload)):
                    add(
                        "random_event.reserved_payload_field",
                        f"initial_payload cannot override runtime field: {field_name}",
                        f"{path}.initial_payload.{field_name}",
                    )
            severity_values: dict[str, int] = {}
            for field_name, default in (("severity_min", 1), ("severity_max", 10)):
                value = template.get(field_name, default)
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
                    add("random_event.invalid_severity", f"{field_name} must be an integer from 1 to 10", f"{path}.{field_name}")
                else:
                    severity_values[field_name] = value
            if len(severity_values) == 2 and severity_values["severity_min"] > severity_values["severity_max"]:
                add("random_event.invalid_severity_range", "severity_min cannot exceed severity_max", path)
            interval_values: dict[str, float] = {}
            for field_name, default in (("min_interval_days", 30.0), ("max_interval_days", 90.0)):
                value = template.get(field_name, default)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    add("random_event.invalid_interval", f"{field_name} must be a positive finite number", f"{path}.{field_name}")
                else:
                    interval_values[field_name] = float(value)
            if len(interval_values) == 2 and interval_values["min_interval_days"] > interval_values["max_interval_days"]:
                add("random_event.invalid_interval_range", "min_interval_days cannot exceed max_interval_days", path)
            probability = template.get("probability", 1.0)
            if (
                isinstance(probability, bool)
                or not isinstance(probability, (int, float))
                or not math.isfinite(probability)
                or not 0 <= probability <= 1
            ):
                add("random_event.invalid_probability", "probability must be a finite number from 0 to 1", f"{path}.probability")
            max_occurrences = template.get("max_occurrences", 0)
            if isinstance(max_occurrences, bool) or not isinstance(max_occurrences, int) or max_occurrences < 0:
                add("random_event.invalid_max_occurrences", "max_occurrences must be a non-negative integer", f"{path}.max_occurrences")
            enabled = template.get("enabled", True)
            if not isinstance(enabled, bool):
                add("random_event.invalid_enabled", "enabled must be a boolean", f"{path}.enabled")

        for duplicate in self._duplicates(content["initial_events"], "event_id"):
            add("initial_events.duplicate", f"Duplicate event_id: {duplicate}", "initial_events")
        for duplicate in self._duplicates(content["ongoing_events"], "event_id"):
            add("ongoing_events.duplicate", f"Duplicate event_id: {duplicate}", "ongoing_events")

        for index, event in enumerate(content["initial_events"]):
            path = f"initial_events[{index}]"
            event_id = str(event.get("event_id", "")).strip()
            if not event_id:
                add("event.missing_id", "Initial event_id is required", f"{path}.event_id")
            severity = event.get("severity", 5)
            if isinstance(severity, bool) or not isinstance(severity, int) or not 1 <= severity <= 10:
                add("event.invalid_severity", "Initial event severity must be an integer from 1 to 10", f"{path}.severity")
            targets = event.get("target_agents", [])
            if not isinstance(targets, list):
                add("event.invalid_targets", "target_agents must be a list", f"{path}.target_agents")
            else:
                for target in targets:
                    if str(target) not in agent_ids:
                        add("event.missing_agent", f"Unknown target agent: {target}", f"{path}.target_agents")
            payload = event.get("initial_payload", {})
            if not isinstance(payload, dict):
                add("event.invalid_payload", "initial_payload must be an object", f"{path}.initial_payload")
            else:
                for field_name in sorted(CAUSAL_METADATA_FIELDS & set(payload)):
                    add(
                        "event.reserved_payload_field",
                        f"initial_payload cannot override runtime field: {field_name}",
                        f"{path}.initial_payload.{field_name}",
                    )
            generated_at = event.get("generated_at", 0.0)
            if not isinstance(generated_at, (int, float)) or isinstance(generated_at, bool) or not math.isfinite(generated_at):
                add("event.invalid_generated_at", "generated_at must be finite", f"{path}.generated_at")

        for index, event in enumerate(content["ongoing_events"]):
            path = f"ongoing_events[{index}]"
            event_id = str(event.get("event_id", "")).strip()
            entity_id = str(event.get("entity_id", ""))
            if not event_id:
                add("ongoing_event.missing_id", "Ongoing event_id is required", f"{path}.event_id")
            if entity_id not in entity_ids:
                add("ongoing_event.missing_entity", f"Unknown entity: {entity_id}", f"{path}.entity_id")
            if not isinstance(event.get("expected_result", {}), dict):
                add("ongoing_event.invalid_result", "expected_result must be an object", f"{path}.expected_result")
            times: dict[str, float] = {}
            for field_name in ("start_time", "end_time"):
                value = event.get(field_name, 0.0)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                    add("ongoing_event.invalid_time", f"{field_name} must be finite", f"{path}.{field_name}")
                else:
                    times[field_name] = float(value)
            if len(times) == 2 and times["end_time"] < times["start_time"]:
                add("ongoing_event.invalid_time_range", "end_time cannot be earlier than start_time", path)

        for persona_name, persona_text in content["personas"].items():
            path = f"personas.{persona_name}"
            if not str(persona_name).strip():
                add("persona.missing_name", "Persona name cannot be empty", path)
            if not isinstance(persona_text, str) or not persona_text.strip():
                add("persona.invalid_text", "Persona text must be a non-empty string", path)

        try:
            prompt_registry = FrameworkPromptRegistry(content["prompts"])
            for prompt_name in prompt_registry.names:
                allowed = AGENT_PROMPT_VARIABLES if prompt_name == "official_template" or prompt_name in agent_ids else None
                for issue in prompt_registry.lint(prompt_name, allowed_variables=allowed):
                    add(issue.code, issue.message, f"prompts.{issue.path}", issue.severity)
        except (PromptRegistryError, TypeError, ValueError) as exc:
            add("prompt.invalid_definition", str(exc), "prompts")

        for other in self.list_packages():
            if other.manifest.id == dlc_id or other.manifest.status != "published":
                continue
            try:
                other_content = self.load_content(other.manifest.id)
            except DLCRegistryError:
                continue
            own_capability_ids = {
                str(identifier)
                for item in content["capabilities"]
                for identifier in [item.get("capability_id", ""), *(item.get("aliases", []) or [])]
            }
            other_capability_ids = {
                str(identifier)
                for item in other_content["capabilities"]
                for identifier in [item.get("capability_id", ""), *(item.get("aliases", []) or [])]
            }
            own_actor_ids = {str(item.get("actor_id", item.get("id", ""))) for item in actors}
            other_actors = effective_actor_documents(other_content["actors"], other_content["agents"])
            comparisons = (
                ("entity", entity_ids, {str(item.get("id", "")) for item in other_content["entities"]}),
                ("actor", own_actor_ids, {str(item.get("actor_id", item.get("id", ""))) for item in other_actors}),
                ("agent", agent_ids, {str(item.get("id", "")) for item in other_content["agents"]}),
                ("resource", set(resource_map), {str(item.get("name", "")) for item in other_content["resources"]}),
                ("derived_metric", derived_metric_ids, {str(item.get("metric_id", "")) for item in other_content["derived_metrics"]}),
                ("capability", own_capability_ids, other_capability_ids),
                (
                    "relationship_type",
                    {str(item.get("relationship_type_id", item.get("id", ""))) for item in content["relationship_types"]},
                    {str(item.get("relationship_type_id", item.get("id", ""))) for item in other_content["relationship_types"]},
                ),
            )
            for kind, own_ids, other_ids in comparisons:
                for conflict in sorted((own_ids & other_ids) - {""}):
                    add(
                        f"cross_package.{kind}_conflict",
                        f"{kind.title()} id {conflict!r} already exists in published DLC {other.manifest.id}",
                        kind + "s",
                    )
        return issues

    def create(self, manifest_data: dict[str, Any]) -> DLCPackage:
        manifest = DLCManifest.from_dict({
            "status": "draft",
            "enabled": False,
            "format": "bundle-v1",
            "entrypoints": {"content": "content.json", "prompts": "prompts"},
            **manifest_data,
        })
        if not DLC_ID_PATTERN.fullmatch(manifest.id):
            raise DLCRegistryError("DLC id must match ^[a-z][a-z0-9_-]{1,63}$")
        if manifest.id in self._packages:
            raise DLCRegistryError(f"DLC already exists: {manifest.id}")
        if manifest.format != "bundle-v1":
            raise DLCRegistryError("DLC Studio can only create bundle-v1 packages")
        manifest.status = "draft"
        manifest.enabled = False
        root = (self.packages_root / manifest.id).resolve()
        if not self._inside(root, self.packages_root) or root.name != manifest.id:
            raise DLCRegistryError("Unsafe DLC package directory")
        if root.exists():
            raise DLCRegistryError(f"DLC directory already exists: {root}")
        root.mkdir(parents=True)
        (root / "prompts").mkdir()
        self._atomic_write_json(root / "dlc.json", manifest.to_dict())
        self._atomic_write_json(root / "content.json", self._normalize_content({}))
        self.discover()
        return self.get(manifest.id)

    def save_manifest(self, dlc_id: str, data: dict[str, Any]) -> DLCPackage:
        package = self.get(dlc_id)
        merged = package.manifest.to_dict()
        merged.update(data)
        if merged.get("id") != dlc_id:
            raise DLCRegistryError("Changing a DLC id is not supported")
        manifest = DLCManifest.from_dict(merged)
        for relative in manifest.entrypoints.values():
            probe = DLCPackage(manifest, package.root, package.manifest_path)
            self._safe_entrypoint(probe, relative)
        self._atomic_write_json(package.manifest_path, manifest.to_dict())
        self.discover()
        return self.get(dlc_id)

    def save_content(self, dlc_id: str, content: dict[str, Any]) -> dict[str, Any]:
        package = self.get(dlc_id)
        if package.manifest.format != "bundle-v1":
            raise DLCRegistryError("Legacy DLC content is read-only; migrate it to bundle-v1 first")
        normalized = self._normalize_content(content)
        path = self._safe_entrypoint(
            package, package.manifest.entrypoints.get("content", "content.json")
        )
        self._atomic_write_json(path, normalized)
        return normalized

    def plan_content_migration(
        self, dlc_id: str, target_schema_version: str = CURRENT_CONTENT_SCHEMA_VERSION,
    ) -> dict[str, Any]:
        package = self.get(dlc_id)
        if package.manifest.format != "bundle-v1":
            raise DLCRegistryError("Legacy DLC packages must first be copied into a bundle-v1 package")
        path = self._safe_entrypoint(
            package, package.manifest.entrypoints.get("content", "content.json")
        )
        raw = self._read_json(path)
        source = str(raw.get("schema_version") or "0.9")
        target = str(target_schema_version)
        if target != CURRENT_CONTENT_SCHEMA_VERSION:
            raise DLCRegistryError(
                f"Unsupported target content schema {target!r}; latest is {CURRENT_CONTENT_SCHEMA_VERSION}"
            )
        migrated = copy.deepcopy(raw)
        steps: list[dict[str, str]] = []
        warnings: list[str] = []
        if source == target:
            migrated = self._normalize_content(migrated)
        elif source == "0.9" and target == "1.0":
            if "agents" not in migrated and isinstance(migrated.get("officials"), list):
                migrated["agents"] = migrated.pop("officials")
                steps.append({"id": "officials-to-agents", "description": "Rename officials to agents"})
            for resource in migrated.get("resources", []):
                if isinstance(resource, dict) and "kind" not in resource and "type" in resource:
                    legacy_type = str(resource.pop("type"))
                    resource["kind"] = {
                        "item": "asset", "asset": "asset", "value": "metric",
                        "metric": "metric", "parameter": "parameter",
                    }.get(legacy_type, legacy_type)
            if migrated.get("resources"):
                steps.append({"id": "resource-kind", "description": "Normalize resource type to kind"})
            for item in migrated.get("inventory", []):
                if not isinstance(item, dict) or "item_name" in item:
                    continue
                legacy_name = item.pop("resource_name", item.pop("name", None))
                if legacy_name is not None:
                    item["item_name"] = legacy_name
            if migrated.get("inventory"):
                steps.append({"id": "inventory-resource-name", "description": "Normalize inventory item_name"})
            migrated["schema_version"] = "1.0"
            steps.append({"id": "schema-version", "description": "Set content schema_version to 1.0"})
            migrated = self._normalize_content(migrated)
        else:
            raise DLCRegistryError(f"No content migration path from {source!r} to {target!r}")
        if not steps and source == target:
            warnings.append("Content already uses the latest schema")
        return {
            "dlc_id": dlc_id,
            "source_schema_version": source,
            "target_schema_version": target,
            "changed": migrated != raw,
            "steps": steps,
            "warnings": warnings,
            "content": migrated,
        }

    def migrate_content(
        self,
        dlc_id: str,
        target_schema_version: str = CURRENT_CONTENT_SCHEMA_VERSION,
        *,
        expected_source_schema_version: str | None = None,
    ) -> dict[str, Any]:
        report = self.plan_content_migration(dlc_id, target_schema_version)
        if (
            expected_source_schema_version is not None
            and report["source_schema_version"] != expected_source_schema_version
        ):
            raise DLCRegistryError(
                "Content schema changed since preview: expected "
                f"{expected_source_schema_version}, found {report['source_schema_version']}"
            )
        if report["changed"]:
            report["content"] = self.save_content(dlc_id, report["content"])
        return report

    def _assert_dependency_lifecycle(self, package: DLCPackage, *, require_enabled: bool) -> None:
        dependencies = package.manifest.dependencies
        if not isinstance(dependencies, list):
            raise DLCRegistryError("DLC dependencies must be a list")
        for dependency in dependencies:
            target = self.get(dependency)
            constraint = self._dependency_constraint(package.manifest, dependency)
            if not version_satisfies(target.manifest.version, constraint):
                raise DLCRegistryError(
                    f"DLC {package.manifest.id} requires {dependency} {constraint}, "
                    f"installed version is {target.manifest.version}"
                )
            if target.manifest.status != "published":
                raise DLCRegistryError(f"Dependency {dependency} must be published first")
            if require_enabled and not target.manifest.enabled:
                raise DLCRegistryError(f"Dependency {dependency} must be enabled first")

    def publish(self, dlc_id: str) -> DLCPackage:
        issues = self.validate(dlc_id)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            summary = "; ".join(issue.message for issue in errors[:5])
            raise DLCRegistryError(f"DLC validation failed: {summary}")
        package = self.get(dlc_id)
        self._assert_dependency_lifecycle(package, require_enabled=False)
        return self.save_manifest(dlc_id, {"status": "published", "enabled": package.manifest.enabled})

    def set_enabled(self, dlc_id: str, enabled: bool) -> DLCPackage:
        package = self.get(dlc_id)
        if enabled and package.manifest.status != "published":
            raise DLCRegistryError("Only published DLCs can be enabled")
        if enabled:
            self._assert_dependency_lifecycle(package, require_enabled=True)
        updated = self.save_manifest(dlc_id, {"enabled": bool(enabled)})
        if enabled:
            # Defense in depth: validate the entire active graph before runtime sees it.
            self.ordered_active_packages()
        return updated

    def _keyed_update(
        self, dlc_id: str, collection: str, key: str, key_value: str,
        value: dict[str, Any] | None,
    ) -> bool:
        content = self.load_content(dlc_id)
        items = content[collection]
        index = next((i for i, item in enumerate(items) if str(item.get(key, "")) == key_value), None)
        if value is None:
            if index is None:
                return False
            del items[index]
            self.save_content(dlc_id, content)
            return True
        updated = dict(value)
        updated[key] = key_value
        if index is None:
            items.append(updated)
        else:
            items[index] = updated
        self.save_content(dlc_id, content)
        return index is None

    def upsert_entity(self, dlc_id: str, entity_id: str, value: dict[str, Any]) -> bool:
        return self._keyed_update(dlc_id, "entities", "id", entity_id, value)

    def delete_entity(self, dlc_id: str, entity_id: str) -> bool:
        return self._keyed_update(dlc_id, "entities", "id", entity_id, None)

    def upsert_agent(self, dlc_id: str, agent_id: str, value: dict[str, Any]) -> bool:
        return self._keyed_update(dlc_id, "agents", "id", agent_id, value)

    def delete_agent(self, dlc_id: str, agent_id: str) -> bool:
        return self._keyed_update(dlc_id, "agents", "id", agent_id, None)

    def upsert_resource(self, dlc_id: str, resource_name: str, value: dict[str, Any]) -> bool:
        return self._keyed_update(dlc_id, "resources", "name", resource_name, value)

    def delete_resource(self, dlc_id: str, resource_name: str) -> bool:
        return self._keyed_update(dlc_id, "resources", "name", resource_name, None)