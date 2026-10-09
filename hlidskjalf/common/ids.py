"""ULID-style sortable unique ids for Project Hliðskjálf.

A 26-character Crockford base32 string: 48 bits of millisecond timestamp
followed by 80 bits of randomness. Lexicographic order == chronological
order, so ids sort naturally in logs, the vault, and the timeline.

Contract (consumed by other phases, e.g. kista/_compat.py):
    new_id() -> str

Standard library only (``os.urandom``); thread-safe and monotonic within
the same millisecond.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime, timezone

#: Crockford base32 alphabet (no I, L, O, U — avoids visual ambiguity).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_BASE = len(_ALPHABET)
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_lock = threading.Lock()
_last_ms = 0
_last_rand = 0  # 80-bit randomness carried forward for monotonicity


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value % _BASE])
        value //= _BASE
    return "".join(reversed(chars))


def _decode(text: str) -> int:
    value = 0
    for char in text:
        value = value * _BASE + _ALPHABET.index(char)
    return value


def new_id(at: float | None = None) -> str:
    """Return a fresh ULID-style id string.

    Args:
        at: Optional epoch-seconds timestamp to embed (defaults to now).
            Useful for deterministic tests.
    """
    global _last_ms, _last_rand
    ms = int((at if at is not None else time.time()) * 1000)
    with _lock:
        if ms <= _last_ms:
            # Same (or earlier) millisecond: bump randomness to stay monotonic.
            ms = _last_ms
            _last_rand = (_last_rand + 1) & ((1 << 80) - 1)
        else:
            _last_ms = ms
            _last_rand = int.from_bytes(os.urandom(10), "big")
        rand = _last_rand
    return _encode(ms, 10) + _encode(rand, 16)


def is_valid(ulid: str) -> bool:
    """Return True when ``ulid`` is a well-formed 26-char id."""
    return isinstance(ulid, str) and bool(_ULID_RE.match(ulid))


def timestamp_ms(ulid: str) -> int:
    """Extract the embedded millisecond timestamp from an id."""
    if not is_valid(ulid):
        raise ValueError(f"not a valid id: {ulid!r}")
    return _decode(ulid[:10])


def timestamp(ulid: str) -> datetime:
    """Extract the embedded timestamp as a UTC datetime."""
    return datetime.fromtimestamp(timestamp_ms(ulid) / 1000, tz=timezone.utc)
