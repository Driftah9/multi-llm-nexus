"""Outage reasons + mechanical recovery in the chain HealthMonitor (live→Nexus 2026-10-19)."""
import asyncio
import time

import pytest

from src.core.outage_reason import describe, summary
from src.core.provider_chain import (ChainConfig, HealthMonitor, ProviderChain,
                                     ProviderChainEntry, ProviderHealth)


# ── outage_reason ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("detail,klass,code,local", [
    ("Error 429: Too Many Requests", None, "rate_limited", False),
    ("No conversation found with session id abc", None, "stale_session", True),
    ("Separator is not found, and chunk exceed the limit", None, "stream_overflow", True),
    ("503 Service Unavailable", None, "server_error", False),
    ("insufficient balance", "quota", "quota", False),
    ("model xyz does not exist", "model_gone", "model_gone", True),
    ("KeyError: 'choices'", None, "bridge_bug", True),
    ("something odd", None, "unknown", None),
])
def test_describe(detail, klass, code, local):
    r = describe(detail, klass)
    assert (r.code, r.local) == (code, local)


def test_summary_names_whose_side():
    assert "provider side" in summary(describe("503"))
    assert "our side" in summary(describe("No conversation found with session id x"))
    assert summary(describe("odd")) == "unclassified failure"


def test_labels_are_provider_neutral():
    from src.core import outage_reason
    for reason, _ in outage_reason._TABLE:
        assert "claude" not in reason.label.lower() and "anthropic" not in reason.label.lower()


# ── HealthMonitor recovery branches ──────────────────────────────────────────

class _P:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.checks = 0
        self.model = "m"

    async def health_check(self):
        self.checks += 1
        return self.healthy


def _chain(*entries):
    ch = ProviderChain(list(entries), ChainConfig(enable_health_monitoring=False))
    ch.announced = []

    async def on_recovery(text):
        ch.announced.append(text)
    ch.on_recovery = on_recovery
    return ch


def _entry(name, prov, cost_class="paid_subscription"):
    return ProviderChainEntry(provider=prov, priority=1, tier="standard", name=name,
                              cost_class=cost_class)


async def _down(ch, entry, error, n=3):
    for _ in range(n):
        await ch.record_failure(entry.provider, error=error)


def test_transient_outage_is_probed_cleared_and_announced():
    p = _P(healthy=True)
    e = _entry("groq", p)
    ch = _chain(e)

    async def go():
        await _down(ch, e, "503 Service Unavailable")
        assert e.health == ProviderHealth.FAILED and e.cooldown_until > time.time() and e.down_since
        await HealthMonitor(ch).sweep()
    asyncio.run(go())
    assert p.checks == 1
    assert e.health == ProviderHealth.HEALTHY and e.consecutive_failures == 0
    assert e.cooldown_until == 0.0 and e.down_since == 0.0          # bench cleared, not waited out
    assert len(ch.announced) == 1 and "`groq` is back online" in ch.announced[0]
    assert "provider server error" in ch.announced[0] and "down ~" in ch.announced[0]


def test_auth_outage_is_never_probed():
    p = _P(healthy=True)
    e = _entry("openai", p)
    ch = _chain(e)

    async def go():
        await _down(ch, e, "401 Unauthorized: invalid api key")
        await HealthMonitor(ch).sweep()
    asyncio.run(go())
    assert p.checks == 0 and e.health == ProviderHealth.FAILED and not ch.announced


def test_quota_waits_for_its_window_then_probes():
    p = _P(healthy=True)
    e = _entry("gemini", p)
    ch = _chain(e)

    async def go():
        await _down(ch, e, "quota exceeded for this billing period")
        await HealthMonitor(ch).sweep()              # window not passed → no probe
        assert p.checks == 0
        e.cooldown_until = time.time() - 1           # window passes
        await HealthMonitor(ch).sweep()
    asyncio.run(go())
    assert p.checks == 1 and e.health == ProviderHealth.HEALTHY and len(ch.announced) == 1


def test_still_failing_probe_stays_down_and_silent():
    p = _P(healthy=False)
    e = _entry("mistral", p)
    ch = _chain(e)

    async def go():
        await _down(ch, e, "connection reset by peer")
        await HealthMonitor(ch).sweep()
    asyncio.run(go())
    assert e.consecutive_failures == 3 and not ch.announced


def test_local_floor_recovers_silently():
    p = _P(healthy=True)
    e = _entry("ollama", p, cost_class="local")
    ch = _chain(e)

    async def go():
        await _down(ch, e, "connection refused")
        await HealthMonitor(ch).sweep()
    asyncio.run(go())
    assert e.health == ProviderHealth.HEALTHY and not ch.announced


def test_healthy_entry_check_does_not_announce():
    p = _P(healthy=True)
    e = _entry("groq", p)
    ch = _chain(e)
    asyncio.run(HealthMonitor(ch).sweep())
    assert p.checks == 1 and e.health == ProviderHealth.HEALTHY and not ch.announced


def test_down_since_survives_restart(tmp_path):
    path = str(tmp_path / "health.json")
    p = _P()
    e = _entry("groq", p)
    ch = ProviderChain([e], ChainConfig(health_path=path, enable_health_monitoring=False))
    asyncio.run(_down(ch, e, "503", n=1))
    since = e.down_since
    e2 = _entry("groq", _P())
    ProviderChain([e2], ChainConfig(health_path=path, enable_health_monitoring=False))
    assert e2.down_since == since and e2.consecutive_failures == 1


def test_no_on_recovery_means_silent_recovery():
    p = _P(healthy=True)
    e = _entry("groq", p)
    ch = ProviderChain([e], ChainConfig(enable_health_monitoring=False))

    async def go():
        await _down(ch, e, "503")
        await HealthMonitor(ch).sweep()
    asyncio.run(go())
    assert e.health == ProviderHealth.HEALTHY
