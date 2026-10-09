"""Mock text-to-speech for the Hailo neural pipeline.

Skald: a real NPU TTS would run an acoustic model and a vocoder.  The
mock emits a valid 16-bit PCM WAV whose content is a deterministic,
sine-based "voice": each character of the input steers the oscillator
frequency for its time-slice, so the waveform is a stable, audible
placeholder and identical across runs.

The mock also writes a ``cmnt`` chunk carrying the source text.  The
mock STT in :mod:`hlidskjalf.hailo.stt` reads that chunk back, which is
what makes the TTS→STT round-trip work.  This is *mock-only* behaviour,
documented explicitly in both modules; a real recogniser does not work
this way.

Only the standard library is used.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Union

#: WAV format constants for the mock synthesiser.
SAMPLE_RATE = 22050
BITS_PER_SAMPLE = 16
CHANNELS = 1

#: Marker identifying comment chunks written by this mock.  STT only
#: trusts chunks bearing this marker.
MOCK_TTS_MARKER = b"HLIDSKJALF-MOCK-TTS\x00"

#: Seconds of audio per input character at rate=1.0.
SECONDS_PER_CHAR = 0.055

#: Hard bounds on generated audio length (seconds).
MIN_DURATION_S = 0.25
MAX_DURATION_S = 12.0


@dataclass(frozen=True)
class VoiceConfig:
    """Tunable voice parameters for the mock synthesiser."""

    name: str = "default"
    #: Playback-speed multiplier; also scales generated duration.
    rate: float = 1.0
    #: Pitch multiplier applied to the base frequency.
    pitch: float = 1.0

    #: Base oscillator frequency in Hz before character modulation.
    base_freq: float = 140.0

    def __post_init__(self) -> None:
        if self.rate <= 0:
            raise ValueError(f"rate must be positive, got {self.rate}")
        if self.pitch <= 0:
            raise ValueError(f"pitch must be positive, got {self.pitch}")


#: Named voice presets shipped with the mock.
VOICES: Dict[str, VoiceConfig] = {
    "default": VoiceConfig(name="default", rate=1.0, pitch=1.0),
    "deep": VoiceConfig(name="deep", rate=0.9, pitch=0.75),
    "bright": VoiceConfig(name="bright", rate=1.1, pitch=1.25),
}


def get_voice(name: str) -> VoiceConfig:
    """Return the :class:`VoiceConfig` for a named voice."""
    try:
        return VOICES[name]
    except KeyError:
        raise KeyError(
            f"unknown voice {name!r}; available: {sorted(VOICES)}"
        ) from None


def _duration_seconds(text: str, rate: float) -> float:
    raw = max(len(text), 1) * SECONDS_PER_CHAR / rate
    return max(MIN_DURATION_S, min(MAX_DURATION_S, raw))


def _char_frequency(char: str, base_freq: float, pitch: float) -> float:
    """Deterministic per-character oscillator frequency."""
    digest = int.from_bytes(
        hashlib.sha256(char.encode("utf-8")).digest()[:4], "big"
    )
    # ±1 octave around the base, quantised to semitone steps so the
    # output sounds like crude speech cadence rather than noise.
    semitones = (digest % 25) - 12
    return base_freq * pitch * (2.0 ** (semitones / 12.0))


def _synthesize_samples(text: str, config: VoiceConfig) -> bytes:
    """Render the text as 16-bit mono PCM samples."""
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    duration = _duration_seconds(text, config.rate)
    total = int(SAMPLE_RATE * duration)
    chars = text if text else " "
    per_char = max(1, total // len(chars))
    amplitude = 0.35 * 32767

    samples = bytearray()
    for i in range(total):
        char = chars[min(i // per_char, len(chars) - 1)]
        freq = _char_frequency(char, config.base_freq, config.pitch)
        t = i / SAMPLE_RATE
        # Fundamental plus a soft second harmonic; short fade in/out.
        wave = math.sin(2.0 * math.pi * freq * t)
        wave += 0.3 * math.sin(4.0 * math.pi * freq * t)
        wave *= 0.77
        edge = min(i, total - i, 200) / 200.0
        value = int(max(-1.0, min(1.0, wave * edge)) * amplitude)
        samples += struct.pack("<h", value)
    return bytes(samples)


def _build_wav(pcm: bytes, comment: bytes) -> bytes:
    """Pack PCM bytes plus a ``cmnt`` chunk into a RIFF/WAVE file."""
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,            # chunk size
        1,             # PCM
        CHANNELS,
        SAMPLE_RATE,
        SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8,  # byte rate
        CHANNELS * BITS_PER_SAMPLE // 8,                # block align
        BITS_PER_SAMPLE,
    )
    data_chunk = b"data" + struct.pack("<I", len(pcm)) + pcm
    # Comment payloads are padded to even size per the RIFF rule.
    if len(comment) % 2:
        comment += b"\x00"
    cmnt_chunk = b"cmnt" + struct.pack("<I", len(comment)) + comment
    body = b"WAVE" + fmt_chunk + cmnt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


def synthesize(
    text: str,
    voice: str = "default",
    config: Union[VoiceConfig, None] = None,
) -> bytes:
    """Synthesize ``text`` into WAV bytes.

    Args:
        text: The text to speak.  May be empty (yields a short blip).
        voice: Named voice preset; ignored when ``config`` is given.
        config: Explicit :class:`VoiceConfig` override.

    Returns:
        A complete ``.wav`` file as bytes, including a ``cmnt`` chunk
        with the source text for the mock STT round-trip.

    Raises:
        KeyError: If ``voice`` is unknown and no ``config`` is given.
    """
    cfg = config if config is not None else get_voice(voice)
    pcm = _synthesize_samples(text, cfg)
    comment = MOCK_TTS_MARKER + text.encode("utf-8")
    return _build_wav(pcm, comment)


def save_wav(path: Union[str, Path], wav_bytes: bytes) -> Path:
    """Write WAV bytes to ``path`` and return the resolved path."""
    target = Path(path)
    if target.suffix.lower() != ".wav":
        target = target.with_suffix(".wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(wav_bytes)
    return target
