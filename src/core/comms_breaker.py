"""Per-protocol circuit breaker for outbound operator notifications.

Ported from live claude-brain (2026-10-19). State is persisted to disk because the
Notifier is mostly used from short-lived cron/watcher processes: an in-memory failure
counter would reset every run and never trip. Timestamps are wall-clock UTC ISO strings
(monotonic clocks restart on reboot — a persisted monotonic value looks future-dated
forever after one, which is exactly how the reference install once silently throttled a cap).

closed --(THRESHOLD consecutive failures)--> open --(COOLDOWN elapsed)--> half-open
half-open: next send is a trial; success closes, failure re-opens.

State: $COMMS_BREAKER_STATE or $NEXUS_DATA_DIR/comms/breaker.json (flock-guarded).

# NEXUS:PORTABLE — no protocol names in logic; zero LLM calls.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("nexus.comms_breaker")

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
STATE = Path(os.environ.get("COMMS_BREAKER_STATE") or (_DATA_DIR / "comms" / "breaker.json"))
THRESHOLD = 3
COOLDOWN = timedelta(minutes=5)


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def _state():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            data = json.loads(STATE.read_text())
        except (OSError, ValueError):
            data = {}
        yield data
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(STATE)


def allow(proto: str) -> bool:
    """False while open; True when closed or half-open (cooldown elapsed)."""
    with _state() as d:
        s = d.get(proto) or {}
        if not s.get("opened_at"):
            return True
        return _now() - datetime.fromisoformat(s["opened_at"]) >= COOLDOWN


def is_open(proto: str) -> bool:
    with _state() as d:
        return bool((d.get(proto) or {}).get("opened_at"))


def record_failure(proto: str, error: str = "") -> None:
    with _state() as d:
        s = d.setdefault(proto, {"failures": 0, "rerouted": 0})
        s["failures"] = s.get("failures", 0) + 1
        s["last_error"] = error[:300]
        s["last_failure_at"] = _now().isoformat()
        if s.get("opened_at"):
            s["opened_at"] = _now().isoformat()
            logger.warning(f"{proto} half-open trial failed — re-opened ({error[:120]})")
        elif s["failures"] >= THRESHOLD:
            s["opened_at"] = _now().isoformat()
            s.setdefault("down_since", s["opened_at"])
            logger.warning(f"{proto} OPEN after {s['failures']} consecutive failures ({error[:120]})")


def record_success(proto: str) -> dict | None:
    """Close the breaker. Returns the outage record if it had been open, else None."""
    with _state() as d:
        s = d.get(proto) or {}
        outage = None
        if s.get("opened_at"):
            outage = {"down_since": s.get("down_since") or s["opened_at"],
                      "rerouted": s.get("rerouted", 0)}
            logger.info(f"{proto} CLOSED — recovered (down since {outage['down_since']}, "
                        f"{outage['rerouted']} rerouted)")
        d[proto] = {"failures": 0, "rerouted": 0}
        return outage


def record_reroute(primary: str, via: str) -> None:
    with _state() as d:
        s = d.setdefault(primary, {"failures": 0, "rerouted": 0})
        s["rerouted"] = s.get("rerouted", 0) + 1
    logger.info(f"rerouted {primary} -> {via}")


def outage(proto: str) -> dict | None:
    with _state() as d:
        s = d.get(proto) or {}
        if not s.get("opened_at"):
            return None
        return {"down_since": s.get("down_since") or s["opened_at"], "rerouted": s.get("rerouted", 0)}
