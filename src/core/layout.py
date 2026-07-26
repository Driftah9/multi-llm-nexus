"""Resolve install directory locations from the layout manifest.

`config/directory_layout.json` is the single source of truth for where a Nexus install
keeps its files (human-readable face: `docs/DIRECTORY_LAYOUT.md`; the installer scaffolds
from the same file). This module lets the running engine resolve those locations instead of
hardcoding `Path.home() / "..."`, so the installer, the docs, and the system all read one
declaration — and a typo'd folder name fails loud instead of silently creating a stray dir.

Usage:
    from src.core import layout
    layout.path("venv")                  # -> ~/venv
    layout.path(".local/nexus", create=True)
    layout.folders("scaffold")           # -> ["venv", "Tools", ...]
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# This file is src/core/layout.py → parents[2] is the repo root that holds config/.
_MANIFEST_PATH = Path(__file__).resolve().parents[2] / "config" / "directory_layout.json"


@lru_cache(maxsize=1)
def _manifest() -> dict:
    return json.loads(_MANIFEST_PATH.read_text())


def _entry(name: str) -> dict:
    for folder in _manifest()["folders"]:
        if folder["path"] == name:
            return folder
    raise KeyError(
        f"{name!r} is not a declared directory in the layout manifest ({_MANIFEST_PATH}). "
        f"Add it to config/directory_layout.json rather than hardcoding the path."
    )


def path(name: str, *, create: bool = False) -> Path:
    """Absolute path for a manifest folder, resolved under the user's home.

    `name` is the manifest ``path`` key (e.g. ``"venv"``, ``"Tools"``, ``".local/nexus"``).
    Raises ``KeyError`` if the name is not declared in the manifest — hardcoded, undeclared
    locations are exactly what this module exists to prevent. Set ``create=True`` to mkdir it
    (parents, exist_ok).
    """
    _entry(name)  # validate against the manifest; fail loud on typos / undeclared homes
    resolved = Path.home() / name
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def folders(cls: str | None = None) -> list[str]:
    """Manifest folder names, optionally filtered by class (``scaffold``/``runtime``/``managed``)."""
    return [f["path"] for f in _manifest()["folders"] if cls is None or f.get("class") == cls]


def purpose(name: str) -> str:
    """The one-line purpose declared for a folder."""
    return _entry(name).get("purpose", "")


def manifest_path() -> Path:
    """Absolute path to the manifest file itself."""
    return _MANIFEST_PATH
