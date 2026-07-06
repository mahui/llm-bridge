"""Encrypted file-based credential storage."""

from __future__ import annotations

import base64
import hashlib
import json
import platform
from pathlib import Path

from cryptography.fernet import Fernet


def _derive_key() -> bytes:
    """Derive an encryption key from machine-specific data."""
    node = platform.node()
    user = Path.home().name
    seed = f"llm-bridge:{user}@{node}".encode()
    raw = hashlib.sha256(seed).digest()
    return base64.urlsafe_b64encode(raw)


class CredentialStore:
    """Fernet-encrypted JSON credential storage at ~/.llm-bridge/credentials.enc."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else Path.home() / ".llm-bridge" / "credentials.enc"
        self._fernet = Fernet(_derive_key())

    def _load_all(self) -> dict:
        if not self._path.exists():
            return {}
        encrypted = self._path.read_bytes()
        decrypted = self._fernet.decrypt(encrypted)
        return json.loads(decrypted)

    def _save_all(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = self._fernet.encrypt(json.dumps(data).encode())
        self._path.write_bytes(encrypted)

    def save(self, provider: str, credentials: dict) -> None:
        data = self._load_all()
        data[provider] = credentials
        self._save_all(data)

    def load(self, provider: str) -> dict | None:
        data = self._load_all()
        return data.get(provider)

    def delete(self, provider: str) -> None:
        data = self._load_all()
        data.pop(provider, None)
        self._save_all(data)

    def list_providers(self) -> list[str]:
        return list(self._load_all().keys())
