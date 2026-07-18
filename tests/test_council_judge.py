"""Tests for the deferred council judge (grades Skeptic/Advocate vs. chairman
synthesis, feeds capability_map via council_roles.record_role_outcome).

Ported from claude-brain's council_judge.py. Judge roster remapped to Nexus's
top-level providers.yaml ids (no Claude/fable id in the Nexus registry).
"""

import asyncio

import pytest

from src.orchestration.capability_map import CapabilityMap
from src.orchestration.council_roles import role_domain
from src.orchestration.council_judge import (
    CouncilJudge,
    _judge_candidates,
    _pick_judge,
    _JUDGES_BY_TIER,
    _FABLE_JUDGE,
    grade_council_roles,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def cm(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    return CapabilityMap(path=tmp_path / "capability_map.json")


def _judge_with(client, cm):
    j = CouncilJudge()
    j._client = client
    j._cm = cm
    return j


class FakeClient:
    """Deterministic ProviderClient stand-in — no network."""

    def __init__(self, available_ids, responses=None, fail_ids=None):
        self._available = list(available_ids)
        self._responses = responses or {}
        self._fail = set(fail_ids or [])

    def available(self):
        return list(self._available)

    async def complete(self, provider, prompt, system=None):
        if provider in self._fail:
            raise RuntimeError(f"{provider} exploded")
        return self._responses.get(provider, '{"score": 0.9, "hallucinated": false}')


# ── judge roster ─────────────────────────────────────────────────────────────

def test_default_judge_roster_uses_nexus_top_level_ids():
    # Remapped from live model-level ids (claude-*) to this install's
    # top-level provider ids — no Claude/fable id exists in the Nexus registry.
    assert _JUDGES_BY_TIER["high"] == ["sambanova", "openrouter", "google_gemini", "cerebras", "mistral"]
    assert _JUDGES_BY_TIER["standard"] == ["github_models", "google_gemini", "groq", "mistral", "cerebras"]


def test_fable_judge_is_inert_no_op():
    # No fable/subscription tier in Nexus's OpenAI-compatible registry.
    assert not _FABLE_JUDGE
    # Even with enable_fable + high complexity, nothing is inserted since
    # _FABLE_JUDGE is falsy — the insert is guarded.
    cands = _judge_candidates(0.95, enable_fable=True, available={"sambanova", "mistral"})
    assert _FABLE_JUDGE not in cands
    assert all(c for c in cands)  # no empty-string entries either


def test_judges_by_tier_env_override(monkeypatch):
    # Re-import with env vars set to confirm the override path is wired.
    monkeypatch.setenv("COUNCIL_JUDGES_HIGH", "custom_a,custom_b")
    monkeypatch.setenv("COUNCIL_JUDGES_STANDARD", "custom_c")
    import importlib
    from src.orchestration import council_judge as cj_mod
    importlib.reload(cj_mod)
    try:
        assert cj_mod._JUDGES_BY_TIER["high"] == ["custom_a", "custom_b"]
        assert cj_mod._JUDGES_BY_TIER["standard"] == ["custom_c"]
    finally:
        monkeypatch.delenv("COUNCIL_JUDGES_HIGH", raising=False)
        monkeypatch.delenv("COUNCIL_JUDGES_STANDARD", raising=False)
        importlib.reload(cj_mod)  # restore defaults for any tests after this


# ── _judge_candidates tier + availability + failover ordering ───────────────

def test_high_tier_for_complexity_at_or_above_half():
    cands = _judge_candidates(0.5, available=set(_JUDGES_BY_TIER["high"]))
    assert cands == _JUDGES_BY_TIER["high"]
    cands_hi = _judge_candidates(0.9, available=set(_JUDGES_BY_TIER["high"]))
    assert cands_hi == _JUDGES_BY_TIER["high"]


def test_standard_tier_below_half():
    cands = _judge_candidates(0.49, available=set(_JUDGES_BY_TIER["standard"]))
    assert cands == _JUDGES_BY_TIER["standard"]
    cands_lo = _judge_candidates(0.0, available=set(_JUDGES_BY_TIER["standard"]))
    assert cands_lo == _JUDGES_BY_TIER["standard"]


def test_candidates_filtered_to_available():
    # Only "mistral" and "cerebras" of the high-tier roster are available.
    available = {"mistral", "cerebras"}
    cands = _judge_candidates(0.7, available=available)
    assert cands == ["cerebras", "mistral"]  # order preserved from roster, filtered
    assert set(cands) <= available


def test_candidates_advance_past_unavailable_first_choice():
    # sambanova (first choice) unavailable; openrouter (second) is.
    available = {"openrouter", "cerebras"}
    cands = _judge_candidates(0.8, available=available)
    assert cands[0] == "openrouter"  # first available in roster order, not sambanova
    assert isinstance(cands, list)
    assert len(cands) == 2


def test_pick_judge_back_compat_returns_first_candidate():
    available = {"groq", "mistral"}
    picked = _pick_judge(0.1, available=available)
    assert picked == "groq"  # standard tier order: github_models, google_gemini, groq, mistral, cerebras


def test_no_candidates_available_returns_empty_list():
    assert _judge_candidates(0.9, available=set()) == []
    assert _pick_judge(0.9, available=set()) is None


# ── maybe_grade: failover across judges ─────────────────────────────────────

def test_maybe_grade_advances_past_failing_judge(cm):
    # First available candidate (openrouter, high tier since complexity>=0.5)
    # fails outright; judge should fall through to the next (google_gemini).
    client = FakeClient(
        available_ids=["openrouter", "google_gemini"],
        responses={"google_gemini": '{"score": 0.8, "hallucinated": false}'},
        fail_ids={"openrouter"},
    )
    judge = _judge_with(client, cm)
    verdict = asyncio.run(judge.maybe_grade(
        question="Is X true?",
        role="skeptic",
        role_response="X has a flaw: Y",
        role_provider="groq",
        synthesis="The Chairman agreed Y was a real problem.",
        complexity=0.9,
    ))
    assert verdict == {"score": 0.8, "hallucinated": False}


def test_maybe_grade_returns_none_when_no_judge_available(cm):
    client = FakeClient(available_ids=[])
    judge = _judge_with(client, cm)
    verdict = asyncio.run(judge.maybe_grade(
        question="Is X true?",
        role="advocate",
        role_response="X is well supported",
        role_provider="groq",
        synthesis="The Chairman adopted the argument.",
    ))
    assert verdict is None


def test_maybe_grade_malformed_output_does_not_raise_and_returns_none(cm):
    client = FakeClient(
        available_ids=["github_models"],
        responses={"github_models": "not json at all, sorry"},
    )
    judge = _judge_with(client, cm)
    verdict = asyncio.run(judge.maybe_grade(
        question="Is X true?",
        role="skeptic",
        role_response="X has a flaw",
        role_provider="groq",
        synthesis="synthesis text",
        complexity=0.1,
    ))
    assert verdict is None  # unparseable -> no candidates left -> graceful None


def test_maybe_grade_records_role_outcome_updates_capability_map(cm):
    client = FakeClient(
        available_ids=["github_models"],
        responses={"github_models": '{"score": 0.9, "hallucinated": false}'},
    )
    judge = _judge_with(client, cm)
    before = cm.score(role_domain("advocate"), "groq")
    verdict = asyncio.run(judge.maybe_grade(
        question="Is X true?",
        role="advocate",
        role_response="X is well supported by Z",
        role_provider="groq",
        synthesis="The Chairman incorporated the steelman.",
        complexity=0.2,
    ))
    assert verdict["score"] == 0.9
    after = cm.score(role_domain("advocate"), "groq")
    assert after > before  # record_role_outcome() moved the EWMA


def test_maybe_grade_ignores_unknown_role(cm):
    client = FakeClient(available_ids=["github_models"])
    judge = _judge_with(client, cm)
    verdict = asyncio.run(judge.maybe_grade(
        question="Is X true?",
        role="chairman",  # not skeptic/advocate
        role_response="...",
        role_provider="groq",
        synthesis="...",
    ))
    assert verdict is None


def test_maybe_grade_missing_inputs_short_circuits(cm):
    client = FakeClient(available_ids=["github_models"])
    judge = _judge_with(client, cm)
    verdict = asyncio.run(judge.maybe_grade(
        question="", role="skeptic", role_response="x",
        role_provider="groq", synthesis="y",
    ))
    assert verdict is None


# ── grade_council_roles: fire-and-forget, both roles ────────────────────────

def test_grade_council_roles_runs_without_raising(cm, monkeypatch):
    import src.orchestration.council_judge as cj_mod

    client = FakeClient(
        available_ids=["github_models"],
        responses={"github_models": '{"score": 0.7, "hallucinated": false}'},
    )
    # Route the module-level singleton's lazy client/cm to our fakes.
    cj_mod._judge._client = client
    cj_mod._judge._cm = cm

    # Should not raise even though this exercises real asyncio.gather fan-out.
    asyncio.run(grade_council_roles(
        question="Should we ship feature X?",
        role_responses={
            "skeptic": "There's no rollback plan.",
            "advocate": "The rollback plan is documented in runbook Y.",
        },
        role_providers={"skeptic": "groq", "advocate": "mistral"},
        synthesis="The Chairman noted the rollback gap but approved shipping with monitoring.",
        complexity=0.3,
    ))

    # Both roles' EWMA rows should now exist (graded, not neutral-default-only).
    assert cm.grade_count(role_domain("skeptic"), "groq") == 1
    assert cm.grade_count(role_domain("advocate"), "mistral") == 1


def test_grade_council_roles_no_tasks_when_roles_missing(cm):
    import src.orchestration.council_judge as cj_mod
    client = FakeClient(available_ids=["github_models"])
    cj_mod._judge._client = client
    cj_mod._judge._cm = cm

    # Neither role present in role_providers -> no tasks fired, no raise.
    asyncio.run(grade_council_roles(
        question="Q",
        role_responses={"skeptic": "obj"},
        role_providers={},
        synthesis="S",
    ))
