"""Compatibility shims for hlidskjalf.common contracts.

Phase A may not have landed yet, so every shared contract is resolved
defensively here. If ``hlidskjalf.common.ids`` / ``.errors`` / ``.logging``
exist they are used; otherwise stdlib fallbacks keep the vault working.
"""

from __future__ import annotations

import logging
import uuid

try:  # pragma: no cover - exercised when Phase A exists
    from hlidskjalf.common.ids import new_id as _phase_a_new_id  # type: ignore
except Exception:  # noqa: BLE001

    def _phase_a_new_id() -> str:  # type: ignore[misc]
        return uuid.uuid4().hex


def new_id() -> str:
    """Return a fresh unique id (Phase A contract, or uuid4 hex fallback)."""
    return _phase_a_new_id()


try:  # pragma: no cover - exercised when Phase A exists
    from hlidskjalf.common.logging import get_logger as _phase_a_get_logger  # type: ignore
except Exception:  # noqa: BLE001

    def _phase_a_get_logger(name: str) -> logging.Logger:  # type: ignore[misc]
        return logging.getLogger(name)


def get_logger(name: str) -> logging.Logger:
    """Return a logger (Phase A contract, or stdlib logging fallback)."""
    return _phase_a_get_logger(name)


try:  # pragma: no cover - exercised when Phase A exists
    from hlidskjalf.common.errors import (  # type: ignore
        HlidskjalfError,
        NotFoundError,
        ValidationError,
        StorageError,
    )
except Exception:  # noqa: BLE001

    class HlidskjalfError(Exception):
        """Base error for the Hlidskjalf vault stack."""


    class NotFoundError(HlidskjalfError):
        """A requested artifact / version / record does not exist."""


    class ValidationError(HlidskjalfError):
        """Input failed validation."""


    class StorageError(HlidskjalfError):
        """The filesystem backend failed an operation."""
