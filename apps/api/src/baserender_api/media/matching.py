from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from rapidfuzz import fuzz

from baserender.media_inventory import MediaReferenceEntry
from baserender_api.media.provider import CloudMediaObject


@dataclass(frozen=True)
class MatchSuggestion:
    key: str
    score: float


def suggest_matches(
    references: list[MediaReferenceEntry] | tuple[MediaReferenceEntry, ...],
    objects: list[CloudMediaObject] | tuple[CloudMediaObject, ...],
    *,
    limit: int = 3,
    min_score: float = 60.0,
) -> dict[str, tuple[MatchSuggestion, ...]]:
    return {
        reference.id: _suggest_for_reference(
            reference,
            objects,
            limit=limit,
            min_score=min_score,
        )
        for reference in references
    }


def _suggest_for_reference(
    reference: MediaReferenceEntry,
    objects: list[CloudMediaObject] | tuple[CloudMediaObject, ...],
    *,
    limit: int,
    min_score: float,
) -> tuple[MatchSuggestion, ...]:
    query = _reference_query(reference)
    if not query:
        return ()

    scored = [
        MatchSuggestion(key=obj.key, score=_score_candidate(query, obj.key))
        for obj in objects
    ]
    scored = [candidate for candidate in scored if candidate.score >= min_score]
    return tuple(sorted(scored, key=lambda candidate: (-candidate.score, candidate.key))[:limit])


def _score_candidate(query: str, key: str) -> float:
    query_name = _basename(query)
    key_name = _basename(key)
    query_stem = _stem(query_name)
    key_stem = _stem(key_name)

    score = max(
        fuzz.ratio(query_name.casefold(), key_name.casefold()),
        fuzz.partial_ratio(query_name.casefold(), key.casefold()),
        fuzz.token_set_ratio(query_stem.casefold(), key_stem.casefold()),
    )
    if _extension(query_name).casefold() == _extension(key_name).casefold():
        score = min(100.0, score + 5.0)
    return float(score)


def _reference_query(reference: MediaReferenceEntry) -> str | None:
    return reference.normalized_url or reference.target_url or reference.clip_name or None


def _basename(value: str) -> str:
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme else value
    return PurePosixPath(unquote(path)).name or value


def _stem(value: str) -> str:
    return PurePosixPath(value).stem


def _extension(value: str) -> str:
    return PurePosixPath(value).suffix
