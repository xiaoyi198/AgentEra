"""调用链日志 — JSONL 追加写入，支持按事件/链路查询."""

from __future__ import annotations

import dataclasses
import json
from collections import deque
from pathlib import Path
from typing import Any

from .types import ChainLogEntry

_ENTRIES_MAXLEN = 5000


class ChainLog:
    """JSONL 格式的调用链日志."""

    def __init__(self, log_path: str = "chain_log.jsonl") -> None:
        self._path = Path(log_path)
        self._entries: deque[ChainLogEntry] = deque(maxlen=_ENTRIES_MAXLEN)
        self._by_chain: dict[str, list[ChainLogEntry]] = {}
        self._by_event: dict[str, list[ChainLogEntry]] = {}

    def clear(self) -> None:
        """清空内存索引和磁盘文件."""
        self._entries.clear()
        self._by_chain.clear()
        self._by_event.clear()
        if self._path.exists():
            self._path.write_text("", encoding="utf-8")

    def append(self, entry: ChainLogEntry) -> None:
        """追加一条日志."""
        self._entries.append(entry)
        self._by_chain.setdefault(entry.chain_id, []).append(entry)
        if entry.event_id:
            self._by_event.setdefault(entry.event_id, []).append(entry)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dataclasses.asdict(entry), ensure_ascii=False) + "\n")

    def get_chain(self, chain_id: str) -> list[ChainLogEntry]:
        """按 chain_id 查询."""
        return self._by_chain.get(chain_id, [])

    def get_event_chains(self, event_id: str) -> list[ChainLogEntry]:
        """按 event_id 查询所有调用链."""
        return self._by_event.get(event_id, [])

    def get_distortions(self, chain_id: str | None = None) -> list[ChainLogEntry]:
        """查询所有失真记录."""
        source = self._by_chain.get(chain_id, []) if chain_id else self._entries
        return [e for e in source if e.is_distorted]

    def get_withheld(self) -> list[ChainLogEntry]:
        """查询所有被压下的消息."""
        return [e for e in self._entries if e.status == "withheld"]

    def get_max_hop(self, chain_id: str) -> int:
        """获取某条链的最大跳数."""
        entries = self._by_chain.get(chain_id, [])
        return max((e.hop_count for e in entries), default=0)

    def all_entries(self) -> list[ChainLogEntry]:
        return list(self._entries)

    def to_god_view(self, chain_id: str) -> list[dict[str, Any]]:
        """上帝视角：显示某条链的原始信息 vs 传递版本."""
        result = []
        for entry in self.get_chain(chain_id):
            result.append({
                "caller": entry.caller,
                "caller_type": entry.caller_agent_type,
                "original": entry.original_text,
                "transmitted": entry.transmitted_text,
                "distorted": entry.is_distorted,
                "distortion_note": entry.distortion_note,
                "status": entry.status,
                "hop": entry.hop_count,
            })
        return result

    # ── 持久化恢复 ──

    def load_from_file(self) -> int:
        """从 JSONL 文件恢复日志."""
        if not self._path.exists():
            return 0
        self._entries.clear()
        self._by_chain.clear()
        self._by_event.clear()
        loaded = 0
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                entry = ChainLogEntry(**data)
                self._entries.append(entry)
                self._by_chain.setdefault(entry.chain_id, []).append(entry)
                if entry.event_id:
                    self._by_event.setdefault(entry.event_id, []).append(entry)
                loaded += 1
        return loaded
