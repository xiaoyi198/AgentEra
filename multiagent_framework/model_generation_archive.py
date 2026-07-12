"""Encrypted persistence for model Gateway configuration generations.

The archive contains validated model configuration metadata, including provider URLs and
credential environment-variable names, so every payload is encrypted at rest and never
returned directly by public or administrative APIs. Runtime credential values are not part
of ``GatewayConfig.to_dict()`` and are never accepted by this store.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


MODEL_ARCHIVE_SCHEMA_VERSION = 1
_MODEL_ARCHIVE_PATTERN = re.compile(r"^generation-(\d{8,})\.json$")
_FORBIDDEN_KEYS = {
    "api_key", "authorization", "secret", "credential", "credentials",
    "runtime_key", "runtime_keys", "prompt", "prompts", "message", "messages",
    "response", "responses", "memory", "memories",
}


class ModelGenerationArchiveError(ValueError):
    """Raised when a model generation archive cannot be trusted or decoded."""


@dataclass(frozen=True)
class ArchivedModelGeneration:
    generation: int
    created_at: float
    config: dict[str, Any]


@dataclass
class ModelArchiveLoadResult:
    records: list[ArchivedModelGeneration] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _contains_forbidden(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if (
                key in _FORBIDDEN_KEYS
                or key.endswith("_api_key")
                or key.endswith("_secret")
                or key.endswith("_credential")
                or key.endswith("_token")
            ):
                return key
            nested = _contains_forbidden(item)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested = _contains_forbidden(item)
            if nested:
                return nested
    return None


class ModelGenerationArchiveStore:
    """Persist bounded encrypted model configuration generations."""

    def __init__(
        self,
        root: str | Path,
        *,
        key: bytes | str | None = None,
        history_limit: int = 20,
        key_env: str = "WORLD_SIM_MODEL_ARCHIVE_KEY",
    ) -> None:
        self.root = Path(root)
        self.history_limit = max(2, int(history_limit))
        self._key_env = key_env
        self._master_key = self._resolve_key(key)
        self._encryption_key = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=None,
            info=b"world-simulator/model-generation-archive/v1",
        ).derive(self._master_key)
        self._key_id = hashlib.sha256(self._encryption_key).hexdigest()[:16]

    @property
    def key_id(self) -> str:
        return self._key_id

    @staticmethod
    def generate_key() -> str:
        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    def _resolve_key(self, explicit: bytes | str | None) -> bytes:
        source = explicit if explicit is not None else os.environ.get(self._key_env)
        if source is not None:
            return self._decode_key(source)
        self.root.mkdir(parents=True, exist_ok=True)
        key_path = self.root.parent / ".model_generation.key"
        if key_path.exists():
            try:
                return self._decode_key(key_path.read_bytes().strip())
            except (OSError, ValueError) as exc:
                raise ModelGenerationArchiveError("archive_key_invalid") from exc
        raw = os.urandom(32)
        self._atomic_write(key_path, base64.urlsafe_b64encode(raw))
        self._restrict_permissions(key_path)
        return raw

    @staticmethod
    def _decode_key(value: bytes | str) -> bytes:
        raw = value.encode("ascii") if isinstance(value, str) else bytes(value)
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except Exception as exc:
            raise ModelGenerationArchiveError("archive_key_invalid") from exc
        if len(decoded) != 32:
            raise ModelGenerationArchiveError("archive_key_must_be_32_bytes_base64")
        return decoded

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _path(self, generation: int) -> Path:
        return self.root / f"generation-{generation:08d}.json"

    def save(
        self,
        generation: int,
        config: Mapping[str, Any],
        *,
        created_at: float | None = None,
    ) -> ArchivedModelGeneration:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ModelGenerationArchiveError("generation_invalid")
        if not isinstance(config, Mapping):
            raise ModelGenerationArchiveError("config_invalid")
        forbidden = _contains_forbidden(config)
        if forbidden:
            raise ModelGenerationArchiveError(f"forbidden_sensitive_field:{forbidden}")
        timestamp = float(time.time() if created_at is None else created_at)
        payload = {
            "schema_version": MODEL_ARCHIVE_SCHEMA_VERSION,
            "generation": generation,
            "created_at": timestamp,
            "config": json.loads(json.dumps(config)),
        }
        nonce = os.urandom(12)
        aad = _canonical({
            "schema_version": MODEL_ARCHIVE_SCHEMA_VERSION,
            "generation": generation,
            "created_at": timestamp,
            "key_id": self._key_id,
        })
        ciphertext = AESGCM(self._encryption_key).encrypt(nonce, _canonical(payload), aad)
        envelope = {
            "schema_version": MODEL_ARCHIVE_SCHEMA_VERSION,
            "generation": generation,
            "created_at": timestamp,
            "key_id": self._key_id,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        path = self._path(generation)
        self._atomic_write(path, _canonical(envelope) + b"\n")
        self._restrict_permissions(path)
        self._prune()
        return ArchivedModelGeneration(generation, timestamp, dict(payload["config"]))

    def _load_path(
        self, path: Path, *, expected_generation: int
    ) -> ArchivedModelGeneration:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8-sig"))
            generation = int(envelope["generation"])
            created_at = float(envelope["created_at"])
            if generation != expected_generation:
                raise ModelGenerationArchiveError("archive_generation_mismatch")
            if envelope.get("schema_version") != MODEL_ARCHIVE_SCHEMA_VERSION:
                raise ModelGenerationArchiveError("archive_schema_unsupported")
            if envelope.get("key_id") != self._key_id:
                raise ModelGenerationArchiveError("archive_key_mismatch")
            aad = _canonical({
                "schema_version": MODEL_ARCHIVE_SCHEMA_VERSION,
                "generation": generation,
                "created_at": created_at,
                "key_id": self._key_id,
            })
            plaintext = AESGCM(self._encryption_key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                aad,
            )
            payload = json.loads(plaintext.decode("utf-8"))
        except ModelGenerationArchiveError:
            raise
        except Exception as exc:
            raise ModelGenerationArchiveError("archive_decryption_failed") from exc
        if (
            payload.get("schema_version") != MODEL_ARCHIVE_SCHEMA_VERSION
            or payload.get("generation") != generation
            or not isinstance(payload.get("config"), Mapping)
        ):
            raise ModelGenerationArchiveError("archive_payload_invalid")
        forbidden = _contains_forbidden(payload["config"])
        if forbidden:
            raise ModelGenerationArchiveError(f"forbidden_sensitive_field:{forbidden}")
        return ArchivedModelGeneration(generation, created_at, dict(payload["config"]))

    def load_all(self) -> ModelArchiveLoadResult:
        if not self.root.exists():
            return ModelArchiveLoadResult()
        records: list[ArchivedModelGeneration] = []
        issues: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("generation-*.json")):
            match = _MODEL_ARCHIVE_PATTERN.fullmatch(path.name)
            if not match:
                continue
            generation = int(match.group(1))
            try:
                records.append(self._load_path(path, expected_generation=generation))
            except ModelGenerationArchiveError as exc:
                issues.append({"generation": generation, "code": str(exc).split(":", 1)[0]})
        return ModelArchiveLoadResult(
            records=sorted(records, key=lambda item: item.generation),
            issues=issues,
        )

    def _prune(self) -> None:
        paths = [
            path for path in self.root.glob("generation-*.json")
            if _MODEL_ARCHIVE_PATTERN.fullmatch(path.name)
        ]
        paths.sort(key=lambda path: int(_MODEL_ARCHIVE_PATTERN.fullmatch(path.name).group(1)))
        for path in paths[:-self.history_limit]:
            try:
                path.unlink()
            except OSError:
                pass
