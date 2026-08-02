# pylint: disable=import-error,protected-access

from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest

from renamer import musicbrainz


def test_release_selection_prefers_matching_embedded_album():
    candidate, warnings = musicbrainz.select_release(
        [
            {
                "id": "compilation",
                "title": "Dance Hits",
                "status": "Official",
                "date": "2001-01-01",
                "release-group": {"primary-type": "Compilation"},
            },
            {
                "id": "album",
                "title": "Original Album",
                "status": "Official",
                "date": "2002-01-01",
                "release-group": {"primary-type": "Album"},
            },
        ],
        {"album": "Original Album"},
    )

    assert candidate is not None
    assert candidate.release_id == "album"
    assert warnings == ()


def test_release_selection_uses_earliest_official_non_compilation_without_evidence():
    candidate, warnings = musicbrainz.select_release(
        [
            {
                "id": "compilation",
                "title": "Hits",
                "status": "Official",
                "date": "1999-01-01",
                "release-group": {"primary-type": "Compilation"},
            },
            {
                "id": "single",
                "title": "Song",
                "status": "Official",
                "date": "2000-01-01",
                "release-group": {"primary-type": "Single"},
            },
        ]
    )

    assert candidate is not None
    assert candidate.release_id == "single"
    assert "earliest official" in warnings[0]


def test_enrich_recording_maps_verified_release_fields(monkeypatch):
    recording_includes = []
    recording_response = {
        "recording": {
            "id": "recording",
            "title": "Song",
            "artist-credit": [{"name": "Artist"}],
            "isrc-list": ["ISRC123"],
            "genre-list": [{"name": "Electronic"}],
            "release-list": [
                {
                    "id": "release",
                    "title": "Album",
                    "status": "Official",
                    "date": "2020-01-01",
                    "release-group": {"primary-type": "Album"},
                }
            ],
        }
    }
    release_response = {
        "release": {
            "id": "release",
            "title": "Album",
            "status": "Official",
            "date": "2020-01-01",
            "country": "US",
            "artist-credit": [{"name": "Artist"}],
            "release-group": {"id": "group", "primary-type": "Album"},
            "medium-count": 1,
            "medium-list": [
                {
                    "position": "1",
                    "track-count": 10,
                    "track-list": [
                        {
                            "number": "2",
                            "recording": {"id": "recording"},
                        }
                    ],
                }
            ],
        }
    }
    monkeypatch.setattr(musicbrainz, "_available", lambda: True)
    monkeypatch.setattr(
        musicbrainz,
        "_mb",
        lambda: SimpleNamespace(
            get_recording_by_id=lambda *_args, **kwargs: (
                recording_includes.extend(kwargs["includes"])
                or recording_response
            ),
            get_release_by_id=lambda *_args, **_kwargs: release_response,
            get_work_by_id=lambda *_args, **_kwargs: {},
        ),
    )
    monkeypatch.setattr(
        musicbrainz,
        "_cached_musicbrainz",
        lambda namespace, _key, request: request(),
    )

    result = musicbrainz.enrich_recording(
        "recording",
        local_evidence={"album": "Album"},
    )

    assert result is not None
    assert result.values["musicbrainz_recordingid"] == "recording"
    assert result.values["musicbrainz_albumid"] == "release"
    assert result.values["tracknumber"] == "2"
    assert result.values["isrc"] == ["ISRC123"]
    assert "genres" not in recording_includes


def test_artist_credit_name_joins_with_provided_joinphrase():
    name = musicbrainz._artist_credit_name(
        [
            {"artist": {"name": "Aero Chord"}, "joinphrase": " feat. "},
            {"artist": {"name": "DDARK"}, "joinphrase": ""},
        ]
    )

    assert name == "Aero Chord feat. DDARK"


def test_artist_credit_name_falls_back_when_joinphrase_missing():
    # Real MusicBrainz data frequently leaves joinphrase blank between
    # credited artists even when there is more than one. Blank does not mean
    # "&"; use the application's conservative feature convention instead.
    name = musicbrainz._artist_credit_name(
        [
            {"artist": {"name": "Adventure Club"}, "joinphrase": ""},
            {"artist": {"name": "Krewella"}, "joinphrase": ""},
        ]
    )

    assert name == "Adventure Club feat. Krewella"


def test_artist_credit_roles_make_vocalist_a_feature():
    artist, features = musicbrainz._artist_credit_identity(
        [
            {"artist": {"name": "Paul van Dyk"}, "joinphrase": ""},
            {"artist": {"name": "Starkillers"}, "joinphrase": ""},
            {"artist": {"name": "Austin Leeds"}, "joinphrase": ""},
            {"artist": {"name": "Ashley Tomberlin"}, "joinphrase": ""},
        ],
        [
            {
                "type": "vocal",
                "attribute-list": ["additional", "guest", "lead vocals"],
                "artist": {"name": "Ashley Tomberlin"},
            }
        ],
    )

    assert artist == "Paul van Dyk"
    assert features == ("Ashley Tomberlin",)


def test_artist_credit_identity_preserves_explicit_coartist_joinphrase():
    artist, features = musicbrainz._artist_credit_identity(
        [
            {"artist": {"name": "Artist A"}, "joinphrase": " & "},
            {"artist": {"name": "Artist B"}, "joinphrase": ""},
        ]
    )

    assert artist == "Artist A & Artist B"
    assert features == ()


def test_artist_credit_identity_does_not_use_local_text_as_provider_evidence():
    artist, features = musicbrainz._artist_credit_identity(
        [
            {"artist": {"name": "Dimitri Vegas"}, "joinphrase": ""},
            {"artist": {"name": "Like Mike"}, "joinphrase": ""},
            {"artist": {"name": "DVBBS"}, "joinphrase": ""},
        ],
    )

    assert artist == "Dimitri Vegas"
    assert features == ("Like Mike", "DVBBS")


@pytest.mark.parametrize(
    ("credits", "relations", "expected"),
    [
        (
            [{"artist": {"name": "3rd Prototype"}, "joinphrase": ""}],
            [],
            ("3rd Prototype", ()),
        ),
        (
            [
                {"artist": {"name": "Matstubs"}, "joinphrase": ""},
                {"artist": {"name": "8Er$"}, "joinphrase": ""},
            ],
            [],
            ("Matstubs", ("8Er$",)),
        ),
        (
            [
                {"artist": {"name": "Adventure Club"}, "joinphrase": ""},
                {"artist": {"name": "Krewella"}, "joinphrase": ""},
            ],
            [],
            ("Adventure Club", ("Krewella",)),
        ),
        (
            [
                {"artist": {"name": "Baauer"}, "joinphrase": ""},
                {"artist": {"name": "Novelist"}, "joinphrase": ""},
                {"artist": {"name": "Leikeli47"}, "joinphrase": ""},
            ],
            [],
            ("Baauer", ("Novelist", "Leikeli47")),
        ),
        (
            [
                {"artist": {"name": "Paul van Dyk"}, "joinphrase": ""},
                {"artist": {"name": "Starkillers"}, "joinphrase": ""},
                {"artist": {"name": "Austin Leeds"}, "joinphrase": ""},
                {"artist": {"name": "Ashley Tomberlin"}, "joinphrase": ""},
            ],
            [
                {
                    "type": "vocal",
                    "attribute-list": ["additional", "guest", "lead vocals"],
                    "artist": {"name": "Ashley Tomberlin"},
                }
            ],
            ("Paul van Dyk", ("Ashley Tomberlin",)),
        ),
        (
            [
                {"artist": {"name": "Cash Cash"}, "joinphrase": ""},
                {"artist": {"name": "Busta Rhymes"}, "joinphrase": ""},
                {"artist": {"name": "B.o.B"}, "joinphrase": ""},
                {"artist": {"name": "Neon Hitch"}, "joinphrase": ""},
            ],
            [],
            ("Cash Cash", ("Busta Rhymes", "B.o.B", "Neon Hitch")),
        ),
        (
            [
                {"artist": {"name": "Dimitri Vegas"}, "joinphrase": " & "},
                {"artist": {"name": "Like Mike"}, "joinphrase": ""},
            ],
            [],
            ("Dimitri Vegas & Like Mike", ()),
        ),
        (
            [
                {"artist": {"name": "DJ Mustard"}, "joinphrase": ""},
                {"artist": {"name": "Lil Wayne"}, "joinphrase": ""},
                {"artist": {"name": "Big Sean"}, "joinphrase": ""},
                {"artist": {"name": "YG"}, "joinphrase": ""},
                {"artist": {"name": "Boosie Badazz"}, "joinphrase": ""},
            ],
            [],
            ("DJ Mustard", ("Lil Wayne", "Big Sean", "YG", "Boosie Badazz")),
        ),
        (
            [
                {"artist": {"name": "David Guetta"}, "joinphrase": ""},
                {"artist": {"name": "Vassy"}, "joinphrase": ""},
                {"artist": {"name": "Showtek"}, "joinphrase": ""},
            ],
            [
                {
                    "type": "vocal",
                    "attribute-list": ["lead vocals"],
                    "artist": {"name": "Vassy"},
                }
            ],
            ("David Guetta", ("Vassy",)),
        ),
        (
            [
                {"artist": {"name": "Skrillex"}, "joinphrase": ""},
                {"artist": {"name": "G-Dragon"}, "joinphrase": ""},
                {"artist": {"name": "CL"}, "joinphrase": ""},
            ],
            [],
            ("Skrillex", ("G-Dragon", "CL")),
        ),
        (
            [
                {"artist": {"name": "Fytch"}, "joinphrase": ""},
                {"artist": {"name": "Captain Crunch"}, "joinphrase": ""},
                {"artist": {"name": "Carmen Forbes"}, "joinphrase": ""},
            ],
            [],
            ("Fytch", ("Captain Crunch", "Carmen Forbes")),
        ),
        (
            [
                {"artist": {"name": "Aero Chord"}, "joinphrase": " feat. "},
                {"artist": {"name": "DDARK"}, "joinphrase": ""},
            ],
            [],
            ("Aero Chord", ("DDARK",)),
        ),
    ],
)
def test_artist_credit_corpus_patterns(credits, relations, expected):
    assert musicbrainz._artist_credit_identity(credits, relations) == expected


def test_metadata_uses_vocal_relation_for_filename_feature():
    values = musicbrainz._metadata_from_release(
        {
            "title": "New York City",
            "artist-credit": [
                {"artist": {"name": "Paul van Dyk"}, "joinphrase": ""},
                {"artist": {"name": "Starkillers"}, "joinphrase": ""},
                {"artist": {"name": "Austin Leeds"}, "joinphrase": ""},
                {"artist": {"name": "Ashley Tomberlin"}, "joinphrase": ""},
            ],
            "artist-relation-list": [
                {
                    "type": "vocal",
                    "attribute-list": ["additional", "guest", "lead vocals"],
                    "artist": {"name": "Ashley Tomberlin"},
                }
            ],
        },
        {},
        "recording",
    )

    assert values["artist"] == "Paul van Dyk"
    assert values["title"] == "New York City (feat. Ashley Tomberlin)"


def test_artist_credit_name_does_not_add_trailing_separator():
    name = musicbrainz._artist_credit_name(
        [{"artist": {"name": "Solo Artist"}, "joinphrase": ""}]
    )

    assert name == "Solo Artist"


def test_cached_musicbrainz_coalesces_concurrent_identical_requests(monkeypatch):
    stored = {}
    cache_lock = threading.Lock()
    request_calls = []

    class Cache:
        def get(self, namespace, cache_key):
            with cache_lock:
                return stored.get((namespace, cache_key))

        def set(self, namespace, cache_key, value):
            with cache_lock:
                stored[(namespace, cache_key)] = value

    def request():
        request_calls.append(True)
        time.sleep(0.05)
        return {"recording": {"id": "recording"}}

    cache = Cache()

    def cache_provider():
        return cache

    monkeypatch.setattr(musicbrainz, "enrichment_cache", cache_provider)
    monkeypatch.setattr(musicbrainz, "_rate_limit", lambda: None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _item: musicbrainz._cached_musicbrainz(
                    "recording",
                    "recording",
                    request,
                ),
                range(2),
            )
        )

    assert results == [
        {"recording": {"id": "recording"}},
        {"recording": {"id": "recording"}},
    ]
    assert request_calls == [True]


def test_cached_musicbrainz_does_not_hide_programming_errors(monkeypatch):
    class Cache:
        @staticmethod
        def get(_namespace, _cache_key):
            return None

    class InvalidRequest(Exception):
        pass

    monkeypatch.setattr(musicbrainz, "enrichment_cache", Cache)
    monkeypatch.setattr(musicbrainz, "_rate_limit", lambda: None)

    with pytest.raises(InvalidRequest):
        musicbrainz._cached_musicbrainz(
            "recording",
            "recording",
            lambda: (_ for _ in ()).throw(InvalidRequest()),
        )
