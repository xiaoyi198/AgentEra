from __future__ import annotations

import pytest

from multiagent_framework.relationship_contract import (
    LEGACY_RELATIONSHIP_TYPES,
    RelationshipCatalog,
    RelationshipContractError,
    validate_relationship_definitions,
)


def trust_type(**overrides):
    return {
        "relationship_type_id": "trust",
        "display_name": "信任",
        "description": "对等信任关系",
        "direction": "undirected",
        "minimum_strength": -100,
        "maximum_strength": 100,
        "default_strength": 0,
        "default_visibility": "private",
        "allow_expiry": True,
        "dimensions": [
            {"dimension_id": "personal", "display_name": "个人信任", "minimum": -100, "maximum": 100, "default": 0},
            {"dimension_id": "institutional", "display_name": "制度信任", "minimum": 0, "maximum": 100, "default": 50},
        ],
        **overrides,
    }


def test_catalog_keeps_legacy_types_and_accepts_custom_type():
    catalog = RelationshipCatalog([trust_type()])
    assert LEGACY_RELATIONSHIP_TYPES <= catalog.ids
    assert catalog.get("trust").direction == "undirected"
    assert catalog.public_state(generation=4)["generation"] == 4


def test_undirected_edges_are_canonical_and_fill_dimension_defaults():
    catalog = RelationshipCatalog([trust_type()])
    relation = catalog.normalize_relation({
        "parent_id": "zeta",
        "child_id": "alpha",
        "rel_type": "trust",
        "strength": 65,
        "visibility": "limited",
        "starts_at": 10,
        "expires_at": 20,
        "values": {"personal": 70},
    })
    assert (relation["parent_id"], relation["child_id"]) == ("alpha", "zeta")
    assert relation["values"] == {"personal": 70.0, "institutional": 50.0}
    assert not catalog.is_active(relation, 5)
    assert catalog.is_active(relation, 10)
    assert not catalog.is_active(relation, 20)


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"strength": 101}, "relation.strength_out_of_range"),
        ({"visibility": "unknown"}, "relation.invalid_visibility"),
        ({"expires_at": 0}, "relation.invalid_time_range"),
        ({"values": {"unknown": 1}}, "relation.unknown_dimension"),
        ({"values": {"institutional": -1}}, "relation.dimension_out_of_range"),
    ],
)
def test_relation_values_are_contract_checked(patch, code):
    raw = {"parent_id": "a", "child_id": "b", "rel_type": "trust", **patch}
    with pytest.raises(RelationshipContractError) as caught:
        RelationshipCatalog([trust_type()]).normalize_relation(raw)
    assert caught.value.code == code


def test_expiry_requires_type_opt_in():
    with pytest.raises(RelationshipContractError) as caught:
        RelationshipCatalog().normalize_relation({
            "parent_id": "a", "child_id": "b", "rel_type": "subordinate", "expires_at": 2,
        })
    assert caught.value.code == "relation.expiry_not_allowed"


def test_duplicate_relationship_type_has_stable_issue():
    issues = validate_relationship_definitions([trust_type(), trust_type()])
    assert [item.code for item in issues] == ["relationship_type.duplicate_id"]


def test_validation_reports_unknown_type_and_entity_references():
    issues = validate_relationship_definitions(
        [trust_type()],
        [
            {"parent_id": "missing", "child_id": "b", "rel_type": "trust"},
            {"parent_id": "a", "child_id": "b", "rel_type": "unknown"},
        ],
        entity_ids={"a", "b"},
    )
    assert {item.code for item in issues} == {"relation.missing_parent", "relation.unknown_type"}


def test_authored_legacy_override_is_retained_in_persistable_definitions():
    catalog = RelationshipCatalog([{
        "relationship_type_id": "subordinate",
        "display_name": "Command",
        "minimum_strength": 0,
        "maximum_strength": 10,
        "default_strength": 7,
    }])
    persisted = catalog.definitions(include_legacy=False)
    assert [item["relationship_type_id"] for item in persisted] == ["subordinate"]
    assert persisted[0]["default_strength"] == 7
    assert catalog.public_state()["configured"] is True
