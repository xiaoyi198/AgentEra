"""Generation-bound DLC capability registry.

DLCs select from framework-owned handlers; they never inject Python, React, or
arbitrary function names.  The same catalog is exposed to players, ordinary
Agents, and the Router so every surface speaks stable capability IDs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import json
import math
import re
import threading
from typing import Any, Iterable, Mapping, Sequence

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
HANDLERS = {"noop", "message", "world_action", "contract_action", "broadcast"}
AUDIENCES = {"player", "agent", "router"}
TARGET_KINDS = {"none", "agent", "entity"}
TARGET_MODES = {"none", "optional", "required", "multiple"}
URGENCIES = {"normal", "urgent", "critical"}
PARAMETER_TYPES = {"string", "integer", "number", "boolean"}


class CapabilityContractError(ValueError):
    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        path: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.path = path
        self.details = details or {}


@dataclass(frozen=True)
class CapabilityValidationIssue:
    code: str
    message: str
    path: str
    severity: str = "error"


def normalize_capability_document(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("DLC content field 'capabilities' must be a list")
    return [copy.deepcopy(item) if isinstance(item, dict) else item for item in value]


def _mapping(value: Any, code: str, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityContractError(code, path=path)
    return value


def _sequence(value: Any, code: str, path: str, *, limit: int = 256) -> Sequence[Any]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) > limit
    ):
        raise CapabilityContractError(code, path=path)
    return value


def _text(value: Any, code: str, path: str, *, required: bool = True, limit: int = 512) -> str:
    result = str(value or "").strip()
    if (required and not result) or len(result) > limit:
        raise CapabilityContractError(code, path=path)
    return result


def _safe_id(value: Any, code: str, path: str) -> str:
    result = _text(value, code, path, limit=64)
    if not SAFE_ID.fullmatch(result):
        raise CapabilityContractError(code, path=path)
    return result


def _unique_strings(
    value: Any,
    code: str,
    path: str,
    *,
    allowed: set[str] | None = None,
    limit: int = 64,
) -> tuple[str, ...]:
    result: list[str] = []
    for index, raw in enumerate(_sequence(value, code, path, limit=limit)):
        item = _text(raw, code, f"{path}[{index}]", limit=128)
        if allowed is not None and item not in allowed:
            raise CapabilityContractError(code, path=f"{path}[{index}]")
        if item not in result:
            result.append(item)
    return tuple(result)


def _number(value: Any, code: str, path: str) -> float:
    if isinstance(value, bool):
        raise CapabilityContractError(code, path=path)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CapabilityContractError(code, path=path) from exc
    if not math.isfinite(result):
        raise CapabilityContractError(code, path=path)
    return result


@dataclass(frozen=True)
class CapabilityParameter:
    name: str
    type: str
    display_name: str
    description: str = ""
    required: bool = False
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: Mapping[str, Any], path: str) -> "CapabilityParameter":
        name = _safe_id(raw.get("name"), "capability.parameter.invalid_name", f"{path}.name")
        parameter_type = str(raw.get("type", "string")).strip().lower()
        if parameter_type not in PARAMETER_TYPES:
            raise CapabilityContractError("capability.parameter.invalid_type", path=f"{path}.type")
        display_name = _text(
            raw.get("display_name", raw.get("label", name)),
            "capability.parameter.invalid_display_name",
            f"{path}.display_name",
            limit=128,
        )
        description = _text(
            raw.get("description", ""),
            "capability.parameter.invalid_description",
            f"{path}.description",
            required=False,
            limit=500,
        )
        minimum = raw.get("minimum")
        maximum = raw.get("maximum")
        minimum = None if minimum is None else _number(minimum, "capability.parameter.invalid_minimum", f"{path}.minimum")
        maximum = None if maximum is None else _number(maximum, "capability.parameter.invalid_maximum", f"{path}.maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise CapabilityContractError("capability.parameter.invalid_range", path=path)
        options = _unique_strings(
            raw.get("options", []),
            "capability.parameter.invalid_options",
            f"{path}.options",
            limit=64,
        )
        parameter = cls(
            name=name,
            type=parameter_type,
            display_name=display_name,
            description=description,
            required=bool(raw.get("required", False)),
            default=copy.deepcopy(raw.get("default")),
            minimum=minimum,
            maximum=maximum,
            options=options,
        )
        if parameter.default is not None:
            parameter.normalize(parameter.default, supplied=True)
        return parameter

    def normalize(self, value: Any, *, supplied: bool) -> Any:
        if not supplied:
            if self.default is not None:
                value = copy.deepcopy(self.default)
            elif self.required:
                raise CapabilityContractError(
                    "capability.parameter.required", details={"parameter": self.name}
                )
            else:
                return None
        if self.type == "string":
            if not isinstance(value, str):
                raise CapabilityContractError("capability.parameter.invalid_value", details={"parameter": self.name})
            normalized: Any = value.strip()
            if len(normalized) > 2000:
                raise CapabilityContractError("capability.parameter.invalid_value", details={"parameter": self.name})
        elif self.type == "boolean":
            if not isinstance(value, bool):
                raise CapabilityContractError("capability.parameter.invalid_value", details={"parameter": self.name})
            normalized = value
        elif self.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise CapabilityContractError("capability.parameter.invalid_value", details={"parameter": self.name})
            normalized = value
        else:
            normalized = _number(value, "capability.parameter.invalid_value", self.name)
        if self.options and str(normalized) not in self.options:
            raise CapabilityContractError("capability.parameter.invalid_option", details={"parameter": self.name})
        if self.type in {"integer", "number"}:
            numeric = float(normalized)
            if self.minimum is not None and numeric < self.minimum:
                raise CapabilityContractError("capability.parameter.below_minimum", details={"parameter": self.name})
            if self.maximum is not None and numeric > self.maximum:
                raise CapabilityContractError("capability.parameter.above_maximum", details={"parameter": self.name})
        return normalized

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["options"] = list(self.options)
        return result


@dataclass(frozen=True)
class CapabilityTarget:
    kind: str = "none"
    mode: str = "none"
    allowed_ids: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: Any, path: str) -> "CapabilityTarget":
        document = _mapping(raw or {}, "capability.target.invalid", path)
        kind = str(document.get("kind", "none")).strip().lower()
        if kind not in TARGET_KINDS:
            raise CapabilityContractError("capability.target.invalid_kind", path=f"{path}.kind")
        default_mode = "none" if kind == "none" else "optional"
        mode = str(document.get("mode", default_mode)).strip().lower()
        if mode not in TARGET_MODES:
            raise CapabilityContractError("capability.target.invalid_mode", path=f"{path}.mode")
        if kind == "none" and mode != "none":
            raise CapabilityContractError("capability.target.mode_mismatch", path=f"{path}.mode")
        if kind != "none" and mode == "none":
            raise CapabilityContractError("capability.target.mode_mismatch", path=f"{path}.mode")
        allowed_ids = _unique_strings(
            document.get("allowed_ids", []),
            "capability.target.invalid_allowed_ids",
            f"{path}.allowed_ids",
        )
        return cls(kind=kind, mode=mode, allowed_ids=allowed_ids)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "mode": self.mode, "allowed_ids": list(self.allowed_ids)}


@dataclass(frozen=True)
class CapabilityDefinition:
    capability_id: str
    display_name: str
    description: str
    handler: str
    audiences: tuple[str, ...]
    aliases: tuple[str, ...]
    target: CapabilityTarget
    parameters: tuple[CapabilityParameter, ...]
    action_id: str = ""
    default_urgency: str = "normal"
    content_required: bool = False
    content_label: str = "\u5185\u5bb9"
    content_placeholder: str = ""
    enabled: bool = True

    @classmethod
    def parse(cls, raw: Mapping[str, Any], path: str = "capability") -> "CapabilityDefinition":
        capability_id = _safe_id(raw.get("capability_id"), "capability.invalid_id", f"{path}.capability_id")
        display_name = _text(raw.get("display_name", capability_id), "capability.invalid_display_name", f"{path}.display_name", limit=128)
        description = _text(raw.get("description", ""), "capability.invalid_description", f"{path}.description", required=False, limit=1000)
        handler = str(raw.get("handler", "noop")).strip().lower()
        if handler not in HANDLERS:
            raise CapabilityContractError("capability.invalid_handler", path=f"{path}.handler")
        audiences = _unique_strings(raw.get("audiences", []), "capability.invalid_audiences", f"{path}.audiences", allowed=AUDIENCES)
        if not audiences:
            raise CapabilityContractError("capability.audiences_required", path=f"{path}.audiences")
        aliases = _unique_strings(raw.get("aliases", []), "capability.invalid_aliases", f"{path}.aliases")
        for alias in aliases:
            if not SAFE_ID.fullmatch(alias):
                raise CapabilityContractError("capability.invalid_alias", path=f"{path}.aliases")
        target = CapabilityTarget.parse(raw.get("target", {}), f"{path}.target")
        parameters_raw = _sequence(raw.get("parameters", []), "capability.parameters.invalid", f"{path}.parameters", limit=64)
        parameters: list[CapabilityParameter] = []
        parameter_names: set[str] = set()
        for index, value in enumerate(parameters_raw):
            parameter = CapabilityParameter.parse(
                _mapping(value, "capability.parameter.invalid", f"{path}.parameters[{index}]"),
                f"{path}.parameters[{index}]",
            )
            if parameter.name in parameter_names:
                raise CapabilityContractError("capability.parameter.duplicate", path=f"{path}.parameters[{index}].name")
            parameter_names.add(parameter.name)
            parameters.append(parameter)
        action_id = str(raw.get("action_id", "") or "").strip()
        if action_id and not SAFE_ID.fullmatch(action_id):
            raise CapabilityContractError("capability.invalid_action_id", path=f"{path}.action_id")
        if handler == "contract_action" and not action_id:
            raise CapabilityContractError("capability.action_id_required", path=f"{path}.action_id")
        if handler != "contract_action" and action_id:
            raise CapabilityContractError("capability.action_id_forbidden", path=f"{path}.action_id")
        urgency = str(raw.get("default_urgency", "normal")).strip().lower()
        if urgency not in URGENCIES:
            raise CapabilityContractError("capability.invalid_urgency", path=f"{path}.default_urgency")
        input_document = _mapping(raw.get("input", {}), "capability.input.invalid", f"{path}.input")
        content_required = bool(input_document.get("content_required", handler in {"message", "world_action", "broadcast"}))
        content_label = _text(input_document.get("content_label", "\u5185\u5bb9"), "capability.input.invalid_label", f"{path}.input.content_label", limit=128)
        content_placeholder = _text(input_document.get("content_placeholder", ""), "capability.input.invalid_placeholder", f"{path}.input.content_placeholder", required=False, limit=500)
        if handler == "noop" and (target.kind != "none" or content_required or parameters):
            raise CapabilityContractError("capability.noop.invalid_shape", path=path)
        if handler in {"message", "broadcast"} and target.kind != "agent":
            raise CapabilityContractError("capability.target.agent_required", path=f"{path}.target.kind")
        return cls(
            capability_id=capability_id,
            display_name=display_name,
            description=description,
            handler=handler,
            audiences=audiences,
            aliases=aliases,
            target=target,
            parameters=tuple(parameters),
            action_id=action_id,
            default_urgency=urgency,
            content_required=content_required,
            content_label=content_label,
            content_placeholder=content_placeholder,
            enabled=bool(raw.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "display_name": self.display_name,
            "description": self.description,
            "handler": self.handler,
            "audiences": list(self.audiences),
            "aliases": list(self.aliases),
            "target": self.target.to_dict(),
            "parameters": [item.to_dict() for item in self.parameters],
            "action_id": self.action_id,
            "default_urgency": self.default_urgency,
            "input": {
                "content_required": self.content_required,
                "content_label": self.content_label,
                "content_placeholder": self.content_placeholder,
            },
            "enabled": self.enabled,
        }


def validate_capability_definitions(
    definitions: Any,
    *,
    action_ids: Iterable[str] = (),
    entity_ids: Iterable[str] = (),
    agent_ids: Iterable[str] = (),
) -> list[CapabilityValidationIssue]:
    issues: list[CapabilityValidationIssue] = []
    if not isinstance(definitions, list):
        return [CapabilityValidationIssue("capabilities.invalid", "capabilities must be a list", "capabilities")]
    known_actions = {str(item) for item in action_ids}
    known_entities = {str(item) for item in entity_ids}
    known_agents = {str(item) for item in agent_ids}
    identifiers: dict[str, str] = {}
    for index, raw in enumerate(definitions):
        path = f"capabilities[{index}]"
        try:
            definition = CapabilityDefinition.parse(_mapping(raw, "capability.invalid", path), path)
        except CapabilityContractError as exc:
            issues.append(CapabilityValidationIssue(exc.code, str(exc), exc.path or path))
            continue
        for identifier in (definition.capability_id, *definition.aliases):
            owner = identifiers.get(identifier)
            if owner is not None:
                issues.append(CapabilityValidationIssue("capabilities.identifier_duplicate", f"Capability id/alias {identifier!r} is already owned by {owner}", path))
            else:
                identifiers[identifier] = definition.capability_id
        if definition.handler == "contract_action" and definition.action_id not in known_actions:
            issues.append(CapabilityValidationIssue("capability.action_not_found", f"Unknown action_id: {definition.action_id}", f"{path}.action_id"))
        known_targets = known_agents if definition.target.kind == "agent" else known_entities
        for target_id in definition.target.allowed_ids:
            if target_id not in known_targets:
                issues.append(CapabilityValidationIssue("capability.target_not_found", f"Unknown {definition.target.kind} target: {target_id}", f"{path}.target.allowed_ids"))
    return issues


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CapabilityCatalog:
    """Atomic current-generation catalog and invocation normalizer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._definitions: dict[str, CapabilityDefinition] = {}
        self._aliases: dict[str, str] = {}
        self._generation = 0
        self._fingerprint = _fingerprint([])

    @property
    def configured(self) -> bool:
        with self._lock:
            return bool(self._definitions)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def fingerprint(self) -> str:
        with self._lock:
            return self._fingerprint

    def configure(self, definitions: Iterable[Mapping[str, Any] | CapabilityDefinition], *, generation: int = 0) -> None:
        parsed: dict[str, CapabilityDefinition] = {}
        aliases: dict[str, str] = {}
        for index, raw in enumerate(definitions):
            definition = raw if isinstance(raw, CapabilityDefinition) else CapabilityDefinition.parse(_mapping(raw, "capability.invalid", f"capabilities[{index}]"), f"capabilities[{index}]")
            if definition.capability_id in parsed or definition.capability_id in aliases:
                raise CapabilityContractError("capabilities.identifier_duplicate", path=f"capabilities[{index}].capability_id")
            parsed[definition.capability_id] = definition
            for alias in definition.aliases:
                if alias in parsed or alias in aliases:
                    raise CapabilityContractError("capabilities.identifier_duplicate", path=f"capabilities[{index}].aliases")
                aliases[alias] = definition.capability_id
        canonical = [parsed[key].to_dict() for key in sorted(parsed)]
        with self._lock:
            self._definitions = parsed
            self._aliases = aliases
            self._generation = max(0, int(generation))
            self._fingerprint = _fingerprint(canonical)

    def resolve(self, capability_id_or_alias: str, *, audience: str | None = None) -> CapabilityDefinition | None:
        identifier = str(capability_id_or_alias or "").strip()
        with self._lock:
            canonical = self._aliases.get(identifier, identifier)
            definition = self._definitions.get(canonical)
            if definition is None or not definition.enabled:
                return None
            if audience and audience not in definition.audiences:
                return None
            return definition

    def definitions_for(self, audience: str | None = None) -> list[CapabilityDefinition]:
        with self._lock:
            return [
                self._definitions[key]
                for key in sorted(self._definitions)
                if self._definitions[key].enabled
                and (audience is None or audience in self._definitions[key].audiences)
            ]

    def public_state(self, *, audience: str | None = None) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "generation": self.generation,
            "fingerprint": self.fingerprint,
            "audience": audience or "all",
            "capabilities": [item.to_dict() for item in self.definitions_for(audience)],
        }

    def prepare_invocation(
        self,
        capability_id: str,
        raw: Mapping[str, Any] | None,
        *,
        audience: str,
        source_id: str = "",
    ) -> dict[str, Any]:
        document = _mapping(raw or {}, "capability.invocation.invalid", "invocation")
        expected = document.get("expected_generation")
        if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 0):
            raise CapabilityContractError("capability.invocation.invalid_generation", path="invocation.expected_generation")
        if expected is not None and expected != self.generation:
            raise CapabilityContractError(
                "capability.generation_conflict",
                details={"expected_generation": expected, "actual_generation": self.generation},
            )
        definition = self.resolve(capability_id, audience=audience)
        if definition is None:
            known = self.resolve(capability_id)
            if known is not None:
                raise CapabilityContractError("capability.audience_forbidden", details={"capability_id": capability_id, "audience": audience})
            raise CapabilityContractError("capability.not_found", details={"capability_id": capability_id})
        content = str(document.get("content", "") or "").strip()
        if len(content) > 12000:
            raise CapabilityContractError("capability.content_too_long")
        if definition.content_required and not content:
            raise CapabilityContractError("capability.content_required")
        target_id = str(document.get("target_id", "") or "").strip()
        raw_target_ids = document.get("target_ids", [])
        if not isinstance(raw_target_ids, list) or len(raw_target_ids) > 64:
            raise CapabilityContractError("capability.targets.invalid")
        target_ids = list(dict.fromkeys(str(item).strip() for item in raw_target_ids if str(item).strip()))
        if target_id and target_id not in target_ids:
            target_ids.insert(0, target_id)
        if definition.target.mode == "none" and target_ids:
            raise CapabilityContractError("capability.target.forbidden")
        if definition.target.mode == "required" and len(target_ids) != 1:
            raise CapabilityContractError("capability.target.required")
        if definition.target.mode == "optional" and len(target_ids) > 1:
            raise CapabilityContractError("capability.target.single_required")
        if definition.target.mode == "multiple" and not target_ids:
            raise CapabilityContractError("capability.targets.required")
        if definition.target.allowed_ids:
            outside = [item for item in target_ids if item not in definition.target.allowed_ids]
            if outside:
                raise CapabilityContractError("capability.target.forbidden", details={"target_ids": outside})
        supplied_parameters = document.get("parameters", {})
        if not isinstance(supplied_parameters, Mapping) or len(supplied_parameters) > 64:
            raise CapabilityContractError("capability.parameters.invalid")
        known_parameters = {item.name: item for item in definition.parameters}
        if definition.handler != "contract_action":
            unknown = sorted(str(key) for key in supplied_parameters if key not in known_parameters)
            if unknown:
                raise CapabilityContractError("capability.parameter.unknown", details={"parameters": unknown})
        parameters: dict[str, Any] = {}
        if definition.handler == "contract_action":
            parameters.update(copy.deepcopy(dict(supplied_parameters)))
        else:
            for name, parameter in known_parameters.items():
                value = parameter.normalize(supplied_parameters.get(name), supplied=name in supplied_parameters)
                if value is not None:
                    parameters[name] = value
        urgency = str(document.get("urgency", definition.default_urgency) or definition.default_urgency).strip().lower()
        if urgency not in URGENCIES:
            raise CapabilityContractError("capability.invalid_urgency")
        invocation_id = str(document.get("invocation_id", "") or "").strip()
        if invocation_id and (len(invocation_id) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", invocation_id)):
            raise CapabilityContractError("capability.invocation.invalid_id")
        return {
            "capability_id": definition.capability_id,
            "handler": definition.handler,
            "action_id": definition.action_id,
            "generation": self.generation,
            "expected_generation": expected,
            "invocation_id": invocation_id,
            "audience": audience,
            "source_id": str(source_id or document.get("source_id", "") or "").strip(),
            "content": content,
            "target_id": target_ids[0] if target_ids else "",
            "target_ids": target_ids,
            "target_kind": definition.target.kind,
            "parameters": parameters,
            "urgency": urgency,
            "original_intent": str(document.get("original_intent", "") or "").strip()[:12000],
            "is_distorting": bool(document.get("is_distorting", False)),
        }

    def build_tool(self, *, audience: str, name: str, description: str) -> dict[str, Any] | None:
        definitions = self.definitions_for(audience)
        if not definitions:
            return None
        summary = "; ".join(
            f"{item.capability_id}={item.display_name}({item.handler})"
            for item in definitions
        )[:3000]
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{description}\n\u53ef\u7528\u80fd\u529b\uff1a{summary}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "capability_id": {
                            "type": "string",
                            "enum": [item.capability_id for item in definitions],
                            "description": "\u8981\u8c03\u7528\u7684 DLC \u80fd\u529b ID\uff0c\u5fc5\u987b\u4ece\u5f53\u524d\u679a\u4e3e\u4e2d\u9009\u62e9\u3002",
                        },
                        "content": {"type": "string", "description": "\u80fd\u529b\u4e3b\u8981\u5185\u5bb9\uff0c\u4f8b\u5982\u6d88\u606f\u3001\u547d\u4ee4\u6216\u4e16\u754c\u884c\u52a8\u8bf4\u660e\u3002"},
                        "target_id": {"type": "string", "description": "\u5355\u4e2a\u76ee\u6807 ID\u3002"},
                        "target_ids": {"type": "array", "items": {"type": "string"}, "description": "\u4e00\u4e2a\u6216\u591a\u4e2a\u76ee\u6807 ID \u6570\u7ec4\u3002"},
                        "parameters": {"type": "object", "description": "\u6309\u80fd\u529b\u5b9a\u4e49\u586b\u5199\u7684\u7ed3\u6784\u5316\u53c2\u6570\uff1b\u5951\u7ea6 Action \u53ef\u900f\u4f20\u53c2\u6570\u3002"},
                        "urgency": {"type": "string", "enum": ["normal", "urgent", "critical"]},
                        "original_intent": {"type": "string", "description": "\u53ef\u9009\u7684\u539f\u59cb\u610f\u56fe\uff0c\u7528\u4e8e\u5ba1\u8ba1\u548c\u8def\u7531\u89e3\u91ca\u3002"},
                        "is_distorting": {"type": "boolean", "description": "\u8be5\u8def\u7531\u662f\u5426\u6539\u53d8\u4e86\u539f\u59cb\u610f\u56fe\u3002"},
                    },
                    "required": ["capability_id"],
                },
            },
        }
