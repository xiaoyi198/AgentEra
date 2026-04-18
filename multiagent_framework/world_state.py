"""世界状态数据库 — 存储世界中所有实体的动态状态和库存.

与 CourtGraph 并列：
- CourtGraph 管"这个官是谁、在哪、听谁的"（结构层，不变）
- WorldState 管"这个地方/机构发生了什么、有多少粮"（动态层，可变）
- 两者通过相同 ID 关联（如 location_id="吴县" 在两边都有）
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any


# ── 资源代币定义 ──

RESOURCE_TOKENS: list[dict[str, Any]] = [
    {"name": "金钱", "unit": "两", "default": 0, "min": 0, "max": None, "type": "resource"},
    {"name": "粮草", "unit": "石", "default": 0, "min": 0, "max": None, "type": "resource"},
    {"name": "工料", "unit": "工日", "default": 0, "min": 0, "max": None, "type": "resource"},
    {"name": "科技点", "unit": "点", "default": 0, "min": 0, "max": 200, "type": "data"},
    {"name": "兵力", "unit": "人", "default": 0, "min": 0, "max": None, "type": "resource"},
    {"name": "满意度", "unit": "", "default": 50, "min": -1, "max": 100, "type": "data"},
    {"name": "人口", "unit": "人", "default": 0, "min": -1, "max": None, "type": "resource"},
    # ── 核心参数 ──
    {"name": "生活质量", "unit": "", "default": 1.0, "min": 0.2, "max": 1.5, "type": "param"},
    {"name": "税率", "unit": "%", "default": 0.08, "min": 0, "max": 0.5, "type": "param"},
    {"name": "生产系数", "unit": "", "default": 1.0, "min": 0.5, "max": 2.5, "type": "param"},
]
RESOURCE_NAMES: set[str] = {t["name"] for t in RESOURCE_TOKENS}
RESOURCE_META: dict[str, dict[str, Any]] = {t["name"]: t for t in RESOURCE_TOKENS}


RELATION_TYPES: set[str] = {"subordinate", "affiliated"}


# ── 数据类 ──

@dataclass
class Entity:
    """世界中的一个实体（地点/人物/机构/组织/物品）."""
    id: str
    type: str                                   # location / person / institution / organization / object
    name: str
    description: str = ""                       # 基本情况简介（相对静态）
    current_status: str = ""                    # 目前状况（动态更新）
    updated_at: float = 0.0                     # 最后更新的 sim_time
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InventoryItem:
    """实体的一项库存."""
    entity_id: str
    category: str                               # 粮食 / 货币 / 兵力 / 物资
    item_name: str                              # 稻米 / 白银 / 官兵
    quantity: float = 0.0
    description: str = ""                       # 单位/补充说明


# ── 主类 ──

class WorldState:
    """基于 SQLite 的世界状态存储 + 查询."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    current_status TEXT DEFAULT '',
                    updated_at REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    entity_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    quantity REAL DEFAULT 0,
                    description TEXT DEFAULT '',
                    PRIMARY KEY (entity_id, category, item_name),
                    FOREIGN KEY (entity_id) REFERENCES entities(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entity_relations (
                    parent_id TEXT NOT NULL,
                    child_id  TEXT NOT NULL,
                    rel_type  TEXT NOT NULL,
                    note      TEXT DEFAULT '',
                    PRIMARY KEY (parent_id, child_id, rel_type)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_child  ON entity_relations(child_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rel_parent ON entity_relations(parent_id)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 实体 CRUD ──

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        meta_raw = row["metadata"] or "{}"
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError:
            meta = {}
        return Entity(
            id=row["id"],
            type=row["type"],
            name=row["name"],
            description=row["description"] or "",
            current_status=row["current_status"] or "",
            updated_at=row["updated_at"] or 0.0,
            metadata=meta,
        )

    def get_entity(self, entity_id: str) -> Entity | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?", (entity_id,)
            ).fetchone()
        return self._row_to_entity(row) if row else None

    def get_coordinates(self, entity_id: str) -> tuple[float, float, str] | None:
        """从实体 metadata 提取坐标 (x, y, region)，不存在则返回 None."""
        entity = self.get_entity(entity_id)
        if not entity:
            return None
        meta = entity.metadata
        if "x" not in meta and "y" not in meta:
            return None
        return (float(meta.get("x", 0.0)), float(meta.get("y", 0.0)), str(meta.get("region", "")))

    def get_entities_by_type(self, entity_type: str) -> list[Entity]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = ?", (entity_type,)
            ).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def get_all_entities(self) -> list[Entity]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM entities").fetchall()
        return [self._row_to_entity(r) for r in rows]

    def upsert_entity(self, entity: Entity) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entities
                   (id, type, name, description, current_status, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity.id, entity.type, entity.name,
                    entity.description, entity.current_status,
                    entity.updated_at,
                    json.dumps(entity.metadata, ensure_ascii=False),
                ),
            )
        self.init_default_inventory(entity.id, entity.type)

    def init_default_inventory(self, entity_id: str, entity_type: str = "") -> None:
        """为实体补齐 7 个标准资源代币（INSERT OR IGNORE，不覆盖已有值）."""
        with sqlite3.connect(self._db_path) as conn:
            for token in RESOURCE_TOKENS:
                default_qty = token["default"]
                if entity_type == "institution" and token["name"] in (
                    "满意度", "人口", "生活质量", "税率", "生产系数",
                ):
                    default_qty = -1
                conn.execute(
                    """INSERT OR IGNORE INTO inventory
                       (entity_id, category, item_name, quantity, description)
                       VALUES (?, ?, ?, ?, ?)""",
                    (entity_id, token["name"], token["name"],
                     default_qty, token["unit"]),
                )

    def update_status(self, entity_id: str, new_status: str, sim_time: float) -> None:
        """增量更新实体的 current_status 字段."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE entities SET current_status = ?, updated_at = ? WHERE id = ?",
                (new_status, sim_time, entity_id),
            )

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体（级联删除其库存与关系）。返回是否存在并删除。"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM inventory WHERE entity_id = ?", (entity_id,))
            conn.execute(
                "DELETE FROM entity_relations WHERE parent_id = ? OR child_id = ?",
                (entity_id, entity_id),
            )
            cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        return cur.rowcount > 0

    def delete_inventory_item(self, entity_id: str, category: str, item_name: str) -> bool:
        """删除单条库存记录。标准资源代币不可删除。"""
        if item_name in RESOURCE_NAMES:
            return False
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM inventory WHERE entity_id=? AND category=? AND item_name=?",
                (entity_id, category, item_name),
            )
        return cur.rowcount > 0

    def search_entities(self, keyword: str) -> list[Entity]:
        """在 id/name/description 中模糊搜索."""
        pattern = f"%{keyword}%"
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM entities
                   WHERE id LIKE ? OR name LIKE ? OR description LIKE ?""",
                (pattern, pattern, pattern),
            ).fetchall()
        return [self._row_to_entity(r) for r in rows]

    # ── 库存 CRUD ──

    def _row_to_item(self, row: sqlite3.Row) -> InventoryItem:
        return InventoryItem(
            entity_id=row["entity_id"],
            category=row["category"],
            item_name=row["item_name"],
            quantity=row["quantity"] or 0.0,
            description=row["description"] or "",
        )

    def get_inventory(self, entity_id: str) -> list[InventoryItem]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE entity_id = ?", (entity_id,)
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    @staticmethod
    def _clamp_resource(name: str, value: float) -> float:
        """根据资源代币的 min/max 约束裁剪数值。-1 表示不适用，不参与约束。"""
        if value == -1:
            return -1
        meta = RESOURCE_META.get(name)
        if not meta:
            return value
        lo = meta["min"] if meta["min"] is not None else float("-inf")
        if lo == -1:
            lo = 0
        hi = meta["max"] if meta["max"] is not None else float("inf")
        return max(lo, min(hi, value))

    def set_inventory(
        self,
        entity_id: str,
        category: str,
        item_name: str,
        quantity: float,
        description: str = "",
    ) -> None:
        """绝对赋值：覆盖数量. 仅允许标准资源代币。"""
        if item_name not in RESOURCE_NAMES:
            raise ValueError(f"不允许操作非标准资源代币: {item_name}")
        category = item_name
        quantity = self._clamp_resource(item_name, quantity)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO inventory
                   (entity_id, category, item_name, quantity, description)
                   VALUES (?, ?, ?, ?, ?)""",
                (entity_id, category, item_name, quantity, description),
            )

    def update_inventory(
        self,
        entity_id: str,
        category: str,
        item_name: str,
        quantity_delta: float,
        reason: str = "",
    ) -> float:
        """相对增减：返回变更后的新数量. 仅允许标准资源代币。"""
        if item_name not in RESOURCE_NAMES:
            raise ValueError(f"不允许操作非标准资源代币: {item_name}")
        category = item_name
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT quantity, description FROM inventory
                   WHERE entity_id = ? AND category = ? AND item_name = ?""",
                (entity_id, category, item_name),
            ).fetchone()
            if row:
                old_qty = row[0] or 0.0
                desc = row[1] or ""
            else:
                old_qty = 0.0
                desc = ""
            if old_qty == -1:
                raise ValueError(f"实体 {entity_id} 的 {item_name} 不适用（值为 -1），不可修改")
            new_qty = self._clamp_resource(item_name, old_qty + quantity_delta)
            conn.execute(
                """INSERT OR REPLACE INTO inventory
                   (entity_id, category, item_name, quantity, description)
                   VALUES (?, ?, ?, ?, ?)""",
                (entity_id, category, item_name, new_qty, desc),
            )
        return new_qty

    # ── 关系 CRUD ──

    def add_relation(
        self,
        parent_id: str,
        child_id: str,
        rel_type: str,
        note: str = "",
    ) -> None:
        """创建或覆盖一条实体关系边。rel_type 必须是标准类型。"""
        if rel_type not in RELATION_TYPES:
            raise ValueError(f"未知关系类型: {rel_type}")
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO entity_relations
                   (parent_id, child_id, rel_type, note)
                   VALUES (?, ?, ?, ?)""",
                (parent_id, child_id, rel_type, note),
            )

    def remove_relation(
        self,
        parent_id: str,
        child_id: str,
        rel_type: str,
    ) -> bool:
        """删除一条边。返回是否存在并删除。"""
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                """DELETE FROM entity_relations
                   WHERE parent_id=? AND child_id=? AND rel_type=?""",
                (parent_id, child_id, rel_type),
            )
        return cur.rowcount > 0

    def get_parents(self, child_id: str) -> list[dict[str, str]]:
        """返回指向 child_id 的所有父节点。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT parent_id, rel_type, note FROM entity_relations
                   WHERE child_id = ?""",
                (child_id,),
            ).fetchall()
        return [
            {"parent_id": r["parent_id"], "rel_type": r["rel_type"], "note": r["note"] or ""}
            for r in rows
        ]

    def get_children(self, parent_id: str) -> list[dict[str, str]]:
        """返回 parent_id 的所有子节点。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT child_id, rel_type, note FROM entity_relations
                   WHERE parent_id = ?""",
                (parent_id,),
            ).fetchall()
        return [
            {"child_id": r["child_id"], "rel_type": r["rel_type"], "note": r["note"] or ""}
            for r in rows
        ]

    def get_siblings(self, entity_id: str) -> list[dict[str, str]]:
        """返回所有"同父同 rel_type"的兄弟节点，带上 via_parent_id 说明来源。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT r2.child_id AS sibling_id, r.parent_id AS via_parent_id, r.rel_type
                   FROM entity_relations r
                   JOIN entity_relations r2
                     ON r.parent_id = r2.parent_id AND r.rel_type = r2.rel_type
                   WHERE r.child_id = ? AND r2.child_id != ?""",
                (entity_id, entity_id),
            ).fetchall()
        return [
            {
                "sibling_id": r["sibling_id"],
                "via_parent_id": r["via_parent_id"],
                "rel_type": r["rel_type"],
            }
            for r in rows
        ]

    def get_all_relations(self) -> list[dict[str, str]]:
        """整图导出所有关系边。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT parent_id, child_id, rel_type, note FROM entity_relations"
            ).fetchall()
        return [
            {
                "parent_id": r["parent_id"],
                "child_id": r["child_id"],
                "rel_type": r["rel_type"],
                "note": r["note"] or "",
            }
            for r in rows
        ]

    # ── 批量操作 ──

    def bulk_load(
        self,
        entities: list[Entity],
        inventory: list[InventoryItem],
        relations: list[dict[str, str]] | None = None,
    ) -> tuple[int, int, int]:
        """批量导入实体、库存与关系（INSERT OR REPLACE 语义，安全重入）.

        导入后自动补齐每个实体的 7 个标准资源代币。
        返回 (实体数, 库存数, 关系数)。
        """
        entity_types: dict[str, str] = {}
        with sqlite3.connect(self._db_path) as conn:
            for e in entities:
                entity_types[e.id] = e.type
                conn.execute(
                    """INSERT OR REPLACE INTO entities
                       (id, type, name, description, current_status, updated_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        e.id, e.type, e.name, e.description,
                        e.current_status, e.updated_at,
                        json.dumps(e.metadata, ensure_ascii=False),
                    ),
                )
            for it in inventory:
                conn.execute(
                    """INSERT OR REPLACE INTO inventory
                       (entity_id, category, item_name, quantity, description)
                       VALUES (?, ?, ?, ?, ?)""",
                    (it.entity_id, it.category, it.item_name,
                     it.quantity, it.description),
                )
            rel_count = 0
            for r in (relations or []):
                rel_type = r.get("rel_type", "")
                if rel_type not in RELATION_TYPES:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO entity_relations
                       (parent_id, child_id, rel_type, note)
                       VALUES (?, ?, ?, ?)""",
                    (r["parent_id"], r["child_id"], rel_type, r.get("note", "")),
                )
                rel_count += 1
        for e in entities:
            self.init_default_inventory(e.id, entity_types.get(e.id, ""))
        return len(entities), len(inventory), rel_count

    def restore_all(self, data: dict[str, Any]) -> tuple[int, int, int]:
        """清空并从导出数据完整恢复世界状态（读档用）.

        恢复后自动补齐每个实体的 7 个标准资源代币。
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM entity_relations")
            conn.execute("DELETE FROM inventory")
            conn.execute("DELETE FROM entities")
        entities = [
            Entity(
                id=e["id"], type=e["type"], name=e["name"],
                description=e.get("description", ""),
                current_status=e.get("current_status", ""),
                updated_at=e.get("updated_at", 0.0),
                metadata=e.get("metadata", {}),
            )
            for e in data.get("entities", [])
        ]
        inventory = [
            InventoryItem(
                entity_id=i["entity_id"], category=i["category"],
                item_name=i["item_name"], quantity=i.get("quantity", 0.0),
                description=i.get("description", ""),
            )
            for i in data.get("inventory", [])
        ]
        relations = list(data.get("relations", []))
        return self.bulk_load(entities, inventory, relations)

    def export_all(self) -> dict[str, Any]:
        """导出为 JSON 友好结构."""
        entities = self.get_all_entities()
        with self._conn() as conn:
            inv_rows = conn.execute("SELECT * FROM inventory").fetchall()
        return {
            "entities": [
                {
                    "id": e.id, "type": e.type, "name": e.name,
                    "description": e.description,
                    "current_status": e.current_status,
                    "updated_at": e.updated_at,
                    "metadata": e.metadata,
                }
                for e in entities
            ],
            "inventory": [
                {
                    "entity_id": r["entity_id"],
                    "category": r["category"],
                    "item_name": r["item_name"],
                    "quantity": r["quantity"],
                    "description": r["description"],
                }
                for r in inv_rows
            ],
            "relations": self.get_all_relations(),
        }

    # ── Action Agent 专用：动态上下文 ──

    def get_context_for_action(
        self,
        location_id: str,
        executor_id: str | None = None,
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
            inv = self.get_inventory(location_id)
            resource_items = [
                it for it in inv
                if it.quantity != -1
                and RESOURCE_META.get(it.item_name, {}).get("type") == "resource"
            ]
            if resource_items:
                lines.append("- 库存：")
                for it in resource_items:
                    qty = f"{it.quantity:g}"
                    unit = f" {it.description}" if it.description else ""
                    lines.append(f"  · {it.item_name}：{qty}{unit}")
            else:
                lines.append("- 库存：（无记录）")
            # 展示核心参数（type=param）
            param_items = [
                it for it in inv
                if it.quantity != -1
                and RESOURCE_META.get(it.item_name, {}).get("type") == "param"
            ]
            if param_items:
                lines.append("- 核心参数：")
                for it in param_items:
                    qty = f"{it.quantity:g}"
                    unit = f" {it.description}" if it.description else ""
                    lines.append(f"  · {it.item_name}：{qty}{unit}")
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

        return "\n".join(lines)
