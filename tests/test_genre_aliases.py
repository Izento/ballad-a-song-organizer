from renamer.genre_aliases import normalize_genre, normalize_genre_list


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
