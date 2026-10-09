"""Hailo NPU neural pipeline — edge-native neural co-processor.

Mock backend by default (per ``config/hlidskjalf.yaml`` ``hailo.mock``);
the real Hailo-10 path is stubbed behind the same interface for the
Raspberry Pi 5 target.
"""

from .embeddings import EMBED_DIM, batch_embed, cosine_similarity, embed
from .runtime import (
    MOCK_BACKEND,
    REAL_BACKEND,
    Backend,
    HailoRuntime,
    MockBackend,
    RealBackend,
    resolve_backend_name,
)
from .scheduler import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    Job,
    NeuralScheduler,
)
from .stt import MockSttError, transcribe
from .tts import (
    SAMPLE_RATE,
    VOICES,
    VoiceConfig,
    get_voice,
    save_wav,
    synthesize,
)

__all__ = [
    # embeddings
    "EMBED_DIM",
    "embed",
    "batch_embed",
    "cosine_similarity",
    # tts
    "SAMPLE_RATE",
    "VOICES",
    "VoiceConfig",
    "get_voice",
    "synthesize",
    "save_wav",
    # stt
    "MockSttError",
    "transcribe",
    # scheduler
    "DONE",
    "FAILED",
    "QUEUED",
    "RUNNING",
    "Job",
    "NeuralScheduler",
    # runtime
    "MOCK_BACKEND",
    "REAL_BACKEND",
    "Backend",
    "HailoRuntime",
    "MockBackend",
    "RealBackend",
    "resolve_backend_name",
]
