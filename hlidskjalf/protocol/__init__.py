"""Wire protocol: versioned envelopes and the in-process message bus."""

from hlidskjalf.protocol import bus, envelope  # noqa: F401
from hlidskjalf.protocol.bus import Bus  # noqa: F401
from hlidskjalf.protocol.envelope import (  # noqa: F401
    PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
    build,
    dumps,
    parse,
    validate,
)

__all__ = [
    "Bus",
    "PROTOCOL_VERSION",
    "SUPPORTED_VERSIONS",
    "build",
    "bus",
    "dumps",
    "envelope",
    "parse",
    "validate",
]
