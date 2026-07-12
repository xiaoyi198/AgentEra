"""Security regression tests for the unauthenticated legacy settings endpoint."""

from multiagent_framework.llm_client import LLMConfig
from routers.simulation import SettingsRequest, _prepare_llm_settings_update


def old_config():
    return LLMConfig(
        base_url="https://trusted.example/v1",
        api_key="environment-secret",
        api_key_env="WORLD_SIM_API_KEY",
        model="old-model",
        timeout_s=30,
        max_concurrent=2,
    )


def test_base_url_cannot_reuse_existing_environment_secret():
    config, error = _prepare_llm_settings_update(
        SettingsRequest(base_url="https://attacker.example/v1"),
        old_config(),
    )

    assert config is None
    assert "will not be forwarded" in error


def test_environment_variable_name_cannot_be_changed_remotely():
    config, error = _prepare_llm_settings_update(
        SettingsRequest(api_key_env="SOME_OTHER_SECRET"),
        old_config(),
    )

    assert config is None
    assert "local config file" in error


def test_model_only_update_preserves_safe_provider_configuration():
    config, error = _prepare_llm_settings_update(
        SettingsRequest(
            base_url="https://trusted.example/v1",
            api_key_env="WORLD_SIM_API_KEY",
            model="new-model",
            timeout_s=45,
            max_concurrent=3,
        ),
        old_config(),
    )

    assert error == ""
    assert config is not None
    assert config.base_url == "https://trusted.example/v1"
    assert config.api_key == "environment-secret"
    assert config.api_key_env == "WORLD_SIM_API_KEY"
    assert config.model == "new-model"


def test_base_url_change_requires_new_session_key_and_drops_env_reference():
    config, error = _prepare_llm_settings_update(
        SettingsRequest(
            base_url="https://new-provider.example/v1",
            api_key="session-key",
            model="new-model",
        ),
        old_config(),
    )

    assert error == ""
    assert config is not None
    assert config.api_key == "session-key"
    assert config.api_key_env == ""
