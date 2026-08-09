from ballad import __version__ as public_version
from cli.parser import build_parser
from gui.app import GUI_TITLE
from renamer.review_models import APP_VERSION
from renamer.version import __version__


def test_release_version_is_shared_across_application_surfaces():
    assert __version__ == "1.4.2"
    assert public_version == __version__
    assert __version__ == APP_VERSION
    assert f"Ballad {__version__}" == GUI_TITLE


def test_cli_exposes_release_version(capsys):
    try:
        build_parser().parse_args(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.strip() == "ballad 1.4.2"
