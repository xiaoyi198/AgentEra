"""DLC-authored relationship type definitions and relation-edge validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
import math
import re
from typing import Any, Mapping, Sequence

RELATIONSHIP_ID_PATTERN = re.compile(r"^[^\s/\\\x00-\x1f]{1,128}$")
RELATIONSHIP_DIRECTIONS = {"directed", "undirected"}
RELATIONSHIP_VISIBILITIES = {"public", "limited", "private", "secret"}


class RelationshipContractError(ValueError):
    """Stable, path-aware relationship contract failure."""

    def __init__(self, code: str, message: str | None = None, *, path: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class RelationshipValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 512) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise RelationshipContractError(code, path=path)
    return result


def _identifier(value: Any, code: str, path: str) -> str:
    result = _text(value, code, path, limit=128)
    if not RELATIONSHIP_ID_PATTERN.fullmatch(result):
        raise RelationshipContractError(code, path=path)
    return result


def _number(value: Any, code: str, path: str) -> float:
    if isinstance(value, bool):
        raise RelationshipContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RelationshipContractError(code, path=path) from exc
    if not math.isfinite(result):
        raise RelationshipContractError(code, path=path)
    return result


def _optional_number(value: Any, code: str, path: str) -> float | None:
    return None if value is None or value == "" else _number(value, code, path)


def _safe_json(value: Any, code: str, path: str, depth: int = 0) -> Any:
    if depth > 4:
        raise RelationshipContractError(code, path=path)
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise RelationshipContractError(code, path=path)
        return {
            _identifier(key, code, f"{path}.{key}"): _safe_json(item, code, f"{path}.{key}", depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 64:
            raise RelationshipContractError(code, path=path)
        return [_safe_json(item, code, f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]
    if isinstance(value, str):
        if len(value) > 1024:
            raise RelationshipContractError(code, path=path)
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise RelationshipContractError(code, path=path)
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise RelationshipContractError(code, path=path)
    return copy.deepcopy(value)


def normalize_relationship_type_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("DLC content field 'relationship_types' must be a list")
    return [copy.deepcopy(item) if isinstance(item, dict) else item for item in value]


@dataclass(frozen=True)
class RelationshipDimensionDefinition:
    dimension_id: str
    display_name: str
    minimum: float = -100.0
    maximum: float = 100.0
    default: float = 0.0
    description: str = ""

    @classmethod
    def parse(cls, raw: Any, path: str) -> "RelationshipDimensionDefinition":
        if not isinstance(raw, Mapping):
            raise RelationshipContractError("relationship.dimension.invalid", path=path)
        dimension_id = _identifier(raw.get("dimension_id", raw.get("id")), "relationship.dimension.invalid_id", f"{path}.dimension_id")
        minimum = _number(raw.get("minimum", -100), "relationship.dimension.invalid_minimum", f"{path}.minimum")
        maximum = _number(raw.get("maximum", 100), "relationship.dimension.invalid_maximum", f"{path}.maximum")
        if maximum < minimum:
            raise RelationshipContractError("relationship.dimension.invalid_range", path=path)
        default = _number(raw.get("default", 0), "relationship.dimension.invalid_default", f"{path}.default")
        if not minimum <= default <= maximum:
            raise RelationshipContractError("relationship.dimension.default_out_of_range", path=f"{path}.default")
        return cls(
            dimension_id=dimension_id,
            display_name=_text(raw.get("display_name", dimension_id), "relationship.dimension.invalid_display_name", f"{path}.display_name", limit=128),
            minimum=minimum,
            maximum=maximum,
            default=default,
            description=_text(raw.get("description", ""), "relationship.dimension.invalid_description", f"{path}.description", required=False, limit=1000),
        )


@dataclass(frozen=True)
class RelationshipTypeDefinition:
    relationship_type_id: str
    display_name: str
    description: str = ""
    direction: str = "directed"
    minimum_strength: float = -100.0
    maximum_strength: float = 100.0
    default_strength: float = 0.0
    default_visibility: str = "public"
    allow_expiry: bool = False
    dimensions: tuple[RelationshipDimensionDefinition, ...] = ()
    style: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any, path: str = "relationship_types") -> "RelationshipTypeDefinition":
        if not isinstance(raw, Mapping):
            raise RelationshipContractError("relationship_type.invalid", path=path)
        type_id = _identifier(raw.get("relationship_type_id", raw.get("id")), "relationship_type.invalid_id", f"{path}.relationship_type_id")
        direction = str(raw.get("direction", "directed")).strip().lower()
        if direction not in RELATIONSHIP_DIRECTIONS:
            raise RelationshipContractError("relationship_type.invalid_direction", path=f"{path}.direction")
        minimum = _number(raw.get("minimum_strength", -100), "relationship_type.invalid_minimum_strength", f"{path}.minimum_strength")
        maximum = _number(raw.get("maximum_strength", 100), "relationship_type.invalid_maximum_strength", f"{path}.maximum_strength")
        if maximum < minimum:
            raise RelationshipContractError("relationship_type.invalid_strength_range", path=path)
        default = _number(raw.get("default_strength", 0), "relationship_type.invalid_default_strength", f"{path}.default_strength")
        if not minimum <= default <= maximum:
            raise RelationshipContractError("relationship_type.default_strength_out_of_range", path=f"{path}.default_strength")
        visibility = str(raw.get("default_visibility", "public")).strip().lower()
        if visibility not in RELATIONSHIP_VISIBILITIES:
            raise RelationshipContractError("relationship_type.invalid_visibility", path=f"{path}.default_visibility")
        raw_dimensions = raw.get("dimensions", [])
        if not isinstance(raw_dimensions, Sequence) or isinstance(raw_dimensions, (str, bytes, bytearray)) or len(raw_dimensions) > 32:
            raise RelationshipContractError("relationship_type.invalid_dimensions", path=f"{path}.dimensions")
        dimensions = tuple(
            RelationshipDimensionDefinition.parse(item, f"{path}.dimensions[{index}]")
            for index, item in enumerate(raw_dimensions)
        )
        ids = [item.dimension_id for item in dimensions]
        if len(ids) != len(set(ids)):
            raise RelationshipContractError("relationship_type.duplicate_dimension", path=f"{path}.dimensions")
        style = raw.get("style", {})
        metadata = raw.get("metadata", {})
        if not isinstance(style, Mapping):
            raise RelationshipContractError("relationship_type.invalid_style", path=f"{path}.style")
        if not isinstance(metadata, Mapping):
            raise RelationshipContractError("relationship_type.invalid_metadata", path=f"{path}.metadata")
        return cls(
            relationship_type_id=type_id,
            display_name=_text(raw.get("display_name", type_id), "relationship_type.invalid_display_name", f"{path}.display_name", limit=128),
            description=_text(raw.get("description", ""), "relationship_type.invalid_description", f"{path}.description", required=False, limit=2000),
            direction=direction,
            minimum_strength=minimum,
            maximum_strength=maximum,
            default_strength=default,
            default_visibility=visibility,
            allow_expiry=bool(raw.get("allow_expiry", False)),
            dimensions=dimensions,
            style=dict(_safe_json(style, "relationship_type.invalid_style", f"{path}.style")),
            metadata=dict(_safe_json(metadata, "relationship_type.invalid_metadata", f"{path}.metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


LEGACY_RELATIONSHIP_TYPE_DOCUMENTS: tuple[dict[str, Any], ...] = (
    {
        "relationship_type_id": "subordinate",
        "display_name": "上下级",
        "description": "有方向的管理、指挥或隶属关系。",
        "direction": "directed",
        "minimum_strength": 0,
        "maximum_strength": 100,
        "default_strength": 100,
        "default_visibility": "public",
        "allow_expiry": False,
        "style": {"label": "属", "color": "#c9a84c", "dashed": False, "marker": "closed"},
    },
    {
        "relationship_type_id": "affiliated",
        "display_name": "附属",
        "description": "有方向的归属、挂靠或附属关系。",
        "direction": "directed",
        "minimum_strength": 0,
        "maximum_strength": 100,
        "default_strength": 50,
        "default_visibility": "public",
        "allow_expiry": False,
        "style": {"label": "附", "color": "#4a6fa5", "dashed": True, "marker": "arrow"},
    },
)
LEGACY_RELATIONSHIP_TYPES = frozenset(item["relationship_type_id"] for item in LEGACY_RELATIONSHIP_TYPE_DOCUMENTS)


def effective_relationship_type_documents(documents: Any) -> list[dict[str, Any]]:
    authored = normalize_relationship_type_document(documents)
    by_id = {item["relationship_type_id"]: copy.deepcopy(item) for item in LEGACY_RELATIONSHIP_TYPE_DOCUMENTS}
    order = [item["relationship_type_id"] for item in LEGACY_RELATIONSHIP_TYPE_DOCUMENTS]
    seen: set[str] = set()
    for index, raw in enumerate(authored):
        parsed = RelationshipTypeDefinition.parse(raw, f"relationship_types[{index}]")
        if parsed.relationship_type_id in seen:
            raise RelationshipContractError("relationship_type.duplicate_id", path=f"relationship_types[{index}].relationship_type_id")
        seen.add(parsed.relationship_type_id)
        if parsed.relationship_type_id not in by_id:
            order.append(parsed.relationship_type_id)
        by_id[parsed.relationship_type_id] = parsed.to_dict()
    return [copy.deepcopy(by_id[type_id]) for type_id in order]


class RelationshipCatalog:
    """Immutable effective type catalog used to validate and normalize edges."""

    def __init__(self, documents: Any = None) -> None:
        authored = normalize_relationship_type_document(documents)
        definitions = effective_relationship_type_documents(authored)
        self._authored_ids = frozenset(
            RelationshipTypeDefinition.parse(raw, f"relationship_types[{index}]").relationship_type_id
            for index, raw in enumerate(authored)
        )
        self._definitions = {
            item.relationship_type_id: item
            for index, raw in enumerate(definitions)
            for item in [RelationshipTypeDefinition.parse(raw, f"relationship_types[{index}]")]
        }

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._definitions)

    def get(self, relationship_type_id: str) -> RelationshipTypeDefinition | None:
        return self._definitions.get(str(relationship_type_id))

    def definitions(self, *, include_legacy: bool = True) -> list[dict[str, Any]]:
        return [
            definition.to_dict()
            for type_id, definition in self._definitions.items()
            if include_legacy or type_id not in LEGACY_RELATIONSHIP_TYPES or type_id in self._authored_ids
        ]

    def normalize_relation(self, raw: Any, path: str = "relation") -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise RelationshipContractError("relation.invalid", path=path)
        parent_id = _identifier(raw.get("parent_id"), "relation.invalid_parent", f"{path}.parent_id")
        child_id = _identifier(raw.get("child_id"), "relation.invalid_child", f"{path}.child_id")
        rel_type = _identifier(raw.get("rel_type"), "relation.invalid_type", f"{path}.rel_type")
        definition = self.get(rel_type)
        if definition is None:
            raise RelationshipContractError("relation.unknown_type", f"Unknown relationship type: {rel_type}", path=f"{path}.rel_type")
        if parent_id == child_id:
            raise RelationshipContractError("relation.self_reference", path=path)
        if definition.direction == "undirected" and child_id < parent_id:
            parent_id, child_id = child_id, parent_id
        strength = _number(raw.get("strength", definition.default_strength), "relation.invalid_strength", f"{path}.strength")
        if not definition.minimum_strength <= strength <= definition.maximum_strength:
            raise RelationshipContractError("relation.strength_out_of_range", path=f"{path}.strength")
        visibility = str(raw.get("visibility", definition.default_visibility)).strip().lower()
        if visibility not in RELATIONSHIP_VISIBILITIES:
            raise RelationshipContractError("relation.invalid_visibility", path=f"{path}.visibility")
        starts_at = _number(raw.get("starts_at", 0), "relation.invalid_starts_at", f"{path}.starts_at")
        expires_at = _optional_number(raw.get("expires_at"), "relation.invalid_expires_at", f"{path}.expires_at")
        if expires_at is not None:
            if not definition.allow_expiry:
                raise RelationshipContractError("relation.expiry_not_allowed", path=f"{path}.expires_at")
            if expires_at <= starts_at:
                raise RelationshipContractError("relation.invalid_time_range", path=f"{path}.expires_at")
        raw_values = raw.get("values", raw.get("dimension_values", {}))
        if not isinstance(raw_values, Mapping):
            raise RelationshipContractError("relation.invalid_values", path=f"{path}.values")
        dimension_map = {item.dimension_id: item for item in definition.dimensions}
        unknown = sorted(set(map(str, raw_values)) - set(dimension_map))
        if unknown:
            raise RelationshipContractError("relation.unknown_dimension", f"Unknown relationship dimension: {unknown[0]}", path=f"{path}.values.{unknown[0]}")
        values: dict[str, float] = {}
        for dimension_id, dimension in dimension_map.items():
            value = _number(raw_values.get(dimension_id, dimension.default), "relation.invalid_dimension_value", f"{path}.values.{dimension_id}")
            if not dimension.minimum <= value <= dimension.maximum:
                raise RelationshipContractError("relation.dimension_out_of_range", path=f"{path}.values.{dimension_id}")
            values[dimension_id] = value
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise RelationshipContractError("relation.invalid_metadata", path=f"{path}.metadata")
        return {
            "parent_id": parent_id,
            "child_id": child_id,
            "rel_type": rel_type,
            "strength": strength,
            "visibility": visibility,
            "starts_at": starts_at,
            "expires_at": expires_at,
            "values": values,
            "note": _text(raw.get("note", ""), "relation.invalid_note", f"{path}.note", required=False, limit=2000),
            "metadata": dict(_safe_json(metadata, "relation.invalid_metadata", f"{path}.metadata")),
        }

    @staticmethod
    def is_active(relation: Mapping[str, Any], sim_time: float = 0.0) -> bool:
        now = _number(sim_time, "relation.invalid_sim_time", "sim_time")
        starts_at = _number(relation.get("starts_at", 0), "relation.invalid_starts_at", "relation.starts_at")
        expires_at = _optional_number(relation.get("expires_at"), "relation.invalid_expires_at", "relation.expires_at")
        return now >= starts_at and (expires_at is None or now < expires_at)

    def public_state(self, *, generation: int = 0) -> dict[str, Any]:
        definitions = self.definitions()
        return {
            "configured": bool(self._authored_ids),
            "generation": int(generation),
            "relationship_type_count": len(definitions),
            "relationship_types": definitions,
        }


def validate_relationship_definitions(
    documents: Any,
    relations: Any = None,
    *,
    entity_ids: set[str] | None = None,
) -> list[RelationshipValidationIssue]:
    issues: list[RelationshipValidationIssue] = []
    try:
        catalog = RelationshipCatalog(documents)
    except (RelationshipContractError, TypeError) as exc:
        code = getattr(exc, "code", "relationship_type.invalid")
        path = getattr(exc, "path", "relationship_types")
        issues.append(RelationshipValidationIssue(code, str(exc), path))
        return issues
    if relations is None:
        return issues
    if not isinstance(relations, list):
        return [RelationshipValidationIssue("relation.invalid_list", "relations must be a list", "relations")]
    known = set(entity_ids or set())
    for index, raw in enumerate(relations):
        path = f"relations[{index}]"
        raw_parent = str(raw.get("parent_id", "")) if isinstance(raw, Mapping) else ""
        raw_child = str(raw.get("child_id", "")) if isinstance(raw, Mapping) else ""
        if raw_parent and raw_parent == raw_child:
            issues.append(RelationshipValidationIssue("relation.self_reference", "A relation cannot point to itself", path))
        if entity_ids is not None:
            if raw_parent not in known:
                issues.append(RelationshipValidationIssue("relation.missing_parent", f"Unknown parent entity: {raw_parent}", f"{path}.parent_id"))
            if raw_child not in known:
                issues.append(RelationshipValidationIssue("relation.missing_child", f"Unknown child entity: {raw_child}", f"{path}.child_id"))
        try:
            catalog.normalize_relation(raw, path)
        except RelationshipContractError as exc:
            if exc.code != "relation.self_reference":
                issues.append(RelationshipValidationIssue(exc.code, str(exc), exc.path or path))
    return issues
