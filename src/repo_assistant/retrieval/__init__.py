"""Retrieval pipeline."""

from repo_assistant.retrieval.assembly import assemble_context
from repo_assistant.retrieval.fusion import reciprocal_rank_fusion
from repo_assistant.retrieval.identifiers import extract_identifiers
from repo_assistant.retrieval.service import RetrievedChunk, hybrid_retrieve, retrieve

__all__ = [
    "RetrievedChunk",
    "assemble_context",
    "extract_identifiers",
    "hybrid_retrieve",
    "reciprocal_rank_fusion",
    "retrieve",
]
