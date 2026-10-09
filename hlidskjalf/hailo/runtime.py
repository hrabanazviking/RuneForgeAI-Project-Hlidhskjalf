"""Hailo neural-accelerator runtime abstraction.

Skald: this is the boundary between the edge host and whatever silicon
executes neural work.  On the dev machine (and anywhere without a
Hailo-10 NPU) a deterministic CPU mock backend answers every call; on
the Raspberry Pi 5 the real backend drives the Hailo runtime SDK behind
the exact same interface.

Backend selection order (first hit wins, per the "defaults < YAML <
env < overrides" config law):

1. explicit ``backend=`` argument to :class:`HailoRuntime`
2. ``hailo.mock`` from a config mapping passed in (overrides)
3. ``HLIDSKJALF_HAILO_MOCK`` environment variable
4. ``hailo.mock`` from ``config/hlidskjalf.yaml`` (repo root default)
5. ``"mock"`` — always fall back to the mock, never fail to boot

Only the standard library is used.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

MOCK_BACKEND = "mock"
REAL_BACKEND = "real"

_ENV_MOCK = "HLIDSKJALF_HAILO_MOCK"


def _default_config_path() -> Path:
    """Locate ``config/hlidskjalf.yaml`` relative to the repo root."""
    here = Path(__file__).resolve()
    # hlidskjalf/hailo/runtime.py -> repo root is three levels up
    return here.parents[2] / "config" / "hlidskjalf.yaml"


def _read_mock_flag_from_yaml(path: Path) -> Optional[bool]:
    """Parse the ``hailo.mock`` flag out of the YAML config.

    A tiny purpose-built parser: only the ``hailo:`` section is read,
    so a missing/invalid file never crashes backend selection.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    in_hailo = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_hailo = stripped.startswith("hailo:")
            continue
        if in_hailo and stripped.startswith("mock"):
            _, _, value = stripped.partition(":")
            value = value.strip().lower()
            if value in ("true", "yes", "1"):
                return True
            if value in ("false", "no", "0"):
                return False
    return None


def _config_mock_flag(config: Optional[Mapping[str, Any]]) -> Optional[bool]:
    """Extract ``hailo.mock`` from an in-memory config mapping."""
    if not config:
        return None
    hailo = config.get("hailo")
    if isinstance(hailo, Mapping):
        return bool(hailo.get("mock", True)) if "mock" in hailo else None
    return None


def resolve_backend_name(
    backend: Optional[str] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> str:
    """Decide which backend to use, honouring the selection order.

    ``backend`` of ``"mock"``/``"real"`` always wins.  Otherwise the
    ``hailo.mock`` flag is consulted: an explicit config mapping
    (overrides) first, then the ``HLIDSKJALF_HAILO_MOCK`` environment
    variable, then ``config/hlidskjalf.yaml``, then ``"mock"``.
    """
    if backend in (MOCK_BACKEND, REAL_BACKEND):
        return backend
    if backend is not None:
        raise ValueError(
            f"unknown hailo backend {backend!r}; expected 'mock' or 'real'"
        )

    flag = _config_mock_flag(config)
    if flag is None:
        env = os.environ.get(_ENV_MOCK, "").strip().lower()
        if env in ("0", "false", "no"):
            flag = False
        elif env in ("1", "true", "yes"):
            flag = True
    if flag is None:
        flag = _read_mock_flag_from_yaml(_default_config_path())
    if flag is None:
        flag = True
    return MOCK_BACKEND if flag else REAL_BACKEND


class Backend:
    """Common interface every Hailo backend must satisfy."""

    name = "base"

    def is_available(self) -> bool:
        raise NotImplementedError

    def device_info(self) -> Dict[str, Any]:
        raise NotImplementedError

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        raise NotImplementedError

    def synthesize(
        self,
        text: str,
        voice: str = "default",
        rate: float = 1.0,
        pitch: float = 1.0,
    ) -> bytes:
        raise NotImplementedError

    def transcribe(self, wav_bytes: bytes) -> str:
        raise NotImplementedError

    def close(self) -> None:
        """Release device resources.  No-op for the mock."""


class MockBackend(Backend):
    """Deterministic CPU backend for development and CI.

    Implements the full neural surface with reproducible pseudo-model
    outputs so the rest of the edge stack can be developed and tested
    with no Hailo hardware present.
    """

    name = MOCK_BACKEND

    def is_available(self) -> bool:
        return True

    def device_info(self) -> Dict[str, Any]:
        return {
            "backend": self.name,
            "device": "cpu-mock",
            "description": "deterministic CPU mock of the Hailo-10 NPU",
            "real_hardware": False,
            "capabilities": ["embeddings", "tts", "stt"],
        }

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        from .embeddings import batch_embed

        return batch_embed(texts)

    def synthesize(
        self,
        text: str,
        voice: str = "default",
        rate: float = 1.0,
        pitch: float = 1.0,
    ) -> bytes:
        from .tts import VoiceConfig, synthesize

        return synthesize(
            text,
            voice=voice,
            config=VoiceConfig(name=voice, rate=rate, pitch=pitch),
        )

    def transcribe(self, wav_bytes: bytes) -> str:
        from .stt import transcribe

        return transcribe(wav_bytes)


class RealBackend(Backend):
    """Stub for the real Hailo-10 NPU backend on the Raspberry Pi 5.

    This class intentionally ships without an implementation: the Hailo
    runtime SDK (``hailort`` and the compiled HEF models) only exists on
    the target Pi image.  Every method raises
    :class:`NotImplementedError` with a message that says exactly what
    is missing, so a misconfigured edge node fails loudly instead of
    silently returning mock data.
    """

    name = REAL_BACKEND

    _MESSAGE = (
        "Hailo-10 NPU backend is not implemented in this build: "
        "real inference requires the HailoRT SDK and compiled HEF "
        "models on a Raspberry Pi 5.  Set hailo.mock: true in "
        "config/hlidskjalf.yaml (or HLIDSKJALF_HAILO_MOCK=1) to use "
        "the deterministic CPU mock backend."
    )

    def is_available(self) -> bool:
        try:
            import hailort  # noqa: F401  (only exists on the Pi image)

            return True
        except ImportError:
            return False

    def device_info(self) -> Dict[str, Any]:
        raise NotImplementedError(self._MESSAGE)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        raise NotImplementedError(self._MESSAGE)

    def synthesize(
        self,
        text: str,
        voice: str = "default",
        rate: float = 1.0,
        pitch: float = 1.0,
    ) -> bytes:
        raise NotImplementedError(self._MESSAGE)

    def transcribe(self, wav_bytes: bytes) -> str:
        raise NotImplementedError(self._MESSAGE)


_BACKENDS: Dict[str, Callable[[], Backend]] = {
    MOCK_BACKEND: MockBackend,
    REAL_BACKEND: RealBackend,
}


class HailoRuntime:
    """Entry point for all neural work on the edge node.

    Selects and owns a backend (mock by default, per the
    ``hailo.mock`` config flag) and exposes the neural surface —
    embeddings, TTS, STT — plus device introspection.
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        config: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._backend_name = resolve_backend_name(backend, config)
        self._backend = _BACKENDS[self._backend_name]()

    @property
    def backend_name(self) -> str:
        """The active backend: ``"mock"`` or ``"real"``."""
        return self._backend_name

    @property
    def backend(self) -> Backend:
        """The underlying backend instance."""
        return self._backend

    def is_available(self) -> bool:
        """True when the selected backend can serve inference."""
        return self._backend.is_available()

    def device_info(self) -> Dict[str, Any]:
        """Describe the device behind the backend."""
        return self._backend.device_info()

    def embed(self, text: str) -> Sequence[float]:
        """Embed a single text into a vector."""
        return self._backend.embed([text])[0]

    def batch_embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embed many texts at once."""
        return self._backend.embed(texts)

    def synthesize(
        self,
        text: str,
        voice: str = "default",
        rate: float = 1.0,
        pitch: float = 1.0,
    ) -> bytes:
        """Synthesize text to WAV bytes."""
        return self._backend.synthesize(text, voice, rate, pitch)

    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV bytes to text."""
        return self._backend.transcribe(wav_bytes)

    def close(self) -> None:
        """Release backend resources."""
        self._backend.close()

    def __enter__(self) -> "HailoRuntime":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
