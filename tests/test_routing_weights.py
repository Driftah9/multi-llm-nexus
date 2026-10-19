"""routing_weights — derivation from telemetry + council sessions, demote-only ordering,
and the Nexus seams (ProviderChain, council). Ported from live 2026-10-19. Every ledger path is redirected to tmp; nothing live is read."""
import json
import time

import pytest

from src.core import routing_weights as rw


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "TELEMETRY_PATH", tmp_path / "tel.jsonl")
    monkeypatch.setattr(rw, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(rw, "WEIGHTS_PATH", tmp_path / "weights.json")
    monkeypatch.setattr(rw, "_known_providers", lambda: {"good", "bad", "slow", "leaky", "thin", "claude-sonnet"})
    monkeypatch.setattr(rw, "_earned_domains", lambda: {"good": ["coding"]})
    monkeypatch.delenv("ROUTING_WEIGHTS_ENABLED", raising=False)
    rw._cache.update(mtime=None, doc=None)
    (tmp_path / "sessions").mkdir()
    return tmp_path


def _tel(path, provider, n, success=True, ms=800, text_ok=True, ts=None):
    with open(path, "a") as f:
        for _ in range(n):
            f.write(json.dumps({"ts": ts or time.time(), "provider": provider, "duration_ms": ms,
                                "success": success, "output_ok": (text_ok if success else None)}) + "\n")


def _session(dirp, name, roles, failed):
    (dirp / f"council_session_{name}.json").write_text(json.dumps({"roles": roles, "failed_providers": failed}))


def test_weights_from_both_sources(_isolated):
    _tel(rw.TELEMETRY_PATH, "good", 10)
    _tel(rw.TELEMETRY_PATH, "bad", 6, success=False)
    for i in range(5):
        _session(rw.SESSIONS_DIR, f"s{i}", {"skeptic": "good"}, ["bad"])
    p = rw.derive()["providers"]
    assert p["good"]["weight"] > 0.8 and not p["good"]["demoted"]
    assert p["good"]["council"] == {"seated": 5, "failed": 0}
    assert p["good"]["domains"] == ["coding"]
    assert p["bad"]["samples"] == 11 and p["bad"]["demoted"]


def test_thin_evidence_never_demotes(_isolated):
    _tel(rw.TELEMETRY_PATH, "thin", rw.MIN_SAMPLES - 1, success=False)
    assert not rw.derive()["providers"]["thin"]["demoted"]


def test_unknown_and_stale_rows_ignored(_isolated):
    _tel(rw.TELEMETRY_PATH, "fake", 20, success=False)                      # not in catalog
    _tel(rw.TELEMETRY_PATH, "good", 20, success=False,
         ts=time.time() - (rw.WINDOW_DAYS + 1) * 86400)                      # outside window
    p = rw.derive()["providers"]
    assert "fake" not in p
    assert p["good"]["samples"] == 0          # present only via its earned domains


def test_latency_and_leak_penalties(_isolated):
    _tel(rw.TELEMETRY_PATH, "slow", 10, ms=rw.LAT_BAD_MS + 5000)
    _tel(rw.TELEMETRY_PATH, "leaky", 10, text_ok=False)
    p = rw.derive()["providers"]
    assert p["slow"]["latency_factor"] == rw.LAT_MIN_FACTOR
    assert p["slow"]["demoted"] and p["slow"]["demote_reason"] == "slow"
    assert p["leaky"]["quality"] < 0.2 and p["leaky"]["demoted"]


def test_order_demote_only_stable_with_anchor(_isolated):
    _tel(rw.TELEMETRY_PATH, "bad", 10, success=False)
    _tel(rw.TELEMETRY_PATH, "good", 10)
    rw.rebuild()
    anchor = lambda pid: pid.startswith("claude")
    # demoted slides to the end of its run but never past the Claude anchor
    assert rw.order(["bad", "good", "thin", "claude-sonnet"], is_anchor=anchor) == \
        ["good", "thin", "bad", "claude-sonnet"]
    # anchors keep their index; runs on each side reorder independently
    assert rw.order(["bad", "claude-sonnet", "bad", "good"], is_anchor=anchor) == \
        ["bad", "claude-sonnet", "good", "bad"]
    # works with a key over objects
    objs = [{"p": "bad"}, {"p": "good"}]
    assert [o["p"] for o in rw.order(objs, key=lambda o: o["p"])] == ["good", "bad"]


def test_kill_switch_and_no_evidence_are_identity(_isolated, monkeypatch):
    assert rw.order(["bad", "good"]) == ["bad", "good"]          # nothing demoted
    _tel(rw.TELEMETRY_PATH, "bad", 10, success=False)
    rw.rebuild()
    monkeypatch.setenv("ROUTING_WEIGHTS_ENABLED", "0")
    assert rw.order(["bad", "good"]) == ["bad", "good"]


def test_load_rebuilds_when_stale_and_accessors(_isolated):
    _tel(rw.TELEMETRY_PATH, "good", 10)
    assert not rw.WEIGHTS_PATH.exists()
    assert rw.weight("good") > 0.8                                # lazy first build
    assert rw.domains("good") == ["coding"] and rw.weight("nobody") is None
    assert rw.WEIGHTS_PATH.exists()


# ── Nexus wiring: ProviderChain + council ────────────────────────────────────

class _P:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.model = name, fail, name

    async def health_check(self):
        return True


def _chain(*names):
    from src.core.provider_chain import ChainConfig, ProviderChain, ProviderChainEntry
    entries = [ProviderChainEntry(provider=_P(n), priority=i + 1, tier="standard", name=n)
               for i, n in enumerate(names)]
    return ProviderChain(entries, ChainConfig(enable_health_monitoring=False))


def test_chain_select_demotes_learned_failer(_isolated):
    import asyncio
    _tel(rw.TELEMETRY_PATH, "bad", 10, success=False)
    rw.rebuild()
    ch = _chain("bad", "good")                     # bad is the configured primary
    picked = asyncio.run(ch.select_provider(tier="standard"))
    assert picked.name == "good"


def test_chain_select_unchanged_without_evidence(_isolated):
    import asyncio
    ch = _chain("bad", "good")
    assert asyncio.run(ch.select_provider(tier="standard")).name == "bad"


def test_chain_select_unchanged_when_disabled(_isolated, monkeypatch):
    import asyncio
    _tel(rw.TELEMETRY_PATH, "bad", 10, success=False)
    rw.rebuild()
    monkeypatch.setenv("ROUTING_WEIGHTS_ENABLED", "0")
    assert asyncio.run(_chain("bad", "good").select_provider(tier="standard")).name == "bad"


def test_chain_records_telemetry_for_every_attempt(monkeypatch, tmp_path):
    """Phase 1 was never wired in Nexus — the chain now feeds it (success + failure)."""
    import asyncio
    from src.core import routing_telemetry
    log = tmp_path / "tel.jsonl"
    monkeypatch.setattr(routing_telemetry, "_LOG_PATH", str(log))
    ch = _chain("flaky", "steady")
    ch.config.retry_delay = 0

    async def invoke(provider):
        if provider.name == "flaky":
            raise RuntimeError("401 Unauthorized")     # auth: advance, no same-provider retry
        return "Paris is the capital."

    ok, result, used, fallback, err = asyncio.run(ch.try_with_fallback(invoke, tier="standard"))
    assert ok and used.name == "steady" and fallback
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [(r["provider"], r["success"], r["output_ok"]) for r in rows] == \
        [("flaky", False, None), ("steady", True, True)]
    assert all(r["task_class"] == "standard" and r["duration_ms"] >= 0 for r in rows)


def test_council_members_are_reordered(_isolated, monkeypatch):
    """council_executor applies routing_weights.order() after the worker-health gate."""
    _tel(rw.TELEMETRY_PATH, "bad", 10, success=False)
    rw.rebuild()
    assert rw.order(["bad", "good", "thin"]) == ["good", "thin", "bad"]
    import inspect
    from src.orchestration import council_executor
    assert "routing_weights.order(members)" in inspect.getsource(council_executor.run)


def test_end_to_end_chain_evidence_demotes_without_explicit_paths(monkeypatch, tmp_path):
    """Writer and reader linked through the default seam (TELEMETRY_PATH=None → the
    telemetry module's own path): failures recorded by the chain become a demotion."""
    import asyncio
    from src.core import routing_telemetry
    from src.core.provider_chain import ProviderHealth
    monkeypatch.setattr(routing_telemetry, "_LOG_PATH", str(tmp_path / "t.jsonl"))
    monkeypatch.setattr(rw, "TELEMETRY_PATH", None)
    monkeypatch.setattr(rw, "_known_providers", lambda: None)
    ch = _chain("bad", "good")
    ch.config.retry_delay = 0

    async def invoke(provider):
        if provider.name == "bad":
            raise RuntimeError("401 Unauthorized")
        return "ok answer"

    async def drive():
        for _ in range(12):
            for e in ch.entries:            # reset bench so "bad" keeps being tried first
                e.health, e.cooldown_until, e.consecutive_failures = (
                    ProviderHealth.UNKNOWN, 0.0, 0)
            await ch.try_with_fallback(invoke, tier="standard")
    asyncio.run(drive())
    rw.rebuild()
    assert rw.is_demoted("bad") and not rw.is_demoted("good")
    for e in ch.entries:
        e.health, e.cooldown_until, e.consecutive_failures = (
            ProviderHealth.UNKNOWN, 0.0, 0)
    assert asyncio.run(ch.select_provider(tier="standard")).name == "good"
