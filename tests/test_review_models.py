# pylint: disable=import-error

import pytest

from renamer.review_models import FileSnapshot, ReviewPlan, TagProposal


def test_tag_proposal_round_trips_staged_artwork_and_evidence(tmp_path):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(
        str(source),
        tags={"artist": "Artist"},
        artwork={"sha256": "before", "size": 1, "mime_type": "image/jpeg"},
    )
    proposal = TagProposal(
        id="tag",
        decision_group_id="group",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Artist"},
        after={"artist": "Artist", "genre": ["Electronic", "House"]},
        confidence="high",
        reason="enrichment",
        artwork_before=snapshot.artwork,
        artwork_after={
            "path": "cover.jpg",
            "sha256": "after",
            "size": 2,
            "mime_type": "image/jpeg",
            "release_id": "release",
            "source_url": "https://example.invalid/cover.jpg",
        },
        evidence={"recording_id": "recording"},
    )

    plan = ReviewPlan.create(str(tmp_path), False, tag_proposals=[proposal])
    restored = ReviewPlan.from_dict(plan.to_dict())

    assert restored.tag_proposals[0].artwork_after == proposal.artwork_after
    assert restored.tag_proposals[0].evidence == {"recording_id": "recording"}


def test_review_metadata_and_evidence_are_deeply_immutable(tmp_path):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    input_tags = {"artist": "Artist", "genre": ["House"]}
    input_evidence = {"provider": {"ids": ["recording"]}}
    proposal = TagProposal(
        id="tag",
        decision_group_id="group",
        snapshot=FileSnapshot.capture(str(source), tags=input_tags),
        path=str(source),
        before=input_tags,
        after=input_tags,
        confidence="high",
        reason="test",
        evidence=input_evidence,
    )

    input_tags["artist"] = "Mutated"
    input_tags["genre"].append("Techno")
    input_evidence["provider"]["ids"].append("other")

    assert proposal.after == {"artist": "Artist", "genre": ["House"]}
    assert proposal.evidence == {"provider": {"ids": ["recording"]}}
    with pytest.raises(TypeError):
        proposal.after["artist"] = "Nope"


def test_schema_two_plan_has_stable_serialized_shape():
    snapshot = FileSnapshot(
        path=r"C:\Music\Artist - Song.mp3",
        file_id="1:2",
        size=123,
        mtime_ns=456,
        tags={"artist": "Artist", "title": "Song"},
        artwork={"sha256": "before", "size": 10, "mime_type": "image/jpeg"},
        sha256="audio-digest",
    )
    proposal = TagProposal(
        id="tag-id",
        decision_group_id="song-id",
        snapshot=snapshot,
        path=snapshot.path,
        before={"artist": "Artist", "title": "Song"},
        after={
            "artist": "Artist",
            "title": "Song",
            "genre": ["Electronic", "House"],
        },
        confidence="high",
        reason="enrichment",
        warnings=("warning",),
        artwork_before=snapshot.artwork,
        artwork_after={
            "path": r"C:\Cache\cover.jpg",
            "sha256": "after",
            "size": 20,
            "mime_type": "image/jpeg",
            "release_id": "release-id",
            "source_url": "https://coverartarchive.org/release/release-id/front-500",
        },
        evidence={"recording_id": "recording-id"},
    )
    plan = ReviewPlan.create(
        r"C:\Music",
        True,
        tag_proposals=[proposal],
        issues=[
            {
                "path": snapshot.path,
                "category": "metadata-enrichment",
                "message": "example",
            }
        ],
    )

    payload = plan.to_dict()

    assert payload["schema_version"] == 2
    assert set(payload) == {
        "batch_id",
        "schema_version",
        "app_version",
        "root",
        "recursive",
        "created_at",
        "rename_proposals",
        "tag_proposals",
        "duplicate_findings",
        "issues",
        "digest",
    }
    assert set(payload["tag_proposals"][0]) == {
        "id",
        "decision_group_id",
        "snapshot",
        "path",
        "before",
        "after",
        "confidence",
        "reason",
        "warnings",
        "status",
        "artwork_before",
        "artwork_after",
        "evidence",
    }
    assert ReviewPlan.from_dict(payload).to_dict() == payload
