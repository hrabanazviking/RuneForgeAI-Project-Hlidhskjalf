"""Slice 11 — Kista encryption at rest.

Per-artifact AES-256-GCM when the ``cryptography`` package is available;
otherwise a clearly-marked **MOCK** XOR keystream fallback so the API
contract stays intact on bare stdlib installs.

KeyManager holds named keys, supports rotation (new active key, old keys
retained for decryption), and can persist its keyring to JSON (base64).

Envelope format on disk (bytes)::

    b"KST1" + key_id_len(1) + key_id + nonce(12) + ciphertext

``cryptography`` presence is detected once at import; ``BACKEND`` is
either ``"aesgcm"`` or ``"mock"``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from hlidskjalf.kista._compat import NotFoundError, ValidationError, get_logger, new_id

log = get_logger(__name__)

ENVELOPE_MAGIC = b"KST1"
NONCE_LEN = 12

try:  # prefer real AES-GCM when the dependency is installed
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore

    _AESGCM = AESGCM
    BACKEND = "aesgcm"
except Exception:  # noqa: BLE001
    _AESGCM = None
    BACKEND = "mock"

MOCK_WARNING = (
    "MOCK crypto backend: XOR keystream fallback in use — NOT secure. "
    "Install the 'cryptography' package for real AES-256-GCM."
)


class KeyManager:
    """Named-key keyring with rotation."""

    def __init__(self) -> None:
        self._keys: Dict[str, bytes] = {}
        self._active: Optional[str] = None
        self._created: Dict[str, float] = {}

    def generate(self, key_id: Optional[str] = None) -> str:
        """Generate a fresh 256-bit key; becomes active if it is the first."""
        kid = key_id or f"key-{new_id()}"
        if kid in self._keys:
            raise ValidationError(f"key id already exists: {kid}")
        self._keys[kid] = secrets.token_bytes(32)
        self._created[kid] = time.time()
        if self._active is None:
            self._active = kid
        log.info("crypto.key.generate id=%s backend=%s", kid, BACKEND)
        return kid

    def rotate(self) -> str:
        """Create a new active key; old keys are kept for decryption."""
        kid = self.generate()
        self._active = kid
        log.info("crypto.key.rotate new_active=%s", kid)
        return kid

    @property
    def active_key_id(self) -> Optional[str]:
        return self._active

    def use(self, key_id: str) -> None:
        if key_id not in self._keys:
            raise NotFoundError(f"unknown key: {key_id}")
        self._active = key_id

    def key_ids(self) -> List[str]:
        return sorted(self._keys)

    def get(self, key_id: str) -> bytes:
        if key_id not in self._keys:
            raise NotFoundError(f"unknown key: {key_id}")
        return self._keys[key_id]

    def save(self, path: str | os.PathLike[str]) -> None:
        data = {
            "active": self._active,
            "keys": {k: base64.b64encode(v).decode() for k, v in self._keys.items()},
            "created": self._created,
        }
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "KeyManager":
        data = json.loads(Path(path).read_text())
        km = cls()
        km._keys = {k: base64.b64decode(v) for k, v in data["keys"].items()}
        km._created = {k: float(v) for k, v in data.get("created", {}).items()}
        km._active = data.get("active")
        return km


def _mock_keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def encrypt(data: bytes, key: bytes, key_id: str) -> bytes:
    """Encrypt with the active backend; returns the KST1 envelope."""
    nonce = secrets.token_bytes(NONCE_LEN)
    kid = key_id.encode()
    if len(kid) > 255:
        raise ValidationError("key_id too long")
    if BACKEND == "aesgcm":
        assert _AESGCM is not None
        ct = _AESGCM(key).encrypt(nonce, bytes(data), None)
    else:
        log.warning(MOCK_WARNING)
        ct = _xor(bytes(data), _mock_keystream(key, nonce, len(data)))
    return ENVELOPE_MAGIC + bytes([len(kid)]) + kid + nonce + ct


def decrypt(envelope: bytes, key: bytes) -> bytes:
    """Decrypt a KST1 envelope with the raw key bytes."""
    if not envelope.startswith(ENVELOPE_MAGIC):
        raise ValidationError("not a KST1 envelope")
    pos = len(ENVELOPE_MAGIC)
    kid_len = envelope[pos]
    pos += 1 + kid_len
    nonce = envelope[pos : pos + NONCE_LEN]
    ct = envelope[pos + NONCE_LEN :]
    if BACKEND == "aesgcm":
        assert _AESGCM is not None
        return _AESGCM(key).decrypt(nonce, ct, None)
    log.warning(MOCK_WARNING)
    return _xor(ct, _mock_keystream(key, nonce, len(ct)))


def envelope_key_id(envelope: bytes) -> str:
    if not envelope.startswith(ENVELOPE_MAGIC):
        raise ValidationError("not a KST1 envelope")
    kid_len = envelope[len(ENVELOPE_MAGIC)]
    start = len(ENVELOPE_MAGIC) + 1
    return envelope[start : start + kid_len].decode()


class EncryptedStore:
    """Wraps raw bytes with per-artifact encryption via a KeyManager."""

    def __init__(self, keys: KeyManager) -> None:
        self.keys = keys

    def seal(self, data: bytes, key_id: Optional[str] = None) -> bytes:
        """Encrypt bytes under the given (or active) key."""
        kid = key_id or self.keys.active_key_id
        if kid is None:
            kid = self.keys.generate()
        return encrypt(bytes(data), self.keys.get(kid), kid)

    def open(self, envelope: bytes) -> bytes:
        """Decrypt an envelope, resolving the key from its embedded key id."""
        kid = envelope_key_id(envelope)
        return decrypt(envelope, self.keys.get(kid))
