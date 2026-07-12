"""Versioned, composable prompt definitions with deterministic lint/render reports."""

from __future__ import annotations

import hashlib
import json
import math
import string
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


class PromptRegistryError(ValueError):
    """Raised when a prompt definition cannot be parsed or rendered safely."""


@dataclass(frozen=True)
class PromptLintIssue:
    code: str
    message: str
    path: str = ""
    severity: str = "error"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PromptBlock:
    name: str
    template: str
    priority: int = 0
    required_variables: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class PromptDefinition:
    name: str
    version: str = "1.0"
    blocks: tuple[PromptBlock, ...] = ()
    max_chars: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptRenderResult:
    name: str
    version: str
    text: str
    content_hash: str
    used_variables: tuple[str, ...]
    missing_variables: tuple[str, ...]
    unknown_variables: tuple[str, ...]
    char_count: int
    estimated_tokens: int
    issues: tuple[PromptLintIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = [item.to_dict() for item in self.issues]
        return data


class PromptRegistry:
    """Store prompt definitions and render ordered blocks with reproducible reports."""

    def __init__(self, prompts: Mapping[str, Any] | None = None) -> None:
        self._definitions: dict[str, PromptDefinition] = {}
        for name, source in dict(prompts or {}).items():
            self.register(str(name), source)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def has(self, name: str) -> bool:
        return name in self._definitions

    def definition(self, name: str) -> PromptDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise PromptRegistryError(f"Unknown prompt: {name}") from exc

    def register(self, name: str, source: Any) -> PromptDefinition:
        if not name.strip():
            raise PromptRegistryError("Prompt name cannot be empty")
        definition = self._coerce_definition(name, source)
        self._definitions[name] = definition
        return definition

    def get(self, name: str, default: str = "") -> str:
        definition = self._definitions.get(name)
        if definition is None:
            return default
        return "\n\n".join(
            block.template for block in self._ordered_blocks(definition) if block.enabled
        )

    def lint(
        self,
        name: str,
        *,
        values: Mapping[str, Any] | None = None,
        allowed_variables: Iterable[str] | None = None,
    ) -> list[PromptLintIssue]:
        definition = self.definition(name)
        supplied = set((values or {}).keys())
        allowed = set(allowed_variables) if allowed_variables is not None else None
        issues: list[PromptLintIssue] = []
        referenced: set[str] = set()

        if not definition.blocks:
            issues.append(PromptLintIssue("prompt.empty", "Prompt has no blocks", name))
            return issues

        block_names: set[str] = set()
        for index, block in enumerate(self._ordered_blocks(definition)):
            path = f"{name}.blocks[{index}]"
            if block.name in block_names:
                issues.append(PromptLintIssue(
                    "prompt.duplicate_block", f"Duplicate block name: {block.name}", path
                ))
            block_names.add(block.name)
            if not block.template.strip():
                issues.append(PromptLintIssue("prompt.empty_block", "Prompt block is empty", path))
                continue
            try:
                fields = self._template_variables(block.template)
            except ValueError as exc:
                issues.append(PromptLintIssue("prompt.invalid_template", str(exc), path))
                continue
            referenced.update(fields)
            for required in block.required_variables:
                if required not in fields:
                    issues.append(PromptLintIssue(
                        "prompt.required_not_referenced",
                        f"Required variable is not referenced by this block: {required}",
                        path,
                    ))

        required = set().union(*(set(block.required_variables) for block in definition.blocks))
        required.update(referenced)
        missing = sorted(required - supplied) if values is not None else []
        for variable in missing:
            issues.append(PromptLintIssue(
                "prompt.missing_variable", f"Missing render variable: {variable}", name
            ))
        if allowed is not None:
            for variable in sorted(referenced - allowed):
                issues.append(PromptLintIssue(
                    "prompt.unknown_variable",
                    f"Template references unsupported variable: {variable}",
                    name,
                ))
        if values is not None:
            for variable in sorted(supplied - referenced):
                issues.append(PromptLintIssue(
                    "prompt.unused_variable",
                    f"Render variable is not used: {variable}",
                    name,
                    severity="warning",
                ))
        return issues

    def render(
        self,
        name: str,
        values: Mapping[str, Any] | None = None,
        *,
        strict: bool = True,
        max_chars: int | None = None,
    ) -> PromptRenderResult:
        definition = self.definition(name)
        variables = dict(values or {})
        issues = self.lint(name, values=variables)
        errors = [item for item in issues if item.severity == "error"]
        if strict and errors:
            raise PromptRegistryError(
                f"Cannot render prompt {name!r}: " + "; ".join(item.message for item in errors)
            )

        rendered: list[str] = []
        referenced: set[str] = set()
        for block in self._ordered_blocks(definition):
            if not block.enabled:
                continue
            try:
                fields = self._template_variables(block.template)
                referenced.update(fields)
                rendered.append(block.template.format_map(_SafeFormatMap(variables, strict=strict)))
            except (KeyError, ValueError, AttributeError, IndexError) as exc:
                if strict:
                    raise PromptRegistryError(
                        f"Cannot render prompt {name!r}, block {block.name!r}: {exc}"
                    ) from exc
                rendered.append(block.template)

        text = "\n\n".join(part.strip() for part in rendered if part.strip())
        budget = max_chars if max_chars is not None else definition.max_chars
        if budget is not None and len(text) > budget:
            issue = PromptLintIssue(
                "prompt.budget_exceeded",
                f"Rendered prompt has {len(text)} chars, exceeding budget {budget}",
                name,
            )
            issues.append(issue)
            if strict:
                raise PromptRegistryError(issue.message)

        missing = tuple(sorted(referenced - set(variables)))
        unknown = tuple(sorted(set(variables) - referenced))
        digest_source = json.dumps(
            {"name": name, "version": definition.version, "text": text},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return PromptRenderResult(
            name=name,
            version=definition.version,
            text=text,
            content_hash=hashlib.sha256(digest_source).hexdigest(),
            used_variables=tuple(sorted(referenced & set(variables))),
            missing_variables=missing,
            unknown_variables=unknown,
            char_count=len(text),
            estimated_tokens=math.ceil(len(text) / 2),
            issues=tuple(issues),
        )

    def export_definitions(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in self.names:
            definition = self.definition(name)
            result[name] = {
                "version": definition.version,
                "max_chars": definition.max_chars,
                "metadata": dict(definition.metadata),
                "blocks": [asdict(block) for block in definition.blocks],
            }
        return result

    @staticmethod
    def _ordered_blocks(definition: PromptDefinition) -> list[PromptBlock]:
        indexed = list(enumerate(definition.blocks))
        indexed.sort(key=lambda item: (item[1].priority, item[0]))
        return [block for _, block in indexed]

    @staticmethod
    def _template_variables(template: str) -> set[str]:
        fields: set[str] = set()
        formatter = string.Formatter()
        parsed = formatter.parse(template)
        for _, field_name, _, _ in parsed:
            if field_name:
                root = field_name.split(".", 1)[0].split("[", 1)[0]
                if root:
                    fields.add(root)
        return fields

    @classmethod
    def _coerce_definition(cls, name: str, source: Any) -> PromptDefinition:
        if isinstance(source, str):
            return PromptDefinition(
                name=name,
                blocks=(PromptBlock(name="main", template=source),),
            )
        if not isinstance(source, Mapping):
            raise PromptRegistryError(f"Prompt {name!r} must be a string or object")

        version = str(source.get("version", "1.0"))
        max_chars_raw = source.get("max_chars")
        max_chars = None if max_chars_raw in (None, "") else int(max_chars_raw)
        if max_chars is not None and max_chars <= 0:
            raise PromptRegistryError(f"Prompt {name!r} max_chars must be positive")
        metadata = dict(source.get("metadata", {}))
        raw_blocks = source.get("blocks")
        if raw_blocks is None:
            template = source.get("template", source.get("content"))
            if not isinstance(template, str):
                raise PromptRegistryError(
                    f"Prompt {name!r} requires template/content or blocks"
                )
            raw_blocks = [{"name": "main", "template": template}]
        if not isinstance(raw_blocks, list):
            raise PromptRegistryError(f"Prompt {name!r} blocks must be a list")

        blocks: list[PromptBlock] = []
        for index, raw in enumerate(raw_blocks):
            if isinstance(raw, str):
                blocks.append(PromptBlock(name=f"block_{index + 1}", template=raw))
                continue
            if not isinstance(raw, Mapping):
                raise PromptRegistryError(
                    f"Prompt {name!r} block {index} must be a string or object"
                )
            template = raw.get("template", raw.get("content"))
            if not isinstance(template, str):
                raise PromptRegistryError(
                    f"Prompt {name!r} block {index} requires template/content"
                )
            required = raw.get("required_variables", raw.get("required", []))
            if isinstance(required, str):
                required = [required]
            if not isinstance(required, list):
                raise PromptRegistryError(
                    f"Prompt {name!r} block {index} required_variables must be a list"
                )
            blocks.append(PromptBlock(
                name=str(raw.get("name", raw.get("id", f"block_{index + 1}"))),
                template=template,
                priority=int(raw.get("priority", index * 10)),
                required_variables=tuple(str(item) for item in required),
                enabled=bool(raw.get("enabled", True)),
            ))
        return PromptDefinition(
            name=name,
            version=version,
            blocks=tuple(blocks),
            max_chars=max_chars,
            metadata=metadata,
        )


class _SafeFormatMap(dict[str, Any]):
    def __init__(self, values: Mapping[str, Any], *, strict: bool) -> None:
        super().__init__(values)
        self.strict = strict

    def __missing__(self, key: str) -> str:
        if self.strict:
            raise KeyError(key)
        return "{" + key + "}"
