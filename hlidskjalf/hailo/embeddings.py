"""Deterministic text-embedding pipeline for the Hailo mock backend.

Skald: a real NPU would run a transformer and emit a dense vector.  The
mock reproduces the *shape* of that contract — a fixed-dimensional,
L2-normalised vector per text — using hashed token buckets.  Outputs are
fully deterministic: the same text always yields the same vector on any
machine, with no randomness and no model weights to ship.

Rúnhild (design): token hashes are split across two independent SHA-256
digests — one chooses the bucket, one chooses the sign — so related
tokens land in unrelated buckets and cancellation noise averages out.
The whole-text digest seeds bucket ``0`` so even texts with no known
tokens (or empty strings) still get a stable, non-degenerate vector.

Only the standard library is used.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Sequence

#: Embedding dimensionality for the mock model.  Kept modest so the
#: vectors stay cheap to move through the IPC bus.
EMBED_DIM = 128

_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _bucket_and_sign(token: str) -> tuple[int, float]:
    """Map a token to ``(bucket_index, sign)`` deterministically."""
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "big") % EMBED_DIM
    # A second independent hash decides the sign, halving collisions.
    sign_digest = hashlib.sha256(b"sign:" + token.encode("utf-8")).digest()
    sign = 1.0 if (sign_digest[0] & 1) else -1.0
    return index, sign


def _raw_vector(text: str, dim: int = EMBED_DIM) -> List[float]:
    """Accumulate the unnormalised hashed-token-bucket vector."""
    vec = [0.0] * dim
    tokens = _TOKEN_RE.findall(text.lower())
    for token in tokens:
        index, sign = _bucket_and_sign(token)
        vec[index] += sign
    # Whole-text digest seeds bucket 0 so degenerate inputs (empty
    # string, pure punctuation) still embed to a stable direction.
    seed = int.from_bytes(
        hashlib.sha256(b"seed:" + text.encode("utf-8")).digest()[:8], "big"
    )
    vec[seed % dim] += 1.0 if (seed & 1) else -1.0
    return vec


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        # Cannot happen with the seed bucket, but stay total.
        return [1.0 / math.sqrt(len(vec))] * len(vec)
    return [x / norm for x in vec]


def embed(text: str, dim: int = EMBED_DIM) -> List[float]:
    """Embed ``text`` into a deterministic, L2-normalised vector.

    Args:
        text: The text to embed.
        dim: Vector dimensionality (must be positive).

    Returns:
        A list of ``dim`` floats with unit L2 norm.

    Raises:
        TypeError: If ``text`` is not a string.
        ValueError: If ``dim`` is not positive.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    if dim <= 0:
        raise ValueError(f"dim must be positive, got {dim}")
    return _l2_normalize(_raw_vector(text, dim))


def batch_embed(texts: Sequence[str], dim: int = EMBED_DIM) -> List[List[float]]:
    """Embed many texts; equivalent to calling :func:`embed` per item."""
    return [embed(text, dim) for text in texts]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors in ``[-1, 1]``.

    Raises:
        ValueError: If the vectors are empty or have different lengths.
    """
    if len(a) != len(b):
        raise ValueError(
            f"vector length mismatch: {len(a)} != {len(b)}"
        )
    if not a:
        raise ValueError("cannot compare empty vectors")
    dot = sum(x * y for x, y in zip(a, b))
    # Clamp: float noise can push a hair outside the theoretical range.
    return max(-1.0, min(1.0, dot))
