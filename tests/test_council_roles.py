"""Tests for council roles — fixed-role prompts, role assignment, and the
read-only local research broker (grep + web-lookup grounding tags).

Mirrors the live claude-brain coverage; adapted for Nexus's agnostic research
root (this repo's own tree, not a hardcoded install path) and research entry
point (src.research.research_worker.research).
"""
import asyncio

import pytest

from src.orchestration import council_roles as cr
from src.orchestration.capability_map import CapabilityMap


# ── role prompts / domain naming ─────────────────────────────────────────────

def test_role_prompt_debate_mode_default():
    for role in cr.ROLE_NAMES:
        prompt = cr.role_prompt(role)
        assert prompt == cr._ROLE_PROMPTS[role]
        assert "NO tools" in prompt


def test_role_prompt_review_mode():
    for role in cr.ROLE_NAMES:
        prompt = cr.role_prompt(role, mode="review")
        assert prompt == cr._ROLE_PROMPTS_REVIEW[role]
        assert "GROUNDING" in prompt


def test_role_domain_naming():
    assert cr.role_domain("skeptic") == "council_skeptic"
    assert cr.role_domain("advocate") == "council_advocate"
    assert cr.role_domain("verifier") == "council_verifier"


# ── assign_roles ──────────────────────────────────────────────────────────────

def test_assign_roles_gives_three_distinct_providers():
    members = ["alpha", "beta", "gamma"]
    assigned = cr.assign_roles(members)
    assert set(assigned.keys()) == set(cr.ROLE_NAMES)
    assert len(set(assigned.values())) == 3
    assert set(assigned.values()) == set(members)


def test_assign_roles_no_capability_map_falls_back_first_available():
    members = ["p1", "p2", "p3"]
    assigned = cr.assign_roles(members, capability_map=None)
    assert assigned["skeptic"] == "p1"
    assert assigned["advocate"] == "p2"
    assert assigned["verifier"] == "p3"


def test_assign_roles_fewer_members_than_roles():
    assigned = cr.assign_roles(["only_one"])
    assert assigned == {"skeptic": "only_one"}


def test_assign_roles_uses_capability_map_choose(tmp_path):
    cm = CapabilityMap(path=tmp_path / "capability_map.json")
    # Give 'beta' a strong track record as skeptic so it's exploited (rng seeded
    # to avoid the ~12% explore branch flaking the assertion).
    cm.rng = __import__("random").Random(0)
    for _ in range(6):
        cm.update(cr.role_domain("skeptic"), "beta", 1.0)
        cm.update(cr.role_domain("skeptic"), "alpha", 0.0)
    assigned = cr.assign_roles(["alpha", "beta", "gamma"], capability_map=cm)
    assert assigned["skeptic"] == "beta"
    assert len(set(assigned.values())) == 3


# ── _is_nano_tier delegation (adaptation #1) ────────────────────────────────

def test_is_nano_tier_delegates_to_worker_pool(monkeypatch):
    # council_roles imports the name directly (`from .worker_pool import
    # is_nano_tier as _wp_is_nano_tier`), so the patch target is council_roles'
    # own bound name, not the worker_pool module attribute.
    monkeypatch.setattr(cr, "_wp_is_nano_tier", lambda p: p == "nano-provider")
    assert cr._is_nano_tier("nano-provider") is True
    assert cr._is_nano_tier("other-provider") is False


def test_is_nano_tier_swallows_errors(monkeypatch):
    def _boom(p):
        raise RuntimeError("no registry")
    monkeypatch.setattr(cr, "_wp_is_nano_tier", _boom)
    assert cr._is_nano_tier("whatever") is False


def test_role_eligible_gate_demotes_failing_nano_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "_wp_is_nano_tier", lambda p: p == "nano-fail")

    cm = CapabilityMap(path=tmp_path / "capability_map.json")
    domain = cr.role_domain("skeptic")
    for _ in range(cr.ROLE_MIN_SAMPLES + 1):
        cm.update(domain, "nano-fail", 0.0)

    assert cr._role_eligible("skeptic", "nano-fail", cm) is False
    assert cr._role_eligible("skeptic", "nano-fail", None) is True  # no map => no gate
    assert cr._role_eligible("skeptic", "standard-provider", cm) is True


# ── research roots (adaptation #2) ──────────────────────────────────────────

def test_default_roots_is_repo_root_not_hardcoded_install_path():
    roots = cr._default_roots()
    assert len(roots) == 1
    root = roots[0]
    assert root.exists()
    # It's the Nexus project root — contains src/ and tests/ — not a
    # /home/claude/adapters style hardcoded live-install path.
    assert (root / "src").is_dir()
    assert (root / "tests").is_dir()
    assert "adapters" not in str(root)


def test_default_roots_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COUNCIL_RESEARCH_ROOTS", str(tmp_path))
    roots = cr._default_roots()
    assert roots == [tmp_path]


# ── extract_check_terms / extract_web_terms ─────────────────────────────────

def test_extract_check_terms_parses_and_dedupes():
    text = (
        "Some claim [CHECK: council_router.py] and another "
        "[CHECK: _get_council_members] and a repeat [CHECK: council_router.py]."
    )
    terms = cr.extract_check_terms(text)
    assert terms == ["council_router.py", "_get_council_members"]


def test_extract_check_terms_caps_at_max():
    text = " ".join(f"[CHECK: term{i}]" for i in range(10))
    terms = cr.extract_check_terms(text)
    assert len(terms) == cr._MAX_TERMS_PER_CALL


def test_extract_check_terms_empty_when_absent():
    assert cr.extract_check_terms("no markers here") == []


def test_extract_web_terms_parses_and_caps():
    text = " ".join(f"[WEB: query {i}]" for i in range(10))
    terms = cr.extract_web_terms(text)
    assert len(terms) == cr._MAX_WEB_TERMS_PER_CALL
    assert terms[0] == "query 0"


def test_extract_web_terms_empty_when_absent():
    assert cr.extract_web_terms("nothing to see") == []


# ── resolve_grounding_tags ───────────────────────────────────────────────────

def test_resolve_grounding_tags_returns_input_unchanged_when_no_tags():
    text = "Plain role response with no grounding markers at all."
    result = asyncio.run(cr.resolve_grounding_tags(text))
    assert result == text


def test_resolve_grounding_tags_appends_check_results(monkeypatch):
    async def _fake_grep(term):
        return f"[VERIFIED: {term!r} found — fake]"
    monkeypatch.setattr(cr, "_grep_term", _fake_grep)

    text = "A claim [CHECK: role_prompt]."
    result = asyncio.run(cr.resolve_grounding_tags(text))
    assert "[LIVE-SYSTEM CHECK RESULTS]" in result
    assert "role_prompt" in result


def test_resolve_grounding_tags_appends_web_results(monkeypatch):
    async def _fake_research(query):
        return f"[WEB RESULT for {query!r} — cited, verify links]\nfake summary"
    monkeypatch.setattr(cr, "_research_term", _fake_research)

    text = "An external fact [WEB: some current event]."
    result = asyncio.run(cr.resolve_grounding_tags(text))
    assert "[EXTERNAL WEB LOOKUP RESULTS]" in result
    assert "fake summary" in result


def test_verify_check_tags_is_alias_for_resolve_grounding_tags(monkeypatch):
    async def _fake_grep(term):
        return "[VERIFIED: fake]"
    monkeypatch.setattr(cr, "_grep_term", _fake_grep)

    text = "[CHECK: something]"
    result = asyncio.run(cr.verify_check_tags(text))
    assert "[LIVE-SYSTEM CHECK RESULTS]" in result


# ── _grep_term (real subprocess, read-only, against this repo) ──────────────

def test_grep_term_finds_known_string_in_repo():
    # "def role_prompt" is defined in this very module's source tree, so a
    # grep of the default roots (the Nexus repo root) must find it.
    result = asyncio.run(cr._grep_term("def role_prompt"))
    assert result.startswith("[VERIFIED:")
    assert "council_roles.py" in result


def test_grep_term_not_found_for_nonsense_string():
    import uuid
    needle = f"zzz_never_exists_{uuid.uuid4().hex}_zzz"
    result = asyncio.run(cr._grep_term(needle))
    assert result.startswith("[NOT FOUND:")


# ── _research_term (mocked — no real network) ───────────────────────────────

def test_research_term_success(monkeypatch):
    async def _fake_run_research(query, scope="general", num_results=4):
        return f"# Research: {query}\n\nSome cited summary."

    monkeypatch.setattr(
        "src.research.research_worker.research", _fake_run_research
    )
    result = asyncio.run(cr._research_term("some external fact"))
    assert result.startswith("[WEB RESULT for")
    assert "Some cited summary" in result


def test_research_term_no_result(monkeypatch):
    async def _fake_run_research(query, scope="general", num_results=4):
        return f"# Research Failed: {query}\n\nNo search results found."

    monkeypatch.setattr(
        "src.research.research_worker.research", _fake_run_research
    )
    result = asyncio.run(cr._research_term("nothing findable"))
    assert result.startswith("[WEB NO RESULT:")


def test_research_term_error_is_caught(monkeypatch):
    async def _fake_run_research(query, scope="general", num_results=4):
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "src.research.research_worker.research", _fake_run_research
    )
    result = asyncio.run(cr._research_term("anything"))
    assert result.startswith("[WEB LOOKUP ERROR:")


# ── record_role_outcome ──────────────────────────────────────────────────────

def test_record_role_outcome_updates_and_saves(tmp_path):
    cm = CapabilityMap(path=tmp_path / "capability_map.json")
    cr.record_role_outcome("verifier", "provX", True, cm)
    assert cm.score(cr.role_domain("verifier"), "provX") > 0.5
    assert cm.path.exists()  # save() wrote the file


def test_record_role_outcome_noop_without_capability_map():
    # Should not raise even though there's nothing to update.
    cr.record_role_outcome("verifier", "provX", True, None)
