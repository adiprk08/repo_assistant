"""Reciprocal Rank Fusion for combining retrieval channels (docs/adr/0004).

RRF merges ranked lists using only rank position, so channels with
incomparable score scales (cosine similarity vs. trigram similarity vs. BM25)
combine robustly without any score calibration.
"""

from collections import defaultdict

RRF_K = 60  # standard dampening constant; larger = flatter rank weighting


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one ranking of (id, fused_score), best first."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
