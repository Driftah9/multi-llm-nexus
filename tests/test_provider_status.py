"""Provider lifecycle tracker — trust ladder, shadow council, graduation."""
import pytest

from src.orchestration.provider_status import (
    ProviderStatus,
    ProviderStatusStore,
    KNOWN_PROVIDERS,
    FRONTIER_COUNCIL,
    SHADOW_MIN_CALLS,
    SHADOW_AGREE_RATE,
)


@pytest.fixture
def store(tmp_path):
    return ProviderStatusStore(path=str(tmp_path / "provider_status.json"))


# ── lifecycle defaults ───────────────────────────────────────────────────────

def test_known_provider_defaults_to_known(store):
    provider = next(iter(KNOWN_PROVIDERS))
    s = store.get(provider)
    assert s.lifecycle == "known"
    assert s.shadow_council_active is False


def test_unknown_provider_defaults_to_unknown_with_shadow_active(store):
    s = store.get("some-brand-new-provider")
    assert s.lifecycle == "unknown"
    assert s.shadow_council_active is True


def test_frontier_council_has_no_claude():
    assert "claude" not in FRONTIER_COUNCIL
    assert "claude" not in KNOWN_PROVIDERS


def test_frontier_council_is_subset_of_expected_defaults():
    # Defaults per convergence spec: strongest deep-tier config providers.
    assert FRONTIER_COUNCIL == {"sambanova", "openrouter", "cerebras"}


# ── record_call / record_shadow accounting ──────────────────────────────────

def test_record_call_increments_total_calls(store):
    store.record_call("groq")
    store.record_call("groq")
    s = store.get("groq")
    assert s.total_calls == 2


def test_record_shadow_increments_shadow_calls_and_agreements(store):
    store.record_shadow("newcomer", agreed=True)
    store.record_shadow("newcomer", agreed=False)
    s = store.get("newcomer")
    assert s.shadow_calls == 2
    assert s.shadow_agreements == 1


def test_shadow_agree_rate(store):
    s = store.get("newcomer")
    assert s.shadow_agree_rate == 0.0
    for _ in range(3):
        store.record_shadow("newcomer", agreed=True)
    store.record_shadow("newcomer", agreed=False)
    s = store.get("newcomer")
    assert s.shadow_agree_rate == pytest.approx(0.75)


# ── graduation ───────────────────────────────────────────────────────────────

def test_graduation_eligible_requires_min_calls_and_agree_rate(store):
    s = store.get("newcomer")
    assert s.graduation_eligible is False
    for _ in range(SHADOW_MIN_CALLS - 1):
        store.record_shadow("newcomer", agreed=True)
    s = store.get("newcomer")
    # not yet enough calls
    assert s.graduation_eligible is False


def test_graduation_promotes_to_known(store):
    for _ in range(SHADOW_MIN_CALLS):
        store.record_shadow("newcomer", agreed=True)
    s = store.get("newcomer")
    assert s.lifecycle == "known"
    assert s.shadow_council_active is False
    assert "Graduated to Known" in s.notes


def test_graduation_blocked_by_low_agree_rate(store):
    # Enough calls, but agree rate below threshold — should stay unknown.
    for _ in range(SHADOW_MIN_CALLS):
        store.record_shadow("flaky", agreed=False)
    s = store.get("flaky")
    assert s.lifecycle == "unknown"
    assert s.shadow_agree_rate < SHADOW_AGREE_RATE


# ── bench ────────────────────────────────────────────────────────────────────

def test_bench_marks_provider_benched(store):
    store.bench("groq", reason="sustained 500s")
    s = store.get("groq")
    assert s.lifecycle == "benched"
    assert s.shadow_council_active is False
    assert s.benched_reason == "sustained 500s"


# ── should_shadow sampling ───────────────────────────────────────────────────

def test_should_shadow_fires_on_sample_boundary_for_unknown_provider(store):
    assert store.should_shadow("newcomer", call_index=10, sample_rate=10) is True
    assert store.should_shadow("newcomer", call_index=11, sample_rate=10) is False


def test_should_shadow_false_for_known_provider(store):
    provider = next(iter(KNOWN_PROVIDERS))
    assert store.should_shadow(provider, call_index=10, sample_rate=10) is False


def test_should_shadow_false_once_shadow_inactive(store):
    store.bench("newcomer", reason="test")
    assert store.should_shadow("newcomer", call_index=10, sample_rate=10) is False


# ── persistence ──────────────────────────────────────────────────────────────

def test_persists_across_store_instances(tmp_path):
    path = str(tmp_path / "provider_status.json")
    s1 = ProviderStatusStore(path=path)
    s1.record_call("groq")
    s1.bench("groq", reason="down")

    s2 = ProviderStatusStore(path=path)
    s = s2.get("groq")
    assert s.total_calls == 1
    assert s.lifecycle == "benched"
    assert s.benched_reason == "down"
