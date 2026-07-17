"""定时器系统 — heapq + SQLite 持久化."""

from __future__ import annotations

import heapq
import json
import sqlite3
from typing import TYPE_CHECKING

from .types import TimerEntry

if TYPE_CHECKING:
    pass


class TimerSystem:
    """管理所有延迟定时器，支持 SQLite 持久化."""

    def __init__(self, db_path: str | None = None) -> None:
        self._heap: list[TimerEntry] = []
        self._claimed: dict[str, TimerEntry] = {}
        self._db_path = db_path
        if db_path:
            self._init_db()

    # ── 持久化 ──

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_timers (
                    timer_id TEXT PRIMARY KEY,
                    fire_at REAL NOT NULL,
                    target_agent TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    chain_id TEXT,
                    parent_timer_id TEXT,
                    created_at REAL,
                    cancelled INTEGER DEFAULT 0
                )
            """)

    def _persist(self, entry: TimerEntry) -> None:
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending_timers
                   (timer_id, fire_at, target_agent, payload, chain_id,
                    parent_timer_id, created_at, cancelled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.timer_id,
                    entry.fire_at,
                    entry.target_agent,
                    json.dumps(entry.payload, ensure_ascii=False),
                    entry.chain_id,
                    entry.parent_timer_id,
                    entry.created_at,
                    int(entry.cancelled),
                ),
            )

    def _remove_from_db(self, timer_id: str) -> None:
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM pending_timers WHERE timer_id = ?", (timer_id,))

    # ── 核心操作 ──

    def schedule(self, entry: TimerEntry) -> str:
        """添加一个定时器，返回 timer_id."""
        heapq.heappush(self._heap, entry)
        self._persist(entry)
        return entry.timer_id

    def cancel(self, timer_id: str) -> bool:
        """取消一个定时器（懒删除）."""
        for entry in self._heap:
            if entry.timer_id == timer_id:
                entry.cancelled = True
                self._persist(entry)
                return True
        return False

    def claim_due(self, current_time: float) -> list[TimerEntry]:
        """Claim due timers without deleting their durable SQLite rows.

        Claimed timers leave the in-memory heap so one process does not deliver
        them twice concurrently. Until ``ack`` they remain in SQLite and will be
        loaded again after a crash.
        """
        due: list[TimerEntry] = []
        while self._heap and self._heap[0].fire_at <= current_time:
            entry = heapq.heappop(self._heap)
            if entry.cancelled:
                self._remove_from_db(entry.timer_id)
                continue
            self._claimed[entry.timer_id] = entry
            due.append(entry)
        return due

    def ack(self, timer_id: str) -> bool:
        """Acknowledge a claimed timer and remove its durable row."""
        entry = self._claimed.pop(timer_id, None)
        if entry is None:
            return False
        self._remove_from_db(timer_id)
        return True

    def release_claim(self, timer_id: str) -> bool:
        """Return a claimed timer to the heap when outbox enqueue fails."""
        entry = self._claimed.pop(timer_id, None)
        if entry is None:
            return False
        heapq.heappush(self._heap, entry)
        return True

    def pop_due(self, current_time: float) -> list[TimerEntry]:
        """Compatibility API: claim and immediately acknowledge due timers."""
        due = self.claim_due(current_time)
        for entry in due:
            self.ack(entry.timer_id)
        return due

    def peek_next_time(self) -> float | None:
        """查看下一个定时器的触发时间."""
        while self._heap and self._heap[0].cancelled:
            entry = heapq.heappop(self._heap)
            self._remove_from_db(entry.timer_id)
        return self._heap[0].fire_at if self._heap else None

    def get_in_transit(self) -> list[TimerEntry]:
        """获取所有未到期、未取消的定时器（在途公文）."""
        return [e for e in self._heap if not e.cancelled]

    def count(self) -> int:
        return sum(1 for e in self._heap if not e.cancelled)

    # ── 持久化恢复 ──

    def load_from_db(self) -> int:
        """从 SQLite 恢复定时器，返回加载数量."""
        if not self._db_path:
            return 0
        self._heap.clear()
        self._claimed.clear()
        loaded = 0
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT timer_id, fire_at, target_agent, payload, chain_id, "
                "parent_timer_id, created_at, cancelled FROM pending_timers"
            ).fetchall()
        for row in rows:
            entry = TimerEntry(
                timer_id=row[0],
                fire_at=row[1],
                target_agent=row[2],
                payload=json.loads(row[3]),
                chain_id=row[4] or "",
                parent_timer_id=row[5],
                created_at=row[6] or 0.0,
                cancelled=bool(row[7]),
            )
            if not entry.cancelled:
                heapq.heappush(self._heap, entry)
                loaded += 1
        return loaded

    def save_all(self) -> int:
        """将所有定时器存入 SQLite（存档用）."""
        if not self._db_path:
            return 0
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM pending_timers")
        count = 0
        for entry in [*self._heap, *self._claimed.values()]:
            if not entry.cancelled:
                self._persist(entry)
                count += 1
        return count

    def export_all(self) -> list[dict]:
        """导出所有未触发定时器为列表（存档/导出用）."""
        return [
            {
                "timer_id": e.timer_id,
                "fire_at": e.fire_at,
                "target_agent": e.target_agent,
                "payload": e.payload,
                "chain_id": e.chain_id,
                "parent_timer_id": e.parent_timer_id,
                "created_at": e.created_at,
            }
            for e in [*self._heap, *self._claimed.values()]
            if not e.cancelled
        ]

    def import_all(self, timers: list[dict]) -> int:
        """清空并从导出数据恢复定时器（读档用）."""
        if self._db_path:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("DELETE FROM pending_timers")
        self._heap.clear()
        self._claimed.clear()
        for t in timers:
            entry = TimerEntry(
                timer_id=t["timer_id"],
                fire_at=t["fire_at"],
                target_agent=t["target_agent"],
                payload=t["payload"],
                chain_id=t.get("chain_id", ""),
                parent_timer_id=t.get("parent_timer_id"),
                created_at=t.get("created_at", 0.0),
            )
            heapq.heappush(self._heap, entry)
            self._persist(entry)
        return len(timers)
