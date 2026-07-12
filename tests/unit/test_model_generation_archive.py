from __future__ import annotations

import json

import pytest

from multiagent_framework.model_generation_archive import (
    ModelGenerationArchiveError,
    ModelGenerationArchiveStore,
)
from tests.unit.test_model_gateway import make_config


def _store(tmp_path, *, key=None, history_limit=20):
    return ModelGenerationArchiveStore(
        tmp_path / "archive",
        key=key or ModelGenerationArchiveStore.generate_key(),
        history_limit=history_limit,
    )


def test_encrypted_archive_round_trip_hides_connection_metadata(tmp_path):
    store = _store(tmp_path)
    config = make_config(tmp_path).to_dict()
    store.save(3, config, created_at=123.5)

    raw = (tmp_path / "archive" / "generation-00000003.json").read_text(
        encoding="utf-8"
    )
    loaded = store.load_all()

    assert len(loaded.records) == 1
    assert loaded.records[0].generation == 3
    assert loaded.records[0].created_at == 123.5
    assert loaded.records[0].config == config
    assert loaded.issues == []
    assert "example.invalid" not in raw
    assert "TEST_GATEWAY_KEY" not in raw
    assert "fast-model" not in raw
    envelope = json.loads(raw)
    assert set(envelope) == {
        "schema_version", "generation", "created_at", "key_id", "nonce", "ciphertext"
    }


def test_archive_rejects_plaintext_credentials_and_conversation_content(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ModelGenerationArchiveError, match="forbidden_sensitive_field"):
        store.save(1, {"providers": {"x": {"api_key": "secret"}}})
    with pytest.raises(ModelGenerationArchiveError, match="forbidden_sensitive_field"):
        store.save(1, {"messages": [{"content": "private"}]})


def test_archive_reports_tamper_and_key_mismatch_without_plaintext(tmp_path):
    key = ModelGenerationArchiveStore.generate_key()
    store = _store(tmp_path, key=key)
    store.save(1, make_config(tmp_path).to_dict())
    path = tmp_path / "archive" / "generation-00000001.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["ciphertext"] = envelope["ciphertext"][:-4] + "AAAA"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    tampered = store.load_all()
    assert tampered.records == []
    assert tampered.issues == [{"generation": 1, "code": "archive_decryption_failed"}]

    other = _store(tmp_path, key=ModelGenerationArchiveStore.generate_key())
    mismatch = other.load_all()
    assert mismatch.records == []
    assert mismatch.issues == [{"generation": 1, "code": "archive_key_mismatch"}]


def test_archive_retention_keeps_latest_generations(tmp_path):
    store = _store(tmp_path, history_limit=2)
    config = make_config(tmp_path).to_dict()
    for generation in range(1, 5):
        store.save(generation, config, created_at=float(generation))

    assert [item.generation for item in store.load_all().records] == [3, 4]
    assert sorted(path.name for path in (tmp_path / "archive").glob("*.json")) == [
        "generation-00000003.json", "generation-00000004.json"
    ]
