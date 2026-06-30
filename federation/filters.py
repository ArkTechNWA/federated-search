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


# Pattern for SCREAMING_SNAKE_CASE entity names (3+ chars, at least one underscore)
import re
_ENTITY_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)(?![A-Za-z0-9_])")


def annotate_cross_bank_overlap(results: list[FederatedResult]) -> None:
    """Annotate flex results that reference KG entities already in the results.

    Adds overlaps_with to metadata. Does NOT suppress or demote.
    The consuming agent decides whether the flex context adds value.
    Modifies results in place.
    """
    # Collect KG entity names
    kg_entities = {r.title for r in results if r.source_type == "entity"}
    if not kg_entities:
        return

    for r in results:
        if r.source_type != "chunk":
            continue

        # Find SCREAMING_SNAKE_CASE names in the snippet
        found_in_snippet = _ENTITY_NAME_RE.findall(r.snippet)
        overlaps = [name for name in found_in_snippet if name in kg_entities]

        if overlaps:
            r.metadata["overlaps_with"] = list(set(overlaps))
