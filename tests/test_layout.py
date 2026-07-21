"""Tests for the directory-layout resolver (src/core/layout.py)."""
from pathlib import Path

import pytest

from src.core import layout


def test_scaffold_folders_include_the_essentials():
    scaffold = layout.folders("scaffold")
    for name in ("venv", "Tools", "workspace", "skills", "adapters"):
        assert name in scaffold


def test_runtime_folders_are_not_scaffolded():
    # runtime state dirs are created by the engine, never by the installer's scaffold loop
    assert ".local/nexus" in layout.folders("runtime")
    assert ".local/nexus" not in layout.folders("scaffold")


def test_path_resolves_under_home():
    assert layout.path("venv") == Path.home() / "venv"
    assert layout.path(".local/nexus") == Path.home() / ".local" / "nexus"


def test_unknown_folder_fails_loud():
    with pytest.raises(KeyError):
        layout.path("NotADeclaredFolder")


def test_scaffold_names_are_home_relative():
    # nothing scaffolded should be an absolute path
    for name in layout.folders("scaffold"):
        assert not name.startswith("/"), name


def test_manifest_matches_installer_source_of_truth():
    # the file the installer reads is the same file this module reads
    assert layout.manifest_path().name == "directory_layout.json"
    assert layout.manifest_path().exists()


def test_path_create_makes_the_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    made = layout.path("venv", create=True)
    assert made == tmp_path / "venv"
    assert made.is_dir()
