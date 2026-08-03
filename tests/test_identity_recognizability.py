# pylint: disable=import-error

import pytest

from renamer.domain.issues import ReviewIssue
from renamer.naming.identity import (
    artist_appears_in,
    identity_is_recognizable,
    is_placeholder_artist,
)


def _recognizable(local: tuple[str, str], proposed: tuple[str, str]) -> bool:
    return identity_is_recognizable(
        local_artist=local[0],
        local_title=local[1],
        proposed_artist=proposed[0],
        proposed_title=proposed[1],
    )


@pytest.mark.parametrize(
    ("local", "proposed"),
    [
        # A wrong embedded MusicBrainz ID resolves to clean metadata for an
        # entirely unrelated song. These are real cases from a library where
        # every one of them was reported as high confidence.
        (("Activator & Zatox", "Uocciu Fink"), ("Efdemin", "Time")),
        (
            ("Bass Hunter", "En porrig sommar"),
            ("David Oistrakh & Sviatoslav Richter", "Violin Sonata No. 2"),
        ),
        # Same title, completely different act: the shape of a same-title
        # mixup, which a title-only check would wave straight through.
        (("Alex Sayz", "Faces"), ("Say Just Words", "Faces")),
        (
            ("Alvaro & Joey Dale", "Ready For Action (Original Mix)"),
            ("The Crystal Method", "Ready for Action (Original Mix)"),
        ),
        (
            ("Canibus", "Sway & King Tech (feat. DJ Revolution)"),
            ("Sway & King Tech", "Canibus Freestyle (feat. DJ Revolution)"),
        ),
    ],
)
def test_unrelated_matches_are_not_recognizable(local, proposed):
    assert not _recognizable(local, proposed)


@pytest.mark.parametrize(
    ("local", "proposed"),
    [
        # Crediting a remix to the original artist rewrites both fields, but
        # the local artist survives inside the version label.
        (
            ("Beatman & Ludmilla", "Bazantar"),
            ("Paul Oakenfold", "Ready Steady Go! (Beatman & Ludmilla radio edit)"),
        ),
        (("Baauer", "Mindfields (Baauer Remix)"), ("Prodigy", "Mindfields (Baauer remix)")),
        (
            ("Baggi Begovic", "If A Lie Was Love (Baggi Begovic KNAL Mix)"),
            ("Josie Cotton", "If A Lie Was Love (Baggi Begovic KNAL mix)"),
        ),
        # Spacing and punctuation differences leave no shared whole token.
        (("Bass Hunter", "Contact By Bass"), ("Basshunter", "Contact by Bass")),
        (("A-Lusion", "Veritas"), ("A-lusion", "Veritas")),
        # Accents are routinely dropped in filenames.
        (("DJ Tiesto", "Lethal Industries"), ("Ti\u00ebsto", "Lethal Industry")),
        (("AutoErotique", "I Get Up"), ("Auto\u00c9rotique", "I Get Up")),
        # Featured artists promoted into the artist credit.
        (("Bassjackers", "Derp (feat. MAKJ)"), ("Bassjackers & MAKJ", "Derp")),
        # Nothing local to check against: unverifiable is not the same as wrong.
        (("", ""), ("Some Artist", "Some Title")),
    ],
)
def test_legitimate_corrections_stay_recognizable(local, proposed):
    assert _recognizable(local, proposed)


def test_untagged_file_falls_back_to_its_title():
    assert _recognizable(("", "Uocciu Fink"), ("Activator & Zatox", "Uocciu Fink"))
    assert not _recognizable(("", "Uocciu Fink"), ("Efdemin", "Time"))


@pytest.mark.parametrize(
    ("stem", "proposed_artist"),
    [
        # A label or channel sits where the parser expects the artist.
        ("Future House Records - SvanteG & Abedz - Tantrum", "Abedz & SvanteG"),
        ("Sirup Music - Antonio Giacca - Sensation (Radio Mix)", "Antonio Giacca"),
        # The file credits the members; MusicBrainz credits the alias act.
        ("Skrillex And Diplo - To \u00dc (feat. AlunaGeorge)", "Jack \u00dc & AlunaGeorge"),
        ("Warsongs - Edge of Infinity (Minnesota Remix)", "League of Legends & Minnesota"),
    ],
)
def test_artist_named_anywhere_in_the_filename_counts(stem, proposed_artist):
    assert artist_appears_in(stem, proposed_artist)


@pytest.mark.parametrize(
    ("stem", "proposed_artist"),
    [
        ("Activator & Zatox - Uocciu Fink", "Efdemin"),
        ("Alex Sayz - Faces", "Say Just Words"),
        ("RL Grime - Flood", "Tool"),
        ("Nero - Innocence", "Sennen"),
    ],
)
def test_unnamed_artist_does_not_count(stem, proposed_artist):
    assert not artist_appears_in(stem, proposed_artist)


def test_identity_mismatch_warning_requires_review():
    issue = ReviewIssue.from_message(
        'Identity mismatch: this file says "Alex Sayz - Faces" but the '
        'matched recording is "Say Just Words - Faces". Confirm the match '
        "before applying."
    )

    assert issue.requires_review
    # Still applyable: the user may know better than the check does.
    assert issue.apply_eligible


@pytest.mark.parametrize(
    "artist",
    ["Unknown Artist", "unknown", "Various Artists", "VA", "No Artist"],
)
def test_placeholder_artists_are_not_valid_identities(artist):
    assert is_placeholder_artist(artist)


@pytest.mark.parametrize(
    "message",
    [
        "Placeholder identity: provider supplied Unknown Artist.",
        "Protected local identity: freestyle evidence was discarded.",
    ],
)
def test_hard_identity_guards_are_not_applyable(message):
    issue = ReviewIssue.from_message(message)

    assert issue.requires_review
    assert not issue.apply_eligible
