"""Agent loop (src/core/agent_loop.py) — the ToolRegistry's first real consumer.

Ported down from live claude-brain with its test shapes (2026-07-20).
"""
import asyncio

import pytest

from src.core.agent_loop import AgentRunResult, run_agent
from src.providers.base import BaseProvider, Message, ProviderResponse, ToolCall
from src.tools.definitions import ToolDef, ToolRegistry


class FakeProvider(BaseProvider):
    """Returns scripted ProviderResponses in order; records every send()."""

    def __init__(self, script, tools_ok=True):
        super().__init__({"model": "fake"})
        self.script = list(script)
        self.requests = []
        self._tools_ok = tools_ok

    async def send(self, messages, system="", tools=None):
        self.requests.append({"messages": list(messages), "system": system, "tools": tools})
        if not self.script:
            return ProviderResponse(content="fallback answer")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def supports_tools(self):
        return self._tools_ok

    async def health_check(self):
        return True


def _registry(log):
    reg = ToolRegistry()

    async def echo(text: str = "", **kw):
        log.append(text)
        return {"echoed": text}

    async def danger(**kw):
        log.append("danger-ran")
        return {"done": True}

    reg.register(ToolDef(name="echo", description="echo text",
                         parameters={"type": "object", "properties": {"text": {"type": "string"}},
                                     "required": ["text"]},
                         execute=echo))
    reg.register(ToolDef(name="danger", description="needs confirmation",
                         parameters={"type": "object", "properties": {}},
                         execute=danger, requires_confirmation=True))
    return reg


def _tc_resp(name, args, call_id="c1", content=""):
    return ProviderResponse(content=content,
                            tool_calls=[ToolCall(name=name, arguments=args, call_id=call_id)])


async def _run(provider, registry, **kw):
    return await run_agent(provider, "do the task", registry=registry, **kw)


def test_happy_path_tool_then_answer():
    log = []
    provider = FakeProvider([
        _tc_resp("echo", {"text": "hi"}),
        ProviderResponse(content="task complete", usage={"input_tokens": 5, "output_tokens": 2}),
    ])
    r = asyncio.run(_run(provider, _registry(log)))
    assert r.termination == "done" and not r.error
    assert r.text == "task complete"
    assert log == ["hi"]
    # schemas were sent, preamble present, tool reply round-tripped
    assert provider.requests[0]["tools"] and "EXACTLY the tools" in provider.requests[0]["system"]
    roles = [m.role for m in provider.requests[-1]["messages"]]
    assert roles == ["user", "assistant", "tool"]
    assert provider.requests[-1]["messages"][2].tool_results[0].call_id == "c1"


def test_requires_confirmation_fail_closed():
    log = []
    provider = FakeProvider([
        _tc_resp("danger", {}, "d1"),
        ProviderResponse(content="understood, refused"),
    ])
    r = asyncio.run(_run(provider, _registry(log)))
    assert r.termination == "done"
    assert r.tool_events[0]["refused"] is True
    assert "danger-ran" not in log  # never executed
    tool_msgs = [m for m in provider.requests[-1]["messages"] if m.role == "tool"]
    assert tool_msgs[0].tool_results[0].is_error
    assert "REFUSED" in tool_msgs[0].tool_results[0].content


def test_requires_confirmation_approved_runs():
    log = []
    provider = FakeProvider([
        _tc_resp("danger", {}, "d1"),
        ProviderResponse(content="did the dangerous thing"),
    ])

    async def approve(name, args):
        return True

    r = asyncio.run(_run(provider, _registry(log), confirm_fn=approve))
    assert r.termination == "done"
    assert log == ["danger-ran"]
    assert r.tool_events[0]["refused"] is False


def test_unknown_tool_feeds_error_back():
    log = []
    provider = FakeProvider([
        _tc_resp("teleport", {}, "t1"),
        ProviderResponse(content="ok noted"),
    ])
    r = asyncio.run(_run(provider, _registry(log)))
    assert r.termination == "done"
    assert r.tool_events[0]["error"] is True
    tool_msgs = [m for m in provider.requests[-1]["messages"] if m.role == "tool"]
    assert "Unknown tool" in tool_msgs[0].tool_results[0].content


def test_max_iters_ends_with_summary():
    log = []
    loop_resp = _tc_resp("echo", {"text": "again"})
    provider = FakeProvider([loop_resp, loop_resp, loop_resp,
                             ProviderResponse(content="summary of the loop")])
    r = asyncio.run(_run(provider, _registry(log), max_iters=3))
    assert r.termination == "max_iters"
    assert r.text == "summary of the loop"
    # summarize call carries no tools
    assert provider.requests[-1]["tools"] is None


def test_token_budget_cut():
    log = []
    provider = FakeProvider([
        ProviderResponse(content="", tool_calls=[ToolCall("echo", {"text": "x"}, "c")],
                         usage={"input_tokens": 50_000, "output_tokens": 20_000}),
        ProviderResponse(content="partial summary"),
    ])
    r = asyncio.run(_run(provider, _registry(log), token_budget=60_000))
    assert r.termination == "token_budget" and r.text == "partial summary"


def test_provider_error_is_result_not_raise():
    provider = FakeProvider([RuntimeError("429")])
    r = asyncio.run(_run(provider, _registry([])))
    assert r.error and r.termination == "error" and "429" in r.text


def test_non_tool_provider_rejected():
    provider = FakeProvider([], tools_ok=False)
    r = asyncio.run(_run(provider, _registry([])))
    assert r.error and r.termination == "error"


def test_openai_provider_serializes_agent_transcript():
    """The openai provider must round-trip assistant tool_calls + tool results."""
    from src.providers.openai import OpenAIProvider
    prov = OpenAIProvider.__new__(OpenAIProvider)  # skip __init__ (no SDK client needed)
    from src.providers.base import ToolResult
    msgs = [
        Message(role="user", content="task"),
        Message(role="assistant", content="", tool_calls=[ToolCall("echo", {"text": "hi"}, "c9")]),
        Message(role="tool", content="", tool_results=[ToolResult(call_id="c9", content="{'echoed': 'hi'}")]),
    ]
    wire = prov._convert_messages(msgs, "sys")
    assert wire[0] == {"role": "system", "content": "sys"}
    assert wire[2]["tool_calls"][0]["id"] == "c9"
    assert wire[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert wire[3] == {"role": "tool", "tool_call_id": "c9", "content": "{'echoed': 'hi'}"}
