"""Heimdall ingress authentication.

API-key auth for the gateway. Keys are configured — never hardcoded —
via ``heimdall.auth.api_keys`` in the config file or the
``HLIDSKJALF__HEIMDALL__AUTH__API_KEYS`` environment variable.

Every attempt is logged with a key *fingerprint* (never the raw key).
An empty key list means fail-closed: everything is rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Optional

from hlidskjalf.common.errors import AuthError, ValidationError
from hlidskjalf.common.logging import bind, get_logger

log = get_logger("heimdall.auth")


def fingerprint(key: str) -> str:
    """Return a non-reversible fingerprint safe to put in logs."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


@dataclass
class AuthResult:
    """Outcome of a successful authentication."""

    principal: str
    method: str = "api_key"
    key_fingerprint: str = ""
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return True


class Authenticator:
    """Validates API-key credentials with constant-time comparison."""

    def __init__(self, api_keys: Optional[Iterable[str]] = None) -> None:
        # principal -> key bytes
        self._keys: Dict[str, bytes] = {}
        for entry in api_keys or []:
            self._add_entry(entry)

    def _add_entry(self, entry: str) -> None:
        # Accept "key" (principal derived from fingerprint) or "name:key"
        # where name looks like a principal name.
        if not isinstance(entry, str):
            raise ValidationError(
                f"api key entry must be a string, got {type(entry).__name__}",
                details={"entry_type": type(entry).__name__},
            )
        principal, key = "", entry.strip()
        if ":" in entry:
            maybe_name, _, maybe_key = entry.partition(":")
            if _PRINCIPAL_RE.match(maybe_name.strip()) and maybe_key.strip():
                principal, key = maybe_name.strip(), maybe_key.strip()
        if not key:
            return
        if not principal:
            principal = f"key:{fingerprint(key)}"
        self._keys[principal] = key.encode("utf-8")

    @classmethod
    def from_config(cls, config: object) -> "Authenticator":
        """Build from a :class:`~hlidskjalf.common.config.Config`.

        Reads ``heimdall.auth.api_keys``.
        """
        keys = config.get("heimdall.auth.api_keys", [])  # type: ignore[union-attr]
        return cls(keys or [])

    @property
    def key_count(self) -> int:
        """Number of configured keys (safe to expose)."""
        return len(self._keys)

    def authenticate(self, credential: Optional[str]) -> AuthResult:
        """Validate a credential; return :class:`AuthResult` or raise.

        Raises:
            AuthError: Missing, empty, unknown, or mismatched credential.
        """
        audit = bind(log, key_fingerprint=fingerprint(credential or ""))
        if not credential or not credential.strip():
            audit.warning("auth rejected: missing credential")
            raise AuthError("missing credentials", details={"reason": "missing"})
        if not self._keys:
            audit.warning("auth rejected: no keys configured (fail-closed)")
            raise AuthError(
                "authentication not configured", details={"reason": "not_configured"}
            )
        presented = credential.strip().encode("utf-8")
        for principal, expected in self._keys.items():
            if hmac.compare_digest(presented, expected):
                audit.info("auth accepted", principal=principal)
                return AuthResult(
                    principal=principal,
                    key_fingerprint=fingerprint(credential.strip()),
                )
        audit.warning("auth rejected: unknown key")
        raise AuthError("invalid credentials", details={"reason": "unknown_key"})

    def extract_bearer(self, headers: Mapping[str, str]) -> Optional[str]:
        """Pull the token from an ``Authorization: Bearer <key>`` header."""
        for name, value in headers.items():
            if name.lower() == "authorization":
                scheme, _, token = value.partition(" ")
                if scheme.lower() == "bearer" and token.strip():
                    return token.strip()
        return None

    def extract_query_key(self, params: Mapping[str, str]) -> Optional[str]:
        """Pull ``api_key`` from query parameters (least preferred)."""
        for name, value in params.items():
            if name.lower() == "api_key" and value:
                return value
        return None
