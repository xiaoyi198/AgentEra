"""Encrypted and authenticated persistence for DLC runtime generations.

Archives contain configuration blueprints only. Agent memories, provider clients,
API credentials, local provider URLs, and Authorization values are never accepted
by this storage boundary.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
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


ARCHIVE_SCHEMA_VERSION = 1
BLUEPRINT_SCHEMA_VERSION = 1
_ARCHIVE_PATTERN = re.compile(r"^generation-(\d{8,})\.json$")
_FORBIDDEN_KEYS = {
    "api_key", "api_key_env", "authorization", "provider_url", "base_url",
    "client", "clients", "llm", "secret", "token_env", "config_path",
    "credential", "credentials", "credential_env", "environment_variable",
}
_SENSITIVE_ENV_REFERENCE = re.compile(
    r"(?:\$\{)?[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|CREDENTIAL)(?:\})?"
)


class GenerationArchiveError(ValueError):
    """Raised when a generation archive cannot be trusted or decoded."""


@dataclass(frozen=True)
class ArchivedGeneration:
    generation: int
    created_at: float
    blueprint: dict[str, Any]


@dataclass
class ArchiveLoadResult:
    records: list[ArchivedGeneration] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if (
                key in _FORBIDDEN_KEYS
                or key.endswith("_api_key")
                or key.endswith("_token")
                or key.endswith("_secret")
                or key.endswith("_credential")
                or key.endswith("_env")
            ):
                return key
            nested = _contains_forbidden_key(item)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for item in value:
            nested = _contains_forbidden_key(item)
            if nested:
                return nested
    elif isinstance(value, str) and _SENSITIVE_ENV_REFERENCE.search(value):
        return "sensitive_environment_reference"
    return None


class GenerationArchiveStore:
    """Persist bounded DLC generation blueprints with AES-GCM and HMAC-SHA256."""

    def __init__(
        self,
        root: str | Path,
        *,
        key: bytes | str | None = None,
        history_limit: int = 20,
        key_env: str = "WORLD_SIM_DLC_ARCHIVE_KEY",
    ) -> None:
        self.root = Path(root)
        self.history_limit = max(1, int(history_limit))
        self._key_env = key_env
        self._master_key = self._resolve_key(key)
        material = HKDF(
            algorithm=SHA256(), length=64, salt=None,
            info=b"world-simulator/dlc-generation-archive/v1",
        ).derive(self._master_key)
        self._encryption_key = material[:32]
        self._signing_key = material[32:]
        self._key_id = hashlib.sha256(self._signing_key).hexdigest()[:16]

    @property
    def key_id(self) -> str:
        return self._key_id

    def _resolve_key(self, explicit: bytes | str | None) -> bytes:
        source = explicit
        if source is None:
            source = os.environ.get(self._key_env)
        if source is not None:
            return self._decode_key(source)

        self.root.mkdir(parents=True, exist_ok=True)
        key_path = self.root.parent / ".dlc_generation.key"
        if key_path.exists():
            try:
                return self._decode_key(key_path.read_bytes().strip())
            except (OSError, ValueError) as exc:
                raise GenerationArchiveError("archive_key_invalid") from exc
        raw = os.urandom(32)
        encoded = base64.urlsafe_b64encode(raw)
        self._atomic_write(key_path, encoded)
        self._restrict_permissions(key_path)
        return raw

    @staticmethod
    def _decode_key(value: bytes | str) -> bytes:
        raw = value.encode("ascii") if isinstance(value, str) else bytes(value)
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except Exception as exc:
            raise GenerationArchiveError("archive_key_invalid") from exc
        if len(decoded) != 32:
            raise GenerationArchiveError("archive_key_must_be_32_bytes_base64")
        return decoded

    @staticmethod
    def generate_key() -> str:
        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    @staticmethod
    def _restrict_permissions(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _path_for(self, generation: int) -> Path:
        return self.root / f"generation-{int(generation):08d}.json"

    def save(self, generation: int, blueprint: Mapping[str, Any]) -> ArchivedGeneration:
        generation = int(generation)
        if generation <= 0:
            raise GenerationArchiveError("generation_must_be_positive")
        blueprint_copy = json.loads(json.dumps(blueprint, ensure_ascii=False))
        forbidden = _contains_forbidden_key(blueprint_copy)
        if forbidden:
            raise GenerationArchiveError("archive_contains_forbidden_sensitive_field")
        created_at = time.time()
        payload = {
            "schema_version": BLUEPRINT_SCHEMA_VERSION,
            "generation": generation,
            "blueprint": blueprint_copy,
        }
        header = {
            "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
            "generation": generation,
            "created_at": created_at,
            "algorithm": "AES-256-GCM+HMAC-SHA256",
            "key_id": self._key_id,
        }
        aad = _canonical(header)
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._encryption_key).encrypt(
            nonce, _canonical(payload), aad
        )
        signature = hmac.new(
            self._signing_key, aad + nonce + ciphertext, hashlib.sha256
        ).digest()
        envelope = dict(header)
        envelope.update({
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
            "signature": base64.b64encode(signature).decode("ascii"),
        })
        path = self._path_for(generation)
        self._atomic_write(path, _canonical(envelope))
        self._restrict_permissions(path)
        self._enforce_retention()
        return ArchivedGeneration(generation, created_at, blueprint_copy)

    def _decode_envelope(
        self, envelope: Mapping[str, Any], *, expected_generation: int | None = None
    ) -> ArchivedGeneration:
        if int(envelope.get("archive_schema_version", -1)) != ARCHIVE_SCHEMA_VERSION:
            raise GenerationArchiveError("archive_schema_unsupported")
        generation = int(envelope.get("generation", 0))
        if generation <= 0 or (
            expected_generation is not None and generation != expected_generation
        ):
            raise GenerationArchiveError("archive_generation_mismatch")
        if envelope.get("algorithm") != "AES-256-GCM+HMAC-SHA256":
            raise GenerationArchiveError("archive_algorithm_unsupported")
        if not hmac.compare_digest(str(envelope.get("key_id", "")), self._key_id):
            raise GenerationArchiveError("archive_key_mismatch")
        header = {
            "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
            "generation": generation,
            "created_at": float(envelope.get("created_at", 0.0)),
            "algorithm": str(envelope.get("algorithm")),
            "key_id": str(envelope.get("key_id")),
        }
        try:
            nonce = base64.b64decode(str(envelope["nonce"]), validate=True)
            ciphertext = base64.b64decode(str(envelope["ciphertext"]), validate=True)
            signature = base64.b64decode(str(envelope["signature"]), validate=True)
        except Exception as exc:
            raise GenerationArchiveError("archive_encoding_invalid") from exc
        aad = _canonical(header)
        expected_signature = hmac.new(
            self._signing_key, aad + nonce + ciphertext, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise GenerationArchiveError("archive_signature_invalid")
        try:
            raw_payload = AESGCM(self._encryption_key).decrypt(nonce, ciphertext, aad)
            payload = json.loads(raw_payload.decode("utf-8"))
        except Exception as exc:
            raise GenerationArchiveError("archive_decryption_failed") from exc
        payload = self._migrate_payload(payload)
        if int(payload.get("generation", 0)) != generation:
            raise GenerationArchiveError("archive_payload_generation_mismatch")
        blueprint = payload.get("blueprint")
        if not isinstance(blueprint, dict):
            raise GenerationArchiveError("archive_blueprint_invalid")
        forbidden = _contains_forbidden_key(blueprint)
        if forbidden:
            raise GenerationArchiveError("archive_contains_forbidden_sensitive_field")
        return ArchivedGeneration(generation, header["created_at"], blueprint)

    @staticmethod
    def _migrate_payload(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GenerationArchiveError("archive_payload_invalid")
        version = int(payload.get("schema_version", 0))
        if version == 0 and isinstance(payload.get("snapshot"), dict):
            payload = {
                "schema_version": 1,
                "generation": payload.get("generation"),
                "blueprint": payload["snapshot"],
            }
            version = 1
        if version != BLUEPRINT_SCHEMA_VERSION:
            raise GenerationArchiveError("archive_payload_schema_unsupported")
        return payload

    def load_all(self) -> ArchiveLoadResult:
        result = ArchiveLoadResult()
        if not self.root.exists():
            return result
        candidates: list[tuple[int, Path]] = []
        for path in self.root.iterdir():
            match = _ARCHIVE_PATTERN.fullmatch(path.name)
            if match and path.is_file():
                candidates.append((int(match.group(1)), path))
        for generation, path in sorted(candidates):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
                result.records.append(
                    self._decode_envelope(envelope, expected_generation=generation)
                )
            except GenerationArchiveError as exc:
                result.issues.append({
                    "generation": generation,
                    "code": str(exc),
                })
            except (OSError, ValueError, TypeError, KeyError):
                result.issues.append({
                    "generation": generation,
                    "code": "archive_read_failed",
                })
        if len(result.records) > self.history_limit:
            result.records = result.records[-self.history_limit:]
        return result

    def _enforce_retention(self) -> None:
        candidates: list[tuple[int, Path]] = []
        if not self.root.exists():
            return
        root = self.root.resolve()
        for path in self.root.iterdir():
            match = _ARCHIVE_PATTERN.fullmatch(path.name)
            if match and path.is_file():
                candidates.append((int(match.group(1)), path))
        for _, path in sorted(candidates)[:-self.history_limit]:
            resolved = path.resolve()
            if resolved.parent != root:
                raise GenerationArchiveError("archive_retention_path_escape")
            resolved.unlink()

    def public_status(self) -> dict[str, Any]:
        loaded = self.load_all()
        return {
            "enabled": True,
            "archive_schema_version": ARCHIVE_SCHEMA_VERSION,
            "blueprint_schema_version": BLUEPRINT_SCHEMA_VERSION,
            "persisted_generations": len(loaded.records),
            "issue_count": len(loaded.issues),
            "issues": list(loaded.issues[-20:]),
        }
