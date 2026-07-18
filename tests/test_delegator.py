"""Tests for the worker delegator (D2) — fan-out to cheap workers, staging
of findings, graceful handling when no workers are eligible.

Mocks ProviderClient entirely (no network) and monkeypatches worker_pool's
candidate selection + staging's storage location so tests stay hermetic.
"""
import pytest

from src.orchestration import delegator, staging


class FakeProviderClient:
    """Stand-in for orchestration.providers.ProviderClient — no network."""

    def __init__(self, timeout=45.0, available=None, fan_out_result=None):
        self.timeout = timeout
        self._available = available or []
        self._fan_out_result = fan_out_result or {}
        self.fan_out_calls = []

    def available(self):
        return list(self._available)

    async def fan_out(self, providers, prompt, system=None):
        self.fan_out_calls.append((list(providers), prompt, system))
        return dict(self._fan_out_result)


def _install_fake_client(monkeypatch, available, fan_out_result):
    fake = FakeProviderClient(available=available, fan_out_result=fan_out_result)
    monkeypatch.setattr(delegator, "ProviderClient", lambda timeout=45.0: fake)
    return fake


@pytest.fixture(autouse=True)
def _staging_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)


async def test_delegate_uses_worker_pool_candidates(monkeypatch):
    """Candidate selection is delegated to worker_pool.worker_candidates,
    called with the client's available() list — delegator must not derive
    its own roster."""
    seen = {}

    def fake_worker_candidates(available, max_workers):
        seen["available"] = list(available)
        seen["max_workers"] = max_workers
        return ["groq", "cerebras"]

    monkeypatch.setattr(delegator, "worker_candidates", fake_worker_candidates)
    _install_fake_client(
        monkeypatch,
        available=["groq", "cerebras", "openrouter"],
        fan_out_result={"groq": "finding A", "cerebras": "finding B"},
    )

    findings = await delegator.delegate("do the thing", "research", "task-1")

    assert seen["available"] == ["groq", "cerebras", "openrouter"]
    assert seen["max_workers"] == delegator.DEFAULT_WORKER_COUNT
    assert {f["provider"] for f in findings} == {"groq", "cerebras"}


async def test_delegate_stages_findings_under_task_id(monkeypatch):
    monkeypatch.setattr(delegator, "worker_candidates", lambda available, max_workers: ["groq", "cerebras"])
    _install_fake_client(
        monkeypatch,
        available=["groq", "cerebras"],
        fan_out_result={"groq": "finding A", "cerebras": "finding B"},
    )

    findings = await delegator.delegate("summarize the logs", "ops", "task-42")

    assert len(findings) == 2
    for f in findings:
        assert set(f.keys()) == {"worker_id", "provider", "finding"}

    staged = staging.get_staged_findings("task-42")
    assert len(staged) == 2
    staged_providers = {s["provider"] for s in staged}
    assert staged_providers == {"groq", "cerebras"}
    for s in staged:
        assert s["subtask"] == "summarize the logs"


async def test_delegate_skips_failed_workers(monkeypatch):
    monkeypatch.setattr(delegator, "worker_candidates", lambda available, max_workers: ["groq", "cerebras"])
    _install_fake_client(
        monkeypatch,
        available=["groq", "cerebras"],
        fan_out_result={"groq": "good finding", "cerebras": RuntimeError("boom")},
    )

    findings = await delegator.delegate("do it", "research", "task-fail")

    assert len(findings) == 1
    assert findings[0]["provider"] == "groq"
    staged = staging.get_staged_findings("task-fail")
    assert len(staged) == 1
    assert staged[0]["provider"] == "groq"


async def test_delegate_empty_candidates_returns_empty_and_skips_staging(monkeypatch):
    monkeypatch.setattr(delegator, "worker_candidates", lambda available, max_workers: [])
    _install_fake_client(monkeypatch, available=[], fan_out_result={})

    findings = await delegator.delegate("do it", "research", "task-empty")

    assert findings == []
    assert staging.get_staged_findings("task-empty") == []


async def test_delegate_all_workers_fail_returns_empty(monkeypatch):
    monkeypatch.setattr(delegator, "worker_candidates", lambda available, max_workers: ["groq"])
    _install_fake_client(
        monkeypatch,
        available=["groq"],
        fan_out_result={"groq": RuntimeError("nope")},
    )

    findings = await delegator.delegate("do it", "research", "task-allfail")

    assert findings == []
    assert staging.get_staged_findings("task-allfail") == []
