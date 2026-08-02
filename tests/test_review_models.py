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
