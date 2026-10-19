"""Outage reason — say WHY a provider call failed, in words the operator can act on.

Ported from live claude-brain (2026-10-19), labels made provider-neutral.

error_classifier answers "what should routing do" (retry / advance / bench) with a few
coarse action classes. That is the wrong granularity for telling a human what happened:
on the reference install most "unknown" outages were not the provider at all — a stale
session id, a bad CLI flag, or the bridge's own stream reader choking on a long line.
This module is the human-facing companion: same input (raw failure text + its action
class), finer and honest output.

Pure function, no I/O. It never changes routing — callers keep using error_classifier.

The `local` flag is the useful bit for the operator: True means the provider was
reachable and the fault is on this side (fix the code/config), False means the provider
itself had a problem (wait it out), None means the text can't tell.

# NEXUS:PORTABLE — the reason table is provider-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    code: str
    label: str
    local: bool | None   # True → our side (bridge/CLI/config); False → provider side; None → can't tell


# Ordered most-specific → least; first match wins. Needles are lowercase substrings.
_TABLE: list[tuple[Reason, tuple[str, ...]]] = [
    (Reason("session_limit", "provider session/usage window used up", False),
     ("hit your session limit", "session limit", "usage limit")),
    (Reason("stream_overflow", "bridge choked on an oversized CLI output line", True),
     ("chunk is longer than limit", "chunk exceed the limit", "separator is not found")),
    (Reason("stale_session", "stored CLI session id no longer exists", True),
     ("no conversation found with session id",)),
    (Reason("cli_config", "provider CLI rejected its launch arguments", True),
     ("is invalid. it must be one of", "unknown option", "argument list too long")),
    (Reason("cli_missing", "provider CLI could not be launched", True),
     ("could not determine executable", "npx not found", "failed to start", "command not found")),
    (Reason("overloaded", "provider API overloaded (529)", False),
     ("529", "overloaded")),
    (Reason("rate_limited", "rate-limited by the provider (429)", False),
     ("429", "too many requests", "rate limit", "rate-limit")),
    (Reason("server_error", "provider server error (5xx)", False),
     ("500", "502", "503", "504", "service unavailable", "internal server error",
      "internal error", "bad gateway")),
    (Reason("stalled", "provider CLI went silent and was killed (idle timeout)", None),
     ("stalled", "no output for")),
    (Reason("timeout", "request timed out", False),
     ("timed out", "timeout", "deadline")),
    (Reason("network", "network/connection failure", False),
     ("connection reset", "econnreset", "connection refused", "connection error",
      "name resolution", "getaddrinfo", "network is unreachable", "ssl")),
    (Reason("auth", "authentication rejected (key/login)", False),
     ("401", "403", "unauthorized", "forbidden", "invalid api key", "authentication")),
    (Reason("cli_crash", "provider CLI exited with an error", True),
     ("cli failed (exit",)),
]

_BY_CLASS = {
    "quota": Reason("quota", "provider quota/budget exhausted", False),
    "context_overflow": Reason("context_overflow", "conversation outgrew the context window", True),
    "model_gone": Reason("model_gone", "configured model no longer exists", True),
    "bad_request": Reason("bad_request", "request rejected as malformed", True),
}

_UNKNOWN = Reason("unknown", "unclassified failure", None)

# Python exception types raised by OUR code, not by a provider response.
_LOCAL_EXC = ("valueerror", "keyerror", "typeerror", "attributeerror", "indexerror",
              "jsondecodeerror", "unicodedecodeerror", "assertionerror")


def describe(detail: str | None, klass: str | None = None) -> Reason:
    """Best human-facing reason for a failure.

    `detail` is the raw error text (ideally prefixed with the exception type, e.g.
    "ValueError: ..."); `klass` is the error_classifier action class, used when the
    text alone is not specific.
    """
    t = (detail or "").lower()
    for reason, needles in _TABLE:
        if any(n in t for n in needles):
            return reason
    if klass in _BY_CLASS:
        return _BY_CLASS[klass]
    head = t.split(":", 1)[0].strip()
    if head in _LOCAL_EXC:
        return Reason("bridge_bug", f"engine-side exception ({head})", True)
    return _UNKNOWN


def summary(reason: Reason) -> str:
    """One-line operator phrasing, including whose side the fault is on."""
    if reason.local is None:
        return reason.label
    side = "our side — the provider was reachable" if reason.local else "provider side"
    return f"{reason.label} ({side})"
