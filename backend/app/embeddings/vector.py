from __future__ import annotations
import hashlib
import math
import numpy as np


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    va = np.array(a, dtype=float)
    vb = np.array(b, dtype=float)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def local_hash_embedding(text: str, dim: int = 1536) -> list[float]:
    """Deterministic local fallback embedding for dev/demo. Replace with Azure embeddings in production."""
    vec = [0.0] * dim
    tokens = text.lower().split()
    for token in tokens:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x*x for x in vec)) or 1.0
    return [x / norm for x in vec]
