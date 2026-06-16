"""Mention gate — decide whether the bot should attend to a message.

Multi-user scaling primitive. In a shared channel with many users, the bot
must not respond to every post — only when explicitly addressed by name as
the FIRST token (e.g. "chamberlain what's the status").

This is a GATE, not judgment: deterministic first-token check, no LLM, no
token cost. It runs in front of triage/routing only when enabled.

DORMANT BY DEFAULT. Single-user deployments don't need this — it would only
add friction. Flip MENTION_GATE_ENABLED=true in config when a second user
joins and the bot should only respond when addressed.
"""

import re
from typing import Iterable

# Default bot name — override per-adapter via should_attend(bot_names=...).
# Set this to NEXUS_AGENT_NAME at adapter startup.
DEFAULT_BOT_NAME = "nexus"

# Leading punctuation/whitespace stripped before reading the first token,
# so "@chamberlain,", "chamberlain:", "  Chamberlain ..." all match.
_LEADING = r"[\s@>*_~`\"'(\[]*"
_SEP = r"[\s,:;!.\-—]"


def _normalize(s: str) -> str:
    return s.strip().lower()


def is_addressed(message: str, bot_names: Iterable[str] = (DEFAULT_BOT_NAME,)) -> bool:
    """True if `message` opens by addressing one of `bot_names` as the first token.

    Matches: "nexus ...", "Nexus, ...", "@nexus: ...", "  nexus!".
    Does NOT match: "hey nexus ...", "ask nexus ...", "...nexus" (name not first),
    or a bare name with nothing after it (no actual request).
    """
    if not message:
        return False
    text = _normalize(message)
    for name in bot_names:
        name = _normalize(name)
        if not name:
            continue
        pattern = rf"^{_LEADING}{re.escape(name)}{_SEP}+\S"
        if re.match(pattern, text):
            return True
        pattern_q = rf"^{_LEADING}{re.escape(name)}\s*[?]"
        if re.match(pattern_q, text):
            return True
    return False


def should_attend(
    message: str,
    *,
    enabled: bool = False,
    bot_names: Iterable[str] = (DEFAULT_BOT_NAME,),
    is_dm: bool = False,
    is_thread_reply: bool = False,
) -> bool:
    """Top-level decision: should the bot process this message?

    Args:
        message: raw user message text
        enabled: master switch. When False (DEFAULT, single-user mode), always
                 returns True — current behavior unchanged.
        bot_names: accepted names/aliases for this adapter instance.
        is_dm: direct message — always attend (no channel noise).
        is_thread_reply: reply in a thread the bot is already in — attend
                 (user shouldn't re-say the name every turn).

    Returns:
        True  -> process the message (continue to triage/routing)
        False -> silent drop (zero cost)
    """
    if not enabled:
        return True
    if is_dm or is_thread_reply:
        return True
    return is_addressed(message, bot_names)
