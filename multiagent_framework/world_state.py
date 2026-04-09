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

    def update_status(self, entity_id: str, new_status: str, sim_time: float) -> None:
        """增量更新实体的 current_status 字段."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE entities SET current_status = ?, updated_at = ? WHERE id = ?",
                (new_status, sim_time, entity_id),
            )

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体（同时级联删除其库存）。返回是否存在并删除。"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM inventory WHERE entity_id = ?", (entity_id,))
            cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        return cur.rowcount > 0

    def delete_inventory_item(self, entity_id: str, category: str, item_name: str) -> bool:
        """删除单条库存记录。"""
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

    def set_inventory(
        self,
        entity_id: str,
        category: str,
        item_name: str,
        quantity: float,
        description: str = "",
    ) -> None:
        """绝对赋值：覆盖数量."""
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
        """相对增减：返回变更后的新数量. 不存在的条目按 0 起算."""
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
            new_qty = old_qty + quantity_delta
            conn.execute(
                """INSERT OR REPLACE INTO inventory
                   (entity_id, category, item_name, quantity, description)
                   VALUES (?, ?, ?, ?, ?)""",
                (entity_id, category, item_name, new_qty, desc),
            )
        return new_qty

    # ── 批量操作 ──

    def bulk_load(
        self,
        entities: list[Entity],
        inventory: list[InventoryItem],
    ) -> tuple[int, int]:
        """批量导入实体和库存（INSERT OR REPLACE 语义，安全重入）."""
        with sqlite3.connect(self._db_path) as conn:
            for e in entities:
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
        return len(entities), len(inventory)

    def restore_all(self, data: dict[str, Any]) -> tuple[int, int]:
        """清空并从导出数据完整恢复世界状态（读档用）."""
        with sqlite3.connect(self._db_path) as conn:
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
        return self.bulk_load(entities, inventory)

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
            if inv:
                lines.append("- 库存：")
                for it in inv:
                    qty = f"{it.quantity:g}"
                    unit = f" {it.description}" if it.description else ""
                    lines.append(f"  · {it.category}/{it.item_name}：{qty}{unit}")
            else:
                lines.append("- 库存：（无记录）")
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
