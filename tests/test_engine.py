"""
Tests for Engine: mode transitions, message processing, queue draining.
"""
import asyncio
import pytest

from src.core.engine import Engine, EngineMode, InboundMessage
from src.core.router import Router
from src.core.session import SessionStore
from src.core.triage import Triage
from tests.conftest import MockProvider


def _make_engine(provider=None, orchestrator=None):
    p = provider or MockProvider("primary")
    router = Router(providers={"primary": p}, routing_config={"default": "primary"})
    sessions = SessionStore()
    triage = Triage(provider=None)
    config = {
        "tick_interval": 0,
        "idle_timeout": 300,
        "operator_name": "Test",
        "agent_name": "Nexus",
    }
    return Engine(
        router=router,
        session_store=sessions,
        triage=triage,
        config=config,
        orchestrator=orchestrator,
    )


def _inbound(content="hello", platform="test", channel_id="ch1"):
    return InboundMessage(
        platform=platform,
        channel_id=channel_id,
        session_id="sess1",
        user_id="user1",
        username="testuser",
        content=content,
    )


@pytest.mark.asyncio
async def test_engine_starts_in_standby():
    engine = _make_engine()
    assert engine.mode == EngineMode.STARTING


@pytest.mark.asyncio
async def test_enqueue_activates_standby_engine():
    engine = _make_engine()
    engine.mode = EngineMode.STANDBY
    engine.enqueue(_inbound())
    assert engine.mode == EngineMode.ACTIVE


@pytest.mark.asyncio
async def test_process_standard_returns_response():
    provider = MockProvider("primary", response="hello back")
    engine = _make_engine(provider=provider)

    responses = []
    engine.register_response_handler("test", lambda msg: responses.append(msg))

    msg = _inbound("hello")
    outbound = await engine._process_standard(msg)
    assert outbound is not None
    assert "hello back" in outbound.content


@pytest.mark.asyncio
async def test_process_skips_orchestrator_when_none():
    engine = _make_engine()
    assert engine.orchestrator is None

    msg = _inbound("route me")
    outbound = await engine._process(msg)
    assert outbound is not None


@pytest.mark.asyncio
async def test_mode_transition_recorded():
    engine = _make_engine()
    engine._set_mode(EngineMode.STANDBY)
    engine._set_mode(EngineMode.ACTIVE)
    assert len(engine.mode_transitions) == 2
    assert engine.mode_transitions[-1]["to"] == "active"


@pytest.mark.asyncio
async def test_tick_drains_queue():
    provider = MockProvider("primary", response="ok")
    engine = _make_engine(provider=provider)

    delivered = []
    engine.register_response_handler("test", lambda msg: delivered.append(msg))

    for i in range(3):
        engine.enqueue(_inbound(f"message {i}"))

    result = await engine._tick()
    assert result.messages_processed == 3
