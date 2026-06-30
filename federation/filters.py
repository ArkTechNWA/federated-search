"""Signal quality filters — noise reduction for federated results."""

from __future__ import annotations

from typing import Any

from federation.types import FederatedResult

# Queries shorter than this skip search entirely
MIN_QUERY_LENGTH = 2

# Common single-character and stopword queries that return garbage
STOPWORDS = {"a", "an", "the", "is", "it", "to", "of", "in", "on", "at", "by", "or", "and", "for", "no", "so", "do"}

# Default confidence floor — results below this get cut
DEFAULT_MIN_RELEVANCE = 0.25

# Adaptive threshold — if best result is above this, cut anything below half its score
ADAPTIVE_HIGH_CONFIDENCE = 0.6


def validate_query(query: str) -> str | None:
    """Return an error message if the query is too short/meaningless, or None if ok."""
    stripped = query.strip()
    if not stripped:
        return "Empty query. Please provide a search term."
    if len(stripped) < MIN_QUERY_LENGTH:
        return f"Query too short (min {MIN_QUERY_LENGTH} chars). Try a more specific term."
    if stripped.lower() in STOPWORDS:
        return f"'{stripped}' is too common to produce meaningful results. Try a more specific term."
    return None


def apply_confidence_floor(
    results: list[FederatedResult],
    min_relevance: float = DEFAULT_MIN_RELEVANCE,
) -> tuple[list[FederatedResult], int]:
    """Remove results below the confidence floor.

    Returns (filtered_results, num_cut).
    """
    kept = [r for r in results if r.relevance >= min_relevance]
    return kept, len(results) - len(kept)


def apply_adaptive_count(
    results: list[FederatedResult],
) -> tuple[list[FederatedResult], int]:
    """If there's a big quality gap, trim the tail.

    If the best result is high-confidence and there's a cluster of weak
    results well below it, cut the weak ones. The agent gets clean results
    and a note about omitted noise.
    """
    if not results or len(results) <= 2:
        return results, 0

    best = max(r.relevance for r in results)
    if best < ADAPTIVE_HIGH_CONFIDENCE:
        # All results are mediocre — keep them all, let the agent judge
        return results, 0

    # Cut anything below 40% of the best score
    threshold = best * 0.4
    kept = [r for r in results if r.relevance >= threshold]
    return kept, len(results) - len(kept)
