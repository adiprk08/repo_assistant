"""Indexing: embed chunks, write vectors to Qdrant and metadata to Postgres."""

from repo_assistant.indexing.cache import CachingEmbedder, EmbeddingCacheStore
from repo_assistant.indexing.pipeline import IndexResult, index_repository, index_working_tree
from repo_assistant.indexing.qdrant_index import QdrantVectorIndex

__all__ = [
    "CachingEmbedder",
    "EmbeddingCacheStore",
    "IndexResult",
    "QdrantVectorIndex",
    "index_repository",
    "index_working_tree",
]
