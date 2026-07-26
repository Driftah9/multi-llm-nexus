"""
Tests for flight_recorder: the append-only turn journal at the provider-failover
seam, and its wiring into ProviderChain.try_with_fallback().
"""
import json
import os
import time
from datetime import datetime, timezone

import pytest

from src.core import flight_recorder as fr
from src.core.provider_chain import ProviderChain, ChainConfig
from tests.conftest import MockProvider
from tests.test_provider_chain import _entry


def _read_events(base):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = base / f"{day}.jsonl"
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    """Point the recorder at an isolated, writable tmp directory for this test."""
    d = tmp_path / "flight_recorder"
    monkeypatch.setenv("FLIGHT_RECORDER_DIR", str(d))
    return d


# ── Core event log ───────────────────────────────────────────────────────────

def test_events_written_and_parseable(journal_dir):
    turn_id = fr.new_turn_id()
    fr.turn_start(turn_id, tier="standard", prompt="hello", platform="test")
    fr.attempt_start(turn_id, "primary", "mock-model")
    fr.chunk(turn_id, "primary", "partial text")
    fr.attempt_end(turn_id, "primary", ok=True, text_len=12)
    fr.turn_end(turn_id, ok=True, provider="primary", final_text="hello world")

    evs = [e for e in _read_events(journal_dir) if e["turn"] == turn_id]
    assert [e["ev"] for e in evs] == [
        "turn_start", "attempt_start", "chunk", "attempt_end", "turn_end",
    ]
    assert evs[0]["prompt"] == "hello"
    assert evs[0]["tier"] == "standard"
    assert evs[0]["platform"] == "test"
    assert evs[1]["provider"] == "primary"
    assert evs[1]["model"] == "mock-model"
    assert evs[2]["text"] == "partial text"
    assert evs[3]["ok"] is True
    assert evs[3]["text_len"] == 12
    assert evs[4]["provider"] == "primary"
    assert evs[4]["final_text"] == "hello world"
    # Every line has the common envelope.
    for e in evs:
        assert e["turn"] == turn_id
        assert isinstance(e["ts"], float)


def test_turn_start_works_with_no_context():
    """flight_meta is optional everywhere — turn_start must not require any fields."""
    turn_id = fr.new_turn_id()
    fr.turn_start(turn_id)  # must not raise


def test_field_truncation(journal_dir):
    turn_id = fr.new_turn_id()
    big = "x" * (fr._MAX_FIELD + 500)
    fr.turn_start(turn_id, prompt=big)

    evs = [e for e in _read_events(journal_dir) if e["turn"] == turn_id]
    assert "truncated" in evs[0]["prompt"]
    assert len(evs[0]["prompt"]) < len(big)


# ── Fail-open ─────────────────────────────────────────────────────────────────

def test_fail_open_on_unwritable_dir(monkeypatch, tmp_path):
    """Point FLIGHT_RECORDER_DIR at a path that can never be created (a parent
    component is a regular file, not a directory) and confirm every public
    function swallows the error instead of raising into the caller."""
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("i am a file")
    impossible = blocker / "flight_recorder"
    monkeypatch.setenv("FLIGHT_RECORDER_DIR", str(impossible))

    turn_id = fr.new_turn_id()
    fr.turn_start(turn_id, prompt="should not crash")
    fr.attempt_start(turn_id, "primary", "model")
    fr.chunk(turn_id, "primary", "text")
    fr.attempt_end(turn_id, "primary", ok=False, error="boom")
    fr.turn_end(turn_id, ok=False)

    assert not impossible.exists()


# ── Retention ─────────────────────────────────────────────────────────────────

def test_retention_pruning(journal_dir, monkeypatch):
    monkeypatch.setenv("FLIGHT_RECORDER_RETENTION_DAYS", "1")
    journal_dir.mkdir(parents=True, exist_ok=True)

    old_file = journal_dir / "2000-01-01.jsonl"
    old_file.write_text('{"ts": 1, "ev": "turn_start", "turn": "ancient"}\n')
    old_time = time.time() - 5 * 86400
    os.utime(old_file, (old_time, old_time))

    # Any write triggers the rotation/prune pass.
    fr.turn_start(fr.new_turn_id(), prompt="trigger prune")

    assert not old_file.exists()


def test_retention_keeps_recent_files(journal_dir, monkeypatch):
    monkeypatch.setenv("FLIGHT_RECORDER_RETENTION_DAYS", "14")
    journal_dir.mkdir(parents=True, exist_ok=True)

    recent_file = journal_dir / "2000-01-01.jsonl"
    recent_file.write_text('{"ts": 1, "ev": "turn_start", "turn": "recent"}\n')
    # 2 days old — well within a 14-day retention window.
    recent_time = time.time() - 2 * 86400
    os.utime(recent_file, (recent_time, recent_time))

    fr.turn_start(fr.new_turn_id(), prompt="trigger prune")

    assert recent_file.exists()


# ── ProviderChain wiring ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_try_with_fallback_records_success_turn(journal_dir):
    primary = MockProvider("primary", response="primary response")
    chain = ProviderChain(
        entries=[_entry(primary, priority=1)],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )

    async def invoke(p):
        return "result text"

    success, result, provider, fallback, error = await chain.try_with_fallback(
        invoke, flight_meta={"prompt": "do the thing", "platform": "test"}
    )
    assert success

    events = _read_events(journal_dir)
    kinds = [e["ev"] for e in events]
    assert kinds[0] == "turn_start"
    assert "attempt_start" in kinds
    assert "attempt_end" in kinds
    assert kinds[-1] == "turn_end"

    turn_start = next(e for e in events if e["ev"] == "turn_start")
    assert turn_start["prompt"] == "do the thing"
    assert turn_start["platform"] == "test"

    attempt_start = next(e for e in events if e["ev"] == "attempt_start")
    assert attempt_start["provider"] == "primary"

    attempt_end = next(e for e in events if e["ev"] == "attempt_end")
    assert attempt_end["ok"] is True
    assert attempt_end["provider"] == "primary"

    turn_end = next(e for e in events if e["ev"] == "turn_end")
    assert turn_end["ok"] is True
    assert turn_end["provider"] == "primary"


@pytest.mark.asyncio
async def test_try_with_fallback_records_all_fail_chain(journal_dir):
    failing = MockProvider("only", should_fail=True)
    chain = ProviderChain(
        entries=[_entry(failing, priority=1)],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )

    async def invoke(p):
        raise RuntimeError("always fails")

    success, result, provider, fallback, error = await chain.try_with_fallback(invoke)
    assert not success

    events = _read_events(journal_dir)
    kinds = [e["ev"] for e in events]
    assert kinds[0] == "turn_start"
    assert kinds.count("attempt_start") >= 1
    assert kinds.count("attempt_end") >= 1
    assert kinds[-1] == "turn_end"

    for e in events:
        if e["ev"] == "attempt_end":
            assert e["ok"] is False
            assert "always fails" in (e["error"] or "")

    turn_end = next(e for e in events if e["ev"] == "turn_end")
    assert turn_end["ok"] is False
    assert turn_end["provider"] is None


@pytest.mark.asyncio
async def test_try_with_fallback_works_without_flight_meta(journal_dir):
    """flight_meta is optional — the chain must behave identically when omitted."""
    primary = MockProvider("primary", response="ok")
    chain = ProviderChain(
        entries=[_entry(primary, priority=1)],
        config=ChainConfig(retry_attempts=1, enable_health_monitoring=False),
    )

    async def invoke(p):
        return "ok"

    success, result, provider, fallback, error = await chain.try_with_fallback(invoke)
    assert success
    assert result == "ok"
