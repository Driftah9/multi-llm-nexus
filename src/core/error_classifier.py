"""Provider error classifier — turn a raw provider failure into an action class.

A provider call can fail many ways. The failover/recovery logic needs to know
*which* kind of failure happened, because the right response differs:

  TRANSIENT   — server-side blip (529 overload, 503, timeout, connection reset).
                Retry the same provider once, then advance the chain. Recoverable
                soon → eligible for the recovery probe.
  QUOTA       — out of budget (session limit, free-tier token cap, billing).
                Do NOT retry the same provider; advance the chain. Recovers only
                at a reset window, not by probing — re-enable on reset, not ping.
  AUTH        — bad/expired key (401/403). Advancing helps if another provider has
                a valid key; do not retry the same one. Not probe-recoverable.
  BAD_REQUEST — our payload is wrong (400, content filter). Same error everywhere
                → do NOT burn the chain; surface it.
  UNKNOWN     — unrecognized. Treated conservatively as retry-once-then-advance.

Pure function over the error text — no I/O, no network. The actual up/down detection
is a probe elsewhere; this only reads what a failure *says*.

Ported from claude-brain live (stamped ship-as-is). The classification enum and the
rule table are provider-agnostic by design — any operator's provider set produces these
same five classes. The substring table may grow per provider; the contract (text → class)
is stable.
"""
from __future__ import annotations

import re

# Action classes
TRANSIENT = "transient"
QUOTA = "quota"
AUTH = "auth"
BAD_REQUEST = "bad_request"
UNKNOWN = "unknown"

# Ordered most-specific → least. First matching group wins, so QUOTA (which can
# share tokens like "limit"/"429" with rate-limiting) is tested before TRANSIENT.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    # Out of budget — recovers only at a reset window, never by retrying now.
    (QUOTA, (
        "session limit", "usage limit", "hit your limit", "resets ",
        "quota", "insufficient_quota", "insufficient balance", "exceeded your",
        "out of credit", "billing", "payment", "spending limit", "daily limit",
        "token limit", "free tier", "credit balance",
    )),
    # Bad credentials / permission — advancing to a provider with a valid key helps.
    (AUTH, (
        "401", "403", "unauthorized", "forbidden", "invalid api key",
        "invalid_api_key", "authentication", "auth error", "permission denied",
        "expired key", "no such key",
    )),
    # Our request is malformed / filtered — same failure on every provider.
    (BAD_REQUEST, (
        "400", "bad request", "invalid request", "invalid_request",
        "content filter", "content_filter", "safety", "moderation",
        "prompt too long", "context length", "max tokens", "too many tokens",
    )),
    # Server-side blip — recoverable soon; retry then probe.
    (TRANSIENT, (
        "529", "overloaded", "overload", "503", "502", "504", "500",
        "all backends failed", "bad gateway", "service unavailable",
        "temporarily unavailable", "try again", "timeout", "timed out",
        "deadline", "connection reset", "econnreset", "connection refused",
        "connection error", "connect", "no route to host", "network", "gateway",
        "rate limit", "rate-limit", "ratelimit", "429", "too many requests",
        "server error", "internal error",
    )),
]


def classify_error(text: str | None) -> str:
    """Map a raw provider error string to one of the action classes above.

    Case-insensitive substring match against the rule table, most-specific first.
    Returns UNKNOWN when nothing matches.
    """
    if not text:
        return UNKNOWN
    t = text.lower()
    for klass, needles in _RULES:
        for needle in needles:
            if needle in t:
                return klass
    return UNKNOWN


# Reset-time hint: many quota errors state when they recover ("resets 7:10am UTC").
_RESET_HINT = re.compile(
    r"resets?\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*([ap]m)?\s*\(?utc\)?", re.IGNORECASE
)


def reset_hint(text: str | None) -> tuple[int, int] | None:
    """Best-effort (hour, minute) UTC parsed from a quota error, or None."""
    if not text:
        return None
    m = _RESET_HINT.search(text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def is_retryable_same(klass: str) -> bool:
    """True if retrying the SAME provider could plausibly succeed."""
    return klass in (TRANSIENT, UNKNOWN)


def should_advance_chain(klass: str) -> bool:
    """True if a DIFFERENT provider could plausibly succeed where this one failed.

    BAD_REQUEST is excluded: a malformed/filtered payload fails everywhere, so
    burning the chain wastes providers and produces N identical errors.
    """
    return klass != BAD_REQUEST


def is_probe_recoverable(klass: str) -> bool:
    """True if a cheap live probe is the right way to detect recovery.

    QUOTA recovers at a reset window (re-enable on time, don't probe). AUTH needs
    a human (new key). Only TRANSIENT/UNKNOWN should be polled back to life.
    """
    return klass in (TRANSIENT, UNKNOWN)
