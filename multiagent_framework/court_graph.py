"""朝廷层级图 — SQLite 存储官员层级，坐标数据统一由 WorldState 管理."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from typing import Any

# ── 距离常量 ──

TRAVEL_SPEED_LI_PER_DAY = 300.0  # 基准速度：快马急递日夜兼程，里/天（1里≈500米）
MIN_TRAVEL_DAYS = 0.1             # 同城传递最短耗时（天）


class TravelCoeff:
    """传递时间系数（越小越快）."""
    URGENT = 0.5    # 六百里加急（约 600 里/天）
    NORMAL = 1.0    # 普通公文急递（300 里/天）
    SLOW   = 2.0    # 民信/绕远路（约 150 里/天）


# ── 显式列选择（防止 SELECT * 带入已废弃列）──

_OFFICIAL_SEL = (
    "id, name, rank, location_id, role, agent_type, "
    "superior_id, personality_seed, enabled, extra_prompt"
)


@dataclass
class Official:
    """官员数据."""
    id: str
    name: str
    rank: int
    location_id: str
    role: str                     # official / eunuch / player
    agent_type: str               # official / institution
    superior_id: str | None
    personality_seed: str
    enabled: bool = True          # 是否启用（参与模拟）
    extra_prompt: str = ""        # 附加个性化提示词（有则标记为"活跃"）



class CourtGraph:
    """基于 SQLite 的朝廷层级查询."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._world_state: Any | None = None
        self._init_db()

    def set_world_state(self, world_state: Any) -> None:
        """注入 WorldState 引用，用于坐标查询."""
        self._world_state = world_state

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS officials (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    location_id TEXT,
                    role TEXT NOT NULL DEFAULT 'official',
                    agent_type TEXT NOT NULL DEFAULT 'official',
                    superior_id TEXT,
                    personality_seed TEXT DEFAULT '忠直',
                    enabled INTEGER DEFAULT 1,
                    extra_prompt TEXT DEFAULT ''
                )
            """)
            # ── 迁移：为旧数据库补充缺失列 ──
            off_cols = {row[1] for row in conn.execute("PRAGMA table_info(officials)")}
            if "enabled" not in off_cols:
                conn.execute("ALTER TABLE officials ADD COLUMN enabled INTEGER DEFAULT 1")
            if "extra_prompt" not in off_cols:
                conn.execute("ALTER TABLE officials ADD COLUMN extra_prompt TEXT DEFAULT ''")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 官员查询 ──

    def get_official(self, official_id: str) -> Official | None:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_OFFICIAL_SEL} FROM officials WHERE id = ?", (official_id,)
            ).fetchone()
        if not row:
            return None
        return Official(**dict(row))

    def get_superior(self, official_id: str) -> Official | None:
        official = self.get_official(official_id)
        if not official or not official.superior_id:
            return None
        return self.get_official(official.superior_id)

    def get_subordinates(self, official_id: str) -> list[Official]:
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_OFFICIAL_SEL} FROM officials WHERE superior_id = ?", (official_id,)
            ).fetchall()
        return [Official(**dict(r)) for r in rows]

    def get_chain_to_emperor(self, official_id: str) -> list[Official]:
        """从某官员到皇帝的完整上报链."""
        chain: list[Official] = []
        current = self.get_official(official_id)
        visited: set[str] = set()
        while current and current.id not in visited:
            chain.append(current)
            visited.add(current.id)
            if current.role == "player":
                break
            if current.superior_id:
                current = self.get_official(current.superior_id)
            else:
                break
        return chain

    def get_officials_by_rank(self, rank: int) -> list[Official]:
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_OFFICIAL_SEL} FROM officials WHERE rank = ?", (rank,)
            ).fetchall()
        return [Official(**dict(r)) for r in rows]

    def get_officials_at_location(self, location_id: str) -> list[Official]:
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_OFFICIAL_SEL} FROM officials WHERE location_id = ?", (location_id,)
            ).fetchall()
        return [Official(**dict(r)) for r in rows]

    def get_all_officials(self) -> list[Official]:
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {_OFFICIAL_SEL} FROM officials").fetchall()
        return [Official(**dict(r)) for r in rows]

    def get_distance(
        self,
        from_id: str,
        to_id: str,
        mode: float = TravelCoeff.NORMAL,
    ) -> float:
        """获取两名官员之间的通信耗时（天）.

        坐标数据从 WorldState entity metadata 获取。

        Args:
            from_id: 发送方官员 ID
            to_id:   接收方官员 ID
            mode:    传递系数（TravelCoeff.URGENT / NORMAL / SLOW）
        Returns:
            通信天数（浮点），最少 MIN_TRAVEL_DAYS
        """
        from_official = self.get_official(from_id)
        to_official = self.get_official(to_id)
        if not from_official or not to_official:
            return 5.0 * mode
        if self._world_state and from_official.location_id and to_official.location_id:
            from_c = self._world_state.get_coordinates(from_official.location_id)
            to_c = self._world_state.get_coordinates(to_official.location_id)
            if from_c and to_c:
                dist = math.sqrt((from_c[0] - to_c[0]) ** 2 + (from_c[1] - to_c[1]) ** 2)
                days = dist / TRAVEL_SPEED_LI_PER_DAY * mode
                return max(days, MIN_TRAVEL_DAYS)
        return 5.0 * mode

    # ── 数据写入 ──

    def add_official(self, official: Official) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                f"""INSERT OR REPLACE INTO officials
                   (id, name, rank, location_id, role, agent_type,
                    superior_id, personality_seed, enabled, extra_prompt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    official.id, official.name, official.rank,
                    official.location_id, official.role, official.agent_type,
                    official.superior_id, official.personality_seed,
                    int(official.enabled), official.extra_prompt,
                ),
            )

    def bulk_load_officials(self, officials: list[Official]) -> int:
        with sqlite3.connect(self._db_path) as conn:
            for o in officials:
                conn.execute(
                    f"""INSERT OR REPLACE INTO officials
                       (id, name, rank, location_id, role, agent_type,
                        superior_id, personality_seed, enabled, extra_prompt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (o.id, o.name, o.rank, o.location_id, o.role, o.agent_type,
                     o.superior_id, o.personality_seed, int(o.enabled), o.extra_prompt),
                )
        return len(officials)
