# pylint: disable=import-error,protected-access

from types import SimpleNamespace
from typing import Any

from gui import app as gui_app
from gui.app import SongOrganizerApp
from gui.theme import confidence_row_styles, confidence_style, get_theme_palette


def test_dark_palette_is_the_default_palette():
    dark = get_theme_palette("dark")
    fallback = get_theme_palette("unsupported")

    assert dark == fallback
    assert dark.root_background == "#1e1e1e"
    assert dark.text == "#f1f1f1"


def test_light_palette_has_distinct_surface_and_text_colors():
    dark = get_theme_palette("dark")
    light = get_theme_palette("light")

    assert dark.surface_background != light.surface_background
    assert dark.text != light.text
    assert confidence_row_styles("dark") != confidence_row_styles("light")


def test_confidence_styles_use_semantic_ttk_styles():
    assert confidence_style("HIGH") == "Ballad.Success.TLabel"
    assert confidence_style("medium") == "Ballad.Warning.TLabel"
    assert confidence_style("blocked") == "Ballad.Error.TLabel"


def test_theme_toggle_reapplies_the_selected_mode(monkeypatch):
    app: Any = SongOrganizerApp.__new__(SongOrganizerApp)
    app.root = object()
    app.dark_mode_var = SimpleNamespace(get=lambda: False)
    applied = []
    monkeypatch.setattr(gui_app, "apply_theme", lambda root, mode: applied.append((root, mode)))

    app._toggle_theme()

    assert app._theme_mode == "light"
    assert applied == [(app.root, "light")]
