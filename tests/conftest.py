from pathlib import Path

import pytest


@pytest.fixture
def app_paths(tmp_path: Path):
    """Create isolated application-state paths for transaction tests."""

    def create(root: Path | None = None) -> dict[str, Path]:
        root = root or tmp_path
        paths = {
            "root": root,
            "config": root / "config.yaml",
            "cache": root / "Cache",
            "backups": root / "Backups",
            "journals": root / "Journals",
            "logs": root / "Logs",
        }
        for key, path in paths.items():
            if key != "config":
                path.mkdir(parents=True, exist_ok=True)
        return paths

    return create


class _CliOutput:
    def __init__(self, approved: bool = True):
        self.messages = []
        self.approved = approved

    def print(self, message=""):
        self.messages.append(message)

    def confirm(self, _prompt):
        return self.approved


@pytest.fixture
def cli_output():
    return _CliOutput


class _FakeTree:
    def __init__(self, rows, selected=()):
        self.rows = rows
        self.selected = tuple(selected)
        self.master = None

    def get_children(self, _parent=""):
        return tuple(self.rows)

    def delete(self, *rows):
        for row in rows:
            self.rows.pop(row, None)

    def insert(self, _parent, _index, values, tags=()):
        del tags
        row = f"row-{len(self.rows)}"
        self.rows[row] = tuple(values)
        return row

    def selection(self):
        return self.selected

    def selection_set(self, row):
        self.selected = tuple(row) if isinstance(row, (list, tuple)) else (row,)

    def identify_column(self, _x):
        return "#1"

    def identify_region(self, _x, _y):
        return "cell"

    def identify_row(self, y):
        return y

    def item(self, row, option=None, values=None):
        if values is not None:
            self.rows[row] = tuple(values)
        if option == "values":
            return self.rows[row]
        return {"values": self.rows[row]}


@pytest.fixture
def fake_tree():
    return _FakeTree


class _FakeStatus:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


@pytest.fixture
def fake_status():
    return _FakeStatus


class _FakeActivityLog:
    def __init__(self, view=(0.0, 1.0)):
        self.states = []
        self.entries = []
        self.seen = []
        self.view = view

    def yview(self):
        return self.view

    def configure(self, *, state):
        self.states.append(state)

    def insert(self, _index, value):
        self.entries.append(value)

    def see(self, index):
        self.seen.append(index)


@pytest.fixture
def fake_activity_log():
    return _FakeActivityLog
