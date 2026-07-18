"""Tests for the fixed-role council executor (Skeptic/Advocate/Verifier)."""
import pytest

from src.orchestration import council_executor as ce
from src.orchestration import council_session as cs
from src.orchestration.council_router import RoutePlan


class _Spec:
    api_key_env = None  # keyless => always "available"


class _FakeClient:
    """Deterministic stand-in for ProviderClient — no network."""
    def __init__(self, *a, **k):
        self.registry = {p: _Spec() for p in ("groq", "cerebras", "mistral", "sambanova")}

    def available(self):
        return list(self.registry.keys())

    async def complete(self, provider, prompt, system=None):
        # Echo the role (from the system prompt) so we can assert routing.
        tag = "skeptic" if "Skeptic" in (system or "") else \
              "advocate" if "Advocate" in (system or "") else \
              "verifier" if "Verifier" in (system or "") else "role"
        return f"[{provider}:{tag}] finding with no grounding tags."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ce, "ProviderClient", _FakeClient)
    # redirect council-session writes to tmp so tests don't touch real data
    monkeypatch.setattr(cs, "SESSIONS_DIR", tmp_path / "council_sessions")


@pytest.mark.asyncio
async def test_debate_mode_assigns_three_roles_and_builds_synthesis():
    plan = RoutePlan(mode="council", chairman="primary",
                     members=["groq", "cerebras", "mistral", "sambanova"])
    res = await ce.run(plan, "Is X a good idea?", defer_judge=True)
    # three distinct roles assigned to three distinct providers
    assert set(res.roles.keys()) == {"skeptic", "advocate", "verifier"}
    assert len(set(res.roles.values())) == 3
    # synthesis prompt is the debate template with all three role blocks
    assert "role-based council" in res.synthesis_prompt
    assert "**Skeptic**" in res.synthesis_prompt
    assert "**Advocate**" in res.synthesis_prompt
    assert "**Verifier**" in res.synthesis_prompt
    # top_provider is the verifier's provider
    assert res.top_provider == res.roles["verifier"]
    assert res.peer_review_skipped is True
    assert res.judge_deferred is True
    assert res.session_path  # a session file path was recorded


@pytest.mark.asyncio
async def test_review_mode_uses_findings_and_review_prompt():
    plan = RoutePlan(mode="council", chairman="primary",
                     members=["groq", "cerebras", "mistral"])
    findings = [
        {"provider": "groq", "finding": "the sky is blue"},
        {"provider": "mistral", "finding": "water is wet"},
    ]
    res = await ce.run(plan, "Validate these.", compiled_findings=findings, defer_judge=True)
    assert "reconciling a council's validation" in res.synthesis_prompt
    # findings were blinded (Response A/B), not attributed by provider name
    assert "Response A" in res.synthesis_prompt
    assert res.label_map  # provider attribution captured out-of-band


@pytest.mark.asyncio
async def test_no_members_returns_empty_fallback():
    plan = RoutePlan(mode="council", chairman="primary", members=[])
    res = await ce.run(plan, "hello", defer_judge=True)
    assert res.roles == {}
    assert res.top_provider == ""
    assert res.synthesis_prompt == "hello"  # chairman gets the raw prompt


@pytest.mark.asyncio
async def test_scope_violations_are_stripped():
    class _BuilderClient(_FakeClient):
        async def complete(self, provider, prompt, system=None):
            return "Real finding line.\nLet me implement this for you right now."

    import src.orchestration.council_executor as _ce
    orig = _ce.ProviderClient
    _ce.ProviderClient = _BuilderClient
    try:
        plan = RoutePlan(mode="council", chairman="primary",
                         members=["groq", "cerebras", "mistral"])
        res = await ce.run(plan, "q", defer_judge=True)
        # the builder line is stripped from every role's response
        assert "Let me implement" not in res.synthesis_prompt
        assert "Real finding line." in res.synthesis_prompt
    finally:
        _ce.ProviderClient = orig
