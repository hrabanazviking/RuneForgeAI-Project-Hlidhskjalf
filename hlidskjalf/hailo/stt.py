"""Mock speech-to-text for the Hailo neural pipeline.

Skald: a real NPU STT would run an acoustic model over the waveform.
The mock instead reads the ``cmnt`` chunk written by
:mod:`hlidskjalf.hailo.tts`, which carries the original source text.

**This is mock-only behaviour.**  It exists so the TTS→STT round-trip
can be exercised end-to-end in development and CI without a microphone,
a model, or a Hailo-10.  :func:`transcribe` refuses to "recognise"
audio that was not produced by the mock synthesiser — it raises
:class:`MockSttError` rather than hallucinating a transcript.

Only the standard library is used.
"""

from __future__ import annotations

import struct

from .tts import MOCK_TTS_MARKER


class MockSttError(ValueError):
    """Raised when the mock STT cannot recover a transcript."""


def _find_chunk(wav: bytes, chunk_id: bytes) -> bytes:
    """Return the payload of the first chunk with the given 4-byte id."""
    if len(wav) < 12 or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise MockSttError("not a valid RIFF/WAVE file")
    offset = 12
    while offset + 8 <= len(wav):
        cid = wav[offset : offset + 4]
        (size,) = struct.unpack("<I", wav[offset + 4 : offset + 8])
        payload = wav[offset + 8 : offset + 8 + size]
        if len(payload) < size:
            raise MockSttError(f"truncated chunk {cid!r}")
        if cid == chunk_id:
            return payload
        # RIFF chunks are word-aligned.
        offset += 8 + size + (size % 2)
    raise MockSttError(f"chunk {chunk_id!r} not found")


def transcribe(wav_bytes: bytes) -> str:
    """Recover the text embedded by the mock TTS ``cmnt`` chunk.

    Args:
        wav_bytes: A complete WAV file as bytes.

    Returns:
        The source text that was given to :func:`tts.synthesize`.

    Raises:
        MockSttError: If the bytes are not a valid WAV, lack a mock
            ``cmnt`` chunk, or the embedded payload is corrupt.  Never
            guesses: audio not produced by the mock TTS is rejected.
    """
    if not isinstance(wav_bytes, (bytes, bytearray)):
        raise TypeError(
            f"wav_bytes must be bytes, got {type(wav_bytes).__name__}"
        )
    try:
        payload = _find_chunk(bytes(wav_bytes), b"cmnt")
    except MockSttError as exc:
        raise MockSttError(
            "mock STT cannot transcribe audio that was not produced by "
            f"the mock TTS: {exc}"
        ) from exc
    if not payload.startswith(MOCK_TTS_MARKER):
        raise MockSttError(
            "mock STT cannot transcribe audio that was not produced by "
            "the mock TTS: unrecognised comment chunk"
        )
    text_bytes = payload[len(MOCK_TTS_MARKER) :].rstrip(b"\x00")
    try:
        return text_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MockSttError(
            f"mock STT comment chunk is not valid UTF-8: {exc}"
        ) from exc
