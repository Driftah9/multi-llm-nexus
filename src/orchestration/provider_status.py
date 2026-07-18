"""Provider lifecycle tracker — persistent status per provider.

Tracks the trust ladder position of each provider:
  unknown → known → (council-eligible)
  any tier → benched (on sustained failure)

Shadow council state lives here: how many times a provider has been evaluated
as a shadow member, and how often their verdict agreed with the frontier.

NEXUS:PORTABLE — lifecycle enum, shadow council accounting, graduation logic.
NEXUS:OPERATOR — this install's KNOWN_PROVIDERS/FRONTIER_COUNCIL defaults map to
the top-level provider ids in `config/providers.yaml` (cerebras, groq,
google_gemini, mistral, sambanova, openrouter, cohere, nvidia_nim, huggingface,
cloudflare_workers, deepinfra) — NOT live claude-brain's model-level ids
(groq-8b, claude-opus, github-o3, ...). There is no `claude` entry in this
registry: claude_code is not OpenAI-compatible and does not sit in the Nexus
council, so FRONTIER_COUNCIL is drawn from the strongest OpenAI-compatible
deep-tier providers instead. Both sets are env-overridable per install.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_STATUS_PATH = os.environ.get("PROVIDER_STATUS_PATH", str(_DATA_DIR / "provider_status.json"))

# Graduation thresholds
SHADOW_MIN_CALLS = 20          # min shadow council calls before graduation considered
SHADOW_AGREE_RATE = 0.75       # fraction of shadow calls that must agree with frontier
TOTAL_GRADE_MIN = 50           # min total graded calls before council graduation
DOMAIN_COUNT_MIN = 2           # min domains with established score (≥0.70) for council

# Providers we treat as Known from day 1 — established community track record.
# These skip shadow council entirely and enter as Workers.
# Default = every top-level provider id in config/providers.yaml.
# Override via NEXUS_KNOWN_PROVIDERS (comma-separated) per install.
_DEFAULT_KNOWN_PROVIDERS = {
    "cerebras", "groq", "google_gemini", "mistral", "sambanova",
    "openrouter", "cohere", "nvidia_nim", "huggingface",
    "cloudflare_workers", "deepinfra",
}

# Permanent council members — define the standard, don't earn it.
# No `claude` here: claude_code isn't OpenAI-compatible so it never enters the
# Nexus council. Default = strongest deep-tier config providers.
# Override via NEXUS_FRONTIER_COUNCIL (comma-separated) per install.
_DEFAULT_FRONTIER_COUNCIL = {"sambanova", "openrouter", "cerebras"}


def _set_from_env(env_var: str, default: set[str]) -> set[str]:
    raw = os.environ.get(env_var)
    if not raw:
        return set(default)
    return {p.strip() for p in raw.split(",") if p.strip()}


KNOWN_PROVIDERS: set[str] = _set_from_env("NEXUS_KNOWN_PROVIDERS", _DEFAULT_KNOWN_PROVIDERS)
FRONTIER_COUNCIL: set[str] = _set_from_env("NEXUS_FRONTIER_COUNCIL", _DEFAULT_FRONTIER_COUNCIL)


@dataclass
class ProviderStatus:
    provider: str
    lifecycle: str = "unknown"           # unknown | known | benched
    shadow_council_active: bool = False
    shadow_calls: int = 0                # times run as shadow council member
    shadow_agreements: int = 0           # times agreed with frontier verdict
    total_calls: int = 0                 # total graded calls
    benched_reason: Optional[str] = None
    notes: str = ""

    @property
    def shadow_agree_rate(self) -> float:
        if self.shadow_calls == 0:
            return 0.0
        return self.shadow_agreements / self.shadow_calls

    @property
    def graduation_eligible(self) -> bool:
        """Has enough shadow council data to be considered for Known promotion."""
        return (
            self.lifecycle == "unknown"
            and self.shadow_calls >= SHADOW_MIN_CALLS
            and self.shadow_agree_rate >= SHADOW_AGREE_RATE
        )

    @property
    def is_frontier(self) -> bool:
        return self.provider in FRONTIER_COUNCIL


class ProviderStatusStore:
    """Load/save provider lifecycle records. Thread-safe via file lock pattern."""

    def __init__(self, path: str = _STATUS_PATH):
        self._path = path
        self._data: Dict[str, ProviderStatus] = {}
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for pid, rec in raw.items():
                    self._data[pid] = ProviderStatus(**rec)
        except Exception as e:
            logger.warning(f"provider_status load failed, starting fresh: {e}")
            self._data = {}

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump({k: asdict(v) for k, v in self._data.items()}, f, indent=2)
        except Exception as e:
            logger.warning(f"provider_status save failed: {e}")

    def get(self, provider: str) -> ProviderStatus:
        """Return status, creating it from defaults if first seen."""
        if provider not in self._data:
            lifecycle = "known" if provider in KNOWN_PROVIDERS else "unknown"
            shadow_active = lifecycle == "unknown"
            self._data[provider] = ProviderStatus(
                provider=provider,
                lifecycle=lifecycle,
                shadow_council_active=shadow_active,
            )
            self.save()
        return self._data[provider]

    def record_call(self, provider: str) -> None:
        s = self.get(provider)
        s.total_calls += 1
        self.save()

    def record_shadow(self, provider: str, agreed: bool) -> None:
        """Record one shadow council evaluation result."""
        s = self.get(provider)
        s.shadow_calls += 1
        if agreed:
            s.shadow_agreements += 1
        # Check graduation after each shadow call
        if s.graduation_eligible:
            self._promote_to_known(s)
        self.save()

    def _promote_to_known(self, s: ProviderStatus) -> None:
        logger.info(
            f"provider_status: {s.provider} graduated to Known "
            f"(shadow agree rate {s.shadow_agree_rate:.2f} over {s.shadow_calls} calls)"
        )
        s.lifecycle = "known"
        s.shadow_council_active = False
        s.notes = (
            f"Graduated to Known after {s.shadow_calls} shadow calls "
            f"({s.shadow_agreements} agreements, {s.shadow_agree_rate:.0%} rate)"
        )

    def bench(self, provider: str, reason: str) -> None:
        s = self.get(provider)
        s.lifecycle = "benched"
        s.shadow_council_active = False
        s.benched_reason = reason
        logger.warning(f"provider_status: {provider} benched — {reason}")
        self.save()

    def should_shadow(self, provider: str, call_index: int, sample_rate: int = 10) -> bool:
        """Return True if this call should trigger shadow council for this provider.

        Fires when:
        - Provider is Unknown and shadow_council_active
        - call_index % sample_rate == 0 (1-in-N sampling)
        """
        s = self.get(provider)
        if not s.shadow_council_active or s.lifecycle != "unknown":
            return False
        return call_index % sample_rate == 0

    def all_statuses(self) -> Dict[str, ProviderStatus]:
        return dict(self._data)


# Module-level singleton — orchestration layer imports this
_store: Optional[ProviderStatusStore] = None


def get_store() -> ProviderStatusStore:
    global _store
    if _store is None:
        _store = ProviderStatusStore()
    return _store
