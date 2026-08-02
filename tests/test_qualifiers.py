# pylint: disable=import-error

from renamer.qualifiers import (
    has_matching_qualifier,
    parse_qualifiers,
    preserve_local_versions,
    remove_safe_noise,
)


def test_vip_variants_are_preserved_without_forcing_mix_or_edit():
    qualifiers = parse_qualifiers("Artist - Song [V.I.P.]")

    assert qualifiers[0].value == "VIP"
    assert qualifiers[0].kind == "vip"
    assert qualifiers[0].is_recording_identity


def test_bonus_track_is_release_context_not_recording_identity():
    qualifiers = parse_qualifiers("Boten Anna (Bonus Track)")

    assert qualifiers[0].kind == "release_context"
    assert remove_safe_noise("Boten Anna (Bonus Track)") == "Boten Anna"


def test_explicit_extended_version_is_retained_when_online_title_omits_it():
    assert not has_matching_qualifier(
        "Matador De Dragao (Extended)",
        "Matador De Dragão",
    )
    assert preserve_local_versions(
        "Matador De Dragao (Extended)",
        "Matador De Dragão",
    ) == "Matador De Dragão (Extended)"


def test_quality_wrapper_is_removed_but_radio_edit_remains_identity():
    assert remove_safe_noise("Head Up (Original Radio Edit HQ)") == (
        "Head Up (Original Radio Edit HQ)"
    )
    qualifiers = parse_qualifiers("Head Up (Original Radio Edit HQ)")

    assert qualifiers[0].kind == "version"
