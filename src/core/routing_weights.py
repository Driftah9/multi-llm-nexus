"""Routing weights — Phase 2 of the routing weight feedback loop.

Ported from live claude-brain (2026-10-19). Phase 1 (routing_telemetry) only WATCHED. This
module closes the loop: it derives one learned weight per provider from the evidence the
system already records, and feeds it back into the two places that pick a provider:

  - ProviderChain.select_provider()   — the failover chain a user turn walks
  - council_executor.run()             — the member pool the council seats roles from

Sources (both read-only here):
  1. routing_telemetry.jsonl — one row per chain attempt: success, output_ok
     (reasoning-leak proxy), duration_ms.
  2. council_sessions/*.json — per council run: which providers held a role seat and which
     failed to answer. Council calls do not all reach telemetry (on the reference install one
     provider logged 93 council failures vs 2 telemetry rows), so this is a complementary
     reliability source, not a duplicate.
  Domain strength is NOT re-derived: the council Judge and capability_grader already
  write per-(domain, provider) EWMA into capability_map. `domains` below is the set of
  domains a provider has EARNED qualification in there (flap-guarded), surfaced next to
  the weight so a reader sees both axes in one place.

Why a data file and not a providers.yaml field: providers.yaml is operator-authored source.
Weights are EARNED state, and earned state lives under $NEXUS_DATA_DIR (same as
capability_map.json). Accessors weight() / domains() give the per-provider read.

Routing effect is deliberately narrow — DEMOTE-ONLY and STABLE:
  - A provider is demoted only with >= MIN_SAMPLES of recent evidence AND either a
    weight under DEMOTE_FLOOR or a median success latency over SLOW_DEMOTE_MS (the
    chain's per-attempt cap). Thin evidence never moves anything.
  - Demoted providers slide to the END of their run. They are never dropped (health owns
    exclusion). A caller may pass `is_anchor` to pin some ids in place (an operator's fixed
    backstop); Nexus passes none by default — no provider is special in core.
  - Everything else keeps roster order. With no evidence, behavior is unchanged.
  - Kill switch: ROUTING_WEIGHTS_ENABLED=0 → order() is the identity.

Cost: the file is rebuilt lazily when older than REBUILD_TTL_S (reads ~1 JSONL + up
to 200 session files); between rebuilds a read is one cached json load keyed on
mtime. Every public entry point is fail-open — an error returns the input order.

    python3 -m src.core.routing_weights      # rebuild + print table (from the repo root)

Provider ids are read from whatever the ledgers contain — not filtered by a catalog (chain
names and orchestration ids are separate namespaces in Nexus; filtering by one would hide the
other). Tests must therefore never write to the real ledgers (tests/conftest.py isolates them).

# NEXUS:PORTABLE — no operator paths beyond the $NEXUS_DATA_DIR seam.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, TypeVar

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
WEIGHTS_PATH = Path(os.environ.get("ROUTING_WEIGHTS_PATH", str(DATA_DIR / "routing_weights.json")))
# None → read the telemetry / council-session modules' own configured paths at call time,
# so one env var (or one test monkeypatch) moves writer and reader together.
TELEMETRY_PATH: Optional[Path] = None
SESSIONS_DIR: Optional[Path] = None

# Only RECENT evidence counts: provider fixes land (a credential fix can turn a provider
# from 0% to working overnight) and old failures must age out.
WINDOW_DAYS = float(os.environ.get("ROUTING_WEIGHTS_WINDOW_DAYS", "21"))
PER_PROVIDER_CAP = 100        # newest N telemetry rows per provider
PRIOR_K = 4.0                 # Beta-prior pseudo-observations at 0.5 — damps tiny samples
MIN_SAMPLES = int(os.environ.get("ROUTING_WEIGHTS_MIN_SAMPLES", "8"))
DEMOTE_FLOOR = float(os.environ.get("ROUTING_WEIGHTS_DEMOTE_FLOOR", "0.35"))
LAT_GOOD_MS = 3000            # at/under this median, no latency penalty
LAT_BAD_MS = 20000            # at/over this, the latency factor bottoms out
# A provider whose median SUCCESSFUL latency exceeds a user turn's per-attempt patience
# mostly times out in the user chain even though it "succeeds" in the council's longer
# window — found live 2026-10-07: a 70B with p50 19s and high council reliability would
# otherwise have been promoted ahead of a fast-failing flash model, adding ~12s to turns.
SLOW_DEMOTE_MS = int(os.environ.get("ROUTING_WEIGHTS_SLOW_MS", "12000"))
SLOW_MIN_LAT_SAMPLES = 3
LAT_MIN_FACTOR = 0.5          # latency can halve a weight, never zero it alone
REBUILD_TTL_S = 1800

SCHEMA_VERSION = 1

T = TypeVar("T")


def enabled() -> bool:
    return os.environ.get("ROUTING_WEIGHTS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


# ── derivation ────────────────────────────────────────────────────────────────

def _known_providers() -> Optional[set]:
    """No catalog filter in Nexus (see module docstring)."""
    return None


def _telemetry_path() -> Path:
    if TELEMETRY_PATH is not None:
        return Path(TELEMETRY_PATH)
    from . import routing_telemetry
    return Path(routing_telemetry._LOG_PATH)


def _sessions_dir() -> Path:
    if SESSIONS_DIR is not None:
        return Path(SESSIONS_DIR)
    from ..orchestration import council_session
    return Path(council_session.SESSIONS_DIR)


def _read_telemetry(since: float, known: Optional[set]) -> Dict[str, List[dict]]:
    rows: Dict[str, List[dict]] = {}
    path = _telemetry_path()
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        for ln in f:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            p = r.get("provider")
            if not p or (r.get("ts") or 0) < since or (known is not None and p not in known):
                continue
            rows.setdefault(p, []).append(r)
    return {p: v[-PER_PROVIDER_CAP:] for p, v in rows.items()}


def _read_council(since: float, known: Optional[set]) -> Dict[str, Dict[str, int]]:
    """provider -> {"seated": n, "failed": n} over council sessions in the window.
    A seat means the provider answered its role (council_executor only seats a role
    after a response); failed_providers are members that errored/timed out."""
    out: Dict[str, Dict[str, int]] = {}
    sdir = _sessions_dir()
    if not sdir.is_dir():
        return out
    for fp in sdir.glob("council_session_*.json"):
        try:
            if fp.stat().st_mtime < since:
                continue
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in (d.get("roles") or {}).values():
            if known is None or p in known:
                out.setdefault(p, {"seated": 0, "failed": 0})["seated"] += 1
        for p in d.get("failed_providers") or []:
            if known is None or p in known:
                out.setdefault(p, {"seated": 0, "failed": 0})["failed"] += 1
    return out


def _earned_domains() -> Dict[str, List[str]]:
    """provider -> domains it has EARNED qualification in (capability_map, flap-guarded)."""
    try:
        from ..orchestration.capability_map import CapabilityMap
        cm = CapabilityMap()
    except Exception as e:
        logger.debug(f"routing_weights: capability_map unavailable: {e}")
        return {}
    out: Dict[str, List[str]] = {}
    for domain, row in cm.data.get("research", {}).items():
        for p in row:
            try:
                if cm.earned_qualified(domain, p):
                    out.setdefault(p, []).append(domain)
            except Exception:
                continue
    return {p: sorted(v) for p, v in out.items()}


def _latency_factor(median_ms: Optional[int]) -> float:
    if median_ms is None or median_ms <= LAT_GOOD_MS:
        return 1.0
    frac = min(1.0, (median_ms - LAT_GOOD_MS) / (LAT_BAD_MS - LAT_GOOD_MS))
    return round(1.0 - frac * (1.0 - LAT_MIN_FACTOR), 4)


def derive(now: Optional[float] = None) -> Dict[str, object]:
    """Compute the full weights document from the ledgers. Pure read; no write."""
    now = now or time.time()
    since = now - WINDOW_DAYS * 86400
    known = _known_providers()
    tel = _read_telemetry(since, known)
    council = _read_council(since, known)
    doms = _earned_domains()

    providers: Dict[str, dict] = {}
    for p in sorted(set(tel) | set(council) | set(doms)):
        rows = tel.get(p, [])
        t_n = len(rows)
        t_ok = sum(1 for r in rows if r.get("success"))
        t_clean = sum(1 for r in rows if r.get("output_ok") is True)
        lats = sorted(r["duration_ms"] for r in rows if r.get("success") and r.get("duration_ms"))
        med = lats[len(lats) // 2] if lats else None
        c = council.get(p, {"seated": 0, "failed": 0})

        attempts = t_n + c["seated"] + c["failed"]
        successes = t_ok + c["seated"]
        reliability = (successes + PRIOR_K * 0.5) / (attempts + PRIOR_K)
        # Reasoning-leak proxy: share of successful telemetry answers that were clean.
        # +1/+1 prior so one leak on a single sample doesn't crater a provider.
        quality = (t_clean + 1.0) / (t_ok + 1.0)
        lat_f = _latency_factor(med)
        weight = round(reliability * quality * lat_f, 4)
        enough = attempts >= MIN_SAMPLES
        reason = None
        if enough and weight < DEMOTE_FLOOR:
            reason = "low_weight"
        elif enough and med is not None and len(lats) >= SLOW_MIN_LAT_SAMPLES and med > SLOW_DEMOTE_MS:
            reason = "slow"

        providers[p] = {
            "weight": weight,
            "samples": attempts,
            "demoted": reason is not None,
            "demote_reason": reason,
            "reliability": round(reliability, 4),
            "quality": round(quality, 4),
            "latency_factor": lat_f,
            "median_ms": med,
            "telemetry": {"calls": t_n, "ok": t_ok, "clean": t_clean},
            "council": dict(c),
            "domains": doms.get(p, []),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": round(now, 3),
        "window_days": WINDOW_DAYS,
        "min_samples": MIN_SAMPLES,
        "demote_floor": DEMOTE_FLOOR,
        "slow_demote_ms": SLOW_DEMOTE_MS,
        "providers": providers,
    }


def rebuild(now: Optional[float] = None) -> Dict[str, object]:
    """Derive and atomically write the weights file. Returns the document."""
    doc = derive(now)
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = WEIGHTS_PATH.with_suffix(WEIGHTS_PATH.suffix + f".tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        os.replace(tmp, WEIGHTS_PATH)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    _cache.update(mtime=None, doc=None)
    return doc


# ── read side ─────────────────────────────────────────────────────────────────

_cache: Dict[str, object] = {"mtime": None, "doc": None}


def load(max_age_s: float = REBUILD_TTL_S) -> Dict[str, object]:
    """Current weights document — rebuilt if missing/stale, else served from an
    mtime-keyed cache. Never raises; returns {} on any failure."""
    try:
        stale = (not WEIGHTS_PATH.exists()
                 or time.time() - WEIGHTS_PATH.stat().st_mtime > max_age_s)
        if stale:
            return rebuild()
        mtime = WEIGHTS_PATH.stat().st_mtime
        if _cache["mtime"] != mtime or _cache["doc"] is None:
            _cache["doc"] = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
            _cache["mtime"] = mtime
        return _cache["doc"]  # type: ignore[return-value]
    except Exception as e:
        logger.debug(f"routing_weights.load failed: {e}")
        return {}


def weight(provider: str) -> Optional[float]:
    """Learned weight in [0,1] for a provider, or None if there is no evidence."""
    rec = load().get("providers", {}).get(provider)
    return rec.get("weight") if rec else None


def domains(provider: str) -> List[str]:
    """Domains the provider has earned qualification in (from capability_map)."""
    rec = load().get("providers", {}).get(provider)
    return list(rec.get("domains", [])) if rec else []


def is_demoted(provider: str) -> bool:
    rec = load().get("providers", {}).get(provider)
    return bool(rec and rec.get("demoted"))


def demoted_set() -> set:
    """Ids currently demoted — empty when disabled or on any error (fail-open)."""
    if not enabled():
        return set()
    try:
        return {p for p, r in load().get("providers", {}).items() if r.get("demoted")}
    except Exception:
        return set()


def order(items: Iterable[T], key: Callable[[T], str] = lambda x: x,  # type: ignore[assignment]
          is_anchor: Callable[[str], bool] = lambda pid: False) -> List[T]:
    """Stable demote-only reorder. Items whose provider is an anchor (caller-defined) keep
    their exact index; within each run of non-anchor items, demoted providers move to
    the end of that run, preserving relative order on both sides. Identity when
    disabled, when nothing is demoted, or on any error."""
    items = list(items)
    if not enabled() or len(items) < 2:
        return items
    try:
        provs = load().get("providers", {})
        if not any(r.get("demoted") for r in provs.values()):
            return items
        out: List[T] = []
        run: List[T] = []

        def flush():
            keep = [i for i in run if not provs.get(key(i), {}).get("demoted")]
            drop = [i for i in run if provs.get(key(i), {}).get("demoted")]
            out.extend(keep + drop)
            run.clear()

        for it in items:
            if is_anchor(key(it)):
                flush()
                out.append(it)
            else:
                run.append(it)
        flush()
        if [key(i) for i in out] != [key(i) for i in items]:
            logger.info(f"routing_weights: demoted order {[key(i) for i in items]} -> {[key(i) for i in out]}")
        return out
    except Exception as e:
        logger.debug(f"routing_weights.order fell back to input order: {e}")
        return items


if __name__ == "__main__":  # pragma: no cover — operator inspection
    doc = rebuild()
    print(f"routing weights — window {doc['window_days']}d, demote <{doc['demote_floor']} "
          f"with >={doc['min_samples']} samples → {WEIGHTS_PATH}")
    print(f"{'provider':26} {'weight':>6} {'n':>4} {'rel':>5} {'qual':>5} {'lat':>5} {'p50ms':>7}  demoted  domains")
    for p, r in sorted(doc["providers"].items(), key=lambda kv: -kv[1]["weight"]):
        print(f"{p:26} {r['weight']:6.3f} {r['samples']:4} {r['reliability']:5.2f} {r['quality']:5.2f} "
              f"{r['latency_factor']:5.2f} {str(r['median_ms']):>7}  {(r['demote_reason'] or '').ljust(10)} "
              f"{','.join(r['domains'])}")
