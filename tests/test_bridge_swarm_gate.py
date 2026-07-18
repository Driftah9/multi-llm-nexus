"""Tests for the swarm delegation gate in bridge.invoke (convergence step 15).

Focus: the gate condition, findings synthesis, and — critically — that the gate is
INERT (no swarm import/call) when SWARM_LOOP_ENABLED is off, which is the default.
"""
import pytest

from src.core.bridge import NexusBridge, BridgeResult
from src.core.triage import TriageResult


def _triage(complexity="deep", value="critical"):
    return TriageResult(
        task_type="research", priority="high", is_command=False, command=None,
        confidence=0.9, task_value=value, capability_required="reasoning",
        estimated_complexity=complexity,
    )


# ── gate condition ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("complexity,value,expected", [
    ("deep", "critical", True),
    ("deep", "important", True),
    ("deep", "routine", False),      # not valuable enough
    ("standard", "critical", False), # not complex enough
    ("nano", "critical", False),
])
def test_swarm_task_eligible(complexity, value, expected):
    assert NexusBridge._swarm_task_eligible(_triage(complexity, value)) is expected


# ── synthesis of worker findings ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_synthesize_compiles_findings_and_routes_once(monkeypatch):
    b = NexusBridge.__new__(NexusBridge)   # bypass __init__
    b.pool_router = None
    b.chain = object()                     # truthy → chain path
    captured = {}

    async def fake_chain(prompt, session_key, tier, on_output, system, ephemeral, opc):
        captured["prompt"] = prompt
        captured["tier"] = tier
        captured["ephemeral"] = ephemeral
        return BridgeResult(text="SYNTH", provider_type="cerebras")

    b._invoke_with_chain = fake_chain
    findings = [{"provider": "groq", "finding": "alpha"},
                {"provider": "mistral", "finding": "beta"}]
    res = await b._synthesize_swarm_findings(
        "do the thing", findings, "sess1", None, "sys", None, _triage())
    assert res.text == "SYNTH"
    # both findings compiled into the synthesis prompt
    assert "alpha" in captured["prompt"] and "beta" in captured["prompt"]
    assert "do the thing" in captured["prompt"]
    assert captured["tier"] == "deep"
    assert captured["ephemeral"] is True   # one-shot, no history pollution


@pytest.mark.asyncio
async def test_synthesize_returns_none_on_empty_findings():
    b = NexusBridge.__new__(NexusBridge)
    b.pool_router = None
    b.chain = object()
    res = await b._synthesize_swarm_findings(
        "q", [{"provider": "x", "finding": ""}], "s", None, "sys", None, _triage())
    assert res is None   # None → caller falls through to normal path


# ── inertness: flag OFF (default) must skip the swarm entirely ────────────────

@pytest.mark.asyncio
async def test_gate_inert_when_flag_off(monkeypatch):
    """With SWARM_LOOP_ENABLED unset, invoke() must not touch the swarm path."""
    monkeypatch.delenv("SWARM_LOOP_ENABLED", raising=False)
    from src.orchestration import swarm_loop
    assert swarm_loop.enabled() is False   # default gate is closed

    # if the swarm were reached it would blow up — prove it isn't
    import src.orchestration.swarm_wiring as sw
    async def _boom(*a, **k):
        raise AssertionError("swarm must not run when flag is off")
    monkeypatch.setattr(sw, "run_swarm_delegation", _boom)

    b = NexusBridge.__new__(NexusBridge)
    b.pool_router = None
    b.chain = object()
    b.system_prompt = "sys"
    b._last_provider_used = {}

    async def fake_chain(*a, **k):
        return BridgeResult(text="NORMAL", provider_type="groq")
    b._invoke_with_chain = fake_chain
    # memory injector is a no-op by default; call invoke with an eligible triage
    res = await b.invoke("hard task", "sess", triage=_triage())
    assert res.text == "NORMAL"   # normal path taken, swarm never invoked
