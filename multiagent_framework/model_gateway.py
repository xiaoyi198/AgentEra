"""Task-aware multi-provider model routing, metrics, and redacted tracing."""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from urllib.parse import urlsplit

from .llm_client import LLMClient, LLMConfig, LLMResponse

logger = logging.getLogger(__name__)

DEFAULT_TASK_TYPES = (
    "router.simple",
    "router.ambiguous",
    "agent.turn",
    "action.simulation",
    "memory.compress",
    "tianji.chat",
)


class ModelGatewayError(RuntimeError):
    """Raised after every compatible profile in a task policy has failed."""


@dataclass
class ProviderConfig:
    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = ""
    timeout_s: int = 30

    def __post_init__(self) -> None:
        self.timeout_s = max(1, int(self.timeout_s))
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Provider {self.name!r} has an invalid HTTP(S) base_url")
        if self.api_key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise ValueError(f"Provider {self.name!r} has an invalid api_key_env name")


@dataclass
class ModelProfile:
    name: str
    provider: str
    model: str
    capabilities: tuple[str, ...] = ()
    max_concurrent: int = 4
    max_retries: int = 2

    def __post_init__(self) -> None:
        self.capabilities = tuple(str(item) for item in self.capabilities)
        self.max_concurrent = max(1, int(self.max_concurrent))
        self.max_retries = max(0, int(self.max_retries))

    def supports_tools(self) -> bool:
        if not self.capabilities:
            return True
        return bool({"tools", "function_calling"}.intersection(self.capabilities))


@dataclass
class GatewayConfig:
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelProfile]
    task_policies: dict[str, list[str]]
    source_path: str = "config/models.json"
    legacy_mode: bool = False

    @classmethod
    def from_file(cls, path: str = "config/models.json") -> "GatewayConfig":
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return cls.from_dict(data, source_path=path)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        source_path: str = "config/models.json",
    ) -> "GatewayConfig":
        raw_providers = dict(data.get("providers", {}))
        plaintext = [name for name, raw in raw_providers.items() if raw.get("api_key")]
        if plaintext:
            raise ValueError(
                f"Plaintext api_key is forbidden for providers: {plaintext}; use api_key_env"
            )
        providers = {
            name: ProviderConfig(
                name=name,
                base_url=str(raw.get("base_url", ProviderConfig.base_url)),
                api_key_env=str(raw.get("api_key_env", "") or ""),
                timeout_s=raw.get("timeout_s", 30),
            )
            for name, raw in raw_providers.items()
        }
        models = {
            name: ModelProfile(
                name=name,
                provider=str(raw.get("provider", "")),
                model=str(raw.get("model", "")),
                capabilities=tuple(raw.get("capabilities", ())),
                max_concurrent=raw.get("max_concurrent", 4),
                max_retries=raw.get("max_retries", 2),
            )
            for name, raw in dict(data.get("models", {})).items()
        }
        task_policies = {
            str(task): [str(profile) for profile in profiles]
            for task, profiles in dict(data.get("task_policies", {})).items()
        }
        config = cls(
            providers=providers,
            models=models,
            task_policies=task_policies,
            source_path=source_path,
            legacy_mode=False,
        )
        config.validate()
        return config

    @classmethod
    def from_legacy(
        cls,
        config: LLMConfig,
        source_path: str = "config/llm.json",
    ) -> "GatewayConfig":
        provider = ProviderConfig(
            name="legacy",
            base_url=config.base_url,
            api_key_env=config.api_key_env,
            timeout_s=config.timeout_s,
        )
        profile = ModelProfile(
            name="default",
            provider="legacy",
            model=config.model,
            capabilities=("tools", "json", "stream"),
            max_concurrent=config.max_concurrent,
            max_retries=2,
        )
        return cls(
            providers={"legacy": provider},
            models={"default": profile},
            task_policies={task: ["default"] for task in (*DEFAULT_TASK_TYPES, "default")},
            source_path=source_path,
            legacy_mode=True,
        )

    def validate(self) -> None:
        if not self.providers:
            raise ValueError("Model gateway requires at least one provider")
        if not self.models:
            raise ValueError("Model gateway requires at least one model profile")
        for name, profile in self.models.items():
            if profile.provider not in self.providers:
                raise ValueError(
                    f"Model profile {name!r} references unknown provider {profile.provider!r}"
                )
            if not profile.model:
                raise ValueError(f"Model profile {name!r} has an empty model id")
        for task, profiles in self.task_policies.items():
            if not profiles:
                raise ValueError(f"Task policy {task!r} has no profiles")
            unknown = [name for name in profiles if name not in self.models]
            if unknown:
                raise ValueError(f"Task policy {task!r} references unknown profiles: {unknown}")

    def save_to_file(self, path: str | None = None) -> None:
        target = Path(path or self.source_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "providers": {
                name: {
                    "base_url": provider.base_url,
                    "api_key_env": provider.api_key_env,
                    "timeout_s": provider.timeout_s,
                }
                for name, provider in self.providers.items()
            },
            "models": {
                name: {
                    "provider": profile.provider,
                    "model": profile.model,
                    "capabilities": list(profile.capabilities),
                    "max_concurrent": profile.max_concurrent,
                    "max_retries": profile.max_retries,
                }
                for name, profile in self.models.items()
            },
            "task_policies": self.task_policies,
        }
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def to_display_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "legacy_mode": self.legacy_mode,
            "providers": {
                name: {
                    "base_url": provider.base_url,
                    "api_key_env": provider.api_key_env,
                    "api_key_configured": bool(
                        provider.api_key_env and os.environ.get(provider.api_key_env)
                    ),
                    "timeout_s": provider.timeout_s,
                }
                for name, provider in self.providers.items()
            },
            "models": {
                name: {
                    "provider": profile.provider,
                    "model": profile.model,
                    "capabilities": list(profile.capabilities),
                    "max_concurrent": profile.max_concurrent,
                    "max_retries": profile.max_retries,
                }
                for name, profile in self.models.items()
            },
            "task_policies": self.task_policies,
        }


@dataclass
class TraceContext:
    trace_id: str = ""
    chain_id: str = ""
    turn_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    agent_id: str = ""
    task_type: str = ""
    sim_time_snapshot: float | None = None
    round: int | None = None
    tool_name: str = ""
    command_id: str = ""

    @classmethod
    def coerce(cls, value: "TraceContext | dict[str, Any] | None") -> "TraceContext":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return replace(value)
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})


class TraceStore:
    """Append-only JSONL trace storage with rotation and no prompt/response bodies."""

    def __init__(
        self,
        path: str = "runtime/llm_traces.jsonl",
        max_bytes: int = 2_000_000,
        backups: int = 3,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(1, int(backups))
        self._lock = threading.Lock()

    def append(self, entry: dict[str, Any]) -> None:
        safe_entry = self._sanitize(entry)
        line = json.dumps(safe_entry, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed(len(line.encode("utf-8")))
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line)

    def recent(self, limit: int = 100, task_type: str = "") -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        entries: list[dict[str, Any]] = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if task_type and entry.get("task_type") != task_type:
                continue
            entries.append(entry)
            if len(entries) >= max(1, min(int(limit), 1000)):
                break
        entries.reverse()
        return entries

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists() or self.path.stat().st_size + incoming_bytes <= self.max_bytes:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                source.replace(self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))

    @staticmethod
    def _sanitize(entry: dict[str, Any]) -> dict[str, Any]:
        blocked = {"messages", "prompt", "response", "content", "api_key", "authorization"}
        return {
            key: value
            for key, value in entry.items()
            if key.lower() not in blocked
        }


class GatewayMetrics:
    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def record(
        self,
        *,
        task_type: str,
        profile: str,
        provider: str,
        model: str,
        success: bool,
        fallback: bool,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        key = f"{task_type}|{profile}|{provider}|{model}"
        with self._lock:
            item = self._entries.setdefault(
                key,
                {
                    "task_type": task_type,
                    "model_profile": profile,
                    "provider": provider,
                    "model": model,
                    "calls": 0,
                    "successes": 0,
                    "errors": 0,
                    "fallback_attempts": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "latency_ms_total": 0.0,
                },
            )
            item["calls"] += 1
            item["successes" if success else "errors"] += 1
            item["fallback_attempts"] += int(fallback)
            item["input_tokens"] += int(input_tokens)
            item["output_tokens"] += int(output_tokens)
            item["latency_ms_total"] += float(latency_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            entries = [dict(item) for item in self._entries.values()]
        totals = {
            "calls": sum(item["calls"] for item in entries),
            "successes": sum(item["successes"] for item in entries),
            "errors": sum(item["errors"] for item in entries),
            "fallback_attempts": sum(item["fallback_attempts"] for item in entries),
            "input_tokens": sum(item["input_tokens"] for item in entries),
            "output_tokens": sum(item["output_tokens"] for item in entries),
        }
        for item in entries:
            calls = max(1, item["calls"])
            item["avg_latency_ms"] = round(item["latency_ms_total"] / calls, 2)
            item["total_tokens"] = item["input_tokens"] + item["output_tokens"]
            item["latency_ms_total"] = round(item["latency_ms_total"], 2)
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
        return {"totals": totals, "entries": entries}


class ModelGateway:
    """Route a logical LLM task across compatible model profiles with fallback."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        trace_store: TraceStore | None = None,
        runtime_keys: dict[str, str] | None = None,
    ) -> None:
        config.validate()
        self.config = copy.deepcopy(config)
        self.trace_store = trace_store or TraceStore()
        self.metrics = GatewayMetrics()
        self._state_lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._runtime_keys = dict(runtime_keys or {})
        self._redaction_keys = {key for key in self._runtime_keys.values() if key}
        self._clients: dict[str, LLMClient] = {}
        self._generation = 1
        self._active_calls: dict[int, int] = {}
        self._retired_clients: dict[int, dict[str, LLMClient]] = {}
        self._closed = False
        self._build_clients()
        self._remember_client_secrets(self._clients)

    @classmethod
    def from_files(
        cls,
        models_path: str = "config/models.json",
        legacy_path: str = "config/llm.json",
        *,
        trace_store: TraceStore | None = None,
    ) -> "ModelGateway":
        if Path(models_path).exists():
            return cls(GatewayConfig.from_file(models_path), trace_store=trace_store)
        if Path(legacy_path).exists():
            legacy = LLMConfig.from_file(legacy_path)
        else:
            logger.warning("No model gateway or legacy LLM config found; using defaults")
            legacy = LLMConfig()
        gateway_config = GatewayConfig.from_legacy(legacy, source_path=legacy_path)
        runtime_keys = {"legacy": legacy.api_key} if legacy.api_key else {}
        return cls(gateway_config, trace_store=trace_store, runtime_keys=runtime_keys)

    @staticmethod
    def _create_clients(
        config: GatewayConfig,
        runtime_keys: dict[str, str],
        profile_names: Iterable[str] | None = None,
    ) -> dict[str, LLMClient]:
        clients: dict[str, LLMClient] = {}
        names = profile_names if profile_names is not None else config.models.keys()
        for name in names:
            profile = config.models[name]
            provider = config.providers[profile.provider]
            api_key = runtime_keys.get(provider.name, "")
            if not api_key and provider.api_key_env:
                api_key = os.environ.get(provider.api_key_env, "")
            llm_config = LLMConfig(
                base_url=provider.base_url,
                api_key=api_key,
                api_key_env=provider.api_key_env,
                model=profile.model,
                timeout_s=provider.timeout_s,
                max_concurrent=profile.max_concurrent,
            )
            clients[name] = LLMClient(llm_config, debug_log_path=None)
        return clients

    def _build_clients(self, profile_names: Iterable[str] | None = None) -> None:
        self._clients.update(
            self._create_clients(self.config, self._runtime_keys, profile_names)
        )

    @staticmethod
    def _candidate_names(
        config: GatewayConfig,
        task_type: str,
        profile_override: str = "",
        strict_profile: bool = False,
    ) -> list[str]:
        profiles: list[str] = []
        if profile_override:
            profiles.append(profile_override)
        if not (strict_profile and profile_override):
            profiles.extend(config.task_policies.get(task_type, ()))
        if not profiles:
            profiles.extend(config.task_policies.get("default", ()))
        if not profiles:
            profiles.extend(config.models.keys())
        result: list[str] = []
        for name in profiles:
            if name in config.models and name not in result:
                result.append(name)
        return result

    def _acquire_runtime(
        self,
        task_type: str,
        profile_override: str = "",
        strict_profile: bool = False,
    ) -> tuple[int, GatewayConfig, dict[str, LLMClient], list[str]]:
        with self._state_lock:
            if self._closed:
                raise ModelGatewayError("Model gateway is closed")
            generation = self._generation
            self._active_calls[generation] = self._active_calls.get(generation, 0) + 1
            config = self.config
            clients = self._clients
            candidates = self._candidate_names(
                config, task_type, profile_override, strict_profile
            )
            return generation, config, clients, candidates

    async def _release_runtime(self, generation: int) -> None:
        clients: dict[str, LLMClient] = {}
        with self._state_lock:
            remaining = self._active_calls.get(generation, 0) - 1
            if remaining > 0:
                self._active_calls[generation] = remaining
            else:
                self._active_calls.pop(generation, None)
                clients = self._retired_clients.pop(generation, {})
        await self._close_clients(clients)

    @staticmethod
    async def _close_clients(clients: dict[str, LLMClient]) -> None:
        for client in clients.values():
            try:
                await client.close()
            except Exception as exc:
                logger.warning("Failed to close retired model client: %s", exc)

    def for_task(
        self,
        task_type: str,
        *,
        agent_id: str = "",
        profile_override: str = "",
        strict_profile: bool = False,
    ) -> "TaskLLMClient":
        return TaskLLMClient(
            gateway=self,
            task_type=task_type,
            agent_id=agent_id,
            profile_override=profile_override,
            strict_profile=strict_profile,
        )

    def has_profile(self, profile_name: str) -> bool:
        """Return whether a public model profile exists without exposing provider secrets."""
        with self._state_lock:
            return profile_name in self.config.models

    @property
    def profile_names(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(sorted(self.config.models))

    def profile_metadata(self, profile_name: str) -> dict[str, Any]:
        """Return secret-free metadata for one model profile."""
        with self._state_lock:
            if profile_name not in self.config.models:
                raise ModelGatewayError(f"Unknown model profile: {profile_name!r}")
            profile = self.config.models[profile_name]
            return {
                "profile": profile.name,
                "provider": profile.provider,
                "model": profile.model,
                "capabilities": list(profile.capabilities),
                "supports_tools": profile.supports_tools(),
            }

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    def credentials_available(
        self,
        task_type: str,
        profile_override: str = "",
        *,
        strict_profile: bool = False,
    ) -> bool:
        """Return whether any candidate has a runtime credential, without exposing it."""
        with self._state_lock:
            candidates = self._candidate_names(
                self.config, task_type, profile_override, strict_profile
            )
            return any(
                bool(self._clients[name].config.api_key)
                for name in candidates
                if name in self._clients
            )

    def candidates(
        self,
        task_type: str,
        profile_override: str = "",
        *,
        strict_profile: bool = False,
    ) -> list[str]:
        with self._state_lock:
            return self._candidate_names(
                self.config, task_type, profile_override, strict_profile
            )

    def selected_client_config(
        self,
        task_type: str,
        profile_override: str = "",
        *,
        strict_profile: bool = False,
    ) -> LLMConfig:
        with self._state_lock:
            candidates = self._candidate_names(
                self.config, task_type, profile_override, strict_profile
            )
            if not candidates:
                raise ModelGatewayError(f"No model candidates for task {task_type!r}")
            return self._clients[candidates[0]].config

    def selected_token_counter(
        self,
        task_type: str,
        profile_override: str = "",
        *,
        strict_profile: bool = False,
    ):
        with self._state_lock:
            candidates = self._candidate_names(
                self.config, task_type, profile_override, strict_profile
            )
            if not candidates:
                raise ModelGatewayError(f"No model candidates for task {task_type!r}")
            return self._clients[candidates[0]].token_counter

    def _remember_client_secrets(self, clients: dict[str, LLMClient]) -> None:
        secrets = {
            client.config.api_key
            for client in clients.values()
            if getattr(client.config, "api_key", "")
        }
        if secrets:
            with self._state_lock:
                self._redaction_keys.update(secrets)

    async def chat(
        self,
        *,
        task_type: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_retries: int | None = None,
        trace_context: TraceContext | dict[str, Any] | None = None,
        profile_override: str = "",
        agent_id: str = "",
        strict_profile: bool = False,
    ) -> LLMResponse:
        context = self._prepare_context(trace_context, task_type, agent_id)
        failures: list[str] = []
        generation, config, clients, candidates = self._acquire_runtime(
            task_type, profile_override, strict_profile,
        )
        try:
            for index, profile_name in enumerate(candidates):
                profile = config.models[profile_name]
                if tools and not profile.supports_tools():
                    failures.append(f"{profile_name}: missing tools capability")
                    self._record_attempt(
                        context=context,
                        profile_name=profile_name,
                        profile=profile,
                        generation=generation,
                        success=False,
                        fallback=index > 0,
                        latency_ms=0.0,
                        error="missing tools capability",
                    )
                    continue
                client = clients[profile_name]
                started = time.monotonic()
                try:
                    response = await client.chat(
                        messages=messages,
                        tools=tools,
                        temperature=temperature,
                        max_retries=profile.max_retries if max_retries is None else max_retries,
                    )
                except Exception as exc:
                    latency_ms = (time.monotonic() - started) * 1000
                    redacted = self._redact_error(str(exc))
                    failures.append(
                        f"{profile_name}: {type(exc).__name__}: {redacted}"
                    )
                    self._record_attempt(
                        context=context,
                        profile_name=profile_name,
                        profile=profile,
                        generation=generation,
                        success=False,
                        fallback=index > 0,
                        latency_ms=latency_ms,
                        error=redacted,
                    )
                    continue
                self._record_attempt(
                    context=context,
                    profile_name=profile_name,
                    profile=profile,
                    generation=generation,
                    success=True,
                    fallback=index > 0,
                    latency_ms=response.latency_ms,
                    response=response,
                )
                response.raw.setdefault("model_gateway", {})
                response.raw["model_gateway"].update(
                    {
                        "task_type": task_type,
                        "model_profile": profile_name,
                        "provider": profile.provider,
                        "model": profile.model,
                        "config_generation": generation,
                        "trace_id": context.trace_id,
                    }
                )
                return response
            raise ModelGatewayError(
                f"No model profile succeeded for task {task_type!r}: {'; '.join(failures)}"
            )
        finally:
            await self._release_runtime(generation)

    async def chat_stream(
        self,
        *,
        task_type: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        trace_context: TraceContext | dict[str, Any] | None = None,
        profile_override: str = "",
        agent_id: str = "",
        strict_profile: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        context = self._prepare_context(trace_context, task_type, agent_id)
        failures: list[str] = []
        generation, config, clients, candidates = self._acquire_runtime(
            task_type, profile_override, strict_profile,
        )
        try:
            for index, profile_name in enumerate(candidates):
                profile = config.models[profile_name]
                if tools and not profile.supports_tools():
                    failures.append(f"{profile_name}: missing tools capability")
                    continue
                started = time.monotonic()
                yielded = False
                final_response: LLMResponse | None = None
                try:
                    async for chunk in clients[profile_name].chat_stream(
                        messages=messages,
                        tools=tools,
                        temperature=temperature,
                    ):
                        yielded = True
                        if chunk.get("type") == "done":
                            final_response = chunk.get("response")
                        yield chunk
                except Exception as exc:
                    latency_ms = (time.monotonic() - started) * 1000
                    redacted = self._redact_error(str(exc))
                    self._record_attempt(
                        context=context,
                        profile_name=profile_name,
                        profile=profile,
                        generation=generation,
                        success=False,
                        fallback=index > 0,
                        latency_ms=latency_ms,
                        error=redacted,
                    )
                    if yielded:
                        raise
                    failures.append(
                        f"{profile_name}: {type(exc).__name__}: {redacted}"
                    )
                    continue
                latency_ms = (
                    final_response.latency_ms
                    if final_response is not None
                    else (time.monotonic() - started) * 1000
                )
                self._record_attempt(
                    context=context,
                    profile_name=profile_name,
                    profile=profile,
                    generation=generation,
                    success=True,
                    fallback=index > 0,
                    latency_ms=latency_ms,
                    response=final_response,
                )
                return
            raise ModelGatewayError(
                f"No streaming profile succeeded for task {task_type!r}: {'; '.join(failures)}"
            )
        finally:
            await self._release_runtime(generation)

    async def update_profile_from_legacy(
        self,
        profile_name: str,
        new_config: LLMConfig,
    ) -> dict[str, Any]:
        with self._state_lock:
            candidate = copy.deepcopy(self.config)
            runtime_keys = dict(self._runtime_keys)
        if profile_name not in candidate.models:
            raise KeyError(f"Unknown model profile: {profile_name}")
        profile = candidate.models[profile_name]
        provider = candidate.providers[profile.provider]
        provider.base_url = new_config.base_url
        provider.api_key_env = new_config.api_key_env
        provider.timeout_s = new_config.timeout_s
        profile.model = new_config.model
        profile.max_concurrent = new_config.max_concurrent
        if new_config.api_key:
            runtime_keys[provider.name] = new_config.api_key
        else:
            runtime_keys.pop(provider.name, None)
        return await self.replace_config(candidate, runtime_keys=runtime_keys)

    def preview_config(self, new_config: GatewayConfig) -> dict[str, Any]:
        candidate = copy.deepcopy(new_config)
        candidate.validate()
        with self._state_lock:
            current = self.config
            generation = self._generation
            runtime_keys = dict(self._runtime_keys)
        old_profiles = set(current.models)
        new_profiles = set(candidate.models)
        changed_profiles = sorted(
            name
            for name in old_profiles & new_profiles
            if current.models[name] != candidate.models[name]
        )
        old_tasks = set(current.task_policies)
        new_tasks = set(candidate.task_policies)
        changed_tasks = sorted(
            name
            for name in old_tasks | new_tasks
            if current.task_policies.get(name) != candidate.task_policies.get(name)
        )
        return {
            "current_generation": generation,
            "next_generation": generation + 1,
            "profiles": {
                "added": sorted(new_profiles - old_profiles),
                "removed": sorted(old_profiles - new_profiles),
                "changed": changed_profiles,
            },
            "task_policies_changed": changed_tasks,
            "provider_count": len(candidate.providers),
            "providers": {
                name: {
                    "api_key_configured": bool(
                        runtime_keys.get(name)
                        or (provider.api_key_env and os.environ.get(provider.api_key_env))
                    )
                }
                for name, provider in candidate.providers.items()
            },
        }

    async def replace_config(
        self,
        new_config: GatewayConfig,
        *,
        runtime_keys: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Validate, prebuild, and atomically activate a configuration generation."""
        candidate = copy.deepcopy(new_config)
        candidate.validate()
        close_now: dict[str, LLMClient] = {}
        with self._reload_lock:
            with self._state_lock:
                if self._closed:
                    raise ModelGatewayError("Model gateway is closed")
                previous_keys = dict(self._runtime_keys)
            next_runtime_keys = dict(previous_keys if runtime_keys is None else runtime_keys)
            next_runtime_keys = {
                name: key
                for name, key in next_runtime_keys.items()
                if name in candidate.providers and key
            }
            new_clients = self._create_clients(candidate, next_runtime_keys)
            self._remember_client_secrets(new_clients)
            with self._state_lock:
                if self._closed:
                    close_now = new_clients
                else:
                    old_generation = self._generation
                    old_clients = self._clients
                    self.config = candidate
                    self._clients = new_clients
                    self._runtime_keys = next_runtime_keys
                    self._redaction_keys.update(next_runtime_keys.values())
                    self._generation = old_generation + 1
                    if self._active_calls.get(old_generation, 0):
                        self._retired_clients[old_generation] = old_clients
                    else:
                        close_now = old_clients
                    report = {
                        "status": "reloaded",
                        "previous_generation": old_generation,
                        "generation": self._generation,
                        "profile_count": len(candidate.models),
                        "task_policy_count": len(candidate.task_policies),
                        "provider_count": len(candidate.providers),
                    }
            closed_during_build = close_now is new_clients
        await self._close_clients(close_now)
        if closed_during_build:
            raise ModelGatewayError("Model gateway is closed")
        return report

    def preview_from_file(self, path: str | None = None) -> dict[str, Any]:
        with self._state_lock:
            source_path = path or self.config.source_path
        return self.preview_config(GatewayConfig.from_file(source_path))

    async def reload_from_file(self, path: str | None = None) -> dict[str, Any]:
        with self._state_lock:
            source_path = path or self.config.source_path
        candidate = GatewayConfig.from_file(source_path)
        return await self.replace_config(candidate)

    def save_configuration(self) -> None:
        with self._state_lock:
            config = copy.deepcopy(self.config)
            runtime_keys = dict(self._runtime_keys)
        if config.legacy_mode:
            profile_name = next(iter(config.models))
            profile = config.models[profile_name]
            provider = config.providers[profile.provider]
            LLMConfig(
                base_url=provider.base_url,
                api_key=runtime_keys.get(provider.name, ""),
                api_key_env=provider.api_key_env,
                model=profile.model,
                timeout_s=provider.timeout_s,
                max_concurrent=profile.max_concurrent,
            ).save_to_file(config.source_path)
        else:
            config.save_to_file()

    def display_config(self) -> dict[str, Any]:
        with self._state_lock:
            config = copy.deepcopy(self.config)
            runtime_keys = dict(self._runtime_keys)
            generation = self._generation
        display = config.to_display_dict()
        display["generation"] = generation
        for name, provider in display["providers"].items():
            provider["api_key_configured"] = bool(
                runtime_keys.get(name)
                or (provider.get("api_key_env") and os.environ.get(provider["api_key_env"]))
            )
        return display

    def public_config(self) -> dict[str, Any]:
        """Return operational model metadata without URLs, env names, or secrets."""
        with self._state_lock:
            config = copy.deepcopy(self.config)
            clients = dict(self._clients)
            generation = self._generation
        return {
            "generation": generation,
            "legacy_mode": config.legacy_mode,
            "providers": {
                name: {
                    "api_key_configured": any(
                        bool(getattr(clients.get(profile_name), "config", None).api_key)
                        for profile_name, profile in config.models.items()
                        if profile.provider == name and profile_name in clients
                    )
                }
                for name in config.providers
            },
            "models": {
                name: {
                    "provider": profile.provider,
                    "model": profile.model,
                    "capabilities": list(profile.capabilities),
                    "max_concurrent": profile.max_concurrent,
                    "max_retries": profile.max_retries,
                }
                for name, profile in config.models.items()
            },
            "task_policies": copy.deepcopy(config.task_policies),
        }

    def metrics_snapshot(self) -> dict[str, Any]:
        return self.metrics.snapshot()

    def recent_traces(self, limit: int = 100, task_type: str = "") -> list[dict[str, Any]]:
        return self.trace_store.recent(limit=limit, task_type=task_type)

    async def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            groups = [self._clients, *self._retired_clients.values()]
            self._clients = {}
            self._retired_clients = {}
        unique: dict[int, LLMClient] = {}
        for group in groups:
            for client in group.values():
                unique[id(client)] = client
        await self._close_clients({str(key): value for key, value in unique.items()})

    def _prepare_context(
        self,
        value: TraceContext | dict[str, Any] | None,
        task_type: str,
        agent_id: str,
    ) -> TraceContext:
        context = TraceContext.coerce(value)
        context.trace_id = context.trace_id or uuid.uuid4().hex
        context.turn_id = context.turn_id or uuid.uuid4().hex
        context.task_type = task_type
        context.agent_id = context.agent_id or agent_id
        return context

    def _record_attempt(
        self,
        *,
        context: TraceContext,
        profile_name: str,
        profile: ModelProfile,
        generation: int,
        success: bool,
        fallback: bool,
        latency_ms: float,
        response: LLMResponse | None = None,
        error: str = "",
    ) -> None:
        span_id = uuid.uuid4().hex
        input_tokens = response.input_tokens if response else 0
        output_tokens = response.output_tokens if response else 0
        self.metrics.record(
            task_type=context.task_type,
            profile=profile_name,
            provider=profile.provider,
            model=profile.model,
            success=success,
            fallback=fallback,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )
        entry = asdict(context)
        entry.update(
            {
                "timestamp": time.time(),
                "span_id": span_id,
                "parent_span_id": context.span_id or context.parent_span_id,
                "model_profile": profile_name,
                "provider": profile.provider,
                "model": profile.model,
                "config_generation": generation,
                "success": success,
                "fallback": fallback,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "latency_ms": round(float(latency_ms), 2),
                "error": error[:500],
                "termination_reason": "completed" if success else "model_error",
            }
        )
        try:
            self.trace_store.append(entry)
        except OSError as exc:
            logger.warning("Failed to persist LLM trace: %s", exc)

    def _redact_error(self, message: str) -> str:
        result = message
        with self._state_lock:
            keys = tuple(self._redaction_keys)
        for key in keys:
            if key:
                result = result.replace(key, "[REDACTED]")
        return result


class TaskLLMClient:
    """Compatibility adapter that binds the legacy client interface to one task."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        task_type: str,
        agent_id: str = "",
        profile_override: str = "",
        strict_profile: bool = False,
    ) -> None:
        self.gateway = gateway
        self.task_type = task_type
        self.agent_id = agent_id
        self.profile_override = profile_override
        self.strict_profile = bool(strict_profile)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_retries: int | None = None,
        trace_context: TraceContext | dict[str, Any] | None = None,
    ) -> LLMResponse:
        return await self.gateway.chat(
            task_type=self.task_type,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_retries=max_retries,
            trace_context=trace_context,
            profile_override=self.profile_override,
            agent_id=self.agent_id,
            strict_profile=self.strict_profile,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        trace_context: TraceContext | dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for chunk in self.gateway.chat_stream(
            task_type=self.task_type,
            messages=messages,
            tools=tools,
            temperature=temperature,
            trace_context=trace_context,
            profile_override=self.profile_override,
            agent_id=self.agent_id,
            strict_profile=self.strict_profile,
        ):
            yield chunk

    def for_agent(
        self,
        agent_id: str,
        *,
        profile_override: str | None = None,
    ) -> "TaskLLMClient":
        return TaskLLMClient(
            gateway=self.gateway,
            task_type=self.task_type,
            agent_id=agent_id,
            profile_override=(
                self.profile_override if profile_override is None else profile_override
            ),
            strict_profile=self.strict_profile,
        )

    def with_profile(
        self, profile_name: str, *, strict: bool = False
    ) -> "TaskLLMClient":
        """Bind an explicit profile, optionally disabling task-policy fallbacks."""
        if not self.gateway.has_profile(profile_name):
            raise ModelGatewayError(f"Unknown model profile: {profile_name!r}")
        return TaskLLMClient(
            gateway=self.gateway,
            task_type=self.task_type,
            agent_id=self.agent_id,
            profile_override=profile_name,
            strict_profile=strict,
        )

    @property
    def selected_profile(self) -> str:
        candidates = self.gateway.candidates(
            self.task_type,
            self.profile_override,
            strict_profile=self.strict_profile,
        )
        if not candidates:
            raise ModelGatewayError(f"No model candidates for task {self.task_type!r}")
        return candidates[0]

    @property
    def config(self) -> LLMConfig:
        return self.gateway.selected_client_config(
            self.task_type,
            self.profile_override,
            strict_profile=self.strict_profile,
        )

    @property
    def token_counter(self):
        return self.gateway.selected_token_counter(
            self.task_type,
            self.profile_override,
            strict_profile=self.strict_profile,
        )

    async def update_config(self, new_config: LLMConfig) -> None:
        await self.gateway.update_profile_from_legacy(self.selected_profile, new_config)

    def save_configuration(self) -> None:
        self.gateway.save_configuration()

    async def close(self) -> None:
        await self.gateway.close()
