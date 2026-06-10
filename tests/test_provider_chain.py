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
