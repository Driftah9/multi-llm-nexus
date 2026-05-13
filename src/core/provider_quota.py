"""
Provider Quota Manager — access-tier-aware rate limiting.

Each provider declares its access tier (free, paid, unlimited) and its
known limits. The quota manager tracks real-time usage and exposes
simple queries the rest of the system uses to make decisions:

  - Can I use this provider right now?
  - How much headroom does this provider have?
  - Should I conserve this provider for critical work?

This is the mechanism that lets the system work WITHIN limits instead of
slamming into them and relying on error recovery.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.quota")


class AccessTier(Enum):
    """Provider billing/access level — drives all rate decisions."""
    FREE = "free"            # Hard daily/minute limits, no billing
    TRIAL = "trial"          # Credit-based (e.g., $300/90 days)
    PAID = "paid"            # Pay-per-use, soft limits
    UNLIMITED = "unlimited"  # Enterprise/self-hosted, no limits


@dataclass
class ProviderLimits:
    """Known rate limits for a provider."""
    access_tier: AccessTier = AccessTier.FREE
    requests_per_minute: int = 0     # 0 = unknown/unlimited
    requests_per_day: int = 0        # 0 = unknown/unlimited
    tokens_per_minute: int = 0       # 0 = unknown/unlimited
    tokens_per_day: int = 0          # 0 = unknown/unlimited
    daily_budget_usd: float = 0.0    # 0 = no budget cap (paid/unlimited)

    @property
    def has_rpm_limit(self) -> bool:
        return self.requests_per_minute > 0

    @property
    def has_daily_limit(self) -> bool:
        return self.requests_per_day > 0 or self.tokens_per_day > 0


@dataclass
class UsageBucket:
    """Tracks usage within a time window."""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    window_start: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ── Known free-tier limits (provider defaults) ───────────────────────────────
# Users can override these in providers.yaml — these are sensible defaults

KNOWN_LIMITS: dict[str, ProviderLimits] = {
    "groq": ProviderLimits(
        access_tier=AccessTier.FREE,
        requests_per_minute=30,
        requests_per_day=14_400,
        tokens_per_minute=6_000,
        tokens_per_day=500_000,
    ),
    "cerebras": ProviderLimits(
        access_tier=AccessTier.FREE,
        requests_per_minute=30,
        requests_per_day=14_400,
        tokens_per_minute=60_000,
        tokens_per_day=1_000_000,
    ),
    "gemini": ProviderLimits(
        access_tier=AccessTier.FREE,
        requests_per_minute=15,
        requests_per_day=1_500,
        tokens_per_minute=1_000_000,
        tokens_per_day=1_000_000,
    ),
    "ollama": ProviderLimits(
        access_tier=AccessTier.UNLIMITED,
    ),
}


class ProviderQuotaManager:
    """
    Tracks real-time usage per provider and answers quota questions.

    The rest of the system never checks rate limits directly —
    it asks the quota manager:
      - can_use(provider) → bool
      - headroom(provider) → float (0.0 to 1.0)
      - should_conserve(provider) → bool

    This decouples rate-limit awareness from every caller.
    """

    def __init__(self, persistence_path: Optional[Path] = None):
        self._limits: dict[str, ProviderLimits] = {}
        self._minute_buckets: dict[str, UsageBucket] = {}
        self._daily_buckets: dict[str, UsageBucket] = {}
        self._persistence_path = persistence_path

    def register(self, provider_name: str, limits: ProviderLimits) -> None:
        self._limits[provider_name] = limits
        logger.info(
            f"Quota registered: {provider_name} ({limits.access_tier.value}) "
            f"RPM={limits.requests_per_minute or 'unlimited'} "
            f"RPD={limits.requests_per_day or 'unlimited'}"
        )

    def register_from_config(self, provider_name: str, provider_config: dict) -> None:
        """Register limits from providers.yaml config block."""
        access = provider_config.get("access_tier", "free")
        access_tier = AccessTier(access) if access in [t.value for t in AccessTier] else AccessTier.FREE

        # Start with known defaults if available
        defaults = KNOWN_LIMITS.get(provider_name, ProviderLimits())

        limits = ProviderLimits(
            access_tier=access_tier,
            requests_per_minute=provider_config.get("rpm", defaults.requests_per_minute),
            requests_per_day=provider_config.get("rpd", defaults.requests_per_day),
            tokens_per_minute=provider_config.get("tpm", defaults.tokens_per_minute),
            tokens_per_day=provider_config.get("tpd", defaults.tokens_per_day),
            daily_budget_usd=provider_config.get("daily_budget_usd", defaults.daily_budget_usd),
        )
        self.register(provider_name, limits)

    # ── Usage recording ───────────────────────────────────────────

    def record(self, provider_name: str, input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0) -> None:
        now = time.time()

        # Minute bucket — reset if over 60s old
        minute = self._minute_buckets.get(provider_name)
        if not minute or (now - minute.window_start) > 60:
            minute = UsageBucket(window_start=now)
            self._minute_buckets[provider_name] = minute
        minute.requests += 1
        minute.input_tokens += input_tokens
        minute.output_tokens += output_tokens
        minute.cost_usd += cost_usd

        # Daily bucket — reset if different day
        daily = self._daily_buckets.get(provider_name)
        today_start = now - (now % 86400)
        if not daily or daily.window_start < today_start:
            daily = UsageBucket(window_start=today_start)
            self._daily_buckets[provider_name] = daily
        daily.requests += 1
        daily.input_tokens += input_tokens
        daily.output_tokens += output_tokens
        daily.cost_usd += cost_usd

    # ── Query API (used by the rest of the system) ────────────────

    def can_use(self, provider_name: str) -> bool:
        """Can this provider accept a request right now?"""
        limits = self._limits.get(provider_name)
        if not limits:
            return True  # Unknown provider — allow by default

        if limits.access_tier in (AccessTier.PAID, AccessTier.UNLIMITED):
            return True

        # Check minute window
        if limits.has_rpm_limit:
            minute = self._minute_buckets.get(provider_name)
            if minute and (time.time() - minute.window_start) < 60:
                if minute.requests >= limits.requests_per_minute:
                    return False
                if limits.tokens_per_minute and minute.total_tokens >= limits.tokens_per_minute:
                    return False

        # Check daily window
        if limits.has_daily_limit:
            daily = self._daily_buckets.get(provider_name)
            if daily:
                if limits.requests_per_day and daily.requests >= limits.requests_per_day:
                    return False
                if limits.tokens_per_day and daily.total_tokens >= limits.tokens_per_day:
                    return False

        return True

    def headroom(self, provider_name: str) -> float:
        """
        How much capacity remains (0.0 = exhausted, 1.0 = fresh).

        Uses the tightest constraint (RPM, RPD, TPM, TPD).
        Paid/unlimited providers always return 1.0.
        """
        limits = self._limits.get(provider_name)
        if not limits or limits.access_tier in (AccessTier.PAID, AccessTier.UNLIMITED):
            return 1.0

        ratios = []

        if limits.has_rpm_limit:
            minute = self._minute_buckets.get(provider_name)
            if minute and (time.time() - minute.window_start) < 60:
                if limits.requests_per_minute:
                    ratios.append(1.0 - (minute.requests / limits.requests_per_minute))
                if limits.tokens_per_minute:
                    ratios.append(1.0 - (minute.total_tokens / limits.tokens_per_minute))

        if limits.has_daily_limit:
            daily = self._daily_buckets.get(provider_name)
            if daily:
                if limits.requests_per_day:
                    ratios.append(1.0 - (daily.requests / limits.requests_per_day))
                if limits.tokens_per_day:
                    ratios.append(1.0 - (daily.total_tokens / limits.tokens_per_day))

        if not ratios:
            return 1.0

        return max(0.0, min(ratios))

    def should_conserve(self, provider_name: str) -> bool:
        """
        Should the system avoid non-essential use of this provider?

        True when free-tier provider is below 20% remaining capacity.
        Used by review gate and orchestrator to skip optional calls.
        """
        limits = self._limits.get(provider_name)
        if not limits or limits.access_tier in (AccessTier.PAID, AccessTier.UNLIMITED):
            return False

        return self.headroom(provider_name) < 0.20

    def get_access_tier(self, provider_name: str) -> AccessTier:
        limits = self._limits.get(provider_name)
        return limits.access_tier if limits else AccessTier.FREE

    def is_rate_limited(self, provider_name: str) -> bool:
        """True if this provider has known rate limits (free or trial)."""
        limits = self._limits.get(provider_name)
        if not limits:
            return True  # Assume limited if unknown
        return limits.access_tier in (AccessTier.FREE, AccessTier.TRIAL)

    # ── Status / reporting ────────────────────────────────────────

    def status(self, provider_name: Optional[str] = None) -> list[dict]:
        results = []
        for name, limits in self._limits.items():
            if provider_name and name != provider_name:
                continue

            minute = self._minute_buckets.get(name, UsageBucket())
            daily = self._daily_buckets.get(name, UsageBucket())

            results.append({
                "provider": name,
                "access_tier": limits.access_tier.value,
                "can_use": self.can_use(name),
                "headroom": round(self.headroom(name), 3),
                "should_conserve": self.should_conserve(name),
                "minute": {
                    "requests": minute.requests,
                    "tokens": minute.total_tokens,
                    "limit_rpm": limits.requests_per_minute,
                    "limit_tpm": limits.tokens_per_minute,
                },
                "daily": {
                    "requests": daily.requests,
                    "tokens": daily.total_tokens,
                    "cost_usd": round(daily.cost_usd, 6),
                    "limit_rpd": limits.requests_per_day,
                    "limit_tpd": limits.tokens_per_day,
                },
            })

        return results
