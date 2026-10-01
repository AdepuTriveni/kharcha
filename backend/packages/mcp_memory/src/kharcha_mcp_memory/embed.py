"""Embeddings for memories (PROJECT_SPEC §20.2-20.3), 768 dimensions like ``vector(768)``.

``HashEmbedder`` (default) is deterministic feature hashing of word and character n-grams:
offline, free, good at near-duplicates. ``OllamaEmbedder`` uses a local embedding model
(``nomic-embed-text``, 768-d) for semantic search. Do not mix both in one table; switching
backends needs a re-embed.
"""

import hashlib
import math
import re
from itertools import pairwise
from typing import Protocol

import httpx

from kharcha_common.db.models import EMBEDDING_DIM

_WORD = re.compile(r"[\w₹]+", re.UNICODE)


class Embedder(Protocol):
    name: str

    async def embed(self, text: str) -> list[float]: ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class HashEmbedder:
    name = "hash-v1"

    async def embed(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        words = _WORD.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in pairwise(words)]
        joined = " ".join(words)
        features += [joined[i : i + 4] for i in range(max(0, len(joined) - 3))]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIM
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        return _normalize(vector)


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str = "nomic-embed-text") -> None:
        self.base_url = base_url
        self.model = model
        self.name = f"ollama/{model}"

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": text}
            )
            response.raise_for_status()
        vector = [float(v) for v in response.json()["embeddings"][0]]
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(f"embedding model returned {len(vector)} dims, need {EMBEDDING_DIM}")
        return _normalize(vector)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
