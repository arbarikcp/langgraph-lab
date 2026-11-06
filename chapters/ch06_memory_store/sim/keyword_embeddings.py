"""Deterministic stand-in for an embedding model, so semantic search runs without Ollama.

Each dimension is one on-call concept; synonyms map to the same dimension, so
"checkpoints failing" and "checkpoint timeout" land close together.
"""
from langchain_core.embeddings import Embeddings

CONCEPTS = {
    "checkpoint": ("checkpoint",),
    "failure": ("timeout", "timed out", "failing", "failed"),
    "backpressure": ("backpressure", "backpressured"),
    "join": ("join",),
    "memory": ("heap", "memory", "oom"),
    "lag": ("lag", "lagging"),
    "small_files": ("small file", "compaction"),
    "scan": ("scan", "slow query", "trino"),
}
DIMS = len(CONCEPTS)


class KeywordEmbeddings(Embeddings):
    def _embed(self, text: str) -> list[float]:
        text = text.lower()
        return [1.0 if any(w in text for w in words) else 0.0 for words in CONCEPTS.values()]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
