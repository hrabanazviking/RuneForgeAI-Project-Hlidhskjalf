"""Error taxonomy for Project Hliðskjálf.

Typed exceptions with machine-readable ``code`` values and HTTP status
mappings so every subsystem — edge gateway, vault, HUD, host — speaks the
same failure language. All errors serialize to a dict for envelope payloads.

Contract (consumed by other phases, e.g. kista/_compat.py):
    HlidskjalfError, NotFoundError, ValidationError, StorageError
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


class HlidskjalfError(Exception):
    """Base error for the whole Hliðskjálf stack."""

    code: str = "internal"
    http_status: int = 500

    def __init__(
        self,
        message: str = "",
        *,
        details: Optional[Dict[str, Any]] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details: Dict[str, Any] = dict(details or {})
        if code:
            self.code = code

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict for envelope payloads."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class AuthError(HlidskjalfError):
    """Authentication or authorization failed."""

    code = "auth_failed"
    http_status = 401


class ValidationError(HlidskjalfError):
    """Input failed schema / shape validation."""

    code = "validation_failed"
    http_status = 400


class NotFoundError(HlidskjalfError):
    """A requested resource (artifact, version, handler, record) is missing."""

    code = "not_found"
    http_status = 404


class ConflictError(HlidskjalfError):
    """The request conflicts with current state (duplicate, stale version)."""

    code = "conflict"
    http_status = 409


class StorageError(HlidskjalfError):
    """A storage backend operation failed."""

    code = "storage_failed"
    http_status = 500


class ConfigError(HlidskjalfError):
    """Configuration is missing, malformed, or invalid."""

    code = "config_invalid"
    http_status = 500


class ProtocolError(HlidskjalfError):
    """The wire protocol was violated (bad envelope, version, framing)."""

    code = "protocol_error"
    http_status = 400


class BusError(HlidskjalfError):
    """The internal message bus failed (no subscriber, broker down)."""

    code = "bus_error"
    http_status = 503


class DispatchError(HlidskjalfError):
    """A dispatched handler raised or routing failed."""

    code = "dispatch_failed"
    http_status = 500


class TimeoutError(HlidskjalfError):
    """An operation exceeded its deadline."""

    code = "timeout"
    http_status = 504


class RateLimitError(HlidskjalfError):
    """Too many requests; the caller must back off."""

    code = "rate_limited"
    http_status = 429


#: Lookup from wire ``code`` string back to the exception class.
CODE_TO_ERROR: Dict[str, type[HlidskjalfError]] = {
    cls.code: cls
    for cls in (
        AuthError,
        ValidationError,
        NotFoundError,
        ConflictError,
        StorageError,
        ConfigError,
        ProtocolError,
        BusError,
        DispatchError,
        TimeoutError,
        RateLimitError,
    )
}


def error_from_dict(data: Mapping[str, Any]) -> HlidskjalfError:
    """Rebuild a typed error from its :meth:`HlidskjalfError.to_dict` form."""
    body = data.get("error", {}) if isinstance(data, Mapping) else {}
    code = body.get("code", "internal")
    cls = CODE_TO_ERROR.get(code, HlidskjalfError)
    raw_details = body.get("details", {}) or {}
    if not isinstance(raw_details, Mapping):
        # Tolerate non-dict details (e.g. a plain string from a foreign
        # producer) instead of raising a raw ValueError from dict().
        raw_details = {"value": raw_details}
    return cls(
        body.get("message", code),
        details=dict(raw_details),
    )
