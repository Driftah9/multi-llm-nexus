"""Tests for the swarm worker reachability gate (orchestration/worker_health.py).

No network. Every test writes to a tmp registry — never data/.
Records how providers fail and keeps dead ones out of the swarm's retry budget.
"""

import time

import pytest

from src.core.error_classifier import AUTH, MODEL_GONE, QUOTA, TRANSIENT
from src.orchestration import worker_health
from src.orchestration.worker_health import (
    NOT_CONFIGURED, benched, filter_candidates, is_benched,
    record_failure, record_success, _classify,
)


@pytest.fixture(autouse=True)
def tmp_registry(tmp_path, monkeypatch):
    """Isolate each test to a temporary registry file."""
    monkeypatch.setattr(worker_health, "_PATH", str(tmp_path / "worker_health.json"))


# ── classification ────────────────────────────────────────────────────────────
# The five failures actually observed on the live install 2026-09-04. Four of them
# classify UNKNOWN in core/error_classifier (it matches on text, and httpx renders
# a status as "Client error '404 Not Found' for url ..."), which is exactly why
# this module refines them locally.

@pytest.mark.parametrize("text,expected", [
    ("Client error '404 Not Found' for url 'https://api.groq.com/openai/v1/chat/completions'",
     MODEL_GONE),
    ("Client error '410 Gone' for url 'https://integrate.api.nvidia.com/v1/chat/completions'",
     MODEL_GONE),
    ("Client error '403 Forbidden' for url 'https://api.mistral.ai/v1/chat/completions'",
     AUTH),
    ("openai-gpt4o: all credentials exhausted (last error: [Errno -2] Name or service not known)",
     NOT_CONFIGURED),
    ("ollama-qwen: no credentials available", NOT_CONFIGURED),
    ("Server error '503 Service Unavailable' for url 'https://x/y'", TRANSIENT),
    ("Client error '429 Too Many Requests' for url 'https://x/y'", TRANSIENT),
    ("Client error '402 Payment Required' for url 'https://x/y'", QUOTA),
])
def test_classifies_real_worker_failures(text, expected):
    """Verify classification of actual failure texts seen in production."""
    assert record_failure("w1", text) == expected
    record_success("w1")


def test_classifies_bare_http_status_codes():
    """HTTP status codes embedded in error text are classified correctly."""
    # 5xx → TRANSIENT
    assert record_failure("w1", "got a 502") == TRANSIENT
    # 400 → BAD_REQUEST (malformed payload)
    assert record_failure("w2", "got a 400") == "bad_request"
    # 404/410 → MODEL_GONE
    assert record_failure("w3", "got a 404") == MODEL_GONE
    # 402 → QUOTA
    assert record_failure("w4", "got a 402") == QUOTA


# ── benching policy ───────────────────────────────────────────────────────────

def test_deterministic_failures_bench_on_first_observation():
    """A retired model or a rejected key will not fix itself on attempt two —
    spending a second attempt to confirm is the exact waste this gate exists for."""
    record_failure("dead", "Client error '404 Not Found' for url 'https://x/y'")
    assert is_benched("dead")


def test_auth_failure_benches_immediately():
    """Auth failures (bad/expired key) are deterministic — one fail is enough."""
    record_failure("auth_fail", "Client error '403 Forbidden' for url 'https://x/y'")
    assert is_benched("auth_fail")


def test_transient_failure_needs_two_consecutive_before_benching():
    """One blip must not cost a healthy provider its place in the rotation."""
    record_failure("flaky", "Server error '503 Service Unavailable' for url 'https://x/y'")
    assert not is_benched("flaky")
    record_failure("flaky", "Server error '503 Service Unavailable' for url 'https://x/y'")
    assert is_benched("flaky")


def test_success_between_failures_resets_the_streak():
    """A single success clears the consecutive-failure counter."""
    record_failure("flaky", "connection reset")
    record_success("flaky")
    record_failure("flaky", "connection reset")
    assert not is_benched("flaky")


def test_success_clears_an_existing_bench():
    """Traffic-driven recovery: one success means try this worker again."""
    record_failure("dead", "Client error '404 Not Found' for url 'https://x/y'")
    assert is_benched("dead")
    record_success("dead")
    assert not is_benched("dead")


def test_bench_expires_so_a_recovered_provider_returns():
    """After the cooldown passes, a benched worker becomes eligible again."""
    record_failure("dead", "Client error '404 Not Found' for url 'https://x/y'")
    assert is_benched("dead")
    # Jump 25 hours (MODEL_GONE cooldown is 24h)
    assert not is_benched("dead", now=time.time() + 25 * 3600)


def test_cooldown_length_tracks_the_failure_class():
    """A decommissioned model is benched far longer than a 503 — the whole point
    of classifying rather than applying one flat timeout."""
    record_failure("gone", "Client error '410 Gone' for url 'https://x/y'")
    record_failure("blip", "Server error '502 Bad Gateway' for url 'https://x/y'")
    record_failure("blip", "Server error '502 Bad Gateway' for url 'https://x/y'")
    rows = {r["worker"]: r for r in benched()}
    assert rows["gone"]["benched_until"] > rows["blip"]["benched_until"]


def test_repeated_failures_back_off_the_retry_cadence():
    """After 3+ consecutive failures, cooldown doubles to avoid pointless re-probes."""
    for _ in range(3):
        record_failure("blip", "connection reset")
    early = benched()[0]["benched_until"]
    for _ in range(4):
        record_failure("blip", "connection reset")
    assert benched()[0]["benched_until"] > early


def test_not_configured_benches_immediately():
    """Missing credentials are benched long — only the operator can fix it."""
    record_failure("no_key", "no credentials available")
    assert is_benched("no_key")
    rows = {r["worker"]: r for r in benched()}
    # 24h cooldown
    assert rows["no_key"]["benched_until"] > time.time() + 23 * 3600


# ── candidate filtering ───────────────────────────────────────────────────────

def test_filter_drops_benched_candidates():
    """Benched workers are skipped during selection."""
    record_failure("dead", "Client error '404 Not Found' for url 'https://x/y'")
    assert filter_candidates(["dead", "live"]) == ["live"]


def test_filter_preserves_candidate_order():
    """Filtered list maintains the original ordering of live candidates."""
    record_failure("c", "Client error '404 Not Found' for url 'https://x/y'")
    assert filter_candidates(["a", "b", "c", "d"]) == ["a", "b", "d"]


def test_filter_fails_open_when_everything_is_benched():
    """A health gate must never be the reason a task has no workers.

    This is the fail-open invariant: if all candidates are benched, return them
    anyway rather than blocking the swarm. The swarm's MAX_STEP_RETRIES handles
    the loss of those attempts — at least the task gets tried, not silently skipped.
    """
    record_failure("d1", "Client error '404 Not Found' for url 'https://x/y'")
    record_failure("d2", "Client error '403 Forbidden' for url 'https://x/y'")
    assert filter_candidates(["d1", "d2"]) == ["d1", "d2"]


def test_filter_handles_empty_input():
    """Empty candidate list is returned empty."""
    assert filter_candidates([]) == []


def test_filter_passes_through_all_if_none_benched():
    """When no candidates are benched, all are returned."""
    assert filter_candidates(["a", "b", "c"]) == ["a", "b", "c"]


def test_filter_partial_benching():
    """Typical case: some workers are benched, some are live."""
    record_failure("dead1", "Client error '404 Not Found' for url 'https://x/y'")
    record_failure("dead2", "Client error '403 Forbidden' for url 'https://x/y'")
    result = filter_candidates(["dead1", "live1", "dead2", "live2"])
    assert result == ["live1", "live2"]


# ── the regression this gate was built for ──────────────────────────────────────

def test_regression_dead_top_ranked_workers_dont_starve_healthy_ones():
    """Live measurement 2026-09-04: 10-provider pool, 1 reachable (gemini-flash);
    cerebras-qwen3 and groq-70b had 89 and 75 consecutive failures with zero
    successes and were still being picked first. Without the health gate, swarm
    step completion decayed 100% → 0/11.

    This test simulates: two top-ranked providers both dead, pool of 5 total,
    health gate drops the dead ones, leaving 3 live workers.
    """
    # Bench two top-ranked workers (simulating their 89/75 failure history)
    for _ in range(5):
        record_failure("cerebras-qwen3", "404 Not Found")
    for _ in range(5):
        record_failure("groq-70b", "403 Forbidden")

    # The pool is ranked [cerebras-qwen3, groq-70b, gemini-flash, openai-gpt4o, ollama-local]
    pool = ["cerebras-qwen3", "groq-70b", "gemini-flash", "openai-gpt4o", "ollama-local"]

    # Without the gate, swarm would pick the two benched workers first and exhaust
    # MAX_STEP_RETRIES without trying the healthy ones. With the gate, they're
    # filtered out and the healthy workers get picked.
    filtered = filter_candidates(pool)
    assert "cerebras-qwen3" not in filtered
    assert "groq-70b" not in filtered
    assert len(filtered) == 3
