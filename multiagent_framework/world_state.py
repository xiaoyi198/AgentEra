"""世界状态数据库 — 存储世界中所有实体的动态状态和库存.

与 CourtGraph 并列：
- CourtGraph 管"这个官是谁、在哪、听谁的"（结构层，不变）
- WorldState 管"这个地方/机构发生了什么、有多少粮"（动态层，可变）
- 两者通过相同 ID 关联（如 location_id="吴县" 在两边都有）
"""

from __future__ import annotations

from contextlib import closing
import json
import math
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .world_commands import (
    WorldCommand,
    WorldCommandBus,
    WorldCommandError,
    WorldCommandResult,
    WorldCommandType,
)
from .relationship_contract import (
    LEGACY_RELATIONSHIP_TYPES,
    RelationshipCatalog,
    RelationshipContractError,
)
from .gameplay_effect_runtime import (
    empty_gameplay_effect_state, normalize_gameplay_effect_state,
    reduce_gameplay_effect_state,
)


# ── 资源代币定义 ──

# ── 权威资源目录 ──

# ``type`` remains for older prompts/UI code. ``kind`` is authoritative:
# assets live in inventory and carry an anchor rate; metrics/parameters live in
# entity_values and cannot be spent or transferred as inventory.
RESOURCE_TOKENS: list[dict[str, Any]] = [
    {"name": "\u91d1\u94b1", "unit": "\u4e24", "default": 0, "min": 0, "max": None, "type": "resource", "kind": "asset", "anchor_rate": 1.0},
    {"name": "\u7cae\u8349", "unit": "\u77f3", "default": 0, "min": 0, "max": None, "type": "resource", "kind": "asset", "anchor_rate": 1.0},
    {"name": "\u5de5\u6599", "unit": "\u5de5\u65e5", "default": 0, "min": 0, "max": None, "type": "resource", "kind": "asset", "anchor_rate": 1.0},
    {"name": "\u5175\u529b", "unit": "\u4eba", "default": 0, "min": 0, "max": None, "type": "resource", "kind": "asset", "anchor_rate": 1.0},
    {"name": "\u79d1\u6280\u70b9", "unit": "\u70b9", "default": 0, "min": 0, "max": 200, "type": "data", "kind": "metric", "anchor_rate": None},
    {"name": "\u6ee1\u610f\u5ea6", "unit": "", "default": 50, "min": -1, "max": 100, "type": "data", "kind": "metric", "anchor_rate": None},
    {"name": "\u4eba\u53e3", "unit": "\u4eba", "default": 0, "min": -1, "max": None, "type": "data", "kind": "metric", "anchor_rate": None},
    {"name": "\u751f\u6d3b\u8d28\u91cf", "unit": "", "default": 1.0, "min": 0.2, "max": 1.5, "type": "param", "kind": "parameter", "anchor_rate": None},
    {"name": "\u7a0e\u7387", "unit": "%", "default": 0.08, "min": 0, "max": 0.5, "type": "param", "kind": "parameter", "anchor_rate": None},
    {"name": "\u751f\u4ea7\u7cfb\u6570", "unit": "", "default": 1.0, "min": 0.5, "max": 2.5, "type": "param", "kind": "parameter", "anchor_rate": None},
]
RESOURCE_NAMES: set[str] = {t["name"] for t in RESOURCE_TOKENS}
RESOURCE_META: dict[str, dict[str, Any]] = {t["name"]: t for t in RESOURCE_TOKENS}
ASSET_NAMES: set[str] = {t["name"] for t in RESOURCE_TOKENS if t["kind"] == "asset"}
VALUE_NAMES: set[str] = RESOURCE_NAMES - ASSET_NAMES


# Backward-compatible import surface. Runtime validation is catalog-driven.
RELATION_TYPES: set[str] = set(LEGACY_RELATIONSHIP_TYPES)


# ── 数据类 ──

@dataclass
class Entity:
    """A versioned world entity."""
    id: str
    type: str
    name: str
    description: str = ""
    current_status: str = ""
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 0


@dataclass
class InventoryItem:
    """An anchored asset held by an entity."""
    entity_id: str
    category: str
    item_name: str
    quantity: float = 0.0
    description: str = ""
    version: int = 0
    anchor_rate: float = 1.0

    @property
    def base_value(self) -> float:
        if self.quantity == -1:
            return 0.0
        return float(self.quantity) * float(self.anchor_rate)


@dataclass
class EntityValue:
    """A non-spendable metric or parameter, stored outside inventory."""
    entity_id: str
    name: str
    kind: str
    value: float = 0.0
    description: str = ""
    version: int = 0


@dataclass
class ResourceDefinition:
    name: str
    unit: str = ""
    kind: str = "asset"
    default_quantity: float = 0.0
    min_quantity: float | None = 0.0
    max_quantity: float | None = None
    anchor_rate: float | None = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OngoingEvent:
    """实体正在发生的事情（延迟落账的状态/库存变化）.

    event_type 暂时只用 "state_change"；预留 "info_transit" 等以便未来与
    TimerSystem 合并。expected_result 形如：
        {"new_status": "...", "inventory_deltas": [{"resource": "金钱", "delta": -3000, "reason": "..."}]}
    end_time 到达时由 orchestrator 自动应用 expected_result。
    """
    event_id: str
    entity_id: str
    event_type: str = "state_change"
    description: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    expected_result: dict[str, Any] = field(default_factory=dict)
    source_action_id: str = ""


# ── 主类 ──

class WorldState:
    """基于 SQLite 的世界状态存储 + 查询."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._write_lock = threading.RLock()
        self.relationship_catalog = RelationshipCatalog()
        self._init_db()
        self._restore_relationship_catalog()
        self.command_bus = WorldCommandBus(self)

    @staticmethod
    def _configure_conn(conn: sqlite3.Connection) -> sqlite3.Connection:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _ensure_column(
        cls, conn: sqlite3.Connection, table: str, column: str, ddl: str,
    ) -> bool:
        if column in cls._column_names(conn, table):
            return False
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        return True

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self._db_path)) as raw_conn, raw_conn:
            conn = self._configure_conn(raw_conn)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    current_status TEXT DEFAULT '',
                    updated_at REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    entity_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    quantity REAL DEFAULT 0,
                    description TEXT DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (entity_id, category, item_name),
                    FOREIGN KEY (entity_id) REFERENCES entities(id)
                )
            """)
            self._ensure_column(conn, "entities", "version", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "inventory", "version", "INTEGER NOT NULL DEFAULT 0")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_catalog (
                    name TEXT PRIMARY KEY,
                    unit TEXT DEFAULT '',
                    kind TEXT NOT NULL,
                    default_quantity REAL DEFAULT 0,
                    min_quantity REAL,
                    max_quantity REAL,
                    anchor_rate REAL,
                    metadata TEXT DEFAULT '{}',
                    updated_at REAL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_values (
                    entity_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    value REAL DEFAULT 0,
                    description TEXT DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (entity_id, name),
                    FOREIGN KEY (entity_id) REFERENCES entities(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory_ledger (
                    entry_id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    resource_name TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    quantity_delta REAL NOT NULL,
                    anchor_rate REAL NOT NULL,
                    base_value_delta REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    reason TEXT DEFAULT '',
                    sim_time REAL DEFAULT 0,
                    chain_id TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL NOT NULL,
                    UNIQUE(command_id, entity_id, resource_name, sequence)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS world_commands (
                    command_id TEXT PRIMARY KEY,
                    command_type TEXT NOT NULL,
                    entity_id TEXT DEFAULT '',
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT DEFAULT '{}',
                    error_code TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    chain_id TEXT DEFAULT '',
                    intent_id TEXT DEFAULT '',
                    schema_version TEXT DEFAULT '1.0',
                    sim_time REAL DEFAULT 0,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_entity ON inventory_ledger(entity_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_command ON inventory_ledger(command_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_command_chain ON world_commands(chain_id, created_at)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_relations (
                    parent_id TEXT NOT NULL,
                    child_id TEXT NOT NULL,
                    rel_type TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    PRIMARY KEY (parent_id, child_id, rel_type)
                )
            """)
            strength_added = self._ensure_column(
                conn, "entity_relations", "strength", "REAL DEFAULT 0"
            )
            self._ensure_column(conn, "entity_relations", "visibility", "TEXT DEFAULT 'public'")
            self._ensure_column(conn, "entity_relations", "starts_at", "REAL DEFAULT 0")
            self._ensure_column(conn, "entity_relations", "expires_at", "REAL")
            self._ensure_column(conn, "entity_relations", "dimension_values", "TEXT DEFAULT '{}'")
            self._ensure_column(conn, "entity_relations", "metadata", "TEXT DEFAULT '{}'")
            if strength_added:
                conn.execute(
                    """UPDATE entity_relations SET strength = CASE rel_type
                         WHEN 'subordinate' THEN 100
                         WHEN 'affiliated' THEN 50
                         ELSE strength END"""
                )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_child ON entity_relations(child_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_parent ON entity_relations(parent_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relationship_type_catalog (
                    relationship_type_id TEXT PRIMARY KEY,
                    definition_json TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ongoing_events (
                    event_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    event_type TEXT DEFAULT 'state_change',
                    description TEXT DEFAULT '',
                    start_time REAL DEFAULT 0,
                    end_time REAL DEFAULT 0,
                    expected_result TEXT DEFAULT '{}',
                    source_action_id TEXT DEFAULT '',
                    FOREIGN KEY (entity_id) REFERENCES entities(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_entity ON ongoing_events(entity_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_event_endtime ON ongoing_events(end_time)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gameplay_effect_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}',
                    version INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL DEFAULT 0
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO gameplay_effect_state(state_key,value_json,version,updated_at) VALUES('runtime',?,0,0)",
                (json.dumps(empty_gameplay_effect_state(), ensure_ascii=False, sort_keys=True),),
            )

            for token in RESOURCE_TOKENS:
                conn.execute(
                    """INSERT OR IGNORE INTO resource_catalog
                       (name, unit, kind, default_quantity, min_quantity,
                        max_quantity, anchor_rate, metadata, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        token["name"], token.get("unit", ""), token["kind"],
                        float(token.get("default", 0)), token.get("min"),
                        token.get("max"), token.get("anchor_rate"),
                        json.dumps({"legacy_type": token.get("type", "")}),
                        0.0,
                    ),
                )

            # One-time, idempotent migration: metrics and parameters no longer
            # share the spendable inventory table.
            for token in RESOURCE_TOKENS:
                if token["kind"] == "asset":
                    continue
                conn.execute(
                    """INSERT INTO entity_values
                       (entity_id, name, kind, value, description, version)
                       SELECT entity_id, item_name, ?, quantity, description,
                              COALESCE(version, 0)
                       FROM inventory WHERE item_name = ?
                       ON CONFLICT(entity_id, name) DO NOTHING""",
                    (token["kind"], token["name"]),
                )
                conn.execute("DELETE FROM inventory WHERE item_name = ?", (token["name"],))

    def _conn(self) -> sqlite3.Connection:
        return self._configure_conn(sqlite3.connect(self._db_path, timeout=5.0))

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return Entity(
            id=row["id"], type=row["type"], name=row["name"],
            description=row["description"] or "",
            current_status=row["current_status"] or "",
            updated_at=float(row["updated_at"] or 0.0), metadata=metadata,
            version=int(row["version"] or 0),
        )

    def get_entity(self, entity_id: str) -> Entity | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        return self._row_to_entity(row) if row else None

    def get_coordinates(self, entity_id: str) -> tuple[float, float, str] | None:
        entity = self.get_entity(entity_id)
        if not entity or ("x" not in entity.metadata and "y" not in entity.metadata):
            return None
        return (
            float(entity.metadata.get("x", 0.0)),
            float(entity.metadata.get("y", 0.0)),
            str(entity.metadata.get("region", "")),
        )

    def get_entities_by_type(self, entity_type: str) -> list[Entity]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute("SELECT * FROM entities WHERE type=?", (entity_type,)).fetchall()
        return [self._row_to_entity(row) for row in rows]

    def get_all_entities(self) -> list[Entity]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute("SELECT * FROM entities").fetchall()
        return [self._row_to_entity(row) for row in rows]

    @staticmethod
    def _upsert_entity_conn(
        conn: sqlite3.Connection, entity: Entity, *, preserve_version: bool = False,
    ) -> None:
        version_sql = "excluded.version" if preserve_version else "entities.version + 1"
        conn.execute(
            f"""INSERT INTO entities
                (id,type,name,description,current_status,updated_at,metadata,version)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  type=excluded.type,name=excluded.name,description=excluded.description,
                  current_status=excluded.current_status,updated_at=excluded.updated_at,
                  metadata=excluded.metadata,version={version_sql}""",
            (
                entity.id, entity.type, entity.name, entity.description,
                entity.current_status, entity.updated_at,
                json.dumps(entity.metadata, ensure_ascii=False), max(0, int(entity.version)),
            ),
        )

    @staticmethod
    def _not_applicable_default(entity_type: str, name: str) -> bool:
        return entity_type == "institution" and name in {
            "\u6ee1\u610f\u5ea6", "\u4eba\u53e3", "\u751f\u6d3b\u8d28\u91cf",
            "\u7a0e\u7387", "\u751f\u4ea7\u7cfb\u6570",
        }

    def _init_default_resources_conn(
        self, conn: sqlite3.Connection, entity_id: str, entity_type: str = "",
    ) -> None:
        for row in conn.execute("SELECT * FROM resource_catalog ORDER BY name").fetchall():
            value = float(row["default_quantity"] or 0.0)
            if self._not_applicable_default(entity_type, row["name"]):
                value = -1.0
            if row["kind"] == "asset":
                conn.execute(
                    """INSERT OR IGNORE INTO inventory
                       (entity_id,category,item_name,quantity,description,version)
                       VALUES (?,?,?,?,?,0)""",
                    (entity_id, row["name"], row["name"], value, row["unit"] or ""),
                )
            else:
                conn.execute(
                    """INSERT OR IGNORE INTO entity_values
                       (entity_id,name,kind,value,description,version)
                       VALUES (?,?,?,?,?,0)""",
                    (entity_id, row["name"], row["kind"], value, row["unit"] or ""),
                )

    def upsert_entity(self, entity: Entity) -> None:
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._upsert_entity_conn(conn, entity)
                self._init_default_resources_conn(conn, entity.id, entity.type)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_default_inventory(self, entity_id: str, entity_type: str = "") -> None:
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._require_entity(conn, entity_id)
                self._init_default_resources_conn(conn, entity_id, entity_type)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def update_status(
        self, entity_id: str, new_status: str, sim_time: float, *,
        command_id: str | None = None, expected_version: int | None = None,
        chain_id: str = "", intent_id: str = "",
    ) -> dict[str, Any]:
        return self.command_bus.execute_or_raise(WorldCommand(
            command_type=WorldCommandType.UPDATE_STATUS,
            command_id=command_id or f"wcmd_{uuid.uuid4().hex}",
            entity_id=entity_id, expected_version=expected_version,
            chain_id=chain_id, intent_id=intent_id, sim_time=sim_time,
            payload={"new_status": str(new_status)},
        ))

    def delete_entity(self, entity_id: str) -> bool:
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM inventory WHERE entity_id=?", (entity_id,))
                conn.execute("DELETE FROM entity_values WHERE entity_id=?", (entity_id,))
                conn.execute(
                    "DELETE FROM entity_relations WHERE parent_id=? OR child_id=?",
                    (entity_id, entity_id),
                )
                conn.execute("DELETE FROM ongoing_events WHERE entity_id=?", (entity_id,))
                cur = conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
                conn.commit()
                return cur.rowcount > 0
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def delete_inventory_item(self, entity_id: str, category: str, item_name: str) -> bool:
        if self.get_resource_definition(item_name) is not None:
            return False
        with self._write_lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM inventory WHERE entity_id=? AND category=? AND item_name=?",
                (entity_id, category, item_name),
            )
        return cur.rowcount > 0

    def search_entities(self, keyword: str) -> list[Entity]:
        pattern = f"%{keyword}%"
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE id LIKE ? OR name LIKE ? OR description LIKE ?",
                (pattern, pattern, pattern),
            ).fetchall()
        return [self._row_to_entity(row) for row in rows]

    # Resource read models.

    def _row_to_item(self, row: sqlite3.Row) -> InventoryItem:
        anchor_rate = row["anchor_rate"] if "anchor_rate" in row.keys() else 1.0
        return InventoryItem(
            entity_id=row["entity_id"], category=row["category"], item_name=row["item_name"],
            quantity=float(row["quantity"] if row["quantity"] is not None else 0.0),
            description=row["description"] or "", version=int(row["version"] or 0),
            anchor_rate=float(anchor_rate if anchor_rate is not None else 1.0),
        )

    @staticmethod
    def _row_to_value(row: sqlite3.Row) -> EntityValue:
        return EntityValue(
            entity_id=row["entity_id"], name=row["name"], kind=row["kind"],
            value=float(row["value"] if row["value"] is not None else 0.0),
            description=row["description"] or "", version=int(row["version"] or 0),
        )

    @staticmethod
    def _row_to_resource_definition(row: sqlite3.Row) -> ResourceDefinition:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return ResourceDefinition(
            name=row["name"], unit=row["unit"] or "", kind=row["kind"],
            default_quantity=float(row["default_quantity"] or 0.0),
            min_quantity=float(row["min_quantity"]) if row["min_quantity"] is not None else None,
            max_quantity=float(row["max_quantity"]) if row["max_quantity"] is not None else None,
            anchor_rate=float(row["anchor_rate"]) if row["anchor_rate"] is not None else None,
            metadata=metadata,
        )

    def get_inventory(self, entity_id: str) -> list[InventoryItem]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                """SELECT i.*,COALESCE(rc.anchor_rate,1.0) AS anchor_rate
                   FROM inventory i LEFT JOIN resource_catalog rc ON rc.name=i.item_name
                   WHERE i.entity_id=? ORDER BY i.item_name""", (entity_id,),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get_entity_values(self, entity_id: str, kind: str | None = None) -> list[EntityValue]:
        with closing(self._conn()) as conn, conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM entity_values WHERE entity_id=? AND kind=? ORDER BY name",
                    (entity_id, kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM entity_values WHERE entity_id=? ORDER BY kind,name",
                    (entity_id,),
                ).fetchall()
        return [self._row_to_value(row) for row in rows]

    def get_resource_definition(self, name: str) -> ResourceDefinition | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute("SELECT * FROM resource_catalog WHERE name=?", (name,)).fetchone()
        return self._row_to_resource_definition(row) if row else None

    def list_resource_definitions(self) -> list[ResourceDefinition]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute("SELECT * FROM resource_catalog ORDER BY kind,name").fetchall()
        return [self._row_to_resource_definition(row) for row in rows]

    def get_inventory_valuation(self, entity_id: str) -> dict[str, Any]:
        items = [item for item in self.get_inventory(entity_id) if item.quantity != -1]
        return {
            "entity_id": entity_id,
            "base_value": sum(item.base_value for item in items),
            "items": [{
                "name": item.item_name, "quantity": item.quantity,
                "anchor_rate": item.anchor_rate, "base_value": item.base_value,
                "version": item.version,
            } for item in items],
        }

    def get_economic_invariants(self) -> dict[str, Any]:
        """Return bounded aggregate anchored-value and transfer reconciliation data.

        The snapshot intentionally omits entity, command, chain, and transfer IDs.
        Inventory totals are computed from current balances while issuance,
        destruction, and transfer conservation are derived from the immutable
        ledger. Failed atomic batches never enter the ledger and therefore cannot
        distort the economic totals.
        """
        with closing(self._conn()) as conn, conn:
            resource_rows = conn.execute(
                """SELECT r.name AS resource_name, r.unit AS unit,
                          COALESCE(r.anchor_rate, 1.0) AS anchor_rate,
                          COALESCE(SUM(CASE WHEN i.quantity IS NOT NULL AND i.quantity != -1
                                            THEN i.quantity ELSE 0 END), 0) AS quantity,
                          COALESCE(SUM(CASE WHEN i.quantity IS NOT NULL AND i.quantity != -1
                                             AND ABS(i.quantity) > 0.000000001
                                            THEN 1 ELSE 0 END), 0) AS holder_count
                   FROM resource_catalog r
                   LEFT JOIN inventory i ON i.item_name = r.name
                   WHERE r.kind = 'asset'
                   GROUP BY r.name, r.unit, r.anchor_rate
                   ORDER BY r.name"""
            ).fetchall()
            ledger_summary = conn.execute(
                """SELECT COUNT(*) AS entries_total,
                          COALESCE(SUM(CASE WHEN base_value_delta > 0
                                            THEN base_value_delta ELSE 0 END), 0) AS gross_positive,
                          COALESCE(SUM(CASE WHEN base_value_delta < 0
                                            THEN -base_value_delta ELSE 0 END), 0) AS gross_negative
                   FROM inventory_ledger"""
            ).fetchone()
            transfer_rows = conn.execute(
                """SELECT base_value_delta, metadata
                   FROM inventory_ledger
                   WHERE metadata LIKE '%"transfer_side"%'"""
            ).fetchall()

        resources: list[dict[str, Any]] = []
        total_base_value = 0.0
        total_holders = 0
        for row in resource_rows:
            quantity = float(row["quantity"] or 0.0)
            anchor_rate = float(row["anchor_rate"] or 1.0)
            base_value = quantity * anchor_rate
            holder_count = int(row["holder_count"] or 0)
            total_base_value += base_value
            total_holders += holder_count
            resources.append({
                "resource_name": str(row["resource_name"]),
                "unit": str(row["unit"] or ""),
                "quantity": round(quantity, 8),
                "anchor_rate": round(anchor_rate, 8),
                "base_value": round(base_value, 8),
                "holder_count": holder_count,
            })

        transfer_groups: dict[str, dict[str, float | int]] = {}
        transfer_debit = 0.0
        transfer_credit = 0.0
        transfer_entry_count = 0
        for row in transfer_rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(metadata, dict):
                continue
            side = str(metadata.get("transfer_side", ""))
            if side not in {"debit", "credit"}:
                continue
            delta = float(row["base_value_delta"] or 0.0)
            amount = abs(delta)
            transfer_entry_count += 1
            if side == "debit":
                transfer_debit += amount
            else:
                transfer_credit += amount

            transfer_id = str(metadata.get("transfer_id", "")).strip()
            if not transfer_id:
                event_id = str(metadata.get("causal_event_id", "legacy"))
                try:
                    effect_index = int(metadata.get("effect_index", 0))
                except (TypeError, ValueError):
                    effect_index = 0
                # v0.29+ records the authored effect index on both transfer
                # commands. Older snapshots recorded the command index, so the
                # credit entry must still be paired with its preceding debit.
                if (
                    side == "credit"
                    and metadata.get("effect_type") != "transfer_anchored_value"
                ):
                    effect_index -= 1
                transfer_id = f"legacy:{event_id}:{effect_index}"
            group = transfer_groups.setdefault(
                transfer_id, {"debit": 0.0, "credit": 0.0, "entries": 0},
            )
            group[side] = float(group[side]) + amount
            group["entries"] = int(group["entries"]) + 1

        epsilon = 1e-6
        mismatch_count = 0
        incomplete_count = 0
        max_abs_mismatch = 0.0
        for group in transfer_groups.values():
            debit = float(group["debit"])
            credit = float(group["credit"])
            mismatch = abs(credit - debit)
            max_abs_mismatch = max(max_abs_mismatch, mismatch)
            if not debit or not credit:
                incomplete_count += 1
            if mismatch > epsilon:
                mismatch_count += 1

        gross_positive = float(ledger_summary["gross_positive"] or 0.0)
        gross_negative = float(ledger_summary["gross_negative"] or 0.0)
        issued = max(0.0, gross_positive - transfer_credit)
        destroyed = max(0.0, gross_negative - transfer_debit)
        return {
            "schema_version": "1.0",
            "inventory": {
                "asset_resource_count": len(resources),
                "holder_count": total_holders,
                "total_base_value": round(total_base_value, 8),
                "by_resource": resources,
            },
            "ledger": {
                "entries_total": int(ledger_summary["entries_total"] or 0),
                "issued_base_value": round(issued, 8),
                "destroyed_base_value": round(destroyed, 8),
                "net_non_transfer_base_value": round(issued - destroyed, 8),
            },
            "transfers": {
                "batch_count": len(transfer_groups),
                "entry_count": transfer_entry_count,
                "debit_base_value": round(transfer_debit, 8),
                "credit_base_value": round(transfer_credit, 8),
                "net_base_value": round(transfer_credit - transfer_debit, 8),
                "mismatch_batch_count": mismatch_count,
                "incomplete_batch_count": incomplete_count,
                "max_abs_mismatch": round(max_abs_mismatch, 8),
                "conserved": mismatch_count == 0 and incomplete_count == 0,
            },
        }

    def get_inventory_ledger(
        self, entity_id: str | None = None, *, command_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if entity_id:
            clauses.append("entity_id=?")
            params.append(entity_id)
        if command_id:
            clauses.append("command_id=?")
            params.append(command_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                f"SELECT * FROM inventory_ledger{where} ORDER BY created_at DESC,sequence DESC LIMIT ?",
                params,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (json.JSONDecodeError, TypeError):
                item["metadata"] = {}
            result.append(item)
        return result

    def get_world_command(self, command_id: str) -> dict[str, Any] | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute("SELECT * FROM world_commands WHERE command_id=?", (command_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        for key in ("payload_json", "result_json"):
            try:
                item[key] = json.loads(item.get(key) or "{}")
            except (json.JSONDecodeError, TypeError):
                item[key] = {}
        return item

    # Transactional reducers.

    @staticmethod
    def _finite_number(value: Any, field_name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise WorldCommandError("invalid_number", f"{field_name} must be numeric", {"field": field_name}) from exc
        if not math.isfinite(number):
            raise WorldCommandError("invalid_number", f"{field_name} must be finite", {"field": field_name})
        return number

    @staticmethod
    def _require_entity(conn: sqlite3.Connection, entity_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise WorldCommandError("entity_not_found", f"entity does not exist: {entity_id}", {"entity_id": entity_id})
        return row

    @staticmethod
    def _resource_definition_conn(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM resource_catalog WHERE name=?", (name,)).fetchone()
        if row is None:
            raise WorldCommandError("resource_not_found", f"resource is not registered: {name}", {"resource_name": name})
        return row

    @staticmethod
    def _check_version(
        expected: int | None, actual: int, *, target: str, details: dict[str, Any],
    ) -> None:
        if expected is not None and int(expected) != int(actual):
            raise WorldCommandError(
                "version_conflict", f"{target} version conflict",
                {**details, "expected_version": int(expected), "actual_version": int(actual)},
            )

    def _reduce_world_command(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        reducer = {
            WorldCommandType.UPDATE_STATUS: self._reduce_update_status,
            WorldCommandType.ADJUST_RESOURCE: self._reduce_resource,
            WorldCommandType.SET_RESOURCE: self._reduce_resource,
            WorldCommandType.CREATE_EVENT: self._reduce_create_event,
            WorldCommandType.UPDATE_EVENT: self._reduce_update_event,
            WorldCommandType.DELETE_EVENT: self._reduce_delete_event,
            WorldCommandType.APPLY_MATURED_EVENT: self._reduce_apply_matured_event,
            WorldCommandType.CREATE_ENTITY: self._reduce_create_entity,
            WorldCommandType.MOVE_ENTITY: self._reduce_move_entity,
            WorldCommandType.TRANSFORM_ENTITY: self._reduce_transform_entity,
            WorldCommandType.REMOVE_ENTITY: self._reduce_remove_entity,
            WorldCommandType.MODIFY_RELATIONSHIP: self._reduce_modify_relationship,
            WorldCommandType.SET_AGENT_ENABLED: self._reduce_gameplay_effect,
            WorldCommandType.MODIFY_MEMBERSHIP: self._reduce_gameplay_effect,
            WorldCommandType.CREATE_ISSUE: self._reduce_gameplay_effect,
            WorldCommandType.SET_WORLD_TRUTH: self._reduce_gameplay_effect,
            WorldCommandType.SET_ACTOR_BELIEF: self._reduce_gameplay_effect,
            WorldCommandType.REVEAL_INFORMATION: self._reduce_gameplay_effect,
            WorldCommandType.PROPAGATE_KNOWLEDGE: self._reduce_gameplay_effect,
            WorldCommandType.START_INVESTIGATION: self._reduce_gameplay_effect,
            WorldCommandType.ADVANCE_INVESTIGATION: self._reduce_gameplay_effect,
            WorldCommandType.ADVANCE_OBJECTIVE: self._reduce_gameplay_effect,
            WorldCommandType.UNLOCK_ACTION: self._reduce_gameplay_effect,
            WorldCommandType.SWITCH_PHASE: self._reduce_gameplay_effect,
        }.get(command.command_type)
        if reducer is None:
            raise WorldCommandError("unsupported_command", f"unsupported command: {command.command_type}")
        return reducer(conn, command)

    @staticmethod
    def _entity_metadata(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(row["metadata"] or "{}")
        except (TypeError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    def _reduce_create_entity(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        if conn.execute("SELECT 1 FROM entities WHERE id=?", (command.entity_id,)).fetchone() is not None:
            raise WorldCommandError("entity_exists", f"entity already exists: {command.entity_id}", {"entity_id": command.entity_id})
        entity_type = str(command.payload.get("entity_type", "")).strip()
        name = str(command.payload.get("name", "")).strip()
        metadata = command.payload.get("metadata", {})
        if not command.entity_id or not entity_type or not name or not isinstance(metadata, dict):
            raise WorldCommandError("entity_invalid", "entity payload is invalid", {"entity_id": command.entity_id})
        entity = Entity(
            id=command.entity_id, type=entity_type, name=name,
            description=str(command.payload.get("description", "")),
            current_status=str(command.payload.get("current_status", "")),
            updated_at=float(command.sim_time), metadata=dict(metadata), version=0,
        )
        self._upsert_entity_conn(conn, entity, preserve_version=True)
        self._init_default_resources_conn(conn, entity.id, entity.type)
        return {"entity_id": entity.id, "entity_type": entity.type, "name": entity.name, "version": 0}

    def _reduce_move_entity(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        row = self._require_entity(conn, command.entity_id)
        self._check_version(command.expected_version, int(row["version"] or 0), target="entity", details={"entity_id": command.entity_id})
        metadata = self._entity_metadata(row)
        changed = False
        for key in ("x", "y"):
            if key in command.payload:
                metadata[key] = self._finite_number(command.payload[key], key)
                changed = True
        for key in ("region", "location_id"):
            if key in command.payload:
                metadata[key] = str(command.payload[key])
                changed = True
        if not changed:
            raise WorldCommandError("location_invalid", "move_entity requires a location field")
        conn.execute(
            "UPDATE entities SET metadata=?,updated_at=?,version=version+1 WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), float(command.sim_time), command.entity_id),
        )
        return {"entity_id": command.entity_id, "metadata": metadata, "version": int(row["version"] or 0) + 1}

    def _reduce_transform_entity(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        row = self._require_entity(conn, command.entity_id)
        self._check_version(command.expected_version, int(row["version"] or 0), target="entity", details={"entity_id": command.entity_id})
        metadata = self._entity_metadata(row)
        patch = command.payload.get("metadata_patch", {})
        if patch is not None and not isinstance(patch, dict):
            raise WorldCommandError("metadata_invalid", "metadata_patch must be an object")
        metadata.update(dict(patch or {}))
        values = {
            "type": str(command.payload.get("new_type", row["type"])),
            "name": str(command.payload.get("new_name", row["name"])),
            "description": str(command.payload.get("new_description", row["description"] or "")),
            "current_status": str(command.payload.get("new_status", row["current_status"] or "")),
        }
        if not values["type"] or not values["name"]:
            raise WorldCommandError("entity_invalid", "transformed entity requires type and name")
        conn.execute(
            """UPDATE entities SET type=?,name=?,description=?,current_status=?,metadata=?,updated_at=?,version=version+1 WHERE id=?""",
            (values["type"], values["name"], values["description"], values["current_status"],
             json.dumps(metadata, ensure_ascii=False, sort_keys=True), float(command.sim_time), command.entity_id),
        )
        return {"entity_id": command.entity_id, **values, "metadata": metadata, "version": int(row["version"] or 0) + 1}

    def _reduce_remove_entity(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        row = self._require_entity(conn, command.entity_id)
        self._check_version(command.expected_version, int(row["version"] or 0), target="entity", details={"entity_id": command.entity_id})
        conn.execute("DELETE FROM inventory WHERE entity_id=?", (command.entity_id,))
        conn.execute("DELETE FROM entity_values WHERE entity_id=?", (command.entity_id,))
        conn.execute("DELETE FROM entity_relations WHERE parent_id=? OR child_id=?", (command.entity_id, command.entity_id))
        conn.execute("DELETE FROM ongoing_events WHERE entity_id=?", (command.entity_id,))
        conn.execute("DELETE FROM entities WHERE id=?", (command.entity_id,))
        return {"entity_id": command.entity_id, "removed": True, "previous_type": str(row["type"]), "previous_name": str(row["name"])}

    def _reduce_modify_relationship(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        payload = dict(command.payload)
        operation = str(payload.pop("operation", "upsert"))
        parent_id = str(payload.get("parent_id", ""))
        child_id = str(payload.get("child_id", ""))
        rel_type = str(payload.get("rel_type", ""))
        self._require_entity(conn, parent_id)
        self._require_entity(conn, child_id)
        if operation == "remove":
            definition = self.relationship_catalog.get(rel_type)
            if definition is None:
                raise WorldCommandError("relationship_type_not_found", f"unknown relationship type: {rel_type}")
            if definition.direction == "undirected" and child_id < parent_id:
                parent_id, child_id = child_id, parent_id
            cur = conn.execute("DELETE FROM entity_relations WHERE parent_id=? AND child_id=? AND rel_type=?", (parent_id, child_id, rel_type))
            return {"parent_id": parent_id, "child_id": child_id, "rel_type": rel_type, "removed": cur.rowcount > 0}
        if operation != "upsert":
            raise WorldCommandError("relationship_operation_invalid", "relationship operation is invalid")
        try:
            relation = self.relationship_catalog.normalize_relation(payload, "command.payload")
        except RelationshipContractError as exc:
            raise WorldCommandError(exc.code, str(exc), {"path": exc.path}) from exc
        self._write_relation_conn(conn, relation)
        return {**relation, "operation": "upsert"}

    @staticmethod
    def _load_gameplay_effect_state_conn(conn: sqlite3.Connection) -> tuple[dict[str, Any], int]:
        row = conn.execute("SELECT value_json,version FROM gameplay_effect_state WHERE state_key='runtime'").fetchone()
        if row is None:
            return empty_gameplay_effect_state(), 0
        try:
            value = json.loads(row["value_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            value = {}
        return normalize_gameplay_effect_state(value), int(row["version"] or 0)

    def _reduce_gameplay_effect(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        state, version = self._load_gameplay_effect_state_conn(conn)
        next_state, result = reduce_gameplay_effect_state(
            state, command_type=command.command_type.value, payload=command.payload,
            sim_time=float(command.sim_time), command_id=command.command_id,
        )
        conn.execute(
            """INSERT INTO gameplay_effect_state(state_key,value_json,version,updated_at) VALUES('runtime',?,?,?)
               ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,version=excluded.version,updated_at=excluded.updated_at""",
            (json.dumps(next_state, ensure_ascii=False, sort_keys=True), version + 1, float(command.sim_time)),
        )
        return {**result, "state_version": version + 1}

    def get_gameplay_effect_state(self) -> dict[str, Any]:
        with closing(self._conn()) as conn:
            state, _ = self._load_gameplay_effect_state_conn(conn)
        return state

    def _reduce_update_status(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        row = self._require_entity(conn, command.entity_id)
        self._check_version(command.expected_version, int(row["version"] or 0), target="entity", details={"entity_id": command.entity_id})
        new_status = str(command.payload.get("new_status", ""))
        conn.execute(
            "UPDATE entities SET current_status=?,updated_at=?,version=version+1 WHERE id=?",
            (new_status, float(command.sim_time), command.entity_id),
        )
        return {"entity_id": command.entity_id, "new_status": new_status, "version": int(row["version"] or 0) + 1}

    def _mutate_resource_conn(
        self, conn: sqlite3.Connection, command: WorldCommand, *, entity_id: str,
        resource_name: str, mode: str, value: Any, expected_version: int | None = None,
        reason: str = "", sequence: int = 0,
    ) -> dict[str, Any]:
        self._require_entity(conn, entity_id)
        definition = self._resource_definition_conn(conn, resource_name)
        numeric = self._finite_number(value, "quantity" if mode == "set" else "quantity_delta")
        kind = str(definition["kind"])
        if kind == "asset":
            row = conn.execute(
                "SELECT quantity,description,version FROM inventory WHERE entity_id=? AND item_name=?",
                (entity_id, resource_name),
            ).fetchone()
            old = float(row["quantity"] if row and row["quantity"] is not None else definition["default_quantity"] or 0.0)
            version = int(row["version"] or 0) if row else 0
            description = (row["description"] if row else None) or definition["unit"] or ""
            self._check_version(expected_version, version, target="inventory", details={"entity_id": entity_id, "resource_name": resource_name})
            if old == -1:
                raise WorldCommandError("resource_not_applicable", f"resource is not applicable: {resource_name}")
            new = numeric if mode == "set" else old + numeric
            minimum, maximum = definition["min_quantity"], definition["max_quantity"]
            if minimum is not None and new < float(minimum):
                raise WorldCommandError(
                    "insufficient_balance", f"insufficient {resource_name}",
                    {"entity_id": entity_id, "resource_name": resource_name, "balance": old, "requested": numeric},
                )
            if maximum is not None and new > float(maximum):
                raise WorldCommandError("resource_out_of_range", f"resource exceeds maximum: {resource_name}")
            conn.execute(
                """INSERT INTO inventory(entity_id,category,item_name,quantity,description,version)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(entity_id,category,item_name) DO UPDATE SET
                   quantity=excluded.quantity,description=excluded.description,version=inventory.version+1""",
                (entity_id, resource_name, resource_name, new, description, version + 1),
            )
            delta = new - old
            rate = float(definition["anchor_rate"] or 1.0)
            self._append_inventory_ledger(
                conn, command, entity_id=entity_id, resource_name=resource_name,
                sequence=sequence, quantity_delta=delta, anchor_rate=rate,
                balance_after=new, reason=reason,
            )
            return {
                "entity_id": entity_id, "resource_name": resource_name, "kind": kind,
                "quantity": new, "quantity_delta": delta, "version": version + 1,
                "anchor_rate": rate, "base_value": new * rate,
                "base_value_delta": delta * rate,
            }

        row = conn.execute(
            "SELECT value,description,version FROM entity_values WHERE entity_id=? AND name=?",
            (entity_id, resource_name),
        ).fetchone()
        old = float(row["value"] if row and row["value"] is not None else definition["default_quantity"] or 0.0)
        version = int(row["version"] or 0) if row else 0
        description = (row["description"] if row else None) or definition["unit"] or ""
        self._check_version(expected_version, version, target="entity_value", details={"entity_id": entity_id, "resource_name": resource_name})
        if old == -1:
            raise WorldCommandError("resource_not_applicable", f"value is not applicable: {resource_name}")
        new = numeric if mode == "set" else old + numeric
        minimum, maximum = definition["min_quantity"], definition["max_quantity"]
        if minimum is not None:
            new = max(0.0 if float(minimum) == -1 else float(minimum), new)
        if maximum is not None:
            new = min(float(maximum), new)
        conn.execute(
            """INSERT INTO entity_values(entity_id,name,kind,value,description,version)
               VALUES (?,?,?,?,?,?) ON CONFLICT(entity_id,name) DO UPDATE SET
               kind=excluded.kind,value=excluded.value,description=excluded.description,
               version=entity_values.version+1""",
            (entity_id, resource_name, kind, new, description, version + 1),
        )
        return {
            "entity_id": entity_id, "resource_name": resource_name, "kind": kind,
            "value": new, "value_delta": new - old, "version": version + 1,
        }

    def _reduce_resource(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        name = str(command.payload.get("resource_name") or command.payload.get("item_name") or "")
        mode = "set" if command.command_type == WorldCommandType.SET_RESOURCE else "adjust"
        key = "quantity" if mode == "set" else "quantity_delta"
        return self._mutate_resource_conn(
            conn, command, entity_id=command.entity_id, resource_name=name,
            mode=mode, value=command.payload.get(key), expected_version=command.expected_version,
            reason=str(command.payload.get("reason", "")),
        )

    def _append_inventory_ledger(
        self, conn: sqlite3.Connection, command: WorldCommand, *, entity_id: str,
        resource_name: str, sequence: int, quantity_delta: float, anchor_rate: float,
        balance_after: float, reason: str,
    ) -> None:
        conn.execute(
            """INSERT INTO inventory_ledger
               (entry_id,command_id,entity_id,resource_name,sequence,quantity_delta,
                anchor_rate,base_value_delta,balance_after,reason,sim_time,chain_id,metadata,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"ledger_{command.command_id}_{sequence}_{resource_name}", command.command_id,
                entity_id, resource_name, sequence, quantity_delta, anchor_rate,
                quantity_delta * anchor_rate, balance_after, reason, float(command.sim_time),
                command.chain_id, json.dumps(command.metadata, ensure_ascii=False), time.time(),
            ),
        )

    def set_inventory(
        self, entity_id: str, category: str, item_name: str, quantity: float,
        description: str = "", *, command_id: str | None = None,
        expected_version: int | None = None, chain_id: str = "", intent_id: str = "",
        sim_time: float = 0.0, reason: str = "",
    ) -> float:
        result = self.command_bus.execute_or_raise(WorldCommand(
            command_type=WorldCommandType.SET_RESOURCE,
            command_id=command_id or f"wcmd_{uuid.uuid4().hex}", entity_id=entity_id,
            expected_version=expected_version, chain_id=chain_id, intent_id=intent_id,
            sim_time=sim_time, payload={"resource_name": item_name, "quantity": quantity,
                                        "description": description, "reason": reason},
        ))
        return float(result.get("quantity", result.get("value", 0.0)))

    def update_inventory(
        self, entity_id: str, category: str, item_name: str, quantity_delta: float,
        reason: str = "", *, command_id: str | None = None,
        expected_version: int | None = None, chain_id: str = "", intent_id: str = "",
        sim_time: float = 0.0,
    ) -> float:
        result = self.command_bus.execute_or_raise(WorldCommand(
            command_type=WorldCommandType.ADJUST_RESOURCE,
            command_id=command_id or f"wcmd_{uuid.uuid4().hex}", entity_id=entity_id,
            expected_version=expected_version, chain_id=chain_id, intent_id=intent_id,
            sim_time=sim_time, payload={"resource_name": item_name,
                                        "quantity_delta": quantity_delta, "reason": reason},
        ))
        return float(result.get("quantity", result.get("value", 0.0)))

    # Relationship catalog and CRUD.

    def _restore_relationship_catalog(self) -> None:
        try:
            with closing(self._conn()) as conn, conn:
                rows = conn.execute(
                    "SELECT definition_json FROM relationship_type_catalog ORDER BY relationship_type_id"
                ).fetchall()
            documents = []
            for row in rows:
                try:
                    value = json.loads(row["definition_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(value, dict):
                    documents.append(value)
            self.relationship_catalog = RelationshipCatalog(documents)
        except (sqlite3.Error, RelationshipContractError, TypeError, ValueError):
            self.relationship_catalog = RelationshipCatalog()

    @staticmethod
    def _persist_relationship_catalog_conn(
        conn: sqlite3.Connection, catalog: RelationshipCatalog,
    ) -> None:
        conn.execute("DELETE FROM relationship_type_catalog")
        conn.executemany(
            "INSERT INTO relationship_type_catalog(relationship_type_id,definition_json) VALUES (?,?)",
            [
                (
                    item["relationship_type_id"],
                    json.dumps(item, ensure_ascii=False, sort_keys=True),
                )
                for item in catalog.definitions(include_legacy=False)
            ],
        )

    def configure_relationship_catalog(
        self, definitions: list[dict[str, Any]] | None, *, generation: int = 0,
    ) -> dict[str, Any]:
        candidate = RelationshipCatalog(definitions or [])
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._persist_relationship_catalog_conn(conn, candidate)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            self.relationship_catalog = candidate
        return candidate.public_state(generation=generation)

    def relationship_types_public_state(self, *, generation: int = 0) -> dict[str, Any]:
        return self.relationship_catalog.public_state(generation=generation)

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            decoded = json.loads(value or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _relation_from_row(
        self, row: sqlite3.Row, *, sim_time: float = 0.0,
    ) -> dict[str, Any]:
        relation = {
            "parent_id": str(row["parent_id"]),
            "child_id": str(row["child_id"]),
            "rel_type": str(row["rel_type"]),
            "strength": float(row["strength"] or 0.0),
            "visibility": str(row["visibility"] or "public"),
            "starts_at": float(row["starts_at"] or 0.0),
            "expires_at": (float(row["expires_at"]) if row["expires_at"] is not None else None),
            "values": self._json_object(row["dimension_values"]),
            "note": str(row["note"] or ""),
            "metadata": self._json_object(row["metadata"]),
        }
        relation["active"] = self.relationship_catalog.is_active(relation, sim_time)
        return relation

    @staticmethod
    def _write_relation_conn(conn: sqlite3.Connection, relation: dict[str, Any]) -> None:
        conn.execute(
            """INSERT INTO entity_relations(
                   parent_id,child_id,rel_type,note,strength,visibility,starts_at,expires_at,
                   dimension_values,metadata
               ) VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(parent_id,child_id,rel_type) DO UPDATE SET
                 note=excluded.note,strength=excluded.strength,visibility=excluded.visibility,
                 starts_at=excluded.starts_at,expires_at=excluded.expires_at,
                 dimension_values=excluded.dimension_values,metadata=excluded.metadata""",
            (
                relation["parent_id"], relation["child_id"], relation["rel_type"],
                relation.get("note", ""), relation.get("strength", 0.0),
                relation.get("visibility", "public"), relation.get("starts_at", 0.0),
                relation.get("expires_at"),
                json.dumps(relation.get("values", {}), ensure_ascii=False, sort_keys=True),
                json.dumps(relation.get("metadata", {}), ensure_ascii=False, sort_keys=True),
            ),
        )

    def add_relation(
        self,
        parent_id: str,
        child_id: str,
        rel_type: str,
        note: str = "",
        *,
        strength: float | None = None,
        visibility: str | None = None,
        starts_at: float = 0.0,
        expires_at: float | None = None,
        values: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or overwrite an edge under the active relationship contract."""
        raw: dict[str, Any] = {
            "parent_id": parent_id, "child_id": child_id, "rel_type": rel_type,
            "note": note, "starts_at": starts_at,
            "values": values or {}, "metadata": metadata or {},
        }
        if strength is not None:
            raw["strength"] = strength
        if visibility is not None:
            raw["visibility"] = visibility
        if expires_at is not None:
            raw["expires_at"] = expires_at
        relation = self.relationship_catalog.normalize_relation(raw)
        with self._write_lock, self._conn() as conn:
            self._require_entity(conn, relation["parent_id"])
            self._require_entity(conn, relation["child_id"])
            self._write_relation_conn(conn, relation)
        return relation

    def remove_relation(
        self, parent_id: str, child_id: str, rel_type: str,
    ) -> bool:
        """Delete an edge; undirected endpoints are canonicalized first."""
        definition = self.relationship_catalog.get(rel_type)
        if definition is None:
            return False
        if definition.direction == "undirected" and child_id < parent_id:
            parent_id, child_id = child_id, parent_id
        with self._write_lock, self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM entity_relations WHERE parent_id=? AND child_id=? AND rel_type=?",
                (parent_id, child_id, rel_type),
            )
        return cur.rowcount > 0

    def get_parents(self, child_id: str, *, sim_time: float = 0.0) -> list[dict[str, Any]]:
        """Return incoming directed edges. Legacy field names remain available."""
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM entity_relations WHERE child_id=? ORDER BY parent_id,rel_type",
                (child_id,),
            ).fetchall()
        return [
            {**self._relation_from_row(row, sim_time=sim_time), "parent_id": row["parent_id"]}
            for row in rows
            if (self.relationship_catalog.get(str(row["rel_type"])) or self.relationship_catalog.get("subordinate")).direction == "directed"
        ]

    def get_children(self, parent_id: str, *, sim_time: float = 0.0) -> list[dict[str, Any]]:
        """Return outgoing directed edges. Legacy field names remain available."""
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM entity_relations WHERE parent_id=? ORDER BY child_id,rel_type",
                (parent_id,),
            ).fetchall()
        return [
            {**self._relation_from_row(row, sim_time=sim_time), "child_id": row["child_id"]}
            for row in rows
            if (self.relationship_catalog.get(str(row["rel_type"])) or self.relationship_catalog.get("subordinate")).direction == "directed"
        ]

    def get_neighbors(self, entity_id: str, *, sim_time: float = 0.0) -> list[dict[str, Any]]:
        """Return both endpoints of undirected edges adjacent to an entity."""
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM entity_relations WHERE parent_id=? OR child_id=? ORDER BY rel_type,parent_id,child_id",
                (entity_id, entity_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            definition = self.relationship_catalog.get(str(row["rel_type"]))
            if definition is None or definition.direction != "undirected":
                continue
            relation = self._relation_from_row(row, sim_time=sim_time)
            relation["neighbor_id"] = (
                relation["child_id"] if relation["parent_id"] == entity_id else relation["parent_id"]
            )
            result.append(relation)
        return result

    def get_siblings(self, entity_id: str) -> list[dict[str, str]]:
        """Return entities sharing the same directed parent and relation type."""
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                """SELECT r2.child_id AS sibling_id, r.parent_id AS via_parent_id, r.rel_type
                   FROM entity_relations r
                   JOIN entity_relations r2
                     ON r.parent_id=r2.parent_id AND r.rel_type=r2.rel_type
                   WHERE r.child_id=? AND r2.child_id!=?""",
                (entity_id, entity_id),
            ).fetchall()
        return [
            {"sibling_id": r["sibling_id"], "via_parent_id": r["via_parent_id"], "rel_type": r["rel_type"]}
            for r in rows
            if (self.relationship_catalog.get(str(r["rel_type"])) or self.relationship_catalog.get("subordinate")).direction == "directed"
        ]

    def get_all_relations(self, *, sim_time: float = 0.0) -> list[dict[str, Any]]:
        """Export the complete graph including contract fields and active status."""
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT * FROM entity_relations ORDER BY rel_type,parent_id,child_id"
            ).fetchall()
        return [self._relation_from_row(row, sim_time=sim_time) for row in rows]

    # OngoingEvent CRUD.

    def _row_to_event(self, row: sqlite3.Row) -> OngoingEvent:
        try:
            expected = json.loads(row["expected_result"] or "{}")
        except (json.JSONDecodeError, TypeError):
            expected = {}
        return OngoingEvent(
            event_id=row["event_id"], entity_id=row["entity_id"],
            event_type=row["event_type"] or "state_change",
            description=row["description"] or "",
            start_time=float(row["start_time"] or 0.0),
            end_time=float(row["end_time"] or 0.0), expected_result=expected,
            source_action_id=row["source_action_id"] or "",
        )

    def _reduce_create_event(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        payload = command.payload
        event_id = str(payload.get("event_id") or f"evt_{uuid.uuid4().hex[:10]}")
        entity_id = command.entity_id or str(payload.get("entity_id") or "")
        self._require_entity(conn, entity_id)
        start_time = self._finite_number(payload.get("start_time", command.sim_time), "start_time")
        end_time = self._finite_number(payload.get("end_time", start_time), "end_time")
        expected = payload.get("expected_result") or {}
        if not isinstance(expected, dict):
            raise WorldCommandError("invalid_event", "expected_result must be an object")
        conn.execute(
            """INSERT INTO ongoing_events
               (event_id,entity_id,event_type,description,start_time,end_time,
                expected_result,source_action_id)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                 entity_id=excluded.entity_id,event_type=excluded.event_type,
                 description=excluded.description,start_time=excluded.start_time,
                 end_time=excluded.end_time,expected_result=excluded.expected_result,
                 source_action_id=excluded.source_action_id""",
            (
                event_id, entity_id, str(payload.get("event_type") or "state_change"),
                str(payload.get("description") or ""), start_time, end_time,
                json.dumps(expected, ensure_ascii=False),
                str(payload.get("source_action_id") or command.chain_id),
            ),
        )
        return {"event_id": event_id, "entity_id": entity_id, "end_time": end_time}

    def _reduce_update_event(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        event_id = str(command.payload.get("event_id") or "")
        row = conn.execute("SELECT * FROM ongoing_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise WorldCommandError("event_not_found", f"event does not exist: {event_id}", {"event_id": event_id})
        allowed = {"description", "end_time", "expected_result", "event_type"}
        changes = {key: value for key, value in command.payload.items() if key in allowed and value is not None}
        if "end_time" in changes:
            changes["end_time"] = self._finite_number(changes["end_time"], "end_time")
        if "expected_result" in changes:
            if not isinstance(changes["expected_result"], dict):
                raise WorldCommandError("invalid_event", "expected_result must be an object")
            changes["expected_result"] = json.dumps(changes["expected_result"], ensure_ascii=False)
        if changes:
            assignments = ",".join(f"{key}=?" for key in changes)
            conn.execute(
                f"UPDATE ongoing_events SET {assignments} WHERE event_id=?",
                (*changes.values(), event_id),
            )
        return {"event_id": event_id, "updated_fields": list(changes)}

    def _reduce_delete_event(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        event_id = str(command.payload.get("event_id") or "")
        cur = conn.execute("DELETE FROM ongoing_events WHERE event_id=?", (event_id,))
        if cur.rowcount == 0:
            raise WorldCommandError("event_not_found", f"event does not exist: {event_id}", {"event_id": event_id})
        return {"event_id": event_id, "deleted": True}

    def _reduce_apply_matured_event(self, conn: sqlite3.Connection, command: WorldCommand) -> dict[str, Any]:
        event_id = str(command.payload.get("event_id") or "")
        row = conn.execute("SELECT * FROM ongoing_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise WorldCommandError("event_not_found", f"event does not exist: {event_id}", {"event_id": event_id})
        event = self._row_to_event(row)
        if event.end_time > float(command.sim_time):
            raise WorldCommandError(
                "event_not_matured", f"event has not matured: {event_id}",
                {"event_id": event_id, "end_time": event.end_time, "sim_time": command.sim_time},
            )
        expected = event.expected_result or {}
        changes: list[dict[str, Any]] = []
        new_status = expected.get("new_status")
        if isinstance(new_status, str) and new_status:
            entity = self._require_entity(conn, event.entity_id)
            conn.execute(
                "UPDATE entities SET current_status=?,updated_at=?,version=version+1 WHERE id=?",
                (new_status, float(command.sim_time), event.entity_id),
            )
            changes.append({
                "type": "status", "new_status": new_status,
                "version": int(entity["version"] or 0) + 1,
            })
        deltas = expected.get("inventory_deltas") or []
        if not isinstance(deltas, list):
            raise WorldCommandError("invalid_event", "inventory_deltas must be an array")
        for sequence, delta in enumerate(deltas):
            if not isinstance(delta, dict):
                raise WorldCommandError("invalid_event", "inventory delta must be an object")
            resource = str(delta.get("resource") or delta.get("resource_name") or "")
            changed = self._mutate_resource_conn(
                conn, command, entity_id=event.entity_id, resource_name=resource,
                mode="adjust", value=delta.get("delta"),
                reason=str(delta.get("reason") or event.description), sequence=sequence,
            )
            changes.append({"type": "resource", **changed})
        conn.execute("DELETE FROM ongoing_events WHERE event_id=?", (event_id,))
        return {
            "event_id": event_id, "entity_id": event.entity_id,
            "description": event.description, "changes": changes, "deleted": True,
        }

    def add_ongoing_event(
        self, event: OngoingEvent, *, command_id: str | None = None,
        chain_id: str = "", intent_id: str = "",
    ) -> str:
        result = self.command_bus.execute_or_raise(WorldCommand(
            command_type=WorldCommandType.CREATE_EVENT,
            command_id=command_id or f"wcmd_{uuid.uuid4().hex}",
            entity_id=event.entity_id, chain_id=chain_id or event.source_action_id,
            intent_id=intent_id, sim_time=event.start_time,
            payload={
                "event_id": event.event_id, "event_type": event.event_type,
                "description": event.description, "start_time": event.start_time,
                "end_time": event.end_time, "expected_result": event.expected_result,
                "source_action_id": event.source_action_id,
            },
        ))
        return str(result["event_id"])

    def get_ongoing_event(self, event_id: str) -> OngoingEvent | None:
        with closing(self._conn()) as conn, conn:
            row = conn.execute("SELECT * FROM ongoing_events WHERE event_id=?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def list_ongoing_events(self, entity_id: str | None = None) -> list[OngoingEvent]:
        with closing(self._conn()) as conn, conn:
            if entity_id:
                rows = conn.execute(
                    "SELECT * FROM ongoing_events WHERE entity_id=? ORDER BY end_time", (entity_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM ongoing_events ORDER BY end_time").fetchall()
        return [self._row_to_event(row) for row in rows]

    def update_ongoing_event(
        self, event_id: str, *, description: str | None = None,
        end_time: float | None = None, expected_result: dict[str, Any] | None = None,
        event_type: str | None = None, command_id: str | None = None,
        chain_id: str = "", intent_id: str = "", sim_time: float = 0.0,
    ) -> bool:
        payload: dict[str, Any] = {"event_id": event_id}
        for key, value in {
            "description": description, "end_time": end_time,
            "expected_result": expected_result, "event_type": event_type,
        }.items():
            if value is not None:
                payload[key] = value
        result = self.command_bus.execute(WorldCommand(
            command_type=WorldCommandType.UPDATE_EVENT,
            command_id=command_id or f"wcmd_{uuid.uuid4().hex}", chain_id=chain_id,
            intent_id=intent_id, sim_time=sim_time, payload=payload,
        ))
        if not result.ok and result.error_code == "event_not_found":
            return False
        result.require_ok()
        return True

    def delete_ongoing_event(
        self, event_id: str, *, command_id: str | None = None,
        chain_id: str = "", intent_id: str = "", sim_time: float = 0.0,
    ) -> bool:
        result = self.command_bus.execute(WorldCommand(
            command_type=WorldCommandType.DELETE_EVENT,
            command_id=command_id or f"wcmd_{uuid.uuid4().hex}", chain_id=chain_id,
            intent_id=intent_id, sim_time=sim_time, payload={"event_id": event_id},
        ))
        if not result.ok and result.error_code == "event_not_found":
            return False
        result.require_ok()
        return True

    def get_matured_event_ids(self, sim_time: float) -> list[str]:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                "SELECT event_id FROM ongoing_events WHERE end_time<=? ORDER BY end_time,event_id",
                (sim_time,),
            ).fetchall()
        return [str(row["event_id"]) for row in rows]

    def apply_matured_event(self, event_id: str, sim_time: float) -> WorldCommandResult:
        return self.command_bus.execute(WorldCommand(
            command_type=WorldCommandType.APPLY_MATURED_EVENT,
            command_id=f"mature:{event_id}", sim_time=sim_time,
            payload={"event_id": event_id}, metadata={"source": "world_tick"},
        ))

    def apply_matured_events(self, sim_time: float) -> list[WorldCommandResult]:
        commands = [
            WorldCommand(
                command_type=WorldCommandType.APPLY_MATURED_EVENT,
                command_id=f"mature:{event_id}",
                sim_time=sim_time,
                payload={"event_id": event_id},
                metadata={"source": "world_tick"},
            )
            for event_id in self.get_matured_event_ids(sim_time)
        ]
        return self.command_bus.execute_batch(commands, atomic=False).results

    def pop_matured_events(self, sim_time: float) -> list[OngoingEvent]:
        """Compatibility read: return matured events without deleting or applying them."""
        ids = self.get_matured_event_ids(sim_time)
        return [event for event_id in ids if (event := self.get_ongoing_event(event_id))]

    # Bulk operations.

    @staticmethod
    def _upsert_resource_definition_conn(
        conn: sqlite3.Connection, definition: ResourceDefinition,
    ) -> None:
        if definition.kind not in {"asset", "metric", "parameter"}:
            raise ValueError(f"invalid resource kind: {definition.kind}")
        rate = definition.anchor_rate
        if definition.kind == "asset":
            rate = 1.0 if rate is None else float(rate)
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"invalid anchor_rate for {definition.name}")
        else:
            rate = None
        conn.execute(
            """INSERT INTO resource_catalog
               (name,unit,kind,default_quantity,min_quantity,max_quantity,
                anchor_rate,metadata,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 unit=excluded.unit,kind=excluded.kind,
                 default_quantity=excluded.default_quantity,
                 min_quantity=excluded.min_quantity,max_quantity=excluded.max_quantity,
                 anchor_rate=excluded.anchor_rate,metadata=excluded.metadata,
                 updated_at=excluded.updated_at""",
            (
                definition.name, definition.unit, definition.kind,
                float(definition.default_quantity), definition.min_quantity,
                definition.max_quantity, rate,
                json.dumps(definition.metadata, ensure_ascii=False), time.time(),
            ),
        )

    def upsert_resource_definition(self, definition: ResourceDefinition) -> None:
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._upsert_resource_definition_conn(conn, definition)
                for entity in conn.execute("SELECT id,type FROM entities").fetchall():
                    self._init_default_resources_conn(conn, entity["id"], entity["type"])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _write_loaded_item_conn(
        self, conn: sqlite3.Connection, item: InventoryItem, *, preserve_version: bool,
    ) -> None:
        self._require_entity(conn, item.entity_id)
        definition = conn.execute(
            "SELECT * FROM resource_catalog WHERE name=?", (item.item_name,),
        ).fetchone()
        if definition is not None and definition["kind"] != "asset":
            version_sql = "excluded.version" if preserve_version else "entity_values.version + 1"
            conn.execute(
                f"""INSERT INTO entity_values(entity_id,name,kind,value,description,version)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(entity_id,name) DO UPDATE SET
                    kind=excluded.kind,value=excluded.value,description=excluded.description,
                    version={version_sql}""",
                (
                    item.entity_id, item.item_name, definition["kind"], item.quantity,
                    item.description or definition["unit"] or "", max(0, int(item.version)),
                ),
            )
            conn.execute(
                "DELETE FROM inventory WHERE entity_id=? AND item_name=?",
                (item.entity_id, item.item_name),
            )
            return
        version_sql = "excluded.version" if preserve_version else "inventory.version + 1"
        conn.execute(
            f"""INSERT INTO inventory
                (entity_id,category,item_name,quantity,description,version)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(entity_id,category,item_name) DO UPDATE SET
                  quantity=excluded.quantity,description=excluded.description,
                  version={version_sql}""",
            (
                item.entity_id, item.category or item.item_name, item.item_name,
                item.quantity, item.description, max(0, int(item.version)),
            ),
        )

    @staticmethod
    def _write_loaded_value_conn(
        conn: sqlite3.Connection, value: EntityValue, *, preserve_version: bool,
    ) -> None:
        version_sql = "excluded.version" if preserve_version else "entity_values.version + 1"
        conn.execute(
            f"""INSERT INTO entity_values(entity_id,name,kind,value,description,version)
                VALUES (?,?,?,?,?,?) ON CONFLICT(entity_id,name) DO UPDATE SET
                  kind=excluded.kind,value=excluded.value,description=excluded.description,
                  version={version_sql}""",
            (
                value.entity_id, value.name, value.kind, value.value,
                value.description, max(0, int(value.version)),
            ),
        )

    def _bulk_load_conn(
        self, conn: sqlite3.Connection, entities: list[Entity],
        inventory: list[InventoryItem], relations: list[dict[str, str]] | None,
        ongoing_events: list[OngoingEvent] | None,
        entity_values: list[EntityValue] | None,
        resource_definitions: list[ResourceDefinition] | None,
        *, preserve_versions: bool,
        relationship_catalog: RelationshipCatalog | None = None,
    ) -> tuple[int, int, int]:
        for definition in resource_definitions or []:
            self._upsert_resource_definition_conn(conn, definition)
        entity_types: dict[str, str] = {}
        for entity in entities:
            entity_types[entity.id] = entity.type
            self._upsert_entity_conn(conn, entity, preserve_version=preserve_versions)
        known_ids = {str(row["id"]) for row in conn.execute("SELECT id FROM entities")}
        for item in inventory:
            if item.entity_id not in known_ids:
                raise ValueError(f"inventory references missing entity: {item.entity_id}")
            self._write_loaded_item_conn(conn, item, preserve_version=preserve_versions)
        for value in entity_values or []:
            if value.entity_id not in known_ids:
                raise ValueError(f"entity value references missing entity: {value.entity_id}")
            definition = conn.execute(
                "SELECT kind FROM resource_catalog WHERE name=?", (value.name,),
            ).fetchone()
            if definition is not None and definition["kind"] == "asset":
                raise ValueError(f"asset cannot be loaded as entity value: {value.name}")
            self._write_loaded_value_conn(conn, value, preserve_version=preserve_versions)
        rel_count = 0
        catalog = relationship_catalog or self.relationship_catalog
        for index, raw_relation in enumerate(relations or []):
            relation = catalog.normalize_relation(raw_relation, f"relations[{index}]")
            parent_id, child_id = relation["parent_id"], relation["child_id"]
            if parent_id not in known_ids or child_id not in known_ids:
                raise ValueError(f"relation references missing entity: {parent_id}->{child_id}")
            self._write_relation_conn(conn, relation)
            rel_count += 1
        for entity in entities:
            self._init_default_resources_conn(conn, entity.id, entity_types[entity.id])
        for event in ongoing_events or []:
            if event.entity_id not in known_ids:
                raise ValueError(f"event references missing entity: {event.entity_id}")
            conn.execute(
                """INSERT INTO ongoing_events
                   (event_id,entity_id,event_type,description,start_time,end_time,
                    expected_result,source_action_id)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                     entity_id=excluded.entity_id,event_type=excluded.event_type,
                     description=excluded.description,start_time=excluded.start_time,
                     end_time=excluded.end_time,expected_result=excluded.expected_result,
                     source_action_id=excluded.source_action_id""",
                (
                    event.event_id, event.entity_id, event.event_type,
                    event.description, event.start_time, event.end_time,
                    json.dumps(event.expected_result, ensure_ascii=False),
                    event.source_action_id,
                ),
            )
        return len(entities), len(inventory) + len(entity_values or []), rel_count

    def apply_dlc_schema_update(
        self,
        entities: list[Entity],
        inventory: list[InventoryItem],
        relations: list[dict[str, str]] | None = None,
        ongoing_events: list[OngoingEvent] | None = None,
        entity_values: list[EntityValue] | None = None,
        resource_definitions: list[ResourceDefinition] | None = None,
        officials: list[Any] | None = None,
        replace_official_ids: set[str] | None = None,
        relationship_definitions: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """Apply additive DLC schema changes without replaying dynamic world state.

        Existing entity status, inventory quantities, entity values, event progress, and
        versions are preserved. Resource anchor changes and resource kind changes are
        rejected because either would reinterpret live balances.
        """
        candidate_relationship_catalog = (
            RelationshipCatalog(relationship_definitions)
            if relationship_definitions is not None else self.relationship_catalog
        )
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                resources_added = 0
                resources_updated = 0
                for definition in resource_definitions or []:
                    existing = conn.execute(
                        "SELECT kind,anchor_rate FROM resource_catalog WHERE name=?",
                        (definition.name,),
                    ).fetchone()
                    if existing is not None:
                        if existing["kind"] != definition.kind:
                            raise ValueError(
                                f"resource kind change requires migration: {definition.name}"
                            )
                        if definition.kind == "asset":
                            old_rate = float(existing["anchor_rate"] or 1.0)
                            new_rate = float(definition.anchor_rate or 1.0)
                            if not math.isclose(old_rate, new_rate, rel_tol=0.0, abs_tol=1e-12):
                                raise ValueError(
                                    f"anchor_rate change requires migration: {definition.name}"
                                )
                        resources_updated += 1
                    else:
                        resources_added += 1
                    self._upsert_resource_definition_conn(conn, definition)

                known_before = {
                    str(row["id"]) for row in conn.execute("SELECT id FROM entities")
                }
                entities_added = 0
                entities_updated = 0
                for entity in entities:
                    if entity.id in known_before:
                        conn.execute(
                            """UPDATE entities SET type=?,name=?,description=?,metadata=?
                               WHERE id=?""",
                            (
                                entity.type, entity.name, entity.description,
                                json.dumps(entity.metadata, ensure_ascii=False), entity.id,
                            ),
                        )
                        entities_updated += 1
                    else:
                        conn.execute(
                            """INSERT INTO entities
                               (id,type,name,description,current_status,updated_at,metadata,version)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            (
                                entity.id, entity.type, entity.name, entity.description,
                                entity.current_status, entity.updated_at,
                                json.dumps(entity.metadata, ensure_ascii=False),
                                max(0, int(entity.version)),
                            ),
                        )
                        known_before.add(entity.id)
                        entities_added += 1

                inventory_added = 0
                values_added = 0
                for item in inventory:
                    self._require_entity(conn, item.entity_id)
                    definition = conn.execute(
                        "SELECT kind,unit FROM resource_catalog WHERE name=?",
                        (item.item_name,),
                    ).fetchone()
                    if definition is not None and definition["kind"] != "asset":
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO entity_values
                               (entity_id,name,kind,value,description,version)
                               VALUES (?,?,?,?,?,?)""",
                            (
                                item.entity_id, item.item_name, definition["kind"],
                                item.quantity, item.description or definition["unit"] or "",
                                max(0, int(item.version)),
                            ),
                        )
                        values_added += max(0, cursor.rowcount)
                    else:
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO inventory
                               (entity_id,category,item_name,quantity,description,version)
                               VALUES (?,?,?,?,?,?)""",
                            (
                                item.entity_id, item.category or item.item_name, item.item_name,
                                item.quantity, item.description, max(0, int(item.version)),
                            ),
                        )
                        inventory_added += max(0, cursor.rowcount)

                for value in entity_values or []:
                    self._require_entity(conn, value.entity_id)
                    definition = conn.execute(
                        "SELECT kind FROM resource_catalog WHERE name=?", (value.name,),
                    ).fetchone()
                    if definition is not None and definition["kind"] == "asset":
                        raise ValueError(f"asset cannot be loaded as entity value: {value.name}")
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO entity_values
                           (entity_id,name,kind,value,description,version)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            value.entity_id, value.name, value.kind, value.value,
                            value.description, max(0, int(value.version)),
                        ),
                    )
                    values_added += max(0, cursor.rowcount)

                relations_upserted = 0
                known_ids = {
                    str(row["id"]) for row in conn.execute("SELECT id FROM entities")
                }
                for index, raw_relation in enumerate(relations or []):
                    relation = candidate_relationship_catalog.normalize_relation(
                        raw_relation, f"relations[{index}]"
                    )
                    parent_id = relation["parent_id"]
                    child_id = relation["child_id"]
                    if parent_id not in known_ids or child_id not in known_ids:
                        raise ValueError(
                            f"relation references missing entity: {parent_id}->{child_id}"
                        )
                    self._write_relation_conn(conn, relation)
                    relations_upserted += 1
                if relationship_definitions is not None:
                    self._persist_relationship_catalog_conn(conn, candidate_relationship_catalog)

                events_added = 0
                for event in ongoing_events or []:
                    if event.entity_id not in known_ids:
                        raise ValueError(
                            f"event references missing entity: {event.entity_id}"
                        )
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO ongoing_events
                           (event_id,entity_id,event_type,description,start_time,end_time,
                            expected_result,source_action_id)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            event.event_id, event.entity_id, event.event_type,
                            event.description, event.start_time, event.end_time,
                            json.dumps(event.expected_result, ensure_ascii=False),
                            event.source_action_id,
                        ),
                    )
                    events_added += max(0, cursor.rowcount)

                official_rows = [
                    (
                        official.id, official.name, official.rank, official.location_id,
                        official.role, official.agent_type, official.superior_id,
                        official.personality_seed, int(official.enabled), official.extra_prompt,
                    )
                    for official in officials or []
                ]
                next_official_ids = {str(official.id) for official in officials or []}
                stale_official_ids = sorted(
                    set(replace_official_ids or set()) - next_official_ids
                )
                if stale_official_ids:
                    conn.executemany(
                        "DELETE FROM officials WHERE id=?",
                        [(official_id,) for official_id in stale_official_ids],
                    )
                if official_rows:
                    conn.executemany(
                        """INSERT OR REPLACE INTO officials
                           (id,name,rank,location_id,role,agent_type,superior_id,
                            personality_seed,enabled,extra_prompt)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        official_rows,
                    )

                conn.commit()
                if relationship_definitions is not None:
                    self.relationship_catalog = candidate_relationship_catalog
                return {
                    "resources_added": resources_added,
                    "resources_updated": resources_updated,
                    "entities_added": entities_added,
                    "entities_updated": entities_updated,
                    "inventory_added": inventory_added,
                    "entity_values_added": values_added,
                    "relations_upserted": relations_upserted,
                    "ongoing_events_added": events_added,
                    "officials_upserted": len(official_rows),
                    "officials_removed": len(stale_official_ids),
                }
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def bulk_load(
        self, entities: list[Entity], inventory: list[InventoryItem],
        relations: list[dict[str, str]] | None = None,
        ongoing_events: list[OngoingEvent] | None = None,
        entity_values: list[EntityValue] | None = None,
        resource_definitions: list[ResourceDefinition] | None = None,
        relationship_definitions: list[dict[str, Any]] | None = None,
    ) -> tuple[int, int, int]:
        candidate_relationship_catalog = (
            RelationshipCatalog(relationship_definitions)
            if relationship_definitions is not None else self.relationship_catalog
        )
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = self._bulk_load_conn(
                    conn, entities, inventory, relations, ongoing_events,
                    entity_values, resource_definitions, preserve_versions=False,
                    relationship_catalog=candidate_relationship_catalog,
                )
                if relationship_definitions is not None:
                    self._persist_relationship_catalog_conn(conn, candidate_relationship_catalog)
                conn.commit()
                if relationship_definitions is not None:
                    self.relationship_catalog = candidate_relationship_catalog
                return result
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def restore_all(self, data: dict[str, Any]) -> tuple[int, int, int]:
        entities = [Entity(
            id=e["id"], type=e["type"], name=e["name"],
            description=e.get("description", ""), current_status=e.get("current_status", ""),
            updated_at=e.get("updated_at", 0.0), metadata=e.get("metadata", {}),
            version=e.get("version", 0),
        ) for e in data.get("entities", [])]
        inventory = [InventoryItem(
            entity_id=i["entity_id"], category=i.get("category", i["item_name"]),
            item_name=i["item_name"], quantity=i.get("quantity", 0.0),
            description=i.get("description", ""), version=i.get("version", 0),
            anchor_rate=i.get("anchor_rate", 1.0),
        ) for i in data.get("inventory", [])]
        values = [EntityValue(
            entity_id=v["entity_id"], name=v["name"], kind=v["kind"],
            value=v.get("value", 0.0), description=v.get("description", ""),
            version=v.get("version", 0),
        ) for v in data.get("entity_values", [])]
        events = [OngoingEvent(
            event_id=e["event_id"], entity_id=e["entity_id"],
            event_type=e.get("event_type", "state_change"),
            description=e.get("description", ""), start_time=e.get("start_time", 0.0),
            end_time=e.get("end_time", 0.0), expected_result=e.get("expected_result", {}),
            source_action_id=e.get("source_action_id", ""),
        ) for e in data.get("ongoing_events", [])]
        catalog = [ResourceDefinition(
            name=r["name"], unit=r.get("unit", ""), kind=r.get("kind", "asset"),
            default_quantity=r.get("default_quantity", 0.0),
            min_quantity=r.get("min_quantity"), max_quantity=r.get("max_quantity"),
            anchor_rate=r.get("anchor_rate"), metadata=r.get("metadata", {}),
        ) for r in data.get("resource_catalog", [])]
        relationship_catalog = RelationshipCatalog(data.get("relationship_types", []))
        with self._write_lock:
            conn = self._conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for table in (
                    "ongoing_events", "entity_relations", "relationship_type_catalog",
                    "inventory", "entity_values", "inventory_ledger", "world_commands",
                    "gameplay_effect_state", "entities",
                ):
                    conn.execute(f"DELETE FROM {table}")
                if catalog:
                    conn.execute("DELETE FROM resource_catalog")
                result = self._bulk_load_conn(
                    conn, entities, inventory, list(data.get("relations", [])),
                    events, values, catalog, preserve_versions=True,
                    relationship_catalog=relationship_catalog,
                )
                self._persist_relationship_catalog_conn(conn, relationship_catalog)
                gameplay_state = normalize_gameplay_effect_state(
                    data.get("gameplay_effect_state")
                )
                conn.execute(
                    """INSERT INTO gameplay_effect_state
                       (state_key,value_json,version,updated_at)
                       VALUES ('runtime',?,?,?)""",
                    (
                        json.dumps(gameplay_state, ensure_ascii=False, sort_keys=True),
                        int(data.get("gameplay_effect_state_version", 0) or 0),
                        float(data.get("gameplay_effect_state_updated_at", 0.0) or 0.0),
                    ),
                )
                for row in data.get("inventory_ledger", []):
                    conn.execute(
                        """INSERT INTO inventory_ledger
                           (entry_id,command_id,entity_id,resource_name,sequence,
                            quantity_delta,anchor_rate,base_value_delta,balance_after,
                            reason,sim_time,chain_id,metadata,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            row["entry_id"], row["command_id"], row["entity_id"],
                            row["resource_name"], row.get("sequence", 0),
                            row["quantity_delta"], row["anchor_rate"],
                            row["base_value_delta"], row["balance_after"],
                            row.get("reason", ""), row.get("sim_time", 0.0),
                            row.get("chain_id", ""),
                            json.dumps(row.get("metadata", {}), ensure_ascii=False),
                            row.get("created_at", time.time()),
                        ),
                    )
                conn.commit()
                self.relationship_catalog = relationship_catalog
                return result
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def export_all(self) -> dict[str, Any]:
        with closing(self._conn()) as conn, conn:
            entity_rows = conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
            inv_rows = conn.execute(
                """SELECT i.*,COALESCE(rc.anchor_rate,1.0) anchor_rate
                   FROM inventory i LEFT JOIN resource_catalog rc ON rc.name=i.item_name
                   ORDER BY i.entity_id,i.item_name"""
            ).fetchall()
            value_rows = conn.execute(
                "SELECT * FROM entity_values ORDER BY entity_id,kind,name"
            ).fetchall()
            catalog_rows = conn.execute(
                "SELECT * FROM resource_catalog ORDER BY kind,name"
            ).fetchall()
            event_rows = conn.execute("SELECT * FROM ongoing_events ORDER BY end_time").fetchall()
            ledger_rows = conn.execute(
                "SELECT * FROM inventory_ledger ORDER BY created_at,sequence"
            ).fetchall()
            gameplay_row = conn.execute(
                """SELECT value_json,version,updated_at
                   FROM gameplay_effect_state WHERE state_key='runtime'"""
            ).fetchone()
        entities = [self._row_to_entity(row) for row in entity_rows]
        inventory = [self._row_to_item(row) for row in inv_rows]
        values = [self._row_to_value(row) for row in value_rows]
        catalog = [self._row_to_resource_definition(row) for row in catalog_rows]
        return {
            "schema_version": "2.2",
            "relationship_types": self.relationship_catalog.definitions(),
            "entities": [{
                "id": e.id, "type": e.type, "name": e.name,
                "description": e.description, "current_status": e.current_status,
                "updated_at": e.updated_at, "metadata": e.metadata, "version": e.version,
            } for e in entities],
            "inventory": [{
                "entity_id": i.entity_id, "category": i.category,
                "item_name": i.item_name, "quantity": i.quantity,
                "description": i.description, "version": i.version,
                "anchor_rate": i.anchor_rate, "base_value": i.base_value,
            } for i in inventory],
            "entity_values": [{
                "entity_id": v.entity_id, "name": v.name, "kind": v.kind,
                "value": v.value, "description": v.description, "version": v.version,
            } for v in values],
            "resource_catalog": [{
                "name": r.name, "unit": r.unit, "kind": r.kind,
                "default_quantity": r.default_quantity,
                "min_quantity": r.min_quantity, "max_quantity": r.max_quantity,
                "anchor_rate": r.anchor_rate, "metadata": r.metadata,
            } for r in catalog],
            "relations": self.get_all_relations(),
            "ongoing_events": [{
                "event_id": e.event_id, "entity_id": e.entity_id,
                "event_type": e.event_type, "description": e.description,
                "start_time": e.start_time, "end_time": e.end_time,
                "expected_result": e.expected_result,
                "source_action_id": e.source_action_id,
            } for e in (self._row_to_event(row) for row in event_rows)],
            "inventory_ledger": [self._ledger_row_to_dict(row) for row in ledger_rows],
            "gameplay_effect_state": normalize_gameplay_effect_state(
                json.loads(gameplay_row["value_json"] or "{}") if gameplay_row else None
            ),
            "gameplay_effect_state_version": int(gameplay_row["version"] or 0) if gameplay_row else 0,
            "gameplay_effect_state_updated_at": float(gameplay_row["updated_at"] or 0.0) if gameplay_row else 0.0,
        }

    @staticmethod
    def _ledger_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            item["metadata"] = {}
        return item

    # Action context.

    def get_context_for_action(
        self,
        location_id: str,
        executor_id: str | None = None,
        sim_time: float = 0.0,
        seconds_per_day: float = 86400.0,
    ) -> str:
        """拼接地点描述+现状+库存+执行者信息，供 ActionAgent 注入 prompt.

        如果 location_id 不存在，返回说明性提示而非空字符串，
        这样 LLM 能感知"目标地点在世界状态数据库中还未登记"。
        """
        lines: list[str] = []

        loc = self.get_entity(location_id)
        if loc:
            lines.append(f"### 目标地点：{loc.name}（{loc.type}）")
            if loc.description:
                lines.append(f"- 基本情况：{loc.description}")
            if loc.current_status:
                lines.append(f"- 当前状况：{loc.current_status}")
            inv = [item for item in self.get_inventory(location_id) if item.quantity != -1]
            if inv:
                lines.append("- \u5e93\u5b58\uff1a")
                for item in inv:
                    unit = f" {item.description}" if item.description else ""
                    lines.append(
                        f"  \u00b7 {item.item_name}\uff1a{item.quantity:g}{unit}"
                        f"\uff08\u951a\u5b9a\u4ef7\u503c {item.base_value:g}\uff09"
                    )
            else:
                lines.append("- \u5e93\u5b58\uff1a\uff08\u65e0\u8bb0\u5f55\uff09")
            values = [value for value in self.get_entity_values(location_id) if value.value != -1]
            metrics = [value for value in values if value.kind == "metric"]
            parameters = [value for value in values if value.kind == "parameter"]
            if metrics:
                lines.append("- \u4e16\u754c\u6307\u6807\uff1a")
                for value in metrics:
                    unit = f" {value.description}" if value.description else ""
                    lines.append(f"  \u00b7 {value.name}\uff1a{value.value:g}{unit}")
            if parameters:
                lines.append("- \u6838\u5fc3\u53c2\u6570\uff1a")
                for value in parameters:
                    unit = f" {value.description}" if value.description else ""
                    lines.append(f"  \u00b7 {value.name}\uff1a{value.value:g}{unit}")
            ongoing = self.list_ongoing_events(location_id)
            if ongoing:
                lines.append("- 正在发生的事情：")
                for ev in ongoing:
                    days_left = max(0.0, (ev.end_time - sim_time) / seconds_per_day)
                    lines.append(
                        f"  · [{ev.event_id}] {ev.description}"
                        f"（剩 {days_left:.1f} 天，预期：{ev.expected_result}）"
                    )
        else:
            lines.append(f"### 目标地点：{location_id}")
            lines.append("- （世界状态数据库中尚未登记该地点，请根据行动描述合理推演）")

        if executor_id:
            exec_ent = self.get_entity(executor_id)
            if exec_ent:
                lines.append("")
                lines.append(f"### 执行者：{exec_ent.name}")
                if exec_ent.description:
                    lines.append(f"- 基本情况：{exec_ent.description}")
                if exec_ent.current_status:
                    lines.append(f"- 当前状况：{exec_ent.current_status}")
                exec_ongoing = self.list_ongoing_events(executor_id)
                if exec_ongoing:
                    lines.append("- 正在发生的事情：")
                    for ev in exec_ongoing:
                        days_left = max(0.0, (ev.end_time - sim_time) / seconds_per_day)
                        lines.append(
                            f"  · [{ev.event_id}] {ev.description}（剩 {days_left:.1f} 天）"
                        )

        return "\n".join(lines)
