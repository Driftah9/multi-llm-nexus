"""Council failover (M3): checkpoint serialization, capability gate, graceful no-store.

The Redis-backed lease/fencing/knock + checkpoint persistence logic is verified against a
live store in claude-brain (17/17 lease, 10/10 checkpoint). These tests cover the
store-INDEPENDENT behavior that must hold in Nexus: pure serialization, the activation
gate (dark at the floor), and graceful degradation when no coordination store is wired.
"""
import asyncio

import pytest

from src.core.council_checkpoint import Checkpoint, CheckpointStore
from src.core.council_lease import CouncilLease, REQUIREMENT
from src.core.council_resumer import CouncilResumer
from src.core.capability_gate import SystemCapabilities, evaluate


def test_checkpoint_roundtrip_is_lossless():
    cp = Checkpoint(
        task_id="t1", session_key="s", orchestrator="claude", fencing_token=7,
        original_message="Summarize Q2", partial_result="Drafted intro...",
        next_step="finish", provider_history=["claude", "gemini"],
        meta={"platform": "mm", "channel_id": "c1"},
    )
    back = Checkpoint.from_json(cp.to_json())
    assert back.partial_result == "Drafted intro..."     # rich state, not lossy
    assert back.fencing_token == 7
    assert back.provider_history == ["claude", "gemini"]
    assert back.meta["channel_id"] == "c1"


def test_council_requirement_is_dark_at_floor():
    floor = SystemCapabilities(capable_executors=1, shared_state=False)
    r = evaluate(REQUIREMENT, floor)
    assert not r.active  # needs >=2 executors + shared store


def test_council_lights_up_with_resources():
    grown = SystemCapabilities(capable_executors=2, shared_state=True)
    assert evaluate(REQUIREMENT, grown).active


def test_lease_graceful_without_store(monkeypatch):
    # force "no store" by making the connector return None
    monkeypatch.setattr("src.core.council_lease._connect", lambda: None)
    lease = CouncilLease("claude")
    assert lease.acquire() is None
    assert lease.renew() is False
    assert lease.holds() is False
    assert lease.is_fenced_out(1) is True   # fail-closed: can't prove leadership → don't write


def test_checkpoint_store_graceful_without_store(monkeypatch):
    monkeypatch.setattr("src.core.council_lease._connect", lambda: None)
    store = CheckpointStore()
    assert store.save(Checkpoint(task_id="x")) is False
    assert store.load("x") is None
    assert store.list_open() == []


def test_resumer_does_not_start_at_floor(monkeypatch):
    monkeypatch.setattr("src.core.council_resumer.shared_store_available", lambda: False)

    async def _invoke(p, prompt): ...
    async def _deliver(meta, text): ...
    res = CouncilResumer(_invoke, _deliver, lambda p: True, platform="mm", capable_executors=1)
    g = res.gate()
    assert not g.active
    res.start()
    assert res._task is None   # gated off → never scheduled


@pytest.mark.asyncio
async def test_resumer_gate_active_with_resources(monkeypatch):
    monkeypatch.setattr("src.core.council_resumer.shared_store_available", lambda: True)
    async def _invoke(p, prompt): ...
    async def _deliver(meta, text): ...
    res = CouncilResumer(_invoke, _deliver, lambda p: True, platform="mm", capable_executors=2)
    assert res.gate().active
