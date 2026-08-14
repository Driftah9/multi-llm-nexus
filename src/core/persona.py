"""persona — loads the system personality (SOUL) into the system prompt.

WHAT THIS IS
------------
SOUL is the AUTHORED persona: who this install is and how it talks. The operator
writes it and it stays put. That is a different thing from the memory layer,
which is LEARNED: how the operator actually works, what has been corrected, what
turned out to matter. Personality is authored; behaviour is earned. Keep them
separate — merging them makes both worse.

WHY IT EXISTS
-------------
The installer has always SEEDED a SOUL.md (templates/system/SOUL.md, written by
the setup wizard) but nothing ever loaded it, so every install ran with a
personality file that had no effect. Identity then comes from whatever the
underlying provider defaults to, which is exactly the drift a persona file is
supposed to prevent.

OpenClaw and Hermes Agent independently converge on three properties, and this
module implements them:
  1. one instance-wide location,
  2. injected FIRST, every turn, BY THE RUNTIME — never left to the model
     deciding to read a file,
  3. a defined fallback when it is missing.
Hermes describes SOUL as occupying "slot #1 in the system prompt", ahead of tool
guidance and role instructions. That ordering is deliberate: role and tools
modify a voice that is already established.

RESOLUTION ORDER
----------------
    context/soul/<platform>.md   per-adapter persona    (optional)
    context/SOUL.md              system-wide persona    (the default)
    ~/SOUL.md                    pre-context/ installs  (back-compat)
    _DEFAULT                     built-in floor

Per-adapter personas let one system hold one identity while changing register by
channel — business voice on a work chat, looser on a social one, engineer-ish on
a dev channel. Adding one is a file drop; no code change.

Always safe: any failure returns the floor rather than raising. A personality
lookup must never be why a turn fails.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import layout

logger = logging.getLogger(__name__)

#: Floor identity when no SOUL is found. Deliberately terse — it exists so a
#: half-configured install still behaves like an operator's agent rather than a
#: stock assistant, not to duplicate what SOUL.md should say.
_DEFAULT = (
    "You are a persistent agent working alongside one operator, with real access "
    "to real systems. Not a chatbot, not a generic assistant. Be direct, "
    "peer-level, no filler. Have opinions and state them early. Bold internally "
    "(read, explore, organize freely); careful externally (confirm before "
    "sending, before live service changes, before anything hard to undo)."
)

#: A persona is a voice, not a manual. Truncate rather than bloat every turn.
_MAX_CHARS = 8000

_cache: dict[str, str] = {}


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def load(platform: str | None = None) -> str:
    """Return persona text for `platform`, falling back to the system-wide SOUL."""
    key = platform or "_system"
    if key in _cache:
        return _cache[key]

    try:
        context_dir = layout.path("context")
    except Exception:  # manifest unreadable — still must not break a turn
        context_dir = Path.home() / "context"

    text = ""
    if platform:
        text = _read(context_dir / "soul" / f"{platform.lower()}.md")
        if text:
            logger.info("persona: using per-adapter SOUL for %s", platform)

    if not text:
        text = _read(context_dir / "SOUL.md")

    if not text:
        # Installs created before SOUL moved into context/ keep it at the root.
        text = _read(Path.home() / "SOUL.md")
        if text:
            logger.info("persona: loaded legacy ~/SOUL.md — consider moving it to %s", context_dir)

    if not text:
        logger.warning(
            "persona: no SOUL found under %s — using the built-in floor. This install "
            "will sound generic until a SOUL.md exists.", context_dir
        )
        text = _DEFAULT

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS].rstrip() + "\n[persona truncated]"

    _cache[key] = text
    return text


def reset_cache() -> None:
    """Drop cached personas so an edited SOUL.md applies without a restart."""
    _cache.clear()


__all__ = ["load", "reset_cache"]
