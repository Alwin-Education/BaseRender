from __future__ import annotations

from baserender.media_inventory import MediaReferenceEntry
from baserender_api.media.matching import suggest_matches
from baserender_api.media.provider import CloudMediaObject


def test_suggest_matches_prefers_matching_basename_and_extension() -> None:
    reference = MediaReferenceEntry(
        id="ref-a",
        clip_name="Shot A",
        track_path="Timeline/V1/Shot A",
        reference_kind="ExternalReference",
        target_url="file:///Volumes/Raid/Shot_A.mov",
        normalized_url="/Volumes/Raid/Shot_A.mov",
        status="linked",
    )
    objects = (
        CloudMediaObject(key="project/footage/Shot_A.mov", size=10),
        CloudMediaObject(key="project/footage/Shot_A.wav", size=10),
        CloudMediaObject(key="project/footage/Other.mov", size=10),
    )

    suggestions = suggest_matches((reference,), objects, limit=2, min_score=50)

    assert suggestions["ref-a"][0].key == "project/footage/Shot_A.mov"
    assert suggestions["ref-a"][0].score >= suggestions["ref-a"][1].score


def test_suggest_matches_returns_empty_for_missing_query() -> None:
    reference = MediaReferenceEntry(
        id="missing",
        clip_name="",
        track_path="Timeline/V1/Missing",
        reference_kind="MissingReference",
        target_url=None,
        normalized_url=None,
        status="missing",
    )

    suggestions = suggest_matches((reference,), (CloudMediaObject(key="a.mov", size=1),))

    assert suggestions["missing"] == ()
