"""Worker-pool shim — the Nexus analog of live's TIER_ROSTER + _worker_candidates.

Live claude-brain derives a code-driven TIER_ROSTER from a PROVIDER_CATALOG. Nexus is
config-driven: each provider in `config/providers.yaml` carries `tier:` (nano|standard|
deep) and `priority:`. This shim presents that config through the interface the swarm
delegator + council roles expect, so those modules port DOWN without a rewrite:

  - tier_roster()            -> {tier: [provider_id ordered by priority]}
  - worker_candidates(...)   -> cheapest-first eligible worker ids
  - is_nano_tier(provider)   -> is this provider a nano-only lane?
  - communicator_providers() -> reserved "voice" providers (never workers)

The live system reserves claude-* as the communicator/chairman (D-009: workers
contribute, never speak as the voice). Nexus has no Claude in the OpenAI-compatible
registry, so the reservation is GENERALIZED per live's own NEXUS:OPERATOR guidance in
`core/provider_resolver.py`: the operator names the communicator/synthesis providers
(default = the deep tier — strongest, define-the-standard) via
NEXUS_COMMUNICATOR_PROVIDERS, and those are held out of the worker pool.

NEXUS:PORTABLE — the mechanism: derive an ordered per-tier roster from the operator's
  populated provider registry, reserve the voice, workers are the cheap remainder.
NEXUS:OPERATOR — the default communicator set + tier ordering come from THIS
  deployment's config/providers.yaml. ref: D-009, live provider_resolver stamps.
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional

from .providers import _load_providers_yaml

# Cheapest-first tiers workers are drawn from. Deep is reserved for the
# communicator/synthesis voice — matches live's WORKER_TIERS = ["nano", "standard"].
WORKER_TIERS = ["nano", "standard"]
DEFAULT_WORKER_COUNT = 2


def _enabled_providers() -> Dict[str, dict]:
    """Enabled, council-eligible (OpenAI-compatible + has a model) providers from config.

    Same eligibility test the orchestration ProviderClient registry uses, so the
    roster and the client can never disagree about who exists."""
    cfg = _load_providers_yaml()
    out: Dict[str, dict] = {}
    for pid, spec in (cfg.get("providers") or {}).items():
        if not isinstance(spec, dict):
            continue
        if not spec.get("enabled", True):
            continue
        if not spec.get("base_url") or not spec.get("model"):
            continue
        out[pid] = spec
    return out


def communicator_providers(enabled: Optional[Dict[str, dict]] = None) -> set:
    """Providers reserved as the voice (communicator/chairman) — never workers.

    Default: the deep tier. Override with NEXUS_COMMUNICATOR_PROVIDERS (comma-sep)."""
    env = os.environ.get("NEXUS_COMMUNICATOR_PROVIDERS")
    if env:
        return {p.strip() for p in env.split(",") if p.strip()}
    enabled = enabled if enabled is not None else _enabled_providers()
    return {pid for pid, spec in enabled.items() if spec.get("tier") == "deep"}


def tier_roster() -> Dict[str, List[str]]:
    """{tier: [provider_id, ...]} ordered by ascending priority (first-choice first),
    derived from config/providers.yaml. Only enabled, council-eligible providers."""
    enabled = _enabled_providers()
    roster: Dict[str, List[str]] = {}
    for pid, spec in enabled.items():
        tier = spec.get("tier") or "standard"
        roster.setdefault(tier, []).append(pid)
    for ids in roster.values():
        ids.sort(key=lambda p: (enabled[p].get("priority") or 999, p))
    return roster


def is_nano_tier(provider: str) -> bool:
    """True if the provider is a nano lane (lightweight, not trusted with demanding
    roles by default) — the Nexus analog of live's _is_nano_tier."""
    spec = _enabled_providers().get(provider)
    return bool(spec and spec.get("tier") == "nano")


def worker_candidates(available: Iterable[str], max_workers: int = DEFAULT_WORKER_COUNT,
                      exclude: Optional[Iterable[str]] = None) -> List[str]:
    """Cheapest-first worker ids, filtered to `available`, minus the reserved
    communicator/voice providers. Mirrors live delegator._worker_candidates.

    `available` is what the ProviderClient reports ready (keys present)."""
    avail = set(available)
    roster = tier_roster()
    reserved = communicator_providers()
    if exclude:
        reserved = reserved | set(exclude)
    candidates: List[str] = []
    for tier in WORKER_TIERS:
        for pid in roster.get(tier, []):
            if pid in reserved:
                continue
            if pid in avail and pid not in candidates:
                candidates.append(pid)
            if len(candidates) >= max_workers:
                return candidates
    return candidates
