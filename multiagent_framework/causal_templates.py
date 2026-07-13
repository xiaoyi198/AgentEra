"""Reusable authoring templates for DLC causal-event rules.

The runtime causal engine intentionally understands only fully materialized rules.
This module owns the Studio-facing authoring conveniences and expands them into
that stable runtime shape without mutating the source content document.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import re
from typing import Any

from .causal_events import CausalEventError

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AUTHORING_KEYS = frozenset({"template_id", "condition_group_ids"})


class CausalTemplateError(CausalEventError):
    """Raised when reusable causal authoring definitions cannot be expanded."""

    def __init__(self, code: str, path: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.path = path


def _list_field(content: Mapping[str, Any], name: str) -> list[Any]:
    value = content.get(name, [])
    if not isinstance(value, list):
        raise CausalTemplateError(f"causal_authoring_{name}_invalid", name)
    return value


def _safe_identifier(value: Any, *, code: str, path: str) -> str:
    identifier = str(value or "").strip()
    if not _SAFE_ID.fullmatch(identifier):
        raise CausalTemplateError(code, path)
    return identifier


def _conditions(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CausalTemplateError("causal_condition_group_conditions_invalid", path)
    return [copy.deepcopy(item) for item in value]


def _definitions(
    content: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    groups: dict[str, dict[str, Any]] = {}
    for index, raw_group in enumerate(_list_field(content, "causal_condition_groups")):
        path = f"causal_condition_groups[{index}]"
        if not isinstance(raw_group, Mapping):
            raise CausalTemplateError("causal_condition_group_invalid", path)
        group_id = _safe_identifier(
            raw_group.get("group_id"),
            code="causal_condition_group_id_invalid",
            path=f"{path}.group_id",
        )
        if group_id in groups:
            raise CausalTemplateError("causal_condition_group_duplicate", f"{path}.group_id")
        groups[group_id] = {
            "group_id": group_id,
            "conditions": _conditions(raw_group.get("conditions", []), path=f"{path}.conditions"),
        }

    templates: dict[str, dict[str, Any]] = {}
    for index, raw_template in enumerate(_list_field(content, "causal_rule_templates")):
        path = f"causal_rule_templates[{index}]"
        if not isinstance(raw_template, Mapping):
            raise CausalTemplateError("causal_rule_template_invalid", path)
        template_id = _safe_identifier(
            raw_template.get("template_id"),
            code="causal_rule_template_id_invalid",
            path=f"{path}.template_id",
        )
        if template_id in templates:
            raise CausalTemplateError("causal_rule_template_duplicate", f"{path}.template_id")
        template_rule = raw_template.get("rule", {})
        if not isinstance(template_rule, Mapping):
            raise CausalTemplateError("causal_rule_template_rule_invalid", f"{path}.rule")
        nested = _AUTHORING_KEYS.intersection(template_rule)
        if nested:
            field = sorted(nested)[0]
            raise CausalTemplateError("causal_rule_template_nested_reference", f"{path}.rule.{field}")
        normalized_rule = copy.deepcopy(dict(template_rule))
        if "conditions" in normalized_rule:
            normalized_rule["conditions"] = _conditions(
                normalized_rule["conditions"], path=f"{path}.rule.conditions"
            )
        templates[template_id] = {"template_id": template_id, "rule": normalized_rule}
    return groups, templates


def expand_causal_rule_document(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic runtime rules expanded from a DLC content document.

    Precedence is engine defaults < template rule < authored rule. Conditions are
    additive and retain author order: template conditions, referenced condition
    groups in ``condition_group_ids`` order, then inline rule conditions.
    """

    if not isinstance(content, Mapping):
        raise CausalTemplateError("causal_authoring_content_invalid", "content")
    groups, templates = _definitions(content)
    expanded: list[dict[str, Any]] = []
    for index, raw_rule in enumerate(_list_field(content, "causal_event_rules")):
        path = f"causal_event_rules[{index}]"
        if not isinstance(raw_rule, Mapping):
            raise CausalTemplateError("causal_rule_invalid", path)
        authored = copy.deepcopy(dict(raw_rule))
        template_id_value = authored.pop("template_id", None)
        group_ids_value = authored.pop("condition_group_ids", [])

        base: dict[str, Any] = {}
        if template_id_value not in (None, ""):
            template_id = _safe_identifier(
                template_id_value,
                code="causal_rule_template_reference_invalid",
                path=f"{path}.template_id",
            )
            template = templates.get(template_id)
            if template is None:
                raise CausalTemplateError("causal_rule_template_missing", f"{path}.template_id")
            base = copy.deepcopy(template["rule"])

        if not isinstance(group_ids_value, Sequence) or isinstance(
            group_ids_value, (str, bytes, bytearray)
        ):
            raise CausalTemplateError("causal_condition_group_references_invalid", f"{path}.condition_group_ids")
        group_ids: list[str] = []
        seen_group_ids: set[str] = set()
        for group_index, value in enumerate(group_ids_value):
            group_id = _safe_identifier(
                value,
                code="causal_condition_group_reference_invalid",
                path=f"{path}.condition_group_ids[{group_index}]",
            )
            if group_id in seen_group_ids:
                raise CausalTemplateError(
                    "causal_condition_group_reference_duplicate",
                    f"{path}.condition_group_ids[{group_index}]",
                )
            if group_id not in groups:
                raise CausalTemplateError(
                    "causal_condition_group_missing",
                    f"{path}.condition_group_ids[{group_index}]",
                )
            seen_group_ids.add(group_id)
            group_ids.append(group_id)

        template_conditions = _conditions(
            base.pop("conditions", []), path=f"{path}.template.conditions"
        )
        inline_conditions = _conditions(
            authored.pop("conditions", []), path=f"{path}.conditions"
        )
        materialized = {**base, **authored}
        materialized["conditions"] = [
            *template_conditions,
            *(condition for group_id in group_ids for condition in copy.deepcopy(groups[group_id]["conditions"])),
            *inline_conditions,
        ]
        expanded.append(materialized)
    return expanded
