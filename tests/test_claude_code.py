"""claude_code provider — stream-json parsing + session resume backport."""
import asyncio

import pytest

from src.providers.claude_code import ClaudeCodeProvider
from src.providers.base import Message


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeStderr:
    async def read(self):
        return b""


class _FakeProc:
    """Minimal stand-in for the CLI subprocess for _read_stream."""
    def __init__(self, lines):
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()
        self.returncode = 0

    async def wait(self):
        return 0


STREAM = [
    b'{"type":"system","session_id":"sess-123"}\n',
    b'{"type":"assistant","message":{"content":[{"type":"text","text":"partial..."}]}}\n',
    b'{"type":"result","result":"final answer","session_id":"sess-123",'
    b'"model":"claude-sonnet","usage":{"input_tokens":10,"output_tokens":5}}\n',
]


@pytest.mark.asyncio
async def test_stream_parsing_captures_text_session_usage():
    p = ClaudeCodeProvider({"model": "sonnet"})
    chunks = []
    resp = await p._read_stream(_FakeProc(STREAM), on_output=lambda t: chunks.append(t))
    assert resp.content == "final answer"
    assert resp.model == "claude-sonnet"
    assert resp.usage == {"input_tokens": 10, "output_tokens": 5}
    # incremental output surfaced
    assert "partial..." in chunks
    # session_id discoverable in raw (the bridge scans raw for it to persist resume)
    assert any(isinstance(e, dict) and e.get("session_id") == "sess-123" for e in resp.raw)


@pytest.mark.asyncio
async def test_async_on_output_awaited():
    p = ClaudeCodeProvider({})
    got = []
    async def on_out(t): got.append(t)
    await p._read_stream(_FakeProc(STREAM), on_output=on_out)
    assert got and got[-1] == "partial..."


def test_resume_session_adds_resume_flag(monkeypatch):
    """When the bridge sets config['resume_session'], send() must pass --resume."""
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        raise RuntimeError("stop before real subprocess")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    p = ClaudeCodeProvider({"model": "sonnet"})
    p.config["resume_session"] = "sess-xyz"
    try:
        asyncio.run(p.send([Message(role="user", content="hi")]))
    except RuntimeError:
        pass
    assert "--resume" in captured["args"] and "sess-xyz" in captured["args"]
    assert "stream-json" in captured["args"]
