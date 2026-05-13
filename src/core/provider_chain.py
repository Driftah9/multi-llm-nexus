"""
Provider chain — hierarchical failover for multi-provider orchestration.

Manages an ordered list of providers (primary → secondary → tertiary)
with health checks and automatic promotion on failure.

Enables seamless switching: if Claude API fails, try Gemini, then Ollama.
The system keeps running regardless of provider availability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..providers.base import BaseProvider

logger = logging.getLogger(__name__)

_pool_manager = None  # Set by main.py after PoolManager is loaded


def set_pool_manager(manager) -> None:
    """Wire the pool manager into the chain at startup."""
    global _pool_manager
    _pool_manager = manager


class ProviderHealth(Enum):
    """Health status of a provider."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass
class ProviderChainEntry:
    """Single entry in the failover chain."""
    provider: BaseProvider
    priority: int  # 1=primary, 2=secondary, 3=tertiary
    tier: str  # nano / standard / deep
    name: str = ""
    display_prefix: str = ""    # "Claude", "Local", "Gemini", "OpenAI", etc.
    model_display: str = ""     # "Opus", "tinyllama", "1.5-pro", etc.
    effort_levels: bool = False  # True if provider supports effort (e.g. Claude)
    health: ProviderHealth = ProviderHealth.UNKNOWN
    last_check: float = 0.0
    consecutive_failures: int = 0
    last_error: str = ""
    response_time_ms: float = 0.0
    cooldown_until: float = 0.0  # epoch time until which this entry is skipped on hot path


@dataclass
class ChainConfig:
    """Configuration for provider chain failover behavior."""
    strategy: str = "priority"  # priority | round-robin | cost-optimized
    health_check_interval: int = 60  # seconds between health checks
    retry_attempts: int = 2  # how many providers to try before failing
    retry_delay: float = 0.5  # seconds between retries
    failure_threshold: int = 3  # consecutive failures before marking unhealthy
    on_failure: str = "next_available"  # next_available | local_only | silent
    enable_health_monitoring: bool = True
    cooldown_seconds: float = 30.0  # hot-path skip window after a failure


class ProviderChain:
    """
    Manages hierarchical failover across multiple LLM providers.

    Tries providers in priority order:
    1. Primary (highest priority, usually paid API like Claude)
    2. Secondary (fallback, like Gemini)
    3. Tertiary (always available, like local Ollama)

    Automatically switches to next provider on:
    - API errors (timeout, rate limit, auth failure)
    - Health check failures
    - Exceeding consecutive failure threshold

    Example:
        chain = ProviderChain(
            entries=[claude, gemini, ollama],
            config=ChainConfig(retry_attempts=2)
        )
        provider = await chain.select_provider(tier="standard")
        # Returns the best available provider for this tier
    """

    def __init__(self, entries: list[ProviderChainEntry], config: ChainConfig = None):
        self.entries = sorted(entries, key=lambda e: e.priority)
        self.config = config or ChainConfig()
        self.monitor: Optional[HealthMonitor] = None
        self._lock = asyncio.Lock()

    async def select_provider(self, tier: Optional[str] = None) -> Optional[BaseProvider]:
        """
        Select the best available provider, optionally filtered by tier.

        Strategy:
        1. Find providers matching the tier (if specified)
        2. Sort by priority and health
        3. Return first healthy provider

        Returns None if no provider is available.
        """
        async with self._lock:
            candidates = self.entries

            if tier:
                candidates = [e for e in candidates if e.tier == tier]

            if not candidates:
                logger.warning(f"No providers available for tier: {tier}")
                return None

            now = time.time()

            # Hot-path circuit breaker: skip providers still in cooldown window
            eligible = [
                e for e in candidates
                if not (e.health == ProviderHealth.DEGRADED and now < e.cooldown_until)
            ]
            # Fall back to full candidate list if circuit breaker eliminates everything
            if not eligible:
                eligible = candidates

            # Sort by: health status (healthy first), then priority
            sorted_entries = sorted(
                eligible,
                key=lambda e: (
                    e.health != ProviderHealth.HEALTHY,  # healthy=0, others=1
                    e.priority  # lower priority number = higher precedence
                )
            )

            # Pool-aware selection: prefer non-busy pools when pool_manager is active
            if _pool_manager:
                non_busy = [
                    e for e in sorted_entries
                    if not _pool_manager.is_busy(e.name)
                    and e.health != ProviderHealth.FAILED
                ]
                if non_busy:
                    return non_busy[0].provider
                # All pools busy or no pool config — fall through to normal selection

            if sorted_entries and sorted_entries[0].health != ProviderHealth.FAILED:
                return sorted_entries[0].provider

            logger.warning(f"All providers for tier '{tier}' are failed or degraded")
            return None

    async def try_with_fallback(
        self,
        invoke_fn,
        tier: Optional[str] = None,
        on_attempt=None,
    ):
        """
        Try to invoke a function with automatic fallback to next provider.

        invoke_fn is a coroutine that takes (provider) and returns a result.
        on_attempt is an optional async callback(entry: ProviderChainEntry) called
        before each attempt — used for heartbeat provider-change notifications.
        Returns (success, result, provider_used, error_message)
        """
        attempts = 0
        last_error = None

        while attempts < min(len(self.entries), self.config.retry_attempts):
            provider = await self.select_provider(tier=tier)
            if not provider:
                return False, None, None, "No available providers"

            if on_attempt:
                entry = self._find_entry(provider)
                if entry:
                    try:
                        await on_attempt(entry)
                    except Exception:
                        pass

            try:
                result = await invoke_fn(provider)
                # Mark success
                await self.record_success(provider)
                return True, result, provider, None

            except Exception as e:
                last_error = str(e)
                await self.record_failure(provider, error=last_error)
                logger.warning(
                    f"Provider failed (attempt {attempts + 1}): {last_error}"
                )
                attempts += 1

                if attempts < self.config.retry_attempts:
                    await asyncio.sleep(self.config.retry_delay)

        return False, None, None, last_error

    def _find_entry(self, provider: BaseProvider) -> Optional[ProviderChainEntry]:
        """Find chain entry for a given provider instance."""
        for entry in self.entries:
            if entry.provider is provider:
                return entry
        return None

    async def record_success(self, provider: BaseProvider) -> None:
        """Mark a provider as successful."""
        async with self._lock:
            for entry in self.entries:
                if entry.provider is provider:
                    if entry.consecutive_failures > 0:
                        logger.info(
                            f"Provider {entry.name} recovered after "
                            f"{entry.consecutive_failures} failures"
                        )
                    entry.consecutive_failures = 0
                    entry.health = ProviderHealth.HEALTHY
                    break

    async def record_failure(self, provider: BaseProvider, error: str = "") -> None:
        """Record a provider failure and potentially mark it unhealthy."""
        async with self._lock:
            for entry in self.entries:
                if entry.provider is provider:
                    entry.consecutive_failures += 1
                    entry.last_error = error

                    if (entry.consecutive_failures >= self.config.failure_threshold):
                        entry.health = ProviderHealth.FAILED
                        logger.error(
                            f"Provider {entry.name} marked FAILED after "
                            f"{entry.consecutive_failures} consecutive failures: {error}"
                        )
                    else:
                        entry.health = ProviderHealth.DEGRADED
                        entry.cooldown_until = time.time() + self.config.cooldown_seconds
                    break

    async def start_health_monitoring(self) -> None:
        """Start background health checks."""
        if not self.config.enable_health_monitoring:
            return

        self.monitor = HealthMonitor(self, interval=self.config.health_check_interval)
        asyncio.create_task(self.monitor.run())
        logger.info("Provider chain health monitoring started")

    async def stop_health_monitoring(self) -> None:
        """Stop background health checks."""
        if self.monitor:
            await self.monitor.stop()
            self.monitor = None

    async def get_status(self) -> dict:
        """Return current status of all providers in the chain."""
        async with self._lock:
            return {
                entry.name: {
                    "priority": entry.priority,
                    "tier": entry.tier,
                    "health": entry.health.value,
                    "consecutive_failures": entry.consecutive_failures,
                    "last_error": entry.last_error,
                    "response_time_ms": entry.response_time_ms,
                    "last_check": entry.last_check,
                }
                for entry in self.entries
            }


class HealthMonitor:
    """
    Background health monitor for provider chain.

    Periodically calls provider.health_check() to detect failures
    before they cause user-facing errors.
    """

    def __init__(self, chain: ProviderChain, interval: int = 60):
        self.chain = chain
        self.interval = interval
        self.running = False

    async def run(self) -> None:
        """Run health checks at configured interval."""
        self.running = True

        while self.running:
            await asyncio.sleep(self.interval)

            if not self.running:
                break

            tasks = [self._check_provider(entry) for entry in self.chain.entries]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_provider(self, entry: ProviderChainEntry) -> None:
        """Check health of a single provider."""
        try:
            start = time.time()
            is_healthy = await entry.provider.health_check()
            elapsed_ms = (time.time() - start) * 1000

            async with self.chain._lock:
                entry.last_check = time.time()
                entry.response_time_ms = elapsed_ms

                if is_healthy:
                    if entry.health == ProviderHealth.FAILED:
                        logger.info(f"Provider {entry.name} recovered during health check")
                    entry.health = ProviderHealth.HEALTHY
                    entry.consecutive_failures = 0
                else:
                    entry.health = ProviderHealth.DEGRADED

        except Exception as e:
            logger.warning(f"Health check failed for {entry.name}: {e}")
            entry.last_check = time.time()
            await self.chain.record_failure(entry.provider, error=str(e))

    async def stop(self) -> None:
        """Stop the health monitor."""
        self.running = False
