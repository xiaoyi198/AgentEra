from __future__ import annotations

from multiagent_framework.agent_runtime import AgentConfig, OfficialAgent
from multiagent_framework.derived_metrics import DerivedMetricCatalog


class UnusedLLM:
    async def chat(self, **kwargs):  # pragma: no cover - get_messages never calls it
        raise AssertionError("LLM should not be called")


class BrokenMetricProvider:
    def agent_context(self, *args, **kwargs):
        raise RuntimeError("provider secret must not leak")


def _agent(**config_overrides):
    config = AgentConfig(
        agent_id="watcher", display_name="Watcher", system_prompt="Observe the world.",
        **config_overrides,
    )
    return OfficialAgent(config, UnusedLLM())


def _prompt(agent):
    return "\n".join(str(message.get("content", "")) for message in agent.get_messages())


def test_agent_prompt_injects_only_agent_audience_derived_metrics():
    catalog = DerivedMetricCatalog([
        {
            "metric_id": "visible", "display_name": "Visible Situation",
            "description": "safe", "unit": "pts", "audiences": ["agent"],
            "formula": {"op": "constant", "value": 42},
            "metadata": {"secret": "METADATA_SECRET"},
        },
        {
            "metric_id": "player_only", "display_name": "Player Secret",
            "audiences": ["player"],
            "formula": {"op": "constant", "value": 99},
        },
    ], generation=3)
    agent = _agent()
    agent.bind_runtime(derived_metric_provider=catalog, sim_time_provider=lambda: 120)

    prompt = _prompt(agent)

    assert "Visible Situation (visible): 42.0pts" in prompt
    assert "Player Secret" not in prompt
    assert "player_only" not in prompt
    assert "METADATA_SECRET" not in prompt
    assert '"op"' not in prompt
    assert "\u53ea\u8bfb\u6d3e\u751f\u6307\u6807\u6458\u8981" in prompt
    assert "\u4e0d\u662f\u6307\u4ee4" in prompt


def test_agent_metric_prompt_obeys_record_and_character_budgets():
    catalog = DerivedMetricCatalog([
        {
            "metric_id": f"metric_{index}", "display_name": f"Metric {index}",
            "audiences": ["agent"],
            "formula": {"op": "constant", "value": index},
        }
        for index in range(4)
    ])
    agent = _agent(derived_metric_context_max_records=2, derived_metric_context_char_budget=80)
    agent.bind_runtime(derived_metric_provider=catalog)

    metric_message = next(
        message["content"] for message in agent.get_messages()
        if "\u53ea\u8bfb\u6d3e\u751f\u6307\u6807\u6458\u8981" in str(message.get("content", ""))
    )

    assert len(metric_message) <= 80
    assert metric_message.count("\n- ") <= 2


def test_agent_metric_provider_failure_is_fail_closed(caplog):
    agent = _agent()
    agent.bind_runtime(derived_metric_provider=BrokenMetricProvider())

    prompt = _prompt(agent)

    assert "provider secret must not leak" not in prompt
    assert "\u53ea\u8bfb\u6d3e\u751f\u6307\u6807\u6458\u8981" not in prompt
    assert "derived metric projection failed closed" in caplog.text


def test_agent_metric_provider_can_be_hot_swapped_between_generations():
    old_catalog = DerivedMetricCatalog([{
        "metric_id": "state", "display_name": "Old Situation", "audiences": ["agent"],
        "formula": {"op": "constant", "value": 1},
    }], generation=1)
    new_catalog = DerivedMetricCatalog([{
        "metric_id": "state", "display_name": "New Situation", "audiences": ["agent"],
        "formula": {"op": "constant", "value": 2},
    }], generation=2)
    agent = _agent()
    agent.set_derived_metric_provider(old_catalog)
    assert "Old Situation (state): 1.0" in _prompt(agent)

    agent.set_derived_metric_provider(new_catalog)
    prompt = _prompt(agent)

    assert "New Situation (state): 2.0" in prompt
    assert "Old Situation" not in prompt
