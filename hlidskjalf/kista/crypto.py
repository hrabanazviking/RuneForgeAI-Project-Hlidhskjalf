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
from typing import Any, Dict, List, Optional, Tuple

from hlidskjalf.kista._compat import (
    NotFoundError,
    StorageError,
    ValidationError,
    get_logger,
    new_id,
)

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

# Explicit opt-in for the insecure mock backend. Pass allow_insecure=True
# or set this env var to "1"/"true"/"yes"/"on".
INSECURE_CRYPTO_ENV = "HLIDSKJALF__KISTA__ALLOW_INSECURE_CRYPTO"


def _insecure_opted_in(explicit: bool) -> bool:
    if explicit:
        return True
    return (
        os.environ.get(INSECURE_CRYPTO_ENV, "").strip().lower()
        in ("1", "true", "yes", "on")
    )


def _require_real_or_opt_in(allow_insecure: bool) -> None:
    """Refuse to encrypt on the mock backend without explicit opt-in."""
    if BACKEND == "mock" and not _insecure_opted_in(allow_insecure):
        raise StorageError(
            "refusing to encrypt with the insecure mock crypto backend; "
            "install the 'cryptography' package for real AES-256-GCM, or opt "
            "in explicitly via allow_insecure=True / "
            f"{INSECURE_CRYPTO_ENV}=1"
        )


def _envelope_key_len(envelope: bytes) -> int:
    """Validate envelope bounds; return the embedded key-id length.

    Raises ValidationError("truncated KST1 envelope") when the envelope is
    too short to hold its declared header fields.
    """
    if not isinstance(envelope, (bytes, bytearray)):
        raise ValidationError("envelope must be bytes")
    envelope = bytes(envelope)
    header = len(ENVELOPE_MAGIC) + 1
    if len(envelope) < header:
        raise ValidationError("truncated KST1 envelope")
    if not envelope.startswith(ENVELOPE_MAGIC):
        raise ValidationError("not a KST1 envelope")
    kid_len = envelope[len(ENVELOPE_MAGIC)]
    if len(envelope) < header + kid_len + NONCE_LEN:
        raise ValidationError("truncated KST1 envelope")
    return kid_len


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
        """Persist the keyring with owner-only permissions (0o600)."""
        data = {
            "active": self._active,
            "keys": {k: base64.b64encode(v).decode() for k, v in self._keys.items()},
            "created": self._created,
        }
        raw = json.dumps(data, indent=2).encode()
        target = Path(path)
        # os.open mode applies only on creation; chmod enforces it for
        # pre-existing files too (umask-independent).
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
        except OSError as exc:
            raise StorageError(f"failed saving keyring to {target}: {exc}") from exc
        try:
            os.chmod(target, 0o600)
        except OSError as exc:
            raise StorageError(
                f"failed to set 0o600 on keyring {target}: {exc}"
            ) from exc

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


def encrypt(
    data: bytes, key: bytes, key_id: str, allow_insecure: bool = False
) -> bytes:
    """Encrypt with the active backend; returns the KST1 envelope.

    On the mock backend (no ``cryptography`` package) encryption is NOT
    secure: pass ``allow_insecure=True`` or set
    ``HLIDSKJALF__KISTA__ALLOW_INSECURE_CRYPTO=1`` to opt in explicitly.
    """
    _require_real_or_opt_in(allow_insecure)
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
    envelope = bytes(envelope)
    kid_len = _envelope_key_len(envelope)
    pos = len(ENVELOPE_MAGIC) + 1 + kid_len
    nonce = envelope[pos : pos + NONCE_LEN]
    ct = envelope[pos + NONCE_LEN :]
    if BACKEND == "aesgcm":
        assert _AESGCM is not None
        return _AESGCM(key).decrypt(nonce, ct, None)
    log.warning(MOCK_WARNING)
    return _xor(ct, _mock_keystream(key, nonce, len(ct)))


def envelope_key_id(envelope: bytes) -> str:
    envelope = bytes(envelope)
    kid_len = _envelope_key_len(envelope)
    start = len(ENVELOPE_MAGIC) + 1
    return envelope[start : start + kid_len].decode()


class EncryptedStore:
    """Wraps raw bytes with per-artifact encryption via a KeyManager.

    ``allow_insecure`` (default False) is the explicit opt-in for the mock
    XOR backend when ``cryptography`` is unavailable; without it, ``seal``
    raises StorageError on the mock path.
    """

    def __init__(self, keys: KeyManager, allow_insecure: bool = False) -> None:
        self.keys = keys
        self.allow_insecure = allow_insecure

    def seal(self, data: bytes, key_id: Optional[str] = None) -> bytes:
        """Encrypt bytes under the given (or active) key."""
        kid = key_id or self.keys.active_key_id
        if kid is None:
            kid = self.keys.generate()
        return encrypt(
            bytes(data), self.keys.get(kid), kid, allow_insecure=self.allow_insecure
        )

    def open(self, envelope: bytes) -> bytes:
        """Decrypt an envelope, resolving the key from its embedded key id."""
        kid = envelope_key_id(envelope)
        return decrypt(envelope, self.keys.get(kid))

    def health(self) -> Dict[str, Any]:
        """Store health: always surfaces which crypto backend is active."""
        return {
            "backend": BACKEND,
            "mock": BACKEND == "mock",
            "allow_insecure": self.allow_insecure,
            "active_key": self.keys.active_key_id,
            "keys": self.keys.key_ids(),
        }


def health() -> Dict[str, Any]:
    """Crypto subsystem health: always surfaces which backend is active."""
    return {
        "backend": BACKEND,
        "mock": BACKEND == "mock",
        "insecure_opt_in_env": INSECURE_CRYPTO_ENV,
        "insecure_opted_in": _insecure_opted_in(False),
        "envelope_magic": ENVELOPE_MAGIC.decode("ascii"),
        "nonce_len": NONCE_LEN,
    }
