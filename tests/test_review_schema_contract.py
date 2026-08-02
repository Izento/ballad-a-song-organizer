# pylint: disable=import-error

from renamer.review_models import FileSnapshot, ReviewPlan, TagProposal


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
