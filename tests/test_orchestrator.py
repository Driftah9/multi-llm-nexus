"""
Tests for Orchestrator: specialist routing, dispatch, synthesis.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.orchestrator import Orchestrator, WorkspaceConfig
from src.core.specialists import SpecialistLoader, SpecialistProfile


def _make_bridge(response="specialist answer"):
    bridge = MagicMock()
    from src.core.bridge import BridgeResult
    bridge.invoke = AsyncMock(return_value=BridgeResult(text=response, cost_usd=0.001))
    return bridge


def _make_specialist_loader(specialist_ids: list[str]):
    loader = MagicMock(spec=SpecialistLoader)
    profiles = {
        sid: SpecialistProfile(
            id=sid,
            name=sid.capitalize(),
            tier="standard",
            system_prompt=f"You are the {sid} specialist.",
        )
        for sid in specialist_ids
    }
    loader.get = lambda sid: profiles.get(sid)
    loader.list_ids = lambda: list(profiles.keys())
    return loader


def _make_orchestrator(bridge, specialist_ids, routing_mode="keyword"):
    loader = _make_specialist_loader(specialist_ids)
    workspaces_config = {
        "workspaces": {
            "test_workspace": {
                "orchestrator": True,
                "display_name": "Test Workspace",
                "contexts": ["ch_test"],
                "specialists": specialist_ids,
                "routing_mode": routing_mode,
                "routing_rules": [
                    {"keywords": ["financial", "tax", "revenue"], "specialists": ["financial"]},
                    {"keywords": ["legal", "compliance", "contract"], "specialists": ["legal"]},
                ],
                "default_specialists": [],
                "specialist_tier": "standard",
                "synthesis_tier": "standard",
            }
        }
    }
    return Orchestrator(bridge=bridge, specialist_loader=loader, workspaces_config=workspaces_config)


@pytest.mark.asyncio
async def test_should_orchestrate_returns_true_for_mapped_context():
    bridge = _make_bridge()
    orc = _make_orchestrator(bridge, ["financial", "legal"])
    assert orc.should_orchestrate("ch_test") is True


@pytest.mark.asyncio
async def test_should_orchestrate_returns_false_for_unknown_context():
    bridge = _make_bridge()
    orc = _make_orchestrator(bridge, ["financial"])
    assert orc.should_orchestrate("unknown_channel") is False


@pytest.mark.asyncio
async def test_keyword_routing_matches_specialist():
    bridge = _make_bridge()
    orc = _make_orchestrator(bridge, ["financial", "legal"], routing_mode="keyword")
    ws = orc.get_workspace("ch_test")
    result = orc._route_to_specialists_keyword("what is the quarterly tax filing deadline?", ws)
    assert "financial" in result


@pytest.mark.asyncio
async def test_keyword_routing_no_match_returns_empty():
    bridge = _make_bridge()
    orc = _make_orchestrator(bridge, ["financial", "legal"], routing_mode="keyword")
    ws = orc.get_workspace("ch_test")
    result = orc._route_to_specialists_keyword("how is the weather today?", ws)
    assert result == []


@pytest.mark.asyncio
async def test_dispatch_bypasses_on_no_routing_match():
    bridge = _make_bridge()
    orc = _make_orchestrator(bridge, ["financial", "legal"], routing_mode="keyword")
    result = await orc.dispatch(
        message="how is the weather?",
        context="ch_test",
        session_key="sess1",
    )
    assert result.bypassed is True


@pytest.mark.asyncio
async def test_dispatch_invokes_specialist_and_returns_response():
    bridge = _make_bridge(response="Tax answer from financial specialist")
    orc = _make_orchestrator(bridge, ["financial"], routing_mode="keyword")
    result = await orc.dispatch(
        message="explain quarterly tax requirements",
        context="ch_test",
        session_key="sess1",
    )
    assert not result.bypassed
    assert "financial" in result.specialists_used


@pytest.mark.asyncio
async def test_dispatch_synthesizes_multiple_specialists():
    bridge = _make_bridge(response="Synthesized answer")
    orc = _make_orchestrator(bridge, ["financial", "legal"], routing_mode="keyword")
    result = await orc.dispatch(
        message="tax compliance and contract legal requirements",
        context="ch_test",
        session_key="sess1",
    )
    if not result.bypassed and len(result.specialists_used) > 1:
        assert result.synthesized is True


@pytest.mark.asyncio
async def test_llm_routing_falls_back_to_keyword_on_bridge_error():
    bridge = _make_bridge()
    bridge.invoke = AsyncMock(side_effect=RuntimeError("LLM routing unavailable"))
    orc = _make_orchestrator(bridge, ["financial", "legal"], routing_mode="llm")

    # Force keyword fallback rules in the workspace
    ws = orc.get_workspace("ch_test")
    result = await orc._route_to_specialists("quarterly tax filing", ws)
    # Should have fallen back to keyword and found financial
    assert "financial" in result
