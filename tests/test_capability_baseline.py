"""Tests for the capability baseline (seed registry) + is_qualified = earned OR seeded."""
import json
import tempfile
from pathlib import Path

import pytest

from src.orchestration import capability_baseline as cb
from src.orchestration.capability_map import CapabilityMap, QUALIFY_BAR


@pytest.fixture
def seeded(monkeypatch):
    """Inject a controlled baseline cache (bypasses the shipped JSON)."""
    table = {
        "schema_version": 1,
        "domains": {
            "coding": {"claude-opus": 0.85, "gemini-flash": 0.66, "tinyllama": 0.2},
            "reasoning": {"github-o3": 0.8},
        },
    }
    monkeypatch.setattr(cb, "_cache", table)
    yield
    cb.reload()


# ── baseline loader ──────────────────────────────────────────────────────────

def test_baseline_score_and_none(seeded):
    assert cb.baseline_score("coding", "claude-opus") == 0.85
    assert cb.baseline_score("coding", "nobody") is None
    assert cb.baseline_score("no-domain", "claude-opus") is None


def test_is_seeded_qualified_respects_bar(seeded):
    assert cb.is_seeded_qualified("coding", "claude-opus", 0.6) is True    # >= bar
    assert cb.is_seeded_qualified("coding", "gemini-flash", 0.6) is True   # exactly above
    assert cb.is_seeded_qualified("coding", "tinyllama", 0.6) is False     # seeded-low, must earn
    assert cb.is_seeded_qualified("coding", "unknown", 0.6) is False       # not seeded


def test_seeded_providers_ranked(seeded):
    assert cb.seeded_providers("coding", 0.6) == ["claude-opus", "gemini-flash"]  # tinyllama excluded


# ── is_qualified = earned OR seeded ──────────────────────────────────────────

def test_seeded_frontier_qualifies_with_zero_grades(seeded, tmp_path):
    cm = CapabilityMap(path=tmp_path / "cap.json")
    # no grades anywhere, but the baseline vouches → pre-qualified
    assert cm.grade_count("coding", "claude-opus") == 0
    assert cm.is_qualified("coding", "claude-opus") is True
    # seeded-low local model does NOT qualify — it must earn
    assert cm.is_qualified("coding", "tinyllama") is False
    # unseeded unknown does not qualify
    assert cm.is_qualified("coding", "mystery-llm") is False


def test_earned_path_still_works(seeded, tmp_path):
    cm = CapabilityMap(path=tmp_path / "cap.json")
    # grind an unseeded provider above the bar with enough samples → earns in
    for _ in range(6):
        cm.update("coding", "mystery-llm", 0.9)
    assert cm.grade_count("coding", "mystery-llm") >= 5
    assert cm.is_qualified("coding", "mystery-llm") is True


def test_qualified_ranks_seeded_by_prior(seeded, tmp_path):
    cm = CapabilityMap(path=tmp_path / "cap.json")
    ranked = cm.qualified("coding", ["gemini-flash", "claude-opus", "tinyllama", "unknown"])
    assert ranked == ["claude-opus", "gemini-flash"]  # by baseline prior; low/unseeded dropped


def test_backward_compatible_when_no_baseline(monkeypatch, tmp_path):
    # empty baseline (no file) → is_qualified reduces to earned-only
    monkeypatch.setattr(cb, "_cache", {})
    cm = CapabilityMap(path=tmp_path / "cap.json")
    assert cm.is_qualified("coding", "claude-opus") is False   # nothing seeded, no grades
    for _ in range(6):
        cm.update("coding", "claude-opus", 0.9)
    assert cm.is_qualified("coding", "claude-opus") is True    # earns in normally


# ── operator file: both shapes in one file ───────────────────────────────────

@pytest.fixture
def layered(monkeypatch, tmp_path):
    """One operator file carrying both shapes: `domains` (flat) + `seeds` (rich, wins)."""
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps({"schema_version": 1, "domains": {
        "coding": {"claude-opus": 0.82, "tinyllama": 0.2},
        "writing": {"claude-opus": 0.88},
    }, "seeds": {
        "coding": {"claude-opus": {"score": 0.9, "confidence": 0.7, "source": "x"},
                   "no-score": {"confidence": 0.5}},
        "communication": {"groq-70b": {"score": 0.65}},
    }}))
    monkeypatch.setattr(cb, "_PATH", base)
    cb.reload()
    yield base
    cb.reload()


def test_seeds_win_over_domains_and_domains_fill_gaps(layered):
    assert cb.baseline_score("coding", "claude-opus") == 0.9        # seed overrides
    assert cb.baseline_score("coding", "tinyllama") == 0.2          # domains-only kept
    assert cb.baseline_score("writing", "claude-opus") == 0.88      # domains-only domain kept
    assert cb.baseline_score("communication", "groq-70b") == 0.65   # seed-only domain added
    assert cb.baseline_score("coding", "no-score") is None          # scoreless entry skipped


def test_corrupt_file_means_earned_only(layered):
    layered.write_text("{not json")
    cb.reload()
    assert cb.baseline_score("coding", "claude-opus") is None


# ── choose() routes on the prior until earned evidence exists ────────────────

def test_choose_uses_prior_for_ungraded(seeded, tmp_path, monkeypatch):
    import src.orchestration.capability_map as cmod
    monkeypatch.setattr(cmod, "EXPLORE_RATE", 0.0)
    cm = CapabilityMap(path=tmp_path / "cap.json")
    pick = cm.choose("coding", ["tinyllama", "gemini-flash", "claude-opus"])
    assert pick == {"provider": "claude-opus", "mode": "exploit", "score": 0.85}
    # unseeded unknown stays NEUTRAL: beats seeded-low, loses to seeded-good
    assert cm.choose("coding", ["tinyllama", "mystery"])["provider"] == "mystery"
    assert cm.choose("coding", ["gemini-flash", "mystery"])["provider"] == "gemini-flash"


def test_choose_earned_overrides_prior(seeded, tmp_path, monkeypatch):
    import src.orchestration.capability_map as cmod
    monkeypatch.setattr(cmod, "EXPLORE_RATE", 0.0)
    cm = CapabilityMap(path=tmp_path / "cap.json")
    cm.update("coding", "claude-opus", 0.0)       # one bad grade: earned 0.4 replaces prior 0.85
    pick = cm.choose("coding", ["claude-opus", "gemini-flash"])
    assert pick["provider"] == "gemini-flash"
    assert cm.score("coding", "claude-opus") == 0.4   # score() stays earned-only


def test_shipped_example_parses_but_is_not_live(monkeypatch):
    """Nexus ships only an EXAMPLE — provider ids are the operator's. The example must be
    valid input for the loader, and no real file ships (fresh install = earned-only)."""
    ex = Path(cb._DEFAULT_PATH).with_name("capability_baseline.json.example")
    assert ex.exists() and not Path(cb._DEFAULT_PATH).exists()
    monkeypatch.setattr(cb, "_PATH", ex)
    cb.reload()
    try:
        assert cb.baseline_score("coding", "example-frontier") == 0.85
        assert cb.baseline_score("analysis", "example-frontier") == 0.84   # seeds shape
        assert cb.meta()["schema_version"] == 1
    finally:
        cb.reload()
