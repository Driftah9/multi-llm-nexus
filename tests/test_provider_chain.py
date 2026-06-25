"""
Tests for ProviderChain: health tracking, failover, and circuit breaker.
"""
import asyncio
import time
import pytest

from src.core.provider_chain import (
    ProviderChain, ProviderChainEntry, ChainConfig, ProviderHealth
)
from tests.conftest import MockProvider, FailingProvider


def _entry(provider, priority=1, tier="standard"):
    return ProviderChainEntry(
        provider=provider,
        priority=priority,
        tier=tier,
        name=provider.name,
        display_prefix=provider.name.capitalize(),
        model_display="mock",
    )


@pytest.fixture
def two_provider_chain():
    primary = MockProvider("primary")
    secondary = MockProvider("secondary")
    chain = ProviderChain(
        entries=[_entry(primary, priority=1), _entry(secondary, priority=2)],
        config=ChainConfig(
            retry_attempts=2,
            failure_threshold=3,
            cooldown_seconds=5.0,
            enable_health_monitoring=False,
        ),
    )
    return chain, primary, secondary


@pytest.mark.asyncio
async def test_select_healthy_primary(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    provider = await chain.select_provider()
    assert provider is primary.send.__self__ or provider is primary


@pytest.mark.asyncio
async def test_failover_to_secondary_on_failure(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    primary._should_fail = True  # mark manually

    async def invoke(p):
        if p is primary:
            raise RuntimeError("primary down")
        return "ok from secondary"

    success, result, provider, fallback_occurred, error = await chain.try_with_fallback(invoke)
    assert success
    assert result == "ok from secondary"


@pytest.mark.asyncio
async def test_record_failure_marks_degraded(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    await chain.record_failure(primary, "timeout")

    entry = chain._find_entry(primary)
    assert entry.health == ProviderHealth.DEGRADED
    assert entry.consecutive_failures == 1
    assert entry.cooldown_until > time.time()


@pytest.mark.asyncio
async def test_failure_threshold_marks_failed(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    for _ in range(3):
        await chain.record_failure(primary, "repeated error")

    entry = chain._find_entry(primary)
    assert entry.health == ProviderHealth.FAILED


@pytest.mark.asyncio
async def test_record_success_resets_state(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    await chain.record_failure(primary, "transient")
    await chain.record_success(primary)

    entry = chain._find_entry(primary)
    assert entry.health == ProviderHealth.HEALTHY
    assert entry.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_skips_degraded_in_cooldown(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    # Record one failure — primary goes DEGRADED with cooldown_until in the future
    await chain.record_failure(primary, "transient")

    entry = chain._find_entry(primary)
    assert entry.health == ProviderHealth.DEGRADED
    assert entry.cooldown_until > time.time()

    # select_provider should skip primary and return secondary
    provider = await chain.select_provider()
    assert provider is secondary


@pytest.mark.asyncio
async def test_circuit_breaker_restores_after_cooldown(two_provider_chain):
    chain, primary, secondary = two_provider_chain
    await chain.record_failure(primary, "transient")

    # Manually expire the cooldown
    entry = chain._find_entry(primary)
    entry.cooldown_until = time.time() - 1

    provider = await chain.select_provider()
    assert provider is primary


@pytest.mark.asyncio
async def test_all_providers_exhausted_returns_failure():
    failing = MockProvider("only", should_fail=True)
    chain = ProviderChain(
        entries=[_entry(failing)],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )

    async def invoke(p):
        raise RuntimeError("always fails")

    success, result, provider, fallback_occurred, error = await chain.try_with_fallback(invoke)
    assert not success
    assert error is not None


@pytest.mark.asyncio
async def test_auth_failure_gets_long_cooldown(two_provider_chain):
    """A billing/auth failure benches the provider for the long auth window, not the
    short transient window (classification-aware cooldown)."""
    chain, primary, _ = two_provider_chain
    chain.config.auth_cooldown_seconds = 3600.0
    chain.config.cooldown_seconds = 5.0
    before = time.time()
    await chain.record_failure(primary, error="Error 402 insufficient balance")
    entry = chain._find_entry(primary)
    # cooldown should be ~1h out, far beyond the 5s transient window
    assert entry.cooldown_until - before > 3000


@pytest.mark.asyncio
async def test_transient_failure_gets_short_cooldown(two_provider_chain):
    chain, primary, _ = two_provider_chain
    chain.config.auth_cooldown_seconds = 3600.0
    chain.config.cooldown_seconds = 5.0
    before = time.time()
    await chain.record_failure(primary, error="529 overloaded, try again")
    entry = chain._find_entry(primary)
    assert entry.cooldown_until - before < 60


@pytest.mark.asyncio
async def test_health_persists_across_restart(tmp_path):
    """A benched provider's cooldown survives a 'restart' (new chain instance loading
    the same health_path)."""
    health_file = str(tmp_path / "health.json")
    p = MockProvider("primary")
    cfg = ChainConfig(enable_health_monitoring=False, auth_cooldown_seconds=3600.0,
                      health_path=health_file)
    chain1 = ProviderChain(entries=[_entry(p)], config=cfg)
    await chain1.record_failure(p, error="401 invalid api key")
    saved_cooldown = chain1._find_entry(p).cooldown_until
    assert saved_cooldown > time.time() + 3000

    # "restart": brand-new chain, fresh provider instance, same health file
    p2 = MockProvider("primary")
    chain2 = ProviderChain(entries=[_entry(p2)],
                           config=ChainConfig(enable_health_monitoring=False, health_path=health_file))
    restored = chain2._find_entry(p2)
    assert restored.cooldown_until == saved_cooldown
    assert restored.health == ProviderHealth.DEGRADED


@pytest.mark.asyncio
async def test_no_persistence_when_path_unset(two_provider_chain):
    """Default (no health_path) → no file I/O, pure in-memory (behavior-preserving)."""
    chain, primary, _ = two_provider_chain
    assert chain.config.health_path is None
    await chain.record_failure(primary, error="boom")  # must not raise
