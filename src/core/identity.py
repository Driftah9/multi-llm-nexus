"""Cross-adapter identity resolution.

resolve((platform, native_id)) → person_id. This answers *who* someone is, canonically,
across adapters — the layer that feeds the resolved user into scope-based authorization
(security.py): identity resolves the person, security authorizes the action. They compose;
they don't overlap.

Read order: owner FLOOR → people registry → None. The owner is a flat-file floor that
always resolves even if everything else is missing — the operator is never locked out.
"Never guess": an unknown handle returns None, and the caller treats an unresolved user
as the shared/lowest tier (default-deny for anything sensitive).

Config (operator-supplied, NOT shipped): config/identity.json (or NEXUS_IDENTITY_CONFIG).
A fresh install with no identity config still runs — everyone resolves to None (shared
tier), nothing crashes. Generic schema (see config/identity.json.example):

    {
      "owner": {
        "person_id": "owner",
        "platforms": {"<platform>": {"ids": ["<native id>", ...]}},
        "notify_priority": ["<platform>", ...]
      },
      "people": {
        "<person_id>": {"platforms": {"<platform>": {"ids": [...]}}}
      }
    }

Ported from claude-brain live, stripped of platform-specific special-casing (no hardcoded
mattermost/discord/telegram, no username-vs-user_ids split): every platform entry is a
uniform `ids` list, matched as strings — so any adapter works with no code change.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "identity.json"
_cache: Optional[dict] = None


def _config_path() -> Path:
    return Path(os.environ.get("NEXUS_IDENTITY_CONFIG", str(_DEFAULT_PATH)))


def load() -> dict:
    """Load the identity config once and cache it. Missing/corrupt → {} (graceful: a
    fresh install with no identity configured still runs, resolving everyone to None)."""
    global _cache
    if _cache is not None:
        return _cache
    path = _config_path()
    try:
        with open(path) as f:
            _cache = json.load(f)
    except FileNotFoundError:
        _cache = {}
    except Exception as e:
        logger.warning(f"identity config unreadable ({path}): {e}")
        _cache = {}
    return _cache


def reload() -> dict:
    """Drop the cache and reload (e.g. after the operator edits the registry)."""
    global _cache
    _cache = None
    return load()


def _match(entry: dict, platform: str, native_id) -> bool:
    """True if (platform, native_id) appears in a person's platform handle list."""
    ids = (entry.get("platforms", {}).get(platform, {}) or {}).get("ids", [])
    return str(native_id) in {str(i) for i in ids}


def resolve(platform: str, native_id: str | int) -> Optional[str]:
    """Resolve a platform-native identifier to a canonical person_id.

    Owner FLOOR first (always resolves), then the people registry, else None. Never
    guesses — an unknown handle is None, and the caller treats it as the shared tier.
    """
    data = load()
    owner = data.get("owner")
    if owner and _match(owner, platform, native_id):
        return owner.get("person_id", "owner")
    for pid, entry in (data.get("people") or {}).items():
        if _match(entry, platform, native_id):
            return pid
    return None


def is_owner(platform: str, native_id: str | int) -> bool:
    """Gate check: is this person the registered owner?"""
    data = load()
    owner_id = (data.get("owner") or {}).get("person_id", "owner")
    return resolve(platform, native_id) == owner_id and bool(data.get("owner"))


def handles(person_id: str) -> dict[str, list]:
    """All platform handles for a canonical person_id → {platform: [ids...]}."""
    data = load()
    owner = data.get("owner") or {}
    entry = owner if owner.get("person_id", "owner") == person_id else (data.get("people") or {}).get(person_id)
    if not entry:
        return {}
    return {
        plat: list(h.get("ids", []))
        for plat, h in (entry.get("platforms") or {}).items()
        if h.get("ids")
    }


def notify_priority(person_id: str) -> list[str]:
    """Ordered fallback notification platforms for a person (owner only by default)."""
    data = load()
    owner = data.get("owner") or {}
    if owner.get("person_id", "owner") == person_id:
        return owner.get("notify_priority", [])
    return []
