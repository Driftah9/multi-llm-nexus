"""Turn-failure surfacing (src/core/turn_failure.py + AdapterBase.handle_incoming).

Ported from live claude-brain 2026-10-19: a crashed turn must never leave the placeholder
hanging on "…" / "thinking…" — it becomes a visible error and a failure record.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.adapters.adapter_base import AdapterBase
from src.core import turn_failure
from src.core.behaviors import NexusBehavior

_CFG = {}


@pytest.fixture(autouse=True)
def _records(tmp_path, monkeypatch):
    _CFG["dir"] = str(tmp_path / "config")
    monkeypatch.setattr(turn_failure, "OPEN_DIR", tmp_path / "turn_failures")
    return tmp_path / "turn_failures"


class _Sessions:
    async def mark_active(self, key):
        pass


class _Adapter(AdapterBase):
    PLATFORM_PROPS = {"max_chars": None, "markdown_support": "full",
                      "platform_name": "testchat", "debounce_ms": 0}

    def __init__(self, bridge, triage_raises=None):
        # Real NexusBehavior (review gate + routing) and real heuristic triage — only the
        # platform I/O and the bridge are faked.
        super().__init__({}, bridge, _Sessions(), NexusBehavior(_CFG["dir"]))
        self.edits, self.sent = [], []
        if triage_raises:
            async def boom(text):
                raise triage_raises
            self._triage.classify = boom

    async def send(self, channel_id, text, reply_to=None):
        self.sent.append(text)

    async def _send_placeholder(self, channel_id, tier_display, reply_to=None):
        return "ph1"

    async def _update_placeholder(self, placeholder_id, text, channel_id=None):
        self.edits.append((placeholder_id, text))


class _Bridge:
    def __init__(self, exc=None, text="the answer"):
        self.exc, self.text = exc, text

    async def invoke(self, **kw):
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text, cost_usd=0.0)


def _run(adapter, text="what is the plan?"):
    asyncio.run(adapter.handle_incoming(text, "c1", "general"))


def test_pre_dispatch_crash_replaces_placeholder_and_records(_records):
    a = _Adapter(_Bridge(), triage_raises=ValueError("triage exploded"))
    _run(a)
    assert a.edits and a.edits[-1][0] == "ph1"
    msg = a.edits[-1][1]
    assert "Turn failed" in msg and "pre-dispatch" in msg and "ValueError" in msg
    assert "no usage was spent" in msg
    rec = json.loads((_records / "turn-crash__testchat.json").read_text())
    assert rec["status"] == "FAILED" and rec["occurrences"] == 1
    assert rec["evidence"]["stage"] == "pre-dispatch" and "triage exploded" in rec["detail"]


def test_dispatch_crash_replaces_frozen_heartbeat(_records):
    a = _Adapter(_Bridge(exc=RuntimeError("provider chain blew up")))
    _run(a)
    msg = a.edits[-1][1]
    assert "Turn failed" in msg and "(dispatch)" in msg and "no usage was spent" not in msg
    assert json.loads((_records / "turn-crash__testchat.json").read_text())["evidence"]["stage"] == "dispatch"


def test_repeat_crash_bumps_occurrences_and_recovered_starts_fresh(_records):
    a = _Adapter(_Bridge(exc=RuntimeError("x")))
    _run(a)
    _run(a)
    f = _records / "turn-crash__testchat.json"
    assert json.loads(f.read_text())["occurrences"] == 2
    rec = json.loads(f.read_text())
    rec["status"] = "RECOVERED"
    f.write_text(json.dumps(rec))
    _run(a)
    new = json.loads(f.read_text())
    assert new["occurrences"] == 1 and new["previous"]["status"] == "RECOVERED"


def test_success_path_unchanged(_records):
    a = _Adapter(_Bridge(text="the answer"))
    _run(a)
    assert a.edits[-1] == ("ph1", "the answer")
    assert not _records.exists()


def test_success_path_with_code_reply_reaches_user(_records):
    """Regression: _check_review_gate compared against ReviewTrigger.NONE (no such member,
    since v0.6.0) — every successful reply raised AttributeError before it was posted."""
    a = _Adapter(_Bridge(text="Here:\n```py\nprint(1)\n```\nin `app.py`"))
    _run(a)
    assert a.edits[-1][0] == "ph1" and "print(1)" in a.edits[-1][1]
    assert not _records.exists()


def test_output_stage_crash_is_surfaced(_records, monkeypatch):
    a = _Adapter(_Bridge(text="fine"))

    def broken(message, response):
        raise KeyError("formatter")
    monkeypatch.setattr(a, "_check_review_gate", broken)
    _run(a)
    assert "Turn failed" in a.edits[-1][1] and "(output)" in a.edits[-1][1]


def test_record_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(turn_failure, "OPEN_DIR", blocker / "sub")   # mkdir under a file fails
    turn_failure.record("p", "k", "dispatch", RuntimeError("y"))    # must not raise
