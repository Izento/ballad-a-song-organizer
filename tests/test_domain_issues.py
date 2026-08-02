# pylint: disable=import-error

from renamer.domain.issues import IssueCode, ReviewIssue


def test_issue_policy_is_independent_from_gui_wording():
    collision = ReviewIssue.from_message(
        "Destination collides with another proposal."
    )
    conflict = ReviewIssue.from_message(
        "Version qualifier conflicts with AcoustID metadata; review it."
    )
    evidence = ReviewIssue.from_message("Identity came from AcoustID.")

    assert collision.code is IssueCode.DESTINATION_COLLISION
    assert collision.requires_review
    assert not collision.apply_eligible
    assert conflict.code is IssueCode.VERSION_CONFLICT
    assert conflict.requires_review
    assert conflict.apply_eligible
    assert evidence.code is IssueCode.ONLINE_EVIDENCE
    assert not evidence.requires_review
    assert evidence.apply_eligible
