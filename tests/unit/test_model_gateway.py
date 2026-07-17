"""Unit tests for task-aware model routing, fallback, metrics, and traces."""

import asyncio
import json

import pytest

from multiagent_framework.llm_client import LLMConfig, LLMResponse, TokenCounter
from multiagent_framework.model_gateway import (
    GatewayConfig,
    ModelGateway,
    ModelGatewayError,
    ModelProfile,
    ProviderConfig,
    TraceStore,
)


class FakeClient:
    def __init__(self, model: str, *, response=None, error=None):
        self.config = LLMConfig(model=model)
        self.token_counter = TokenCounter()
        self.response = response
        self.error = error
        self.calls = 0
        self.last_kwargs = {}

    async def chat(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.error:
            raise self.error
        return self.response or LLMResponse(content=self.config.model, latency_ms=4.0)

    async def close(self):
        return None


def make_config(tmp_path):
    return GatewayConfig(
        providers={
            "primary": ProviderConfig(
                name="primary",
                base_url="https://example.invalid/v1",
                api_key_env="TEST_GATEWAY_KEY",
                retry_delay_s=7.5,
                max_concurrent=2,
                queue_interval_s=0.75,
            )
        },
        models={
            "fast": ModelProfile(
                name="fast",
                provider="primary",
                model="fast-model",
                capabilities=("tools", "json"),
                max_concurrent=4,
            ),
            "deep": ModelProfile(
                name="deep",
                provider="primary",
                model="deep-model",
                capabilities=("tools", "long_context"),
                max_concurrent=2,
            ),
        },
        task_policies={
            "router.ambiguous": ["deep", "fast"],
            "agent.turn": ["fast"],
            "default": ["fast"],
        },
        source_path=str(tmp_path / "models.json"),
    )


def test_config_round_trip_persists_local_plaintext_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "local-persisted-secret")
    config = make_config(tmp_path)

    config.save_to_file()

    raw = json.loads((tmp_path / "models.json").read_text(encoding="utf-8"))
    loaded = GatewayConfig.from_file(str(tmp_path / "models.json"))
    assert raw["providers"]["primary"]["api_key"] == "local-persisted-secret"
    assert "api_key_env" not in raw["providers"]["primary"]
    assert loaded.providers["primary"].api_key == "local-persisted-secret"
    assert loaded.providers["primary"].api_key_env == ""
    assert raw["providers"]["primary"]["retry_delay_s"] == 7.5
    assert loaded.providers["primary"].retry_delay_s == 7.5
    assert raw["providers"]["primary"]["max_concurrent"] == 2
    assert raw["providers"]["primary"]["queue_interval_s"] == 0.75
    assert loaded.providers["primary"].max_concurrent == 2
    assert loaded.providers["primary"].queue_interval_s == 0.75
    assert loaded.task_policies["router.ambiguous"] == ["deep", "fast"]

def test_config_rejects_plaintext_provider_key(tmp_path):
    payload = {
        "providers": {
            "primary": {
                "base_url": "https://example.invalid/v1",
                "api_key": "must-not-be-accepted",
            }
        },
        "models": {
            "default": {"provider": "primary", "model": "test-model"}
        },
        "task_policies": {"default": ["default"]},
    }

    config = GatewayConfig.from_dict(payload, source_path=str(tmp_path / "models.json"))

    assert config.providers["primary"].api_key == "must-not-be-accepted"
    assert config.to_dict()["providers"]["primary"].get("api_key") is None
    assert config.to_persisted_dict()["providers"]["primary"]["api_key"] == "must-not-be-accepted"


def test_profiles_on_same_provider_share_one_request_queue(tmp_path):
    gateway = ModelGateway(make_config(tmp_path))

    assert gateway._clients["fast"]._request_queue is gateway._clients["deep"]._request_queue
    queue = gateway._clients["fast"]._request_queue
    assert queue is gateway._provider_queues["primary"]
    assert queue.max_concurrent == 2
    assert queue.queue_interval_s == 0.75


def test_different_providers_receive_different_request_queues(tmp_path):
    config = make_config(tmp_path)
    config.providers["secondary"] = ProviderConfig(
        name="secondary", base_url="https://secondary.invalid/v1"
    )
    config.models["deep"].provider = "secondary"
    gateway = ModelGateway(config)

    assert gateway._clients["fast"]._request_queue is not gateway._clients["deep"]._request_queue
    assert set(gateway._provider_queues) == {"primary", "secondary"}


def test_gateway_passes_safe_queue_context_and_reports_queue_snapshot(tmp_path):
    gateway = ModelGateway(make_config(tmp_path))
    fake = FakeClient("fast-model", response=LLMResponse(content="ok", input_tokens=4, output_tokens=2))
    gateway._clients["fast"] = fake

    response = asyncio.run(gateway.chat(
        task_type="agent.turn",
        messages=[{"role": "user", "content": "secret prompt"}],
        agent_id="agent-1",
        trace_context={
            "turn_id": "turn-1",
            "chain_id": "chain-1",
            "_llm_deadline_monotonic": 123.45,
        },
    ))

    assert response.content == "ok"
    assert fake.last_kwargs["deadline_monotonic"] == 123.45
    context = fake.last_kwargs["queue_context"]
    assert context["task_type"] == "agent.turn"
    assert context["profile"] == "fast"
    assert context["model"] == "fast-model"
    assert context["agent_id"] == "agent-1"
    assert context["turn_id"] == "turn-1"
    assert context["chain_id"] == "chain-1"
    assert "messages" not in context
    snapshot = gateway.metrics_snapshot()
    assert snapshot["generation"] == gateway.generation
    assert snapshot["totals"]["total_tokens"] == 6
    assert snapshot["queues"][0]["provider"] == "primary"


def test_task_policy_selects_different_models(tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    gateway._clients = {
        "fast": FakeClient("fast-model", response=LLMResponse(content="fast", latency_ms=2)),
        "deep": FakeClient("deep-model", response=LLMResponse(content="deep", latency_ms=3)),
    }

    async def run():
        agent = await gateway.for_task("agent.turn").chat([{"role": "user", "content": "x"}])
        router = await gateway.for_task("router.ambiguous").chat([{"role": "user", "content": "x"}])
        return agent, router

    agent, router = asyncio.run(run())

    assert agent.content == "fast"
    assert router.content == "deep"
    assert gateway._clients["fast"].calls == 1
    assert gateway._clients["deep"].calls == 1


def test_failure_falls_back_and_records_metrics(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(trace_path)))
    gateway._clients = {
        "fast": FakeClient(
            "fast-model",
            response=LLMResponse(content="fallback", input_tokens=7, output_tokens=3, latency_ms=5),
        ),
        "deep": FakeClient("deep-model", error=RuntimeError("provider unavailable")),
    }

    response = asyncio.run(
        gateway.for_task("router.ambiguous", agent_id="router").chat(
            [{"role": "user", "content": "route"}]
        )
    )

    assert response.content == "fallback"
    assert response.raw["model_gateway"]["model_profile"] == "fast"
    metrics = gateway.metrics_snapshot()
    assert metrics["totals"]["calls"] == 2
    assert metrics["totals"]["errors"] == 1
    assert metrics["totals"]["fallback_attempts"] == 1
    assert metrics["totals"]["total_tokens"] == 10
    traces = gateway.recent_traces()
    assert [item["model_profile"] for item in traces] == ["deep", "fast"]
    assert traces[-1]["agent_id"] == "router"
    assert all("content" not in item and "messages" not in item for item in traces)


def test_incompatible_tools_profile_is_skipped(tmp_path):
    config = make_config(tmp_path)
    config.models["deep"].capabilities = ("long_context",)
    gateway = ModelGateway(config, trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    gateway._clients = {
        "fast": FakeClient("fast-model", response=LLMResponse(content="tools-ok")),
        "deep": FakeClient("deep-model", response=LLMResponse(content="wrong")),
    }

    response = asyncio.run(
        gateway.for_task("router.ambiguous").chat(
            [{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "route"}}],
        )
    )

    assert response.content == "tools-ok"
    assert gateway._clients["deep"].calls == 0
    assert gateway._clients["fast"].calls == 1


def test_all_profiles_fail_with_clear_task_error(tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    gateway._clients = {
        "fast": FakeClient("fast-model", error=RuntimeError("fast down")),
        "deep": FakeClient("deep-model", error=RuntimeError("deep down")),
    }

    with pytest.raises(ModelGatewayError, match="router.ambiguous"):
        asyncio.run(
            gateway.for_task("router.ambiguous").chat(
                [{"role": "user", "content": "x"}]
            )
        )



def test_explicit_profile_binding_survives_agent_binding(tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    gateway._clients = {
        "fast": FakeClient("fast-model", response=LLMResponse(content="fast")),
        "deep": FakeClient("deep-model", response=LLMResponse(content="deep")),
    }

    client = gateway.for_task("agent.turn").with_profile("deep").for_agent("mayor")
    response = asyncio.run(client.chat([{"role": "user", "content": "decide"}]))

    assert client.agent_id == "mayor"
    assert client.profile_override == "deep"
    assert client.selected_profile == "deep"
    assert response.content == "deep"
    assert gateway.profile_names == ("deep", "fast")


def test_explicit_profile_binding_rejects_unknown_profile(tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))

    with pytest.raises(ModelGatewayError, match="Unknown model profile"):
        gateway.for_task("agent.turn").with_profile("missing")



def test_primary_dlc_task_binding_overrides_global_task_policy(tmp_path):
    from types import SimpleNamespace

    import main

    gateway = ModelGateway(
        make_config(tmp_path),
        trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
    )
    package = SimpleNamespace(
        manifest=SimpleNamespace(
            model_bindings={"tasks": {"action.simulation": "deep"}}
        )
    )

    client = main._task_client_for_primary_dlc(
        gateway,
        package,
        "action.simulation",
        agent_id="action_agent",
    )

    assert client.task_type == "action.simulation"
    assert client.agent_id == "action_agent"
    assert client.profile_override == "deep"
    assert client.selected_profile == "deep"


def test_primary_dlc_task_binding_rejects_unknown_profile(tmp_path):
    from types import SimpleNamespace

    import main
    from multiagent_framework.dlc_registry import DLCRegistryError

    gateway = ModelGateway(
        make_config(tmp_path),
        trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
    )
    package = SimpleNamespace(
        manifest=SimpleNamespace(
            model_bindings={"tasks": {"tianji.chat": "missing"}}
        )
    )

    with pytest.raises(DLCRegistryError, match="unknown model profile"):
        main._task_client_for_primary_dlc(
            gateway,
            package,
            "tianji.chat",
            agent_id="tianji",
        )


def test_credentials_available_exposes_key_only_in_local_display(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "runtime-only-secret")
    gateway = ModelGateway(
        make_config(tmp_path),
        trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
    )

    assert gateway.credentials_available("agent.turn")
    assert gateway.credentials_available("router.ambiguous")
    assert gateway.display_config()["providers"]["primary"]["api_key"] == "runtime-only-secret"
    assert "runtime-only-secret" not in json.dumps(gateway.public_config())


class ControlledClient(FakeClient):
    def __init__(
        self,
        model: str,
        *,
        started,
        release,
        response=None,
        error=None,
        api_key: str = "",
    ):
        super().__init__(model, response=response, error=error)
        self.config.api_key = api_key
        self.started = started
        self.release = release
        self.closed = False

    async def chat(self, **kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        return self.response or LLMResponse(content=self.config.model, latency_ms=4.0)

    async def chat_stream(self, **kwargs):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        response = self.response or LLMResponse(content=self.config.model, latency_ms=4.0)
        yield {"type": "done", "response": response}

    async def close(self):
        self.closed = True


def _replacement_config(tmp_path, *, model="replacement-model"):
    config = make_config(tmp_path)
    config.models["fast"].model = model
    config.task_policies["router.ambiguous"] = ["fast"]
    return config


def test_replace_config_advances_generation_and_existing_task_client_uses_new_policy(tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    task_client = gateway.for_task("router.ambiguous")
    old_clients = {
        "fast": FakeClient("fast-model"),
        "deep": FakeClient("deep-model"),
    }
    gateway._clients = old_clients
    new_fast = FakeClient("replacement-model", response=LLMResponse(content="new"))
    new_deep = FakeClient("deep-model")
    gateway._create_clients = lambda config, runtime_keys, profile_names=None: {
        "fast": new_fast,
        "deep": new_deep,
    }

    report = asyncio.run(gateway.replace_config(_replacement_config(tmp_path)))
    response = asyncio.run(task_client.chat([{"role": "user", "content": "x"}]))

    assert report["previous_generation"] == 1
    assert report["generation"] == 2
    assert gateway.generation == 2
    assert task_client.selected_profile == "fast"
    assert task_client.config.model == "replacement-model"
    assert response.content == "new"


def test_replace_config_validation_failure_preserves_active_generation(tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    config_before = gateway.config
    clients_before = gateway._clients
    invalid = make_config(tmp_path)
    invalid.task_policies["agent.turn"] = ["missing"]

    with pytest.raises(ValueError, match="unknown profiles"):
        asyncio.run(gateway.replace_config(invalid))

    assert gateway.config is config_before
    assert gateway._clients is clients_before
    assert gateway.generation == 1


def test_replace_config_client_build_failure_preserves_active_generation(monkeypatch, tmp_path):
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    config_before = gateway.config
    clients_before = gateway._clients

    def fail_build(config, runtime_keys, profile_names=None):
        raise RuntimeError("client build failed")

    monkeypatch.setattr(gateway, "_create_clients", fail_build)
    with pytest.raises(RuntimeError, match="client build failed"):
        asyncio.run(gateway.replace_config(_replacement_config(tmp_path)))

    assert gateway.config is config_before
    assert gateway._clients is clients_before
    assert gateway.generation == 1


def test_inflight_chat_keeps_retired_client_until_request_finishes(tmp_path):
    async def run():
        gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
        started = asyncio.Event()
        release = asyncio.Event()
        old = ControlledClient("fast-model", started=started, release=release)
        gateway._clients = {"fast": old, "deep": FakeClient("deep-model")}
        gateway._create_clients = lambda config, runtime_keys, profile_names=None: {
            "fast": FakeClient("replacement-model"),
            "deep": FakeClient("deep-model"),
        }
        pending = asyncio.create_task(
            gateway.for_task("agent.turn").chat([{"role": "user", "content": "x"}])
        )
        await started.wait()
        await gateway.replace_config(_replacement_config(tmp_path))
        assert not old.closed
        assert 1 in gateway._retired_clients
        release.set()
        response = await pending
        assert response.content == "fast-model"
        assert old.closed
        assert 1 not in gateway._retired_clients

    asyncio.run(run())


def test_inflight_stream_keeps_retired_client_until_iteration_finishes(tmp_path):
    async def run():
        gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
        started = asyncio.Event()
        release = asyncio.Event()
        old = ControlledClient("fast-model", started=started, release=release)
        gateway._clients = {"fast": old, "deep": FakeClient("deep-model")}
        gateway._create_clients = lambda config, runtime_keys, profile_names=None: {
            "fast": FakeClient("replacement-model"),
            "deep": FakeClient("deep-model"),
        }

        async def consume():
            return [chunk async for chunk in gateway.for_task("agent.turn").chat_stream(
                [{"role": "user", "content": "x"}]
            )]

        pending = asyncio.create_task(consume())
        await started.wait()
        await gateway.replace_config(_replacement_config(tmp_path))
        assert not old.closed
        release.set()
        chunks = await pending
        assert chunks[-1]["response"].content == "fast-model"
        assert old.closed

    asyncio.run(run())


def test_trace_uses_captured_profile_and_generation_across_reload(tmp_path):
    async def run():
        gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
        started = asyncio.Event()
        release = asyncio.Event()
        old = ControlledClient("fast-model", started=started, release=release)
        gateway._clients = {"fast": old, "deep": FakeClient("deep-model")}
        gateway._create_clients = lambda config, runtime_keys, profile_names=None: {
            "fast": FakeClient("replacement-model"),
            "deep": FakeClient("deep-model"),
        }
        pending = asyncio.create_task(
            gateway.for_task("agent.turn").chat([{"role": "user", "content": "x"}])
        )
        await started.wait()
        await gateway.replace_config(_replacement_config(tmp_path))
        release.set()
        response = await pending
        trace = gateway.recent_traces()[-1]
        assert response.raw["model_gateway"]["config_generation"] == 1
        assert trace["config_generation"] == 1
        assert trace["model"] == "fast-model"
        assert trace["provider"] == "primary"

    asyncio.run(run())


def test_retired_runtime_secret_remains_redacted_after_reload(tmp_path):
    async def run():
        old_secret = "old-runtime-secret"
        gateway = ModelGateway(
            make_config(tmp_path),
            trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
            runtime_keys={"primary": old_secret},
        )
        started = asyncio.Event()
        release = asyncio.Event()
        old = ControlledClient(
            "fast-model",
            started=started,
            release=release,
            error=RuntimeError(f"authorization failed for {old_secret}"),
            api_key=old_secret,
        )
        gateway._clients = {"fast": old, "deep": FakeClient("deep-model")}
        gateway._create_clients = lambda config, runtime_keys, profile_names=None: {
            "fast": FakeClient("replacement-model"),
            "deep": FakeClient("deep-model"),
        }
        pending = asyncio.create_task(
            gateway.for_task("agent.turn").chat([{"role": "user", "content": "x"}])
        )
        await started.wait()
        await gateway.replace_config(
            _replacement_config(tmp_path), runtime_keys={"primary": "new-runtime-secret"}
        )
        release.set()
        with pytest.raises(ModelGatewayError) as exc_info:
            await pending
        serialized = json.dumps(gateway.recent_traces()) + str(exc_info.value)
        assert old_secret not in serialized
        assert "[REDACTED]" in serialized

    asyncio.run(run())


def test_public_config_omits_provider_connection_and_secret_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "do-not-expose-this")
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))

    payload = gateway.public_config()
    serialized = json.dumps(payload).lower()

    assert payload["generation"] == 1
    assert payload["providers"]["primary"]["api_key_configured"] is True
    assert "base_url" not in serialized
    assert "api_key_env" not in serialized
    assert "example.invalid" not in serialized
    assert "test_gateway_key" not in serialized
    assert "do-not-expose-this" not in serialized
    assert "authorization" not in serialized


def test_preview_config_reports_only_safe_structural_diff(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_GATEWAY_KEY", "preview-secret")
    gateway = ModelGateway(make_config(tmp_path), trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    candidate = _replacement_config(tmp_path)
    candidate.models["extra"] = ModelProfile(
        name="extra", provider="primary", model="extra-model"
    )

    preview = gateway.preview_config(candidate)
    serialized = json.dumps(preview).lower()

    assert preview["current_generation"] == 1
    assert preview["next_generation"] == 2
    assert preview["profiles"]["added"] == ["extra"]
    assert preview["profiles"]["changed"] == ["fast"]
    assert "base_url" not in serialized
    assert "api_key_env" not in serialized
    assert "preview-secret" not in serialized


def test_strict_profile_disables_task_policy_fallback(tmp_path):
    gateway = ModelGateway(
        make_config(tmp_path),
        trace_store=TraceStore(str(tmp_path / "trace.jsonl")),
    )
    gateway._clients = {
        "fast": FakeClient("fast-model", response=LLMResponse(content="fallback")),
        "deep": FakeClient("deep-model", error=RuntimeError("primary failed")),
    }

    with pytest.raises(ModelGatewayError, match="No model profile succeeded"):
        asyncio.run(
            gateway.for_task(
                "router.ambiguous",
                profile_override="deep",
                strict_profile=True,
            ).chat([{"role": "user", "content": "route"}])
        )

    assert gateway._clients["deep"].calls == 1
    assert gateway._clients["fast"].calls == 0
    assert gateway.candidates(
        "router.ambiguous", "deep", strict_profile=True
    ) == ["deep"]


def test_reload_from_file_applies_and_clears_persisted_key(tmp_path):
    config = GatewayConfig(
        providers={
            "primary": ProviderConfig(
                name="primary",
                base_url="https://example.invalid/v1",
                api_key="first-local-key",
            )
        },
        models={
            "fast": ModelProfile(name="fast", provider="primary", model="fast-model")
        },
        task_policies={"default": ["fast"]},
        source_path=str(tmp_path / "models.json"),
    )
    config.save_to_file()
    gateway = ModelGateway(config, trace_store=TraceStore(str(tmp_path / "trace.jsonl")))
    assert gateway._clients["fast"].config.api_key == "first-local-key"

    config.providers["primary"].api_key = ""
    config.save_to_file()
    asyncio.run(gateway.reload_from_file())

    assert "primary" not in gateway._runtime_keys
    assert gateway._clients["fast"].config.api_key == ""
