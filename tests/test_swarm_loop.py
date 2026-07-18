"""Tests for the swarm loop P1 (orchestration/swarm_loop.py). No network, no Claude."""

import asyncio

import pytest

from src.orchestration import swarm_loop
from src.orchestration.swarm_loop import run_swarm, Step, enabled


@pytest.fixture
def tmp_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_loop.staging, "STAGING_DIR", tmp_path)
    return tmp_path


class FakeClient:
    """complete(provider, prompt, system=None) — matches the Nexus ProviderClient shape."""
    def __init__(self, responses=None, fail=()):
        self.responses = responses or {}
        self.fail = set(fail)
        self.calls = []

    async def complete(self, provider, prompt, system=None):
        self.calls.append((provider, prompt))
        if provider in self.fail:
            raise RuntimeError(f"{provider} boom")
        return self.responses.get(provider, f"[{provider} did: {prompt[:20]}]")


class FakeCapMap:
    """choose() picks the first candidate deterministically (no explore in tests)."""
    def __init__(self, prefer=None):
        self.prefer = prefer or {}

    def choose(self, domain, candidates):
        # honor a domain preference if present + available, else first candidate
        want = self.prefer.get(domain)
        pick = want if want in candidates else candidates[0]
        return {"provider": pick, "mode": "exploit", "score": 0.5}


def _cands(domain):
    # coding→a coding-ish roster; everything else→generic cheap roster
    return {
        "coding": ["coder-lo", "coder-hi"],
        "research": ["groq-8b", "cerebras-8b"],
    }.get(domain, ["groq-8b", "cerebras-8b"])


async def _plan_three(prompt, domain):
    return [
        {"subtask": "gather facts on X", "domain": "research"},
        {"subtask": "reason about tradeoffs", "domain": "reasoning"},
        {"subtask": "draft the summary", "domain": "writing"},
    ]


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("SWARM_LOOP_ENABLED", raising=False)
    assert enabled() is False
    monkeypatch.setenv("SWARM_LOOP_ENABLED", "1")
    assert enabled() is True


def test_happy_path_plan_route_execute_compile(tmp_staging):
    client = FakeClient()
    res = asyncio.run(run_swarm(
        "big task", "general", "t1",
        plan_fn=_plan_three, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=client,
    ))
    assert len(res.steps) == 3
    assert all(s.status == "done" for s in res.steps)
    assert len(res.findings) == 3
    assert "gather facts" in res.compiled and "draft the summary" in res.compiled
    assert res.aborted_reason == ""


def test_capability_routing_picks_by_domain(tmp_staging):
    # research steps must route to a research candidate, not a coding one
    client = FakeClient()
    capmap = FakeCapMap(prefer={"research": "cerebras-8b"})
    res = asyncio.run(run_swarm(
        "task", "general", "t2",
        plan_fn=lambda p, d: _one("gather", "research"),
        worker_candidates_fn=_cands, capability_map=capmap, client=client,
    ))
    assert res.steps[0].provider == "cerebras-8b"


def test_reroute_on_worker_failure(tmp_staging):
    # first candidate for research fails → must re-route to the second and succeed
    client = FakeClient(fail={"groq-8b"})
    res = asyncio.run(run_swarm(
        "task", "general", "t3",
        plan_fn=lambda p, d: _one("gather", "research"),
        worker_candidates_fn=_cands, capability_map=FakeCapMap(), client=client,
    ))
    step = res.steps[0]
    assert step.status == "done"
    assert step.provider == "cerebras-8b"   # rerouted off the failing groq-8b
    assert step.attempts == 2


def test_all_workers_fail_marks_step_failed(tmp_staging):
    client = FakeClient(fail={"groq-8b", "cerebras-8b"})
    res = asyncio.run(run_swarm(
        "task", "general", "t4",
        plan_fn=lambda p, d: _one("gather", "research"),
        worker_candidates_fn=_cands, capability_map=FakeCapMap(), client=client,
    ))
    assert res.steps[0].status == "failed"
    assert res.findings == []


def test_review_loop_adds_followups(tmp_staging):
    client = FakeClient()
    calls = {"n": 0}

    async def review(compiled, steps):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"done": False, "followup": [{"subtask": "verify claim", "domain": "analysis"}]}
        return {"done": True}

    res = asyncio.run(run_swarm(
        "task", "general", "t5",
        plan_fn=lambda p, d: _one("initial", "research"),
        worker_candidates_fn=_cands, capability_map=FakeCapMap(), client=client,
        review_fn=review,
    ))
    # 2 separate batches processed (initial step, then the follow-up) → review called
    # once after each batch drains: round 1 says "not done" + adds followup, round 2 says "done".
    assert res.review_iters == 2
    assert calls["n"] == 2
    assert any(s.subtask == "verify claim" for s in res.steps)
    assert len(res.findings) == 2   # original + the one follow-up, both executed


def test_review_bounded(tmp_staging):
    client = FakeClient()

    async def always_more(compiled, steps):
        return {"done": False, "followup": [{"subtask": "more", "domain": "research"}]}

    res = asyncio.run(run_swarm(
        "task", "general", "t6",
        plan_fn=lambda p, d: _one("start", "research"),
        worker_candidates_fn=_cands, capability_map=FakeCapMap(), client=client,
        review_fn=always_more,
    ))
    assert res.review_iters <= swarm_loop.MAX_REVIEW_ITERS


def test_empty_plan_aborts_clean(tmp_staging):
    client = FakeClient()
    res = asyncio.run(run_swarm(
        "task", "general", "t7",
        plan_fn=lambda p, d: _empty(), worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=client,
    ))
    assert res.aborted_reason == "empty_plan"
    assert res.findings == []


def test_plan_failure_aborts_clean(tmp_staging):
    client = FakeClient()

    async def boom(p, d):
        raise ValueError("planner down")

    res = asyncio.run(run_swarm(
        "task", "general", "t8",
        plan_fn=boom, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=client,
    ))
    assert res.aborted_reason.startswith("plan_failed")


def test_max_steps_cap(tmp_staging):
    client = FakeClient()

    async def huge_plan(p, d):
        return [{"subtask": f"s{i}", "domain": "research"} for i in range(50)]

    res = asyncio.run(run_swarm(
        "task", "general", "t9",
        plan_fn=huge_plan, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=client,
    ))
    assert len(res.steps) <= swarm_loop.MAX_STEPS


# ── P2: parallel waves + DAG + heartbeat ───────────────────────────────────────

class ConcurrencyProbe:
    """Records max simultaneous in-flight completions to prove parallelism."""
    def __init__(self):
        self.inflight = 0
        self.max_inflight = 0
        self.calls = []

    async def complete(self, provider, prompt, system=None):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        self.calls.append((provider, prompt))
        await asyncio.sleep(0.02)   # hold the slot so overlap is observable
        self.inflight -= 1
        return f"[{provider}: {prompt[:15]}]"


def test_independent_steps_run_in_one_parallel_wave(tmp_staging):
    probe = ConcurrencyProbe()

    async def plan_flat(p, d):
        return [{"subtask": f"s{i}", "domain": "research"} for i in range(3)]

    res = asyncio.run(run_swarm(
        "t", "general", "w1",
        plan_fn=plan_flat, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=probe,
    ))
    assert res.waves == 1                 # no deps → single wave
    assert probe.max_inflight == 3        # all three ran concurrently
    assert all(s.status == "done" for s in res.steps)


def test_dag_deps_serialize_into_waves(tmp_staging):
    probe = ConcurrencyProbe()

    async def plan_dag(p, d):
        # step2 depends on step0 and step1 → must run in a later wave
        return [
            {"subtask": "a", "domain": "research"},
            {"subtask": "b", "domain": "research"},
            {"subtask": "c", "domain": "reasoning", "depends_on": [0, 1]},
        ]

    res = asyncio.run(run_swarm(
        "t", "general", "w2",
        plan_fn=plan_dag, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=probe,
    ))
    assert res.waves == 2                 # {a,b} then {c}, both within ONE batch
    assert res.steps[2].status == "done"
    # c ran after a,b: c's call is the last one recorded
    assert probe.calls[-1][1] == "c"
    # no review_fn injected here → review_iters stays 0 even though 2 waves ran;
    # review is per-BATCH (post drain), not per-wave.
    assert res.review_iters == 0


def test_deadlock_deps_marked_failed_not_hung(tmp_staging):
    async def plan_bad(p, d):
        return [{"subtask": "x", "domain": "research", "depends_on": [5]}]  # dep out of range

    res = asyncio.run(run_swarm(
        "t", "general", "w3",
        plan_fn=plan_bad, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=FakeClient(),
    ))
    assert res.steps[0].status == "failed"
    assert res.findings == []


def test_heartbeat_fires_plan_work_review(tmp_staging):
    ticks = []

    async def hb(state):
        ticks.append((state["phase"], state["workers"]))

    async def review_once(compiled, steps):
        return {"done": True}

    res = asyncio.run(run_swarm(
        "t", "general", "w4",
        plan_fn=_plan_three, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=FakeClient(),
        review_fn=review_once, heartbeat_fn=hb,
    ))
    phases = [t[0] for t in ticks]
    assert "planning" in phases
    assert "working" in phases
    assert "reviewing" in phases
    # the working tick carries the wave's worker count (3 independent steps)
    work_ticks = [t for t in ticks if t[0] == "working"]
    assert work_ticks and work_ticks[0][1] == 3


def test_heartbeat_failure_never_breaks_loop(tmp_staging):
    async def bad_hb(state):
        raise RuntimeError("heartbeat down")

    res = asyncio.run(run_swarm(
        "t", "general", "w5",
        plan_fn=_plan_three, worker_candidates_fn=_cands,
        capability_map=FakeCapMap(), client=FakeClient(), heartbeat_fn=bad_hb,
    ))
    assert all(s.status == "done" for s in res.steps)   # loop completed despite hb errors


# ── tiny async plan helpers ────────────────────────────────────────────────────
async def _one(subtask, domain):
    return [{"subtask": subtask, "domain": domain}]


async def _empty():
    return []
