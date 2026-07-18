"""Tests for swarm_wiring — plan coercion, worker selection, fallback contract."""
import pytest

from src.orchestration import swarm_wiring as sw


# ── JSON / step coercion (the provider-agnostic planner parser) ──────────────

def test_extract_json_plain_array():
    assert sw._extract_json('[{"subtask":"a","domain":"research"}]') == \
        [{"subtask": "a", "domain": "research"}]


def test_extract_json_strips_code_fence():
    raw = "```json\n[{\"subtask\":\"a\",\"domain\":\"x\"}]\n```"
    assert sw._extract_json(raw) == [{"subtask": "a", "domain": "x"}]


def test_coerce_steps_wrapper_object():
    obj = {"steps": [{"subtask": "do a", "domain": "research"},
                     {"task": "do b"}]}
    steps = sw._coerce_steps(obj, "general")
    assert steps[0] == {"subtask": "do a", "domain": "research"}
    assert steps[1] == {"subtask": "do b", "domain": "general"}  # alt key + default domain


def test_coerce_steps_single_object_becomes_one_step():
    steps = sw._coerce_steps({"action": "just do it"}, "general")
    assert steps == [{"subtask": "just do it", "domain": "general"}]


def test_coerce_steps_bare_strings():
    steps = sw._coerce_steps(["alpha", "beta"], "writing")
    assert steps == [{"subtask": "alpha", "domain": "writing"},
                     {"subtask": "beta", "domain": "writing"}]


def test_coerce_steps_action_plus_details_merged():
    steps = sw._coerce_steps([{"action": "compare dbs", "details": "on speed"}], "analysis")
    assert steps[0]["subtask"] == "compare dbs — on speed"


def test_add_synthesis_deps_makes_final_step_depend_on_all():
    steps = [{"subtask": "analyze a", "domain": "analysis"},
             {"subtask": "analyze b", "domain": "analysis"},
             {"subtask": "Recommend one based on the findings", "domain": "reasoning"}]
    sw._add_synthesis_deps(steps)
    assert steps[-1]["depends_on"] == [0, 1]


def test_add_synthesis_deps_noop_when_not_synthesis():
    steps = [{"subtask": "a", "domain": "x"}, {"subtask": "b", "domain": "x"}]
    sw._add_synthesis_deps(steps)
    assert "depends_on" not in steps[-1]


# ── worker candidate selection (graduation-ladder gating) ────────────────────

class _FakeCM:
    def __init__(self, proven=None, probes=None):
        self._proven = proven or []
        self._probes = probes or []

    def qualified(self, domain, pool):
        return [p for p in self._proven if p in pool]

    def probe_candidates(self, domain, pool):
        return [p for p in self._probes if p in pool]


def test_worker_fn_non_demanding_returns_full_pool(monkeypatch):
    monkeypatch.setattr(sw, "worker_candidates", lambda avail, max_workers=99: ["groq", "cerebras"])
    client = type("C", (), {"available": lambda self: ["groq", "cerebras"]})()
    fn = sw._make_worker_candidates_fn(client, capability_map=_FakeCM())
    assert fn("general") == ["groq", "cerebras"]  # non-demanding → whole pool


def test_worker_fn_demanding_restricts_to_proven(monkeypatch):
    monkeypatch.setattr(sw, "worker_candidates",
                        lambda avail, max_workers=99: ["groq", "cerebras", "mistral"])
    client = type("C", (), {"available": lambda self: ["groq", "cerebras", "mistral"]})()
    cm = _FakeCM(proven=["cerebras"])
    fn = sw._make_worker_candidates_fn(client, capability_map=cm)
    assert fn("reasoning") == ["cerebras"]  # demanding → only proven


def test_worker_fn_demanding_no_proven_falls_back_to_pool(monkeypatch):
    monkeypatch.setattr(sw, "worker_candidates",
                        lambda avail, max_workers=99: ["groq", "mistral"])
    client = type("C", (), {"available": lambda self: ["groq", "mistral"]})()
    fn = sw._make_worker_candidates_fn(client, capability_map=_FakeCM(proven=[]))
    assert set(fn("coding")) == {"groq", "mistral"}  # nobody proven → don't starve


@pytest.mark.asyncio
async def test_run_swarm_delegation_falls_back_when_no_workers(monkeypatch):
    monkeypatch.setattr(sw, "worker_candidates", lambda avail, max_workers=99: [])
    monkeypatch.setattr(sw, "ProviderClient", lambda *a, **k: type(
        "C", (), {"available": lambda self: []})())
    monkeypatch.setattr(sw, "CapabilityMap", lambda *a, **k: _FakeCM())
    out = await sw.run_swarm_delegation("do a thing", "general", "task-1")
    assert out == []  # [] signals the orchestrator to use the normal delegate


def test_make_heartbeat_fn_none_is_noop():
    assert sw.make_heartbeat_fn(None) is None
