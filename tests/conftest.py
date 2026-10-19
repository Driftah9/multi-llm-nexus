"""
Shared fixtures for Nexus test suite.
"""
import pytest
import asyncio
from dataclasses import dataclass
from typing import Optional


# ── Ledger isolation ─────────────────────────────────────────────────────────
# The provider chain records every attempt to routing telemetry, and routing_weights
# derives demotions from it. A test must never write those ledgers in the real data dir:
# fake providers would enter routing evidence (live hit exactly this, 2026-10-07).

@pytest.fixture(autouse=True)
def _isolate_routing_ledgers(tmp_path, monkeypatch):
    from src.core import routing_telemetry, routing_weights
    monkeypatch.setattr(routing_telemetry, "_LOG_PATH", str(tmp_path / "routing_telemetry.jsonl"))
    monkeypatch.setattr(routing_weights, "WEIGHTS_PATH", tmp_path / "routing_weights.json")
    monkeypatch.setattr(routing_weights, "SESSIONS_DIR", tmp_path / "council_sessions")
    routing_weights._cache.update(mtime=None, doc=None)
    yield


# ── Mock provider ────────────────────────────────────────────────────────────

class MockProviderResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage = {"input_tokens": 10, "output_tokens": 20}
        self.raw = None


class MockProvider:
    """Minimal BaseProvider stand-in for unit tests."""

    def __init__(self, name: str = "mock", should_fail: bool = False, response: str = "ok"):
        self.name = name
        self.should_fail = should_fail
        self._response = response
        self.call_count = 0
        self.model = f"mock-{name}"

    async def send(self, messages, system="", **kwargs):
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError(f"{self.name} provider failed")
        return MockProviderResponse(self._response)

    async def health_check(self) -> bool:
        return not self.should_fail

    def __repr__(self):
        return f"MockProvider({self.name})"


class FailingProvider(MockProvider):
    """Fails on first N calls, then recovers."""

    def __init__(self, name: str, fail_times: int = 1, **kwargs):
        super().__init__(name, **kwargs)
        self.fail_times = fail_times

    async def send(self, messages, system="", **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError(f"{self.name} transient failure #{self.call_count}")
        return MockProviderResponse(self._response)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_provider():
    return MockProvider("primary", response="primary response")


@pytest.fixture
def failing_provider():
    return MockProvider("primary", should_fail=True)


@pytest.fixture
def mock_provider_pair():
    return (
        MockProvider("primary", response="primary response"),
        MockProvider("secondary", response="secondary response"),
    )
