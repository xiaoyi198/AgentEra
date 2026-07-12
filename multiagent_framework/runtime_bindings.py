"""Atomic, generation-scoped Prompt and model bindings for runtime tasks.

Each model-backed task captures one immutable binding at the beginning of a logical
call. Reloads publish a complete snapshot with one object-reference swap, so an
in-flight call cannot observe a new client with an old Prompt (or the reverse).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


ROUTER_TASK = "router"
ACTION_TASK = "action_agent"
MEMORY_TASK = "memory_compressor"
TIANJI_TASK = "tianji"
RUNTIME_TASK_IDS = (ROUTER_TASK, ACTION_TASK, MEMORY_TASK, TIANJI_TASK)


@dataclass(frozen=True)
class RuntimeTaskBinding:
    """One task's immutable binding captured for a complete logical call."""

    task_id: str
    generation: int
    prompt: str
    llm: Any
    model_profile: str = ""


@dataclass(frozen=True)
class RuntimeBindingsSnapshot:
    """A complete immutable set of runtime task bindings for one generation."""

    generation: int
    bindings: Mapping[str, RuntimeTaskBinding]

    @classmethod
    def create(
        cls,
        generation: int,
        bindings: Mapping[str, RuntimeTaskBinding],
    ) -> "RuntimeBindingsSnapshot":
        generation = int(generation)
        prepared: dict[str, RuntimeTaskBinding] = {}
        for task_id, binding in bindings.items():
            if task_id != binding.task_id:
                raise ValueError(f"runtime binding key mismatch: {task_id}")
            if binding.generation != generation:
                raise ValueError(
                    f"runtime binding generation mismatch: {task_id}="
                    f"{binding.generation}, snapshot={generation}"
                )
            prepared[task_id] = binding
        return cls(generation=generation, bindings=MappingProxyType(prepared))

    def capture(self, task_id: str) -> RuntimeTaskBinding:
        try:
            return self.bindings[task_id]
        except KeyError as exc:
            raise KeyError(f"runtime task binding not available: {task_id}") from exc

    def public_state(self) -> dict[str, Any]:
        """Return metadata only; never expose Prompt text or client configuration."""
        return {
            "generation": self.generation,
            "tasks": {
                task_id: {"model_profile": binding.model_profile}
                for task_id, binding in sorted(self.bindings.items())
            },
        }


class RuntimeBindingsHub:
    """Own the active snapshot and publish it with one reference assignment."""

    def __init__(self, snapshot: RuntimeBindingsSnapshot) -> None:
        self._active = snapshot

    @property
    def snapshot(self) -> RuntimeBindingsSnapshot:
        return self._active

    def capture(self, task_id: str) -> RuntimeTaskBinding:
        # Capture the snapshot reference first. A concurrent activate() can only make
        # future captures see the new object; this call remains on the old object.
        snapshot = self._active
        return snapshot.capture(task_id)

    def activate(self, snapshot: RuntimeBindingsSnapshot) -> RuntimeBindingsSnapshot:
        previous = self._active
        self._active = snapshot
        return previous

    def public_state(self) -> dict[str, Any]:
        return self._active.public_state()
