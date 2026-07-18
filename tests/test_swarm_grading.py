"""Tests for swarm worker grading (P2.5b) — the graduation-ladder climb/sink engine."""

import asyncio

import pytest

from src.orchestration import provider_status as provider_status_mod
from src.orchestration.capability_map import CapabilityMap
from src.orchestration.provider_status import ProviderStatusStore
from src.orchestration.swarm_grading import SwarmGrader, _JUDGES


class _AlwaysSample:
    def random(self):
        return 0.0   # 0 <= SAMPLE_RATE → always grade


class _NeverSample:
    def random(self):
        return 1.0   # 1.0 > SAMPLE_RATE → always skip


def _grader_with(client, cm, rng=None):
    g = SwarmGrader(rng=rng or _AlwaysSample())
    g._client = client
    g._cm = cm
    return g


@pytest.fixture
def cm(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    return CapabilityMap(path=tmp_path / "capability_map.json")


@pytest.fixture
def status_store(tmp_path, monkeypatch):
    """Isolated provider_status store, wired in place of the module singleton
    so swarm_grading's internal `from .provider_status import get_store` call
    picks up the tmp-backed store instead of touching real data/."""
    monkeypatch.setenv("PROVIDER_STATUS_PATH", str(tmp_path / "provider_status.json"))
    store = ProviderStatusStore(path=str(tmp_path / "provider_status.json"))
    monkeypatch.setattr(provider_status_mod, "get_store", lambda: store)
    return store


# ── judge roster ─────────────────────────────────────────────────────────────

def test_default_judge_roster_uses_nexus_top_level_ids():
    # Remapped from live model-level ids to this install's top-level provider ids.
    assert _JUDGES == ["github_models", "google_gemini", "groq", "sambanova", "mistral"]


# ── grading moves the score ──────────────────────────────────────────────────

def test_grade_moves_domain_score(cm, status_store):
    class FakeClient:
        def available(self):
            return ["github_models"]

        async def complete(self, provider, prompt, system=None):
            return '{"score": 0.9, "hallucinated": false}'

    g = _grader_with(FakeClient(), cm)
    before = cm.score("analysis", "groq")
    v = asyncio.run(g.maybe_grade("analyze X", "analysis", "groq", "a solid finding"))
    assert v["score"] == 0.9
    assert cm.score("analysis", "groq") > before   # climbed
    assert cm.grade_count("analysis", "groq") == 1
    # provider_status lifecycle also updated
    s = status_store.get("groq")
    assert s.total_calls == 1
    assert s.shadow_calls == 1
    assert s.shadow_agreements == 1   # score >= 0.5 counts as agreement


def test_bad_finding_sinks_score(cm, status_store):
    class FakeClient:
        def available(self):
            return ["github_models"]

        async def complete(self, provider, prompt, system=None):
            return '{"score": 0.1, "hallucinated": true}'

    for _ in range(3):
        cm.update("analysis", "sambanova", 0.6)   # start above bar
    g = _grader_with(FakeClient(), cm)
    before = cm.score("analysis", "sambanova")
    asyncio.run(g.maybe_grade("analyze X", "analysis", "sambanova", "hand-wavy wrong finding"))
    assert cm.score("analysis", "sambanova") < before   # sank


# ── self-grade refusal ───────────────────────────────────────────────────────

def test_judge_never_grades_own_finding(cm, status_store):
    seen = []

    class FakeClient:
        def available(self):
            return ["groq", "github_models"]

        async def complete(self, provider, prompt, system=None):
            seen.append(provider)
            return '{"score": 0.7, "hallucinated": false}'

    g = _grader_with(FakeClient(), cm)
    asyncio.run(g.maybe_grade("s", "analysis", "groq", "f"))   # provider == groq
    assert "groq" not in seen   # picked a different judge


def test_no_available_judge_other_than_provider_returns_none(cm, status_store):
    class FakeClient:
        def available(self):
            return ["groq"]   # only the graded provider itself is available

        async def complete(self, *a, **k):
            raise AssertionError("should not be called — no eligible judge")

    g = _grader_with(FakeClient(), cm)
    v = asyncio.run(g.maybe_grade("s", "analysis", "groq", "f"))
    assert v is None


# ── sampling gate ────────────────────────────────────────────────────────────

def test_sampling_skips_when_over_rate(cm, status_store):
    class FakeClient:
        def available(self):
            return ["github_models"]

        async def complete(self, *a, **k):
            raise AssertionError("should not be called")

    g = _grader_with(FakeClient(), cm, rng=_NeverSample())
    assert asyncio.run(g.maybe_grade("s", "analysis", "groq", "f")) is None


# ── inflight cap ─────────────────────────────────────────────────────────────

def test_inflight_cap_skips_when_saturated(cm, status_store):
    class FakeClient:
        def available(self):
            return ["github_models"]

        async def complete(self, *a, **k):
            raise AssertionError("should not be called — inflight cap hit")

    g = _grader_with(FakeClient(), cm)
    g._inflight = 2   # matches default _MAX_INFLIGHT
    assert asyncio.run(g.maybe_grade("s", "analysis", "groq", "f")) is None


# ── malformed judge output ───────────────────────────────────────────────────

def test_unparseable_verdict_no_write(cm, status_store):
    class FakeClient:
        def available(self):
            return ["github_models"]

        async def complete(self, *a, **k):
            return "not json"

    g = _grader_with(FakeClient(), cm)
    v = asyncio.run(g.maybe_grade("s", "analysis", "groq", "f"))
    assert v is None
    assert cm.grade_count("analysis", "groq") == 0


def test_grading_exception_never_raises(cm, status_store):
    """A judge call blowing up must never propagate — fire-and-forget contract."""
    class FakeClient:
        def available(self):
            return ["github_models"]

        async def complete(self, *a, **k):
            raise RuntimeError("provider 500")

    g = _grader_with(FakeClient(), cm)
    v = asyncio.run(g.maybe_grade("s", "analysis", "groq", "f"))
    assert v is None
