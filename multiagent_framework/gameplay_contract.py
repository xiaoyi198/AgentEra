"""DLC-authored gameplay identity and player control contract.

The framework owns only stable identifiers, validation, and runtime lookup. Theme
semantics such as emperor, commander, memorial, or decree remain DLC content.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

CONTROLLED_OBJECT_KINDS = frozenset({"entity", "agent"})
CONTROL_MODES = frozenset({"direct", "command"})


@dataclass(frozen=True)
class GameplayValidationIssue:
    code: str
    message: str
    path: str


@dataclass(frozen=True)
class ControlledObject:
    kind: str
    id: str
    mode: str = "direct"
    display_name: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ControlledObject":
        return cls(
            kind=str(data.get("kind", "")).strip(),
            id=str(data.get("id", "")).strip(),
            mode=str(data.get("mode", "direct")).strip(),
            display_name=str(data.get("display_name", "")).strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerIdentity:
    identity_id: str
    display_name: str
    actor_id: str = ""
    controlled_objects: tuple[ControlledObject, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlayerIdentity":
        return cls(
            identity_id=str(data.get("identity_id", "")).strip(),
            display_name=str(data.get("display_name", "")).strip(),
            actor_id=str(data.get("actor_id", "")).strip(),
            controlled_objects=tuple(
                ControlledObject.from_dict(item)
                for item in data.get("controlled_objects", [])
                if isinstance(item, dict)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_id": self.identity_id,
            "display_name": self.display_name,
            "actor_id": self.actor_id,
            "controlled_objects": [item.to_dict() for item in self.controlled_objects],
        }


@dataclass(frozen=True)
class GameplayContract:
    package_id: str = ""
    package_name: str = ""
    source: str = "none"
    player: PlayerIdentity | None = None

    @property
    def configured(self) -> bool:
        return self.player is not None

    @property
    def direct_agent_ids(self) -> frozenset[str]:
        if self.player is None:
            return frozenset()
        return frozenset(
            item.id
            for item in self.player.controlled_objects
            if item.kind == "agent" and item.mode == "direct"
        )

    def is_player_recipient(self, target_id: str) -> bool:
        return str(target_id) in self.direct_agent_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "package_id": self.package_id,
            "package_name": self.package_name,
            "source": self.source,
            "player": self.player.to_dict() if self.player else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "GameplayContract":
        document = data if isinstance(data, dict) else {}
        player = document.get("player")
        return cls(
            package_id=str(document.get("package_id", "")),
            package_name=str(document.get("package_name", "")),
            source=str(document.get("source", "none")),
            player=PlayerIdentity.from_dict(player) if isinstance(player, dict) else None,
        )

    @classmethod
    def from_content(
        cls,
        gameplay: dict[str, Any] | None,
        agents: Iterable[dict[str, Any]],
        *,
        package_id: str = "",
        package_name: str = "",
        derive_legacy: bool = True,
    ) -> "GameplayContract":
        document = gameplay if isinstance(gameplay, dict) else {}
        player = document.get("player")
        if isinstance(player, dict) and player:
            return cls(
                package_id=package_id,
                package_name=package_name,
                source="explicit",
                player=PlayerIdentity.from_dict(player),
            )
        if not derive_legacy:
            return cls(package_id=package_id, package_name=package_name)

        legacy_players = [
            item for item in agents
            if (
                isinstance(item, dict)
                and str(item.get("role", "")) == "player"
                and bool(item.get("enabled", True))
            )
        ]
        if not legacy_players:
            return cls(package_id=package_id, package_name=package_name)
        first = legacy_players[0]
        first_id = str(first.get("id", "")).strip()
        return cls(
            package_id=package_id,
            package_name=package_name,
            source="legacy_role",
            player=PlayerIdentity(
                identity_id=first_id or "legacy_player",
                display_name=str(first.get("name", first_id or "Player")).strip(),
                actor_id=first_id,
                controlled_objects=tuple(
                    ControlledObject(
                        kind="agent",
                        id=str(item.get("id", "")).strip(),
                        mode="direct",
                        display_name=str(item.get("name", item.get("id", ""))).strip(),
                    )
                    for item in legacy_players
                    if str(item.get("id", "")).strip()
                ),
            ),
        )


def normalize_gameplay_document(value: Any) -> dict[str, Any]:
    """Return a detached, predictable gameplay document shape."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("DLC content field 'gameplay' must be an object")
    normalized = dict(value)
    player = normalized.get("player")
    if player is not None:
        if not isinstance(player, dict):
            raise TypeError("DLC gameplay.player must be an object")
        normalized_player = dict(player)
        controlled = normalized_player.setdefault("controlled_objects", [])
        if not isinstance(controlled, list):
            raise TypeError("DLC gameplay.player.controlled_objects must be a list")
        normalized_player["controlled_objects"] = [
            dict(item) if isinstance(item, dict) else item for item in controlled
        ]
        normalized["player"] = normalized_player
    return normalized


def validate_gameplay_document(
    gameplay: dict[str, Any],
    *,
    entity_ids: set[str],
    agent_ids: set[str],
) -> list[GameplayValidationIssue]:
    issues: list[GameplayValidationIssue] = []

    def add(code: str, message: str, path: str) -> None:
        issues.append(GameplayValidationIssue(code, message, path))

    player = gameplay.get("player")
    if player is None:
        return issues
    if not isinstance(player, dict):
        add("gameplay.invalid_player", "player must be an object", "gameplay.player")
        return issues

    identity_id = player.get("identity_id")
    display_name = player.get("display_name")
    actor_id = player.get("actor_id", "")
    if not isinstance(identity_id, str) or not identity_id.strip():
        add(
            "gameplay.player.missing_identity_id",
            "identity_id must be a non-empty string",
            "gameplay.player.identity_id",
        )
    if not isinstance(display_name, str) or not display_name.strip():
        add(
            "gameplay.player.missing_display_name",
            "display_name must be a non-empty string",
            "gameplay.player.display_name",
        )
    if actor_id is not None and not isinstance(actor_id, str):
        add(
            "gameplay.player.invalid_actor_id",
            "actor_id must be a string when provided",
            "gameplay.player.actor_id",
        )

    controlled = player.get("controlled_objects", [])
    if not isinstance(controlled, list):
        add(
            "gameplay.player.invalid_controlled_objects",
            "controlled_objects must be a list",
            "gameplay.player.controlled_objects",
        )
        return issues
    if not controlled:
        add(
            "gameplay.player.missing_controlled_objects",
            "player must control at least one entity or agent",
            "gameplay.player.controlled_objects",
        )
        return issues

    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(controlled):
        path = f"gameplay.player.controlled_objects[{index}]"
        if not isinstance(item, dict):
            add("gameplay.control.invalid", "controlled object must be an object", path)
            continue
        kind = item.get("kind")
        object_id = item.get("id")
        mode = item.get("mode", "direct")
        if kind not in CONTROLLED_OBJECT_KINDS:
            add(
                "gameplay.control.invalid_kind",
                f"kind must be one of: {', '.join(sorted(CONTROLLED_OBJECT_KINDS))}",
                f"{path}.kind",
            )
        if not isinstance(object_id, str) or not object_id.strip():
            add("gameplay.control.missing_id", "id must be a non-empty string", f"{path}.id")
            continue
        object_id = object_id.strip()
        if mode not in CONTROL_MODES:
            add(
                "gameplay.control.invalid_mode",
                f"mode must be one of: {', '.join(sorted(CONTROL_MODES))}",
                f"{path}.mode",
            )
        key = (str(kind), object_id)
        if key in seen:
            add("gameplay.control.duplicate", f"duplicate controlled object: {kind}:{object_id}", path)
        seen.add(key)
        if kind == "entity" and object_id not in entity_ids:
            add("gameplay.control.missing_entity", f"unknown controlled entity: {object_id}", f"{path}.id")
        elif kind == "agent" and object_id not in agent_ids:
            add("gameplay.control.missing_agent", f"unknown controlled agent: {object_id}", f"{path}.id")
    return issues
