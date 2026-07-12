"""朝廷层级图 — SQLite 存储官员层级，坐标数据统一由 WorldState 管理."""

from __future__ import annotations

from contextlib import closing
import math
import sqlite3
import threading
from dataclasses import dataclass, replace
from typing import Any

# ── 距离常量 ──

TRAVEL_SPEED_LI_PER_DAY = 300.0  # 基准速度：普通公文急递，里/天（1里≈500米）
MIN_TRAVEL_DAYS = 0.1             # 同城传递最短耗时（天）


class TravelCoeff:
    """传递速度倍率（越大越快）."""
    URGENT  = 1.8   # 急递（约 540 里/天）
    EXPRESS = 1.5   # 快马（约 450 里/天）
    NORMAL  = 1.0   # 普通公文（300 里/天）


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
        self._cache_lock = threading.RLock()
        self._official_cache: dict[str, Official] | None = None
        self._subordinate_cache: dict[str, tuple[Official, ...]] = {}
        self._rank_cache: dict[int, tuple[Official, ...]] = {}
        self._location_cache: dict[str, tuple[Official, ...]] = {}
        self._init_db()

    def set_world_state(self, world_state: Any) -> None:
        """注入 WorldState 引用，用于坐标查询."""
        self._world_state = world_state

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
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

    @staticmethod
    def _copy_official(official: Official) -> Official:
        # Preserve the previous query contract: callers receive a detached value
        # and cannot mutate the graph's in-memory index accidentally.
        return replace(official)

    def _load_cache_locked(self) -> None:
        with closing(self._conn()) as conn, conn:
            rows = conn.execute(
                f"SELECT {_OFFICIAL_SEL} FROM officials ORDER BY rowid"
            ).fetchall()
        officials = [Official(**dict(row)) for row in rows]
        self._official_cache = {official.id: official for official in officials}
        subordinate_cache: dict[str, list[Official]] = {}
        rank_cache: dict[int, list[Official]] = {}
        location_cache: dict[str, list[Official]] = {}
        for official in officials:
            if official.superior_id:
                subordinate_cache.setdefault(official.superior_id, []).append(official)
            rank_cache.setdefault(official.rank, []).append(official)
            if official.location_id:
                location_cache.setdefault(official.location_id, []).append(official)
        self._subordinate_cache = {key: tuple(value) for key, value in subordinate_cache.items()}
        self._rank_cache = {key: tuple(value) for key, value in rank_cache.items()}
        self._location_cache = {key: tuple(value) for key, value in location_cache.items()}

    def _ensure_cache_locked(self) -> dict[str, Official]:
        if self._official_cache is None:
            self._load_cache_locked()
        return self._official_cache or {}

    def refresh_cache(self) -> int:
        """Reload all query indexes after an out-of-band database change."""
        with self._cache_lock:
            self._load_cache_locked()
            return len(self._official_cache or {})

    def _invalidate_cache_locked(self) -> None:
        self._official_cache = None
        self._subordinate_cache = {}
        self._rank_cache = {}
        self._location_cache = {}

    def get_official(self, official_id: str) -> Official | None:
        with self._cache_lock:
            official = self._ensure_cache_locked().get(official_id)
            return self._copy_official(official) if official else None

    def get_superior(self, official_id: str) -> Official | None:
        official = self.get_official(official_id)
        if not official or not official.superior_id:
            return None
        return self.get_official(official.superior_id)

    def get_subordinates(self, official_id: str) -> list[Official]:
        with self._cache_lock:
            self._ensure_cache_locked()
            return [self._copy_official(item) for item in self._subordinate_cache.get(official_id, ())]

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

    def is_direct_relation(self, source_id: str, target_id: str) -> bool:
        """判断两个官员是否存在直接上下级关系。"""
        source = self.get_official(source_id)
        target = self.get_official(target_id)
        if not source or not target:
            return False
        return source.superior_id == target_id or target.superior_id == source_id

    def get_path(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int = 64,
        enabled_only: bool = False,
    ) -> list[Official]:
        """查找两个官员之间的层级路径，支持 DLC 动态扩展后的非固定层级。"""
        source = self.get_official(source_id)
        target = self.get_official(target_id)
        if not source or not target:
            return []
        if enabled_only and (not source.enabled or not target.enabled):
            return []
        if source_id == target_id:
            return [source]
        queue: list[tuple[str, list[str]]] = [(source_id, [source_id])]
        visited = {source_id}
        while queue:
            current_id, path_ids = queue.pop(0)
            if len(path_ids) - 1 >= max_depth:
                continue
            current = self.get_official(current_id)
            if current is None:
                continue
            neighbors: list[Official] = []
            if current.superior_id:
                superior = self.get_official(current.superior_id)
                if superior:
                    neighbors.append(superior)
            neighbors.extend(self.get_subordinates(current_id))
            for neighbor in neighbors:
                if neighbor.id in visited or (enabled_only and not neighbor.enabled):
                    continue
                next_path = [*path_ids, neighbor.id]
                if neighbor.id == target_id:
                    resolved = [self.get_official(item) for item in next_path]
                    return [item for item in resolved if item is not None]
                visited.add(neighbor.id)
                queue.append((neighbor.id, next_path))
        return []

    def is_reachable(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int = 64,
        enabled_only: bool = False,
    ) -> bool:
        return bool(self.get_path(source_id, target_id, max_depth=max_depth, enabled_only=enabled_only))

    def resolve_target_hint(self, hint: str, *, limit: int = 12) -> list[Official]:
        """按 ID 或姓名解析目标提示，优先精确匹配再回退到模糊匹配。"""
        value = (hint or "").strip()
        if not value or limit <= 0:
            return []
        exact_id = self.get_official(value)
        if exact_id:
            return [exact_id]
        folded = value.casefold()
        officials = self.get_all_officials()
        exact_names = [item for item in officials if item.name.casefold() == folded]
        if exact_names:
            return sorted(exact_names, key=lambda item: item.id)[:limit]
        partial = [item for item in officials if folded in item.id.casefold() or folded in item.name.casefold()]
        return sorted(partial, key=lambda item: (item.rank, item.id), reverse=True)[:limit]

    def get_routing_candidates(self, source_id: str, *, hint: str = "", limit: int = 12) -> list[Official]:
        """按目标提示和朝廷层级生成受限的 Router prompt 候选集合。"""
        if limit <= 0:
            return []
        source = self.get_official(source_id)
        ordered: list[Official] = []
        seen: set[str] = set()

        def add(items: list[Official]) -> None:
            for item in items:
                if item.enabled and item.id != source_id and item.id not in seen and len(ordered) < limit:
                    seen.add(item.id)
                    ordered.append(item)

        if hint:
            add(self.resolve_target_hint(hint, limit=limit))
        if source:
            superior = self.get_superior(source_id)
            add([superior] if superior else [])
            add(self.get_subordinates(source_id))
            if source.location_id:
                add(self.get_officials_at_location(source.location_id))
            add(self.get_chain_to_emperor(source_id)[1:])
        if len(ordered) < limit:
            add(sorted(self.get_all_officials(), key=lambda item: (item.rank, item.id), reverse=True))
        return ordered[:limit]

    def get_officials_by_rank(self, rank: int) -> list[Official]:
        with self._cache_lock:
            self._ensure_cache_locked()
            return [self._copy_official(item) for item in self._rank_cache.get(rank, ())]

    def get_officials_at_location(self, location_id: str) -> list[Official]:
        with self._cache_lock:
            self._ensure_cache_locked()
            return [self._copy_official(item) for item in self._location_cache.get(location_id, ())]

    def get_all_officials(self) -> list[Official]:
        with self._cache_lock:
            officials = self._ensure_cache_locked().values()
            return [self._copy_official(item) for item in officials]

    def get_distance(
        self,
        from_id: str,
        to_id: str,
        speed_coeff: float = TravelCoeff.NORMAL,
    ) -> float:
        """获取两名官员之间的通信耗时（天）.

        坐标数据从 WorldState entity metadata 获取。

        Args:
            from_id:     发送方官员 ID
            to_id:       接收方官员 ID
            speed_coeff: 速度倍率（TravelCoeff.URGENT / EXPRESS / NORMAL，越大越快）
        Returns:
            通信天数（浮点），最少 MIN_TRAVEL_DAYS
        """
        from_official = self.get_official(from_id)
        to_official = self.get_official(to_id)
        if not from_official or not to_official:
            return 5.0 / speed_coeff
        if self._world_state and from_official.location_id and to_official.location_id:
            from_c = self._world_state.get_coordinates(from_official.location_id)
            to_c = self._world_state.get_coordinates(to_official.location_id)
            if from_c and to_c:
                dist = math.sqrt((from_c[0] - to_c[0]) ** 2 + (from_c[1] - to_c[1]) ** 2)
                days = dist / (TRAVEL_SPEED_LI_PER_DAY * speed_coeff)
                return max(days, MIN_TRAVEL_DAYS)
        return 5.0 / speed_coeff

    # ── 数据写入 ──

    def add_official(self, official: Official) -> None:
        with self._cache_lock:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
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
            self._invalidate_cache_locked()

    def bulk_load_officials(self, officials: list[Official]) -> int:
        rows = [
            (
                official.id, official.name, official.rank, official.location_id,
                official.role, official.agent_type, official.superior_id,
                official.personality_seed, int(official.enabled), official.extra_prompt,
            )
            for official in officials
        ]
        with self._cache_lock:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                conn.executemany(
                    f"""INSERT OR REPLACE INTO officials
                       (id, name, rank, location_id, role, agent_type,
                        superior_id, personality_seed, enabled, extra_prompt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
            self._invalidate_cache_locked()
        return len(rows)


    def replace_managed_officials(
        self, officials: list[Official], *, replace_ids: set[str]
    ) -> int:
        """Replace one DLC-owned official set in a single database transaction."""
        rows = [
            (
                official.id, official.name, official.rank, official.location_id,
                official.role, official.agent_type, official.superior_id,
                official.personality_seed, int(official.enabled), official.extra_prompt,
            )
            for official in officials
        ]
        next_ids = {official.id for official in officials}
        stale_ids = sorted(set(replace_ids) - next_ids)
        with self._cache_lock:
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                if stale_ids:
                    conn.executemany(
                        "DELETE FROM officials WHERE id=?",
                        [(official_id,) for official_id in stale_ids],
                    )
                conn.executemany(
                    f"""INSERT OR REPLACE INTO officials
                       (id, name, rank, location_id, role, agent_type,
                        superior_id, personality_seed, enabled, extra_prompt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
            self._invalidate_cache_locked()
        return len(rows)
