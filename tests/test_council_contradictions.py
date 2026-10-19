"""Ported from live claude-brain with the module (2026-10-19). Tests for the council contradiction scan (orchestration/council_contradictions.py)
and its wiring through council_executor.run() → synthesis prompt + session record."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.orchestration import council_contradictions as cc
from src.orchestration import council_executor, council_session
from src.orchestration.council_router import RoutePlan

LABELS = ["Response A", "Response B", "Response C"]


# ── scan(): verdict conflicts ────────────────────────────────────────────────

def test_verdict_conflict_inline_labels():
    out = cc.scan({
        "skeptic": "**Response A: DENY** — no evidence.\n**Response B: CONFIRM**",
        "advocate": "Response A: CONFIRM\nResponse B: CONFIRM",
    }, LABELS)
    assert len(out) == 1
    c = out[0]
    assert c["kind"] == "verdict" and c["subject"] == "Response A"
    assert c["roles"]["skeptic"]["position"] == "DENY"
    assert c["roles"]["advocate"]["position"] == "CONFIRM"


def test_verdict_conflict_header_then_verdict_line_and_table():
    out = cc.scan({
        "advocate": "**Response B**\n- strong evidence\n**Verdict:** CONFIRM",
        "verifier": "| Finding | Verdict |\n|---|---|\n| B | DENY | not in code |",
    }, LABELS)
    assert [(c["kind"], c["subject"]) for c in out] == [("verdict", "Response B")]


def test_confirm_vs_flag_is_not_a_contradiction():
    out = cc.scan({
        "skeptic": "Response A: FLAG — unverified",
        "advocate": "Response A: CONFIRM",
    }, LABELS)
    assert out == []


def test_meta_and_mixed_lines_ignored():
    # Preamble listing the options, "Reasons for FLAG", "NOT CONFIRMED" — no verdicts.
    out = cc.scan({
        "skeptic": "I will mark each as CONFIRM, DENY, or FLAG.\nResponse A is NOT CONFIRMED by anything.",
        "advocate": "Response A: CONFIRM",
    }, LABELS)
    assert out == []


def test_mixed_role_verdict_is_not_a_clean_side():
    out = cc.scan({
        "skeptic": "**Response A**\n- part one: CONFIRM\n- part two: DENY",
        "advocate": "Response A: CONFIRM",
    }, LABELS)
    assert out == []


def test_labels_outside_finding_set_ignored():
    # "Response Z" isn't a real finding label → nothing to attach a verdict to.
    out = cc.scan({
        "skeptic": "Response Z: DENY",
        "advocate": "Response Z: CONFIRM",
    }, LABELS)
    assert out == []


def test_topic_keyed_verdict_conflict_without_labels():
    out = cc.scan({
        "skeptic": "- **Wyoming TCP transport**: DENY — adds latency",
        "advocate": "* **Wyoming TCP transport**: CONFIRM",
    })
    assert len(out) == 1 and out[0]["kind"] == "verdict"


def test_grounding_results_not_scanned():
    # A grep hit quoting an old session file full of verdict words must not count.
    out = cc.scan({
        "skeptic": "Response A: CONFIRM\n\n[LIVE-SYSTEM CHECK RESULTS]\n"
                   "[VERIFIED: 'x' found —\n    file.json:3: Response A: DENY]",
        "advocate": "Response A: CONFIRM",
    }, LABELS)
    assert out == []


# ── scan(): claim polarity / numeric ─────────────────────────────────────────

def test_polarity_conflict():
    out = cc.scan({
        "skeptic": "The council router fires the swarm loop on every high stakes turn.",
        "advocate": "The council router never fires the swarm loop on high stakes turns.",
    })
    assert len(out) == 1 and out[0]["kind"] == "polarity"
    assert {i["position"] for i in out[0]["roles"].values()} == {"asserts", "denies"}


def test_negation_in_other_clause_is_not_polarity_conflict():
    # Real false positive seen on live session data before the clause fix.
    out = cc.scan({
        "advocate": "The Google Family Link integration uses an unofficial reverse-engineered API.",
        "skeptic": "The Family Link integration uses an unofficial reverse-engineered API; "
                   "Google does not publish a public one.",
    })
    assert out == []


def test_numeric_conflict():
    out = cc.scan({
        "skeptic": "The gateway CLI rate cap allows 3 calls per hour for each provider.",
        "advocate": "The gateway CLI rate cap allows 20 calls per hour for each provider.",
    })
    assert len(out) == 1 and out[0]["kind"] == "numeric"


def test_single_role_and_empty_input():
    assert cc.scan({"skeptic": "Response A: DENY"}, LABELS) == []
    assert cc.scan({}, LABELS) == []
    assert cc.scan({"skeptic": "", "advocate": None}, LABELS) == []


def test_cap_and_skeptic_advocate_first():
    many = "\n".join(f"Response {l}: DENY" for l in "ABCDEFGH")
    yes = "\n".join(f"Response {l}: CONFIRM" for l in "ABCDEFGH")
    labels = [f"Response {l}" for l in "ABCDEFGH"]
    out = cc.scan({"verifier": yes, "skeptic": many, "advocate": yes}, labels)
    assert len(out) == cc.MAX_CONTRADICTIONS
    assert set(out[0]["roles"]) == {"skeptic", "advocate"}


def test_output_is_json_serialisable():
    out = cc.scan({"skeptic": "Response A: DENY", "advocate": "Response A: CONFIRM"}, LABELS)
    json.dumps(out)


# ── format_for_chairman() ────────────────────────────────────────────────────

def test_format_empty_and_nonempty():
    assert cc.format_for_chairman([]) == ""
    out = cc.scan({"skeptic": "Response A: DENY", "advocate": "Response A: CONFIRM"}, LABELS)
    block = cc.format_for_chairman(out)
    assert block.startswith("FLAGGED CONTRADICTIONS")
    assert "[Response A]" in block and "Skeptic (DENY)" in block and "Advocate (CONFIRM)" in block


# ── Wiring: council_executor.run() → synthesis prompt + session record ───────

class _FakeClient:
    """Stands in for ProviderClient: per-provider canned role answers."""
    answers = {}

    def __init__(self, timeout=60.0):
        self.registry = {p: SimpleNamespace(api_key_env=None) for p in self.answers}

    async def complete(self, provider, prompt, system=None):
        return self.answers[provider]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(council_session, "SESSIONS_DIR", tmp_path / "council_sessions")
    monkeypatch.setattr(council_executor, "ProviderClient", _FakeClient)
    # Keep the test off live state: health registry + memory recall.
    monkeypatch.setattr(council_executor.worker_health, "filter_candidates", lambda c: list(c))
    monkeypatch.setattr(council_executor.worker_health, "record_success", lambda p: None)
    monkeypatch.setattr(council_executor.worker_health, "record_failure", lambda p, e: None)
    return tmp_path


def _run(answers, findings=None):
    _FakeClient.answers = answers
    plan = RoutePlan(mode="council", chairman="claude", members=list(answers))
    return asyncio.run(council_executor.run(
        plan=plan, user_prompt="Is the swarm loop live?", defer_judge=True,
        compiled_findings=findings))


def test_run_review_mode_flags_and_records(wired):
    findings = [{"provider": "w1", "finding": "swarm is live"},
                {"provider": "w2", "finding": "swarm is flag-off"}]
    # Role assignment is first-available (no capability_map): p1→skeptic, p2→advocate, p3→verifier.
    res = _run({
        "p1": "Response A: DENY — SWARM_LOOP_ENABLED defaults to 0.\nResponse B: CONFIRM",
        "p2": "Response A: CONFIRM — it is wired.\nResponse B: CONFIRM",
        "p3": "Response A: FLAG\nResponse B: CONFIRM",
    }, findings)
    assert res.roles == {"skeptic": "p1", "advocate": "p2", "verifier": "p3"}
    assert len(res.contradictions) == 1
    assert res.contradictions[0]["subject"] == "Response A"
    assert "FLAGGED CONTRADICTIONS" in res.synthesis_prompt
    assert "resolve" in res.synthesis_prompt.lower()
    rec = json.loads(open(res.session_path).read())
    assert rec["schema_version"] == 2
    assert rec["contradictions"] == res.contradictions


def test_run_debate_mode_no_conflict_leaves_prompt_clean(wired):
    res = _run({
        "p1": "The premise lacks evidence about latency.",
        "p2": "The strongest case is that throughput improves.",
    })
    assert res.contradictions == []
    assert "FLAGGED CONTRADICTIONS" not in res.synthesis_prompt.split("The council's perspectives:")[1].split("Produce one")[0]
    rec = json.loads(open(res.session_path).read())
    assert rec["contradictions"] == []


def test_run_scan_failure_is_non_blocking(wired, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("scanner exploded")
    monkeypatch.setattr(council_executor, "scan_contradictions", boom)
    res = _run({"p1": "Response A: DENY", "p2": "Response A: CONFIRM"})
    assert res.synthesis_prompt and res.contradictions == []
