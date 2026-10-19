"""Capability baseline — curated priors of what KNOWN providers are good at.

Ported from live claude-brain (2026-10-19). DATA, not code: a lookup table the qualification
gate reads when deciding whether a provider is trusted for a demanding role. Providers the
operator's table vouches for arrive PRE-QUALIFIED in those domains; everything else must EARN
qualification through graded evidence (cold-start-low).

The baseline is a t=0 PRIOR, never a permanent grant. `CapabilityMap.is_qualified` reads it as
`earned OR seeded`, and real grades EWMA-move a provider's earned score — so a seeded-good
provider that underperforms on THIS operator's tasks decays out of the earned path, and an
unseeded one that overperforms climbs in.

The file is OPERATOR data, keyed by the provider ids in the operator's providers.yaml:
`config/capability_baseline.json` (override: CAP_BASELINE_PATH). Two accepted shapes, mixable:

    {"domains": {"coding": {"my-frontier": 0.85, "my-local-7b": 0.45}}}
    {"seeds":   {"coding": {"my-frontier": {"score": 0.85, "confidence": 0.7, "source": "…"}}}}

`seeds` entries win where both define a (domain, provider). Missing/corrupt file → {} → pure
earned behaviour (nobody pre-qualified), fully backward-compatible.
See `config/capability_baseline.json.example`.

# NEXUS:PORTABLE — the mechanism. No provider id appears in logic; code only READS the table.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "capability_baseline.json"
_PATH = Path(os.environ.get("CAP_BASELINE_PATH", _DEFAULT_PATH))

_cache: Optional[dict] = None


def _read_json(path: Path) -> dict:
    """One file → dict. Missing → {}; corrupt → {} with a warning (never raises)."""
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception as e:
        logger.warning(f"capability_baseline: load of {path.name} failed ({e}) — ignoring it")
        return {}


def _flatten(rows: dict) -> dict:
    """{domain: {provider: score | {score, …}}} → {domain: {provider: float}}.
    Entries without a numeric score are skipped."""
    out: dict = {}
    for domain, row in (rows or {}).items():
        if not isinstance(row, dict):
            continue
        for provider, entry in row.items():
            s = entry.get("score") if isinstance(entry, dict) else entry
            if isinstance(s, (int, float)) and not isinstance(s, bool):
                out.setdefault(domain, {})[provider] = float(s)
    return out


def _load() -> dict:
    """Load + cache the merged prior table (`domains`, then `seeds` layered over it)."""
    global _cache
    if _cache is None:
        table = _read_json(_PATH)
        domains = _flatten(table.get("domains"))
        for d, row in _flatten(table.get("seeds")).items():
            domains.setdefault(d, {}).update(row)
        table["domains"] = domains
        _cache = table
    return _cache


def reload() -> None:
    """Drop the cache (after the operator edits the file)."""
    global _cache
    _cache = None


def baseline_score(domain: str, provider: str) -> Optional[float]:
    """Curated prior score for (domain, provider) in [0,1], or None if not seeded."""
    v = _load().get("domains", {}).get(domain, {}).get(provider)
    return float(v) if isinstance(v, (int, float)) else None


def is_seeded_qualified(domain: str, provider: str, bar: float) -> bool:
    """True if the baseline VOUCHES for this provider in this domain at/above the bar. A
    seeded entry below the bar (known-but-weak) returns False: it starts low and must earn."""
    s = baseline_score(domain, provider)
    return s is not None and s >= bar


def seeded_providers(domain: str, bar: float) -> list:
    """Providers the baseline pre-qualifies for a domain (score >= bar), best-first."""
    doms = _load().get("domains", {}).get(domain, {})
    ok = [(p, s) for p, s in doms.items() if isinstance(s, (int, float)) and s >= bar]
    ok.sort(key=lambda t: t[1], reverse=True)
    return [p for p, _ in ok]


def meta() -> dict:
    """Registry provenance (schema_version, last_reviewed, provenance) for display/audit."""
    d = _load()
    return {k: d.get(k) for k in ("schema_version", "last_reviewed", "provenance")}
