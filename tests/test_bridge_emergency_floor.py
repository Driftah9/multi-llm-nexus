"""Tests for the bridge-level emergency local floor (ported from claude-brain live,
2026-09-12 — see CHANGELOG "Ultimate fallback").

When ProviderChain.try_with_fallback() exhausts every candidate configured for the
requested tier, _invoke_with_chain() used to return a bare error string straight to
the user. That leaves an operator with, say, a Gemini-only standard tier and no local
provider in THAT tier silent the moment Gemini goes down — even if they configured a
local Ollama model under a different tier (nano, typically).

These tests pin the fix: before giving up, the bridge checks
ProviderChain.emergency_floor() (cost_class == "local", any tier) and, if one exists,
answers from it with a degraded-mode notice instead of a bare error.
"""
import pytest

from src.core.bridge import NexusBridge
from src.core.provider_chain import ProviderChain, ProviderChainEntry, ChainConfig
from tests.conftest import MockProvider


def _entry(provider, priority, tier, cost_class="paid_subscription"):
    return ProviderChainEntry(
        provider=provider,
        priority=priority,
        tier=tier,
        name=provider.name,
        display_prefix=provider.name.capitalize(),
        model_display="mock",
        cost_class=cost_class,
    )


@pytest.mark.asyncio
async def test_emergency_floor_answers_when_standard_tier_fully_exhausted():
    """Standard tier's only provider is down; a local provider exists under nano.
    The bridge must still answer, with a degraded-mode notice, not a bare error."""
    cloud = MockProvider("cloud-standard", should_fail=True)
    local = MockProvider("local-nano", response="local answer")
    chain = ProviderChain(
        entries=[
            _entry(cloud, priority=1, tier="standard"),
            _entry(local, priority=9, tier="nano", cost_class="local"),
        ],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )
    bridge = NexusBridge(chain=chain)

    result = await bridge.invoke("hello", session_key="test_session", tier="standard")

    assert "local answer" in result.text
    assert "emergency fallback" in result.text.lower()
    assert local.call_count == 1


@pytest.mark.asyncio
async def test_bare_error_preserved_when_no_local_provider_configured():
    """No cost_class=local provider anywhere in the config — behavior must be
    unchanged from before the fix: a bare error, not a crash or a hang."""
    cloud = MockProvider("cloud-only", should_fail=True)
    chain = ProviderChain(
        entries=[_entry(cloud, priority=1, tier="standard")],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )
    bridge = NexusBridge(chain=chain)

    result = await bridge.invoke("hello", session_key="test_session", tier="standard")

    assert "All providers failed" in result.text


@pytest.mark.asyncio
async def test_emergency_floor_failure_falls_through_to_bare_error():
    """Local floor exists but is ALSO down — must not raise; falls through to the
    same bare-error path as if no floor existed at all."""
    cloud = MockProvider("cloud-standard", should_fail=True)
    dead_local = MockProvider("dead-local", should_fail=True)
    chain = ProviderChain(
        entries=[
            _entry(cloud, priority=1, tier="standard"),
            _entry(dead_local, priority=9, tier="nano", cost_class="local"),
        ],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )
    bridge = NexusBridge(chain=chain)

    result = await bridge.invoke("hello", session_key="test_session", tier="standard")

    assert "All providers failed" in result.text
