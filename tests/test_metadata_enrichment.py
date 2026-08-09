# pylint: disable=import-error

import threading
import time

import pytest

from renamer import review_service as review_api
from renamer.genre_aliases import normalize_genre, normalize_genre_list
from renamer.identification import RecordingEvidence
from renamer.media import MediaRead
from renamer.musicbrainz import EnrichmentResult
from renamer.planners.enrichment import plan_metadata_enrichment


def test_metadata_enrichment_coordinates_filename_and_tags(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song (Bonus Track).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Old Artist", "title": "Old Title"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Artist",
                "title": "Song",
                "album": "Album",
                "musicbrainz_recordingid": "recording",
            },
            release_id="release",
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert len(renames) == 1
    assert renames[0].new_path.endswith("Artist - Song.mp3")
    assert len(tags) == 1
    assert tags[0].after["album"] == "Album"
    assert tags[0].after["musicbrainz_recordingid"] == "recording"


def test_metadata_enrichment_preserves_local_latin_identity(tmp_path, monkeypatch):
    source = tmp_path / "Artist - English Song.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Old Tag"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "日本のアーティスト",
                "title": "日本語の曲",
                "album": "日本のアルバム",
                "musicbrainz_recordingid": "recording",
            },
            release_id="release",
            confidence="high",
        ),
    )

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
        include_artwork=False,
    )

    assert issues == []
    assert renames == []
    assert len(tags) == 1
    assert tags[0].after["artist"] == "Artist"
    assert tags[0].after["title"] == "English Song"
    assert tags[0].after["album"] == "日本のアルバム"


def test_ocremix_enrichment_preserves_game_title_and_records_remixer(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Aeroz - The 7th Guest OC ReMix (OC ReMix).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Aeroz", "title": "The 7th Guest OC ReMix (OC ReMix)"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Chernobague",
                "title": "The 7th Guest OC ReMix (OC ReMix)",
                "album": "The 7th Guest",
                "musicbrainz_recordingid": "recording",
            },
            release_id="release",
            confidence="high",
        ),
    )

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
        include_artwork=False,
    )

    assert issues == []
    assert len(renames) == 1
    assert renames[0].new_path.endswith("Aeroz - The 7th Guest (Chernobague) [OC ReMix].mp3")
    assert len(tags) == 1
    assert tags[0].after["artist"] == "Aeroz"
    assert tags[0].after["title"] == "The 7th Guest (Chernobague)"
    assert tags[0].after["remixer"] == ["Chernobague"]
    assert tags[0].after["album_artist"] == "OverClocked ReMix"
    assert "musicbrainz_recordingid" not in tags[0].after


def test_metadata_enrichment_does_not_assign_release_to_local_derivative(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Artist - Song (Extended).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            derived_from_recording_id="source",
            confidence="medium",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="source",
            values={
                "artist": "Artist",
                "title": "Song",
                "composer": ["Composer"],
                "musicbrainz_recordingid": "source",
                "album": "Original Album",
            },
            release_id="release",
            confidence="medium",
        ),
    )

    _renames, tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert tags[0].after["title"] == "Song (Extended)"
    assert tags[0].after["composer"] == ["Composer"]
    assert "album" not in tags[0].after
    assert "musicbrainz_recordingid" not in tags[0].after
    assert tags[0].artwork_after is None


def test_metadata_enrichment_does_not_duplicate_featured_artist(tmp_path, monkeypatch):
    # The original filename already names the featured artist. If the
    # enriched artist string also folds that same artist in (e.g. from an
    # under-specified MusicBrainz artist-credit join), the rename must not
    # mention the featured artist twice.
    source = tmp_path / "Aero Chord feat. DDARK - Shootin Stars.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Aero Chord feat. DDARK", "title": "Shootin Stars"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                # No joinphrase between credits: the feature ends up folded
                # into the artist string without a "feat." marker at all.
                "artist": "Aero Chord & DDARK",
                "title": "Shootin Stars",
                "musicbrainz_recordingid": "recording",
            },
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, _tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert len(renames) == 1
    new_name = renames[0].new_path
    assert new_name.count("DDARK") == 1


def test_metadata_enrichment_extracts_feat_from_enriched_title(tmp_path, monkeypatch):
    source = tmp_path / "Alexander Lewis - So Nice.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Alexander Lewis", "title": "So Nice"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Alexander Lewis",
                "title": "So Nice (feat. KRANE)",
                "musicbrainz_recordingid": "recording",
            },
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, _tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert renames[0].new_path.endswith("Alexander Lewis - So Nice (feat. KRANE).mp3")


def test_metadata_enrichment_deduplicates_stylized_feature_names(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Alexander Lewis - So Nice (feat. Krne).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={
                "artist": "Alexander Lewis",
                "title": "So Nice (feat. Krne)",
            },
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={"artist": "Alexander Lewis", "title": "So Nice (feat. KRANE)"},
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, _tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert len(renames) == 1
    assert renames[0].new_path.endswith("Alexander Lewis - So Nice (feat. KRANE).mp3")
    assert renames[0].new_path.count("Krne") == 0
    assert renames[0].new_path.count("KRANE") == 1


def test_metadata_enrichment_preserves_local_feature_when_provider_omits_it(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Paul van Dyk - Old Name (feat. Ashley).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={
                "artist": "Paul van Dyk",
                "title": "Old Name (feat. Ashley)",
            },
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={"artist": "Paul van Dyk", "title": "New York City"},
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert renames[0].new_path.endswith("Paul van Dyk - New York City (feat. Ashley).mp3")
    assert tags[0].after["title"] == "New York City (feat. Ashley)"


def test_metadata_enrichment_preserves_explicit_local_collaboration_layout(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Dimitri Vegas & Like Mike - Stampede.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Dimitri Vegas & Like Mike", "title": "Stampede"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Dimitri Vegas",
                "title": "Stampede (feat. Like Mike, DVBBS)",
            },
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert renames[0].new_path.endswith("Dimitri Vegas & Like Mike - Stampede (feat. DVBBS).mp3")
    assert tags[0].after["artist"] == "Dimitri Vegas & Like Mike"
    assert tags[0].after["title"] == "Stampede (feat. DVBBS)"


def test_metadata_enrichment_can_skip_cover_art_downloads(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Song"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={"album": "Album"},
            release_id="release",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "download_front_art",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cover art was downloaded")),
    )

    _renames, tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
        include_artwork=False,
    )

    assert tags[0].artwork_after is None


def test_metadata_enrichment_deduplicates_shared_recording_and_artwork(tmp_path):
    for name in ("Artist - Song.mp3", "Artist - Song Copy.mp3"):
        (tmp_path / name).write_bytes(b"audio")
    enriched_calls = []
    artwork_calls = []

    def media_reader(path):
        return MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Song"},
        )

    def recording_enricher(recording_id, **_kwargs):
        enriched_calls.append(recording_id)
        return EnrichmentResult(
            recording_id=recording_id,
            values={"album": "Shared Album"},
            release_id="shared-release",
            confidence="high",
        )

    def artwork_download(release_id):
        artwork_calls.append(release_id)

    _renames, tags, issues = plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
        media_reader=media_reader,
        identifier=lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="shared-recording",
            confidence="high",
        ),
        recording_enricher=recording_enricher,
        artwork_download=artwork_download,
    )

    assert issues == []
    assert len(tags) == 2
    assert enriched_calls == ["shared-recording"]
    assert artwork_calls == ["shared-release"]


def test_metadata_enrichment_identifies_files_concurrently(tmp_path):
    for index in range(4):
        (tmp_path / f"Artist - Song {index}.mp3").write_bytes(b"audio")
    identifier_threads = set()
    thread_lock = threading.Lock()

    def identifier(*_args, **_kwargs):
        with thread_lock:
            identifier_threads.add(threading.get_ident())
        time.sleep(0.05)
        return RecordingEvidence(
            exact_recording_id="shared-recording",
            confidence="high",
        )

    def media_reader(path):
        return MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Song"},
        )

    plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
        include_artwork=False,
        media_reader=media_reader,
        identifier=identifier,
        recording_enricher=lambda recording_id, **_kwargs: EnrichmentResult(
            recording_id=recording_id,
            values={"album": "Album"},
            confidence="high",
        ),
    )

    assert len(identifier_threads) > 1


def test_metadata_enrichment_caps_confidence_at_the_weaker_signal(tmp_path, monkeypatch):
    # A fingerprint-based identification is inherently less certain than an
    # embedded MusicBrainz ID, even when MusicBrainz cleanly resolves a
    # release for whatever recording it was handed. The displayed
    # confidence must reflect that weaker signal, not just the release
    # lookup succeeding.
    source = tmp_path / "Old Artist - Old Title.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Old Artist", "title": "Old Title"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="medium",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Artist",
                "title": "Song",
                "musicbrainz_recordingid": "recording",
            },
            release_id="release",
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert issues == []
    assert len(renames) == 1
    assert renames[0].confidence == "medium"
    assert tags[0].confidence == "medium"


def test_wrong_embedded_recording_id_is_flagged_instead_of_trusted(tmp_path, monkeypatch):
    # A file carrying someone else's MusicBrainz recording ID gets that ID
    # trusted without any fingerprinting, and MusicBrainz returns spotless
    # metadata for the wrong song. Nothing upstream can notice, so the plan
    # must not present it as a confident rename.
    source = tmp_path / "Activator & Zatox - Uocciu Fink.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Activator & Zatox", "title": "Uocciu Fink"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="wrong-recording",
            confidence="high",
            provenance=("embedded MusicBrainz recording ID",),
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="wrong-recording",
            values={
                "artist": "Efdemin",
                "title": "Time",
                "musicbrainz_recordingid": "wrong-recording",
            },
            release_id="release",
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert renames[0].confidence == "low"
    assert renames[0].requires_review
    assert any("Identity mismatch" in warning for warning in renames[0].warnings)
    assert tags[0].confidence == "low"
    assert tags[0].requires_review


def test_placeholder_artist_is_skipped_from_planned_changes(tmp_path, monkeypatch):
    source = tmp_path / "Chino XL - Bat Signals Up.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Chino XL", "title": "Bat Signals Up"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Unknown Artist",
                "title": "Bat Signals Up (feat. Chino XL)",
            },
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert renames == []
    assert tags == []
    assert issues[0].category == "placeholder-identity"
    assert "Unknown Artist" in issues[0].message


def test_placeholder_local_tag_is_skipped_even_without_provider_override(tmp_path, monkeypatch):
    # MusicBrainz can resolve a title without ever asserting an artist name
    # (e.g. a recording with no usable artist credit). When that happens
    # the enriched "after" dict falls back to the file's own tag, which is
    # frequently "Unknown Artist" on ripped/downloaded files even though
    # the filename itself is correct. The safeguard has to catch this
    # merged value, not just an artist MusicBrainz explicitly proposed.
    source = tmp_path / "The Game - Here We Go Again (feat. Dr. Dre).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Unknown Artist", "title": "Track 01"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            # No "artist" key at all -- MusicBrainz filtered it out because
            # it could not resolve a usable artist credit.
            values={"title": "Here We Go Again (feat. Dr. Dre)"},
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert renames == []
    assert tags == []
    assert issues[0].category == "placeholder-identity"
    assert "Skipped filename and metadata changes" in issues[0].message


def test_feature_credit_never_becomes_an_unknown_artist_filename(tmp_path, monkeypatch):
    source = tmp_path / "Dr. Dre - Push Play (feat. Truth Hurts).mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Dr. Dre", "title": "Push Play (feat. Truth Hurts)"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={"artist": "Truth Hurts", "title": "Push Play"},
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert renames == []
    assert tags[0].after["artist"] == "Truth Hurts"


def test_metadata_enrichment_skips_equivalent_multi_value_tag_formats(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={
                "artist": "Artist",
                "title": "Song",
                "tag": ["hip-hop", "rap", "hip-hop", "rap"],
            },
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Artist",
                "title": "Song",
                "tag": ["hip-hop/rap"],
            },
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert renames == []
    assert tags == []
    assert issues == []


def test_genre_aliases_are_applied_to_the_merged_after_value(tmp_path, monkeypatch):
    # The alias has to run on the merged "after" dict, not just whatever
    # MusicBrainz returns -- otherwise a file whose *existing* local tag is
    # "Rap" (with MusicBrainz silent on genre for this recording) would
    # never get consolidated into "Hip-Hop".
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Artist", "title": "Song", "genre": ["Rap"]},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Artist",
                "title": "Song",
                "musicbrainz_recordingid": "recording",
            },
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    _renames, tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert tags[0].after["genre"] == ["Hip-Hop"]


def test_freestyle_identity_cannot_be_silently_replaced(tmp_path, monkeypatch):
    source = tmp_path / "Canibus - Live Freestyle Diss.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Canibus", "title": "Live Freestyle Diss"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={"artist": "Canibus", "title": "No Return"},
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert any("Protected local identity" in warning for warning in renames[0].warnings)
    assert not renames[0].apply_eligible
    assert not tags[0].apply_eligible


def test_enrichment_does_not_flag_a_remix_credited_to_its_original_artist(
    tmp_path,
    monkeypatch,
):
    # Files named after the remixer legitimately gain a different artist and
    # title. The local artist still survives in the version label, so this
    # must not be treated as a mismatch.
    source = tmp_path / "Beatman & Ludmilla - Bazantar.mp3"
    source.write_bytes(b"audio")
    monkeypatch.setattr(
        review_api,
        "read_media",
        lambda path: MediaRead(
            path=path,
            status="ok",
            container="MP3",
            tags={"artist": "Beatman & Ludmilla", "title": "Bazantar"},
        ),
    )
    monkeypatch.setattr(
        review_api,
        "identify",
        lambda *_args, **_kwargs: RecordingEvidence(
            exact_recording_id="recording",
            confidence="high",
        ),
    )
    monkeypatch.setattr(
        review_api,
        "enrich_recording",
        lambda *_args, **_kwargs: EnrichmentResult(
            recording_id="recording",
            values={
                "artist": "Paul Oakenfold",
                "title": "Ready Steady Go! (Beatman & Ludmilla radio edit)",
                "musicbrainz_recordingid": "recording",
            },
            release_id="release",
            confidence="high",
        ),
    )
    monkeypatch.setattr(review_api, "download_front_art", lambda _release: None)

    renames, _tags, _issues = review_api.plan_metadata_enrichment(
        str(tmp_path),
        recursive=False,
    )

    assert renames[0].confidence == "high"
    assert not renames[0].requires_review


def test_metadata_enrichment_does_not_convert_provider_bugs_to_misses(tmp_path):
    source = tmp_path / "Artist - Song.mp3"
    source.write_bytes(b"audio")

    with pytest.raises(RuntimeError, match="invalid provider request"):
        plan_metadata_enrichment(
            str(tmp_path),
            recursive=False,
            include_artwork=False,
            media_reader=lambda path: MediaRead(
                path=path,
                status="ok",
                container="MP3",
                tags={"artist": "Artist", "title": "Song"},
            ),
            identifier=lambda *_args, **_kwargs: RecordingEvidence(
                exact_recording_id="recording",
                confidence="high",
            ),
            recording_enricher=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("invalid provider request")
            ),
        )


def test_normalize_genre_maps_known_aliases_case_insensitively():
    assert normalize_genre("Rap") == "Hip-Hop"
    assert normalize_genre("RAP") == "Hip-Hop"
    assert normalize_genre("Electronic") == "Techno"
    assert normalize_genre("EDM") == "Techno"


def test_normalize_genre_leaves_unmapped_values_untouched():
    assert normalize_genre("Hip Hop") == "Hip Hop"
    assert normalize_genre("Rock") == "Rock"


def test_normalize_genre_list_preserves_order_and_dedupes_collisions():
    assert normalize_genre_list(["Rap", "Rock", "Hip-Hop"]) == ["Hip-Hop", "Rock"]
    assert normalize_genre_list(["EDM", "Electronic", "Techno"]) == ["Techno"]
