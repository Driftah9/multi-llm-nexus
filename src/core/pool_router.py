"""
Pool Router — selects providers from tier pools based on triage results.

Replaces the single-assignment Router for operators who define tier_pools
in providers.yaml. Falls back to the legacy Router when no tier_pools are
configured, so existing single-provider setups are unaffected.

Selection logic:
  1. Determine which pool to use (code_pool, research_pool, etc. by capability)
  2. Ask PoolManager for ordered pool (available first, sorted by cost class)
  3. Return the ordered list — bridge tries them in order with failover

Cost class priority: local → free_limited → paid_subscription
Paid tokens are the provider of last resort for any task cheaper options can handle.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..providers.base import BaseProvider
    from .pool_manager import PoolManager
    from .triage import TriageResult

logger = logging.getLogger("nexus.pool_router")


class PoolRouter:
    """
    Pool-based provider selection.

    Given a TriageResult, returns an ordered list of provider names to try.
    The bridge works through the list in order — first available wins.

    Pools are defined in providers.yaml:
      tier_pools:
        nano:
          providers: [ollama_nano, groq, cerebras]
          parallelism: sequential
        standard:
          providers: [cerebras, claude_sonnet]
        deep:
          providers: [claude_opus]

    Routing config maps capabilities to pools:
      routing:
        default_pool: standard
        triage_pool: nano
        code_pool: standard
        research_pool: deep
        fallback: claude_opus
    """

    def __init__(
        self,
        providers: dict[str, "BaseProvider"],
        pool_manager: "PoolManager",
        routing_config: dict,
    ):
        self.providers = providers
        self.pool_manager = pool_manager
        self.routing = routing_config

        # Convenience lookups
        self._default_pool = routing_config.get("default_pool", "standard")
        self._triage_pool = routing_config.get("triage_pool", "nano")
        self._fallback = routing_config.get("fallback", "")

        # Capability → pool name mapping
        self._capability_pool_map: dict[str, str] = {
            "code":      routing_config.get("code_pool", self._default_pool),
            "search":    routing_config.get("research_pool", self._default_pool),
            "reasoning": routing_config.get("deep_pool", routing_config.get("deep", self._default_pool)),
            "voice":     routing_config.get("voice_pool", self._default_pool),
            "rag":       routing_config.get("rag_pool", self._default_pool),
            "local":     routing_config.get("local_pool", "nano"),
        }

        pool_names = pool_manager.tier_pool_names()
        logger.info(
            f"PoolRouter ready: {len(pool_names)} pool(s) — "
            f"default={self._default_pool} triage={self._triage_pool}"
        )

    def select(self, triage: "TriageResult") -> list[str]:
        """
        Return an ordered list of provider names to try for this triage result.

        First element is the preferred provider (cheapest available).
        Subsequent elements are fallbacks in cost-class priority order.
        All names in the list are known to the providers dict.

        Returns empty list only if the pool is completely empty — callers
        should fall back to the legacy router in that case.
        """
        pool_name = self._pool_for_triage(triage)
        ordered = self.pool_manager.ordered_pool(pool_name)

        # Filter to providers we actually have loaded
        valid = [p for p in ordered if p in self.providers]

        if not valid:
            logger.warning(
                f"Pool '{pool_name}' has no loaded providers — "
                f"trying fallback: {self._fallback}"
            )
            if self._fallback and self._fallback in self.providers:
                return [self._fallback]
            return []

        logger.debug(
            f"Pool '{pool_name}' for {triage.task_type}/{triage.capability_required}: "
            f"{valid}"
        )
        return valid

    def select_triage_provider(self) -> Optional["BaseProvider"]:
        """Return the best available nano provider for triage classification."""
        ordered = self.pool_manager.ordered_pool(self._triage_pool)
        for name in ordered:
            if name in self.providers:
                return self.providers[name]

        # No triage pool — return first available provider
        return next(iter(self.providers.values()), None)

    def _pool_for_triage(self, triage: "TriageResult") -> str:
        """Determine which pool to use based on the triage result."""
        # Commands always use the triage (nano) pool
        if triage.is_command:
            return self._triage_pool

        capability = getattr(triage, "capability_required", "general")
        complexity = getattr(triage, "estimated_complexity", "standard")
        urgency = getattr(triage, "urgency", "normal")

        # Capability-specific pools take precedence
        if capability in self._capability_pool_map:
            pool_name = self._capability_pool_map[capability]
            if self.pool_manager.get_tier_pool(pool_name):
                return pool_name

        # Complexity → tier pool
        complexity_pool_map = {
            "nano": "nano",
            "standard": self._default_pool,
            "deep": self.routing.get("deep_pool", self.routing.get("deep", "deep")),
        }
        pool_name = complexity_pool_map.get(complexity, self._default_pool)

        # Urgency override: immediate tasks prefer nano for latency if task is simple
        if urgency == "immediate" and complexity == "nano":
            pool_name = "nano"

        if self.pool_manager.get_tier_pool(pool_name):
            return pool_name

        return self._default_pool

    def list_pools(self) -> dict[str, list[str]]:
        """Return all tier pools and their provider lists."""
        return {
            name: self.pool_manager.get_tier_pool(name).providers
            for name in self.pool_manager.tier_pool_names()
            if self.pool_manager.get_tier_pool(name)
        }

    def has_pool(self, pool_name: str) -> bool:
        return self.pool_manager.get_tier_pool(pool_name) is not None
