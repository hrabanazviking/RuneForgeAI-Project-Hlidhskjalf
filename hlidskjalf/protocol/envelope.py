"""Versioned JSON message envelope for Project Hliðskjálf.

Every message on the bus and across the gateway wire carries this envelope::

    {
        "v": 1,                       # protocol version (int)
        "id": "01M4...",              # ULID-style unique id
        "ts": 1728460000.123,         # unix epoch seconds (float)
        "type": "host.state",         # dotted message type (str)
        "source": "heimdall",         # sender name (str)
        "payload": {...},             # message body (JSON object)
        "meta": {"trace_id": ...},    # optional routing metadata (object)
    }

Unknown / unsupported protocol versions are rejected outright.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Mapping, Optional, Union

from hlidskjalf.common.errors import ProtocolError, ValidationError
from hlidskjalf.common.ids import is_valid as is_valid_id, new_id

#: Current protocol version produced by :func:`build`.
PROTOCOL_VERSION = 1

#: Versions this runtime accepts. Bump deliberately; never accept unknowns.
SUPPORTED_VERSIONS = frozenset({1})

_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

RawEnvelope = Union[str, bytes, bytearray, Mapping[str, Any]]


def build(
    msg_type: str,
    payload: Mapping[str, Any],
    *,
    source: str = "",
    meta: Optional[Mapping[str, Any]] = None,
    msg_id: Optional[str] = None,
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a new envelope dict.

    Raises:
        ValidationError: ``msg_type`` / ``payload`` are malformed.
    """
    if not isinstance(msg_type, str) or not _TYPE_RE.match(msg_type):
        raise ValidationError(
            f"invalid message type {msg_type!r}",
            details={"msg_type": msg_type},
        )
    if not isinstance(payload, Mapping):
        raise ValidationError(
            "envelope payload must be a JSON object",
            details={"payload_type": type(payload).__name__},
        )
    if not isinstance(source, str):
        raise ValidationError("envelope source must be a string")
    meta_obj = dict(meta or {})
    if not isinstance(meta_obj, dict):
        raise ValidationError("envelope meta must be a JSON object")
    return {
        "v": PROTOCOL_VERSION,
        "id": msg_id or new_id(),
        "ts": float(ts if ts is not None else time.time()),
        "type": msg_type,
        "source": source,
        "payload": dict(payload),
        "meta": meta_obj,
    }


def dumps(envelope: Mapping[str, Any]) -> str:
    """Serialize an envelope to a JSON string."""
    return json.dumps(dict(envelope), separators=(",", ":"), default=str)


def validate(envelope: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a decoded envelope; return it as a plain dict.

    Raises:
        ValidationError: Missing / malformed fields.
        ProtocolError: Unsupported protocol version.
    """
    if not isinstance(envelope, Mapping):
        raise ValidationError(
            "envelope must be a JSON object",
            details={"got": type(envelope).__name__},
        )
    env = dict(envelope)

    version = env.get("v")
    if version not in SUPPORTED_VERSIONS:
        raise ProtocolError(
            f"unsupported protocol version: {version!r}",
            details={"version": version, "supported": sorted(SUPPORTED_VERSIONS)},
        )

    msg_id = env.get("id")
    if not is_valid_id(msg_id):
        raise ValidationError("envelope 'id' must be a 26-char id",
                              details={"id": msg_id})

    ts = env.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts < 0:
        raise ValidationError("envelope 'ts' must be a non-negative number",
                              details={"ts": ts})

    msg_type = env.get("type")
    if not isinstance(msg_type, str) or not _TYPE_RE.match(msg_type):
        raise ValidationError("envelope 'type' must be a dotted name",
                              details={"type": msg_type})

    if not isinstance(env.get("source"), str):
        raise ValidationError("envelope 'source' must be a string")

    if not isinstance(env.get("payload"), Mapping):
        raise ValidationError("envelope 'payload' must be a JSON object")

    meta = env.get("meta", {})
    if not isinstance(meta, Mapping):
        raise ValidationError("envelope 'meta' must be a JSON object")
    env["meta"] = dict(meta)
    env["payload"] = dict(env["payload"])
    return env


def parse(raw: RawEnvelope) -> Dict[str, Any]:
    """Decode and validate an envelope from JSON text/bytes or a dict."""
    if isinstance(raw, Mapping):
        return validate(raw)
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8")
    if not isinstance(raw, str):
        raise ValidationError(
            "cannot parse envelope from non-string input",
            details={"type": type(raw).__name__},
        )
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"envelope is not valid JSON: {exc}") from exc
    return validate(decoded)


def is_reply(envelope: Mapping[str, Any]) -> bool:
    """True when the envelope is a request/reply response."""
    return bool(envelope.get("meta", {}).get("is_reply"))


def correlation_id(envelope: Mapping[str, Any]) -> Optional[str]:
    """Return the reply correlation id, if present."""
    value = envelope.get("meta", {}).get("correlation_id")
    return value if isinstance(value, str) else None
