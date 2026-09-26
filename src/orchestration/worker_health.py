"""Worker reachability gate — keep dead providers out of the swarm's retry budget.

WHY THIS EXISTS
---------------
capability_map ranks workers by how GOOD they are. Nothing ranked them by
whether they ANSWER. Those are different axes and conflating them corrupts both:
a provider that 404s is not bad at reasoning, it is absent, and feeding transport
failures into the quality EWMA would demote a model for its vendor's outage.

So the quality score only ever moved on a SUCCESS (swarm_loop grades a step only
when `status == "done"`). A provider that failed 100% of the time never had its
score touched — it kept its earned rank forever and kept winning `choose()`.
With MAX_STEP_RETRIES=2, two dead top-ranked providers consume the entire retry
budget and the step fails while a healthy worker sits unused further down the list.

That is not theoretical. Measured on the live install 2026-09-04: the worker pool was
10 providers, 1 of them reachable (gemini-flash); cerebras-qwen3 and groq-70b had
89 and 75 consecutive failures with zero successes over two weeks and were still
being picked first. Swarm step completion decayed 100% (July) → 0/11 steps (Sep 4)
without a single user-visible error, because the orchestrator silently falls back
to the classic delegate on an empty result.

WHAT IT DOES
------------
Records transport outcomes per LOGICAL WORKER ID and benches ones that are
demonstrably not answering, with a cooldown sized to the failure class — so a
retired model is not re-probed every turn, while a blip recovers in minutes.

Keyed by worker id (e.g. "openai::gpt-4o"), NOT by vendor ("openai"), deliberately:
a retired MODEL must not bench its vendor's other models, and this registry must
never perturb the tier routing. Writes stay local to the worker pool.

ALWAYS FAIL-OPEN: if benching would empty the candidate list, the unfiltered list
is returned. A health gate must never be the reason a task has no workers — the
same rule the capability gate uses.

# NEXUS:PORTABLE — mechanism and the class→cooldown table are general.
# NEXUS:OPERATOR — the on-disk path (data/worker_health.json).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..core.error_classifier import (
    AUTH, MODEL_GONE, QUOTA, TRANSIENT, UNKNOWN, classify_error,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_PATH = os.environ.get("WORKER_HEALTH_PATH", str(_DATA_DIR / "worker_health.json"))

#: Local class for "this worker has no key/endpoint configured at all". Distinct
#: from AUTH (a key that exists and is rejected) because it never self-heals —
#: only the operator adding credentials clears it.
NOT_CONFIGURED = "not_configured"

#: How long a bench lasts, per failure class. Sized to how the failure recovers,
#: not to how bad it looks: a decommissioned model recovers never, a 429 recovers
#: on its own clock, a 502 recovers in seconds.
_COOLDOWN_SECONDS: Dict[str, int] = {
    MODEL_GONE:      24 * 3600,   # retired/renamed — only a catalog edit fixes it
    NOT_CONFIGURED:  24 * 3600,   # needs the operator, not time
    AUTH:             6 * 3600,   # expired/revoked key — needs the operator
    QUOTA:                3600,   # free-tier window; recovers on its own clock
    TRANSIENT:             300,   # server blip
    UNKNOWN:               900,   # conservative middle ground
}
_DEFAULT_COOLDOWN = 900

#: Classes where retrying is provably pointless, so one observation is enough.
#: Everything else needs two consecutive failures before benching, so a single
#: blip never costs a provider its place in the rotation.
_BENCH_ON_FIRST = {MODEL_GONE, NOT_CONFIGURED, AUTH}
_CONSECUTIVE_TO_BENCH = 2

#: Consecutive failures after which the cooldown starts doubling (capped). Stops
#: a permanently-dead provider from being re-probed on a fixed short cycle.
_BACKOFF_AFTER = 3
_MAX_COOLDOWN = 24 * 3600

# HTTP status codes the shared classifier does not map (it matches on error TEXT,
# and httpx renders these as "Client error '404 Not Found' for url ..." which hits
# none of its needles — verified on live 2026-09-04: 404/410 both classify UNKNOWN).
# Refined HERE rather than in core/error_classifier so the primary seat's failover
# behavior is untouched by a swarm fix.
_STATUS_RE = re.compile(r"\b(4\d\d|5\d\d)\b")
_STATUS_CLASS: Dict[str, str] = {
    "404": MODEL_GONE,   # endpoint/model retired (Groq + Cerebras, both 2026-08)
    "410": MODEL_GONE,   # explicitly Gone (NVIDIA NIM)
    "402": QUOTA,        # payment required
}

# Text fingerprints for failures that never reach HTTP at all — a provider truly
# has no key/manifest configured. Does NOT include "all credentials exhausted":
# that phrase is a WRAPPER providers.py emits whenever every credential attempt
# failed, for ANY reason (429, 503, a bare timeout) — not just missing credentials.
# See _ALL_EXHAUSTED_RE below, which unwraps it and classifies the real cause.
_NO_CREDS = ("no credentials available",
             "name or service not known", "nodename nor servname")

# Matches providers.py's rotation-exhausted wrapper: "{provider}: all credentials
# exhausted (last error: {last_error})". Group 1 is the REAL underlying error.
_ALL_EXHAUSTED_RE = re.compile(r"all credentials exhausted \(last error:\s*(.*)\)\s*$", re.DOTALL)


def _classify(err: object) -> str:
    """Map a worker failure to a bench class, refining the shared classifier."""
    text = str(err or "")

    # Unwrap "all credentials exhausted (last error: ...)" and classify the real
    # cause underneath instead of the wrapper text. Fixes: a single-key provider
    # hitting one rate-limited call was landing here as NOT_CONFIGURED (24h
    # "unavailable") even though the key is present and valid.
    m = _ALL_EXHAUSTED_RE.search(text)
    if m:
        inner = m.group(1).strip()
        if not inner:
            # Every credential failed with an error that stringified to nothing
            # (e.g. a bare timeout). No evidence of "not configured" — treat as
            # an ordinary unclassified failure, not a 24h credential-missing bench.
            return UNKNOWN
        return _classify(inner)

    low = text.lower()
    if any(n in low for n in _NO_CREDS):
        return NOT_CONFIGURED
    klass = classify_error(text)
    if klass == UNKNOWN:
        m = _STATUS_RE.search(text)
        if m:
            return _STATUS_CLASS.get(m.group(1), TRANSIENT if m.group(1)[0] == "5" else klass)
    return klass


def _load() -> dict:
    """Load the worker health registry from disk."""
    try:
        if os.path.exists(_PATH):
            with open(_PATH) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"worker_health: load failed ({e}); starting empty")
    return {}


def _save(data: dict) -> None:
    """Write the worker health registry to disk atomically."""
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        tmp = _PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, _PATH)
    except Exception as e:
        logger.error(f"worker_health: save failed: {e}")


def _cooldown_for(klass: str, consecutive: int) -> int:
    """Cooldown duration for a failure class and consecutive count, with backoff."""
    base = _COOLDOWN_SECONDS.get(klass, _DEFAULT_COOLDOWN)
    if consecutive > _BACKOFF_AFTER:
        base *= 2 ** min(consecutive - _BACKOFF_AFTER, 6)
    return min(base, _MAX_COOLDOWN)


def record_failure(worker: str, err: object) -> str:
    """Record a transport failure for `worker`. Returns the bench class."""
    klass = _classify(err)
    data = _load()
    row = data.get(worker) or {}
    consecutive = int(row.get("consecutive", 0)) + 1
    row.update({
        "worker": worker,
        "klass": klass,
        "detail": str(err or "")[:300],
        "consecutive": consecutive,
        "last_failure": time.time(),
    })
    if klass in _BENCH_ON_FIRST or consecutive >= _CONSECUTIVE_TO_BENCH:
        cooldown = _cooldown_for(klass, consecutive)
        row["benched_until"] = time.time() + cooldown
        logger.info(f"worker_health: {worker} benched {cooldown}s ({klass}, "
                    f"{consecutive} consecutive)")
    data[worker] = row
    _save(data)
    return klass


def record_success(worker: str) -> None:
    """A worker answered — clear any bench. Traffic-driven recovery."""
    data = _load()
    if worker in data:
        del data[worker]
        _save(data)
        logger.info(f"worker_health: {worker} answered — bench cleared")


def is_benched(worker: str, now: Optional[float] = None) -> bool:
    """True if this worker should be skipped right now."""
    now = now if now is not None else time.time()
    row = _load().get(worker)
    if row and float(row.get("benched_until", 0)) > now:
        return True
    return False


def filter_candidates(candidates: List[str]) -> List[str]:
    """Drop currently-benched workers. FAIL-OPEN: never returns [] for a
    non-empty input — a health gate must not be why a task has no workers."""
    if not candidates:
        return candidates
    now = time.time()
    live = [c for c in candidates if not is_benched(c, now)]
    if not live:
        logger.info(f"worker_health: all {len(candidates)} candidate(s) benched — "
                    "failing open, trying them anyway")
        return list(candidates)
    if len(live) < len(candidates):
        logger.debug(f"worker_health: skipped {len(candidates) - len(live)} benched "
                     f"worker(s), {len(live)} live")
    return live


def benched() -> List[dict]:
    """All currently-benched worker records — for diagnostics and monitoring."""
    now = time.time()
    return [r for r in _load().values() if float(r.get("benched_until", 0)) > now]


__all__ = [
    "record_failure", "record_success", "is_benched", "filter_candidates",
    "benched", "NOT_CONFIGURED",
]
