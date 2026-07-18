"""Swarm orchestration loop — P1: plan → capability-route → execute → review → loop.

The delegator (orchestration/delegator.py) fans the SAME prompt to N cheap workers
and compiles. The swarm loop is the next evolution: the orchestrator PLANS a task
into distinct sub-steps, ROUTES each to the cheapest capability-fit worker (by
domain, via capability_map), executes, REVIEWS, and LOOPS until the plan is done.

P1 scope (this file): LINEAR plan (no parallel waves yet — that's P2), reasoning/
research nodes only (no tools — that's P3, needs the neutral tool executor). It
proves the plan→route→execute→review→loop mechanics on the existing scaffolding
(capability_map.choose, ProviderClient, staging) WITHOUT any new dependency.

Ships INERT: gated behind SWARM_LOOP_ENABLED (default "0"). The orchestrator only
calls run_swarm when the flag is on; default behavior is byte-identical to today.

Dependencies are INJECTED (plan_fn, review_fn, worker_candidates_fn, client,
capability_map) so the loop is fully unit-testable with no network and no Claude.

# NEXUS:PORTABLE — the loop is provider-agnostic; the injected fns carry the
#   install's model wiring. Design: Memory/project_swarm_orchestration_loop.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from . import staging

logger = logging.getLogger(__name__)


def enabled() -> bool:
    """Read fresh each call (like provider_blocklist) — a .env flip needs no code touch."""
    return os.environ.get("SWARM_LOOP_ENABLED", "0") == "1"


# Bounds (env-overridable). The whole point is cost control, so cap hard.
MAX_STEPS = int(os.environ.get("SWARM_MAX_STEPS", "8"))          # per plan
MAX_REVIEW_ITERS = int(os.environ.get("SWARM_MAX_REVIEW_ITERS", "2"))  # follow-up rounds
MAX_STEP_RETRIES = int(os.environ.get("SWARM_MAX_STEP_RETRIES", "2"))  # re-route attempts per step

# Domains the swarm may route (mirrors triage TRIAGE_DOMAINS). A step whose domain
# isn't here falls back to "general".
_KNOWN_DOMAINS = ("coding", "research", "writing", "reasoning", "analysis", "admin", "general")


@dataclass
class Step:
    subtask: str
    domain: str = "general"
    # P2: DAG deps — indices (into SwarmResult.steps) that must be `done` before
    # this step is ready. Empty = no deps = runs in the first parallel wave.
    depends_on: List[int] = field(default_factory=list)
    id: int = -1                   # assigned = its index in SwarmResult.steps
    # filled during execution:
    provider: Optional[str] = None
    result: Optional[str] = None
    status: str = "pending"        # pending | done | failed
    attempts: int = 0

    def __post_init__(self):
        if self.domain not in _KNOWN_DOMAINS:
            self.domain = "general"


@dataclass
class SwarmResult:
    steps: List[Step] = field(default_factory=list)
    findings: List[Dict[str, str]] = field(default_factory=list)  # delegator-shape: worker_id/provider/finding
    compiled: str = ""              # merged step results, ready for the communicator/chairman
    review_iters: int = 0
    waves: int = 0                  # P2: number of parallel execution waves run
    aborted_reason: str = ""        # empty = clean finish


# ── Injected-fn type aliases (documentation only) ──────────────────────────────
#   plan_fn(prompt, domain) -> Awaitable[list[dict]]   # [{"subtask","domain"}, ...]
#   review_fn(compiled, steps) -> Awaitable[dict]      # {"done": bool, "followup": [ {subtask,domain}... ]}
#   worker_candidates_fn(domain) -> list[str]          # capability candidates for a domain, cheapest-first
#   client.complete(provider, prompt, system=...) -> Awaitable[str]
#   capability_map.choose(domain, candidates) -> {"provider","mode","score"}
# ──────────────────────────────────────────────────────────────────────────────

_WORKER_SYSTEM = (
    "You are a WORKER executing ONE delegated sub-step for an orchestrator. "
    "You have NO tools, NO file/system access — reason from the prompt only and "
    "report your finding plainly. Do not address the end user, do not offer to do "
    "more, and never claim to have checked live state you cannot access.\n\n"
)


async def _execute_step(
    step: Step,
    *,
    worker_candidates_fn: Callable[[str], List[str]],
    capability_map,
    client,
) -> None:
    """Route one step to the capability-best worker; re-route on failure (bounded)."""
    tried: set = set()
    while step.attempts < MAX_STEP_RETRIES and step.status != "done":
        candidates = [c for c in worker_candidates_fn(step.domain) if c not in tried]
        if not candidates:
            break
        pick = capability_map.choose(step.domain, candidates)
        provider = pick["provider"]
        tried.add(provider)
        step.attempts += 1
        step.provider = provider
        try:
            text = await client.complete(provider, step.subtask, system=_WORKER_SYSTEM)
            text = (text or "").strip()
            if not text:
                logger.warning(f"swarm: step worker {provider} returned empty — re-routing")
                continue
            step.result = text
            step.status = "done"
            logger.info(f"swarm: step done via {provider} (domain={step.domain}, "
                        f"mode={pick.get('mode')}, attempt={step.attempts})")
        except Exception as e:
            logger.warning(f"swarm: step worker {provider} failed ({type(e).__name__}) — re-routing")
    if step.status != "done":
        step.status = "failed"


async def _fire_heartbeat(heartbeat_fn, phase: str, workers: int = 0) -> None:
    """Exception-safe heartbeat tick — UI must never break the loop."""
    if heartbeat_fn is None:
        return
    try:
        await heartbeat_fn({"phase": phase, "workers": workers})
    except Exception as e:
        logger.debug(f"swarm: heartbeat_fn failed (non-blocking): {e}")


def _deps_satisfied(step: Step, steps: List[Step]) -> bool:
    """True if every dependency index points at a step that is `done`."""
    for dep in step.depends_on:
        if not (0 <= dep < len(steps)) or steps[dep].status != "done":
            return False
    return True


async def _run_batch_in_waves(
    batch: List[Step],
    result: SwarmResult,
    task_id: str,
    *,
    worker_candidates_fn,
    capability_map,
    client,
    heartbeat_fn,
    on_step_done=None,
) -> None:
    """Execute a batch of steps respecting DAG deps, one PARALLEL wave at a time.

    A wave = every pending step in the batch whose deps are all done. Steps in a
    wave run concurrently (asyncio.gather). Loops until the batch is drained or a
    wave comes up empty (unmet/circular deps → mark the stragglers failed).
    """
    while True:
        pending = [s for s in batch if s.status == "pending"]
        if not pending:
            return
        wave = [s for s in pending if _deps_satisfied(s, result.steps)]
        if not wave:
            # Deadlock: remaining steps have unmet/circular deps. Don't hang.
            for s in pending:
                s.status = "failed"
            logger.warning(f"swarm: {len(pending)} step(s) had unmet deps — marked failed")
            return

        result.waves += 1
        await _fire_heartbeat(heartbeat_fn, "working", workers=len(wave))
        logger.info(f"swarm: wave {result.waves} — {len(wave)} worker(s) in parallel "
                    f"[{', '.join(s.domain for s in wave)}]")

        await asyncio.gather(*[
            _execute_step(
                s, worker_candidates_fn=worker_candidates_fn,
                capability_map=capability_map, client=client,
            ) for s in wave
        ])

        # Stage + record findings for steps that completed in this wave.
        for step in wave:
            if step.status == "done":
                worker_id = f"{step.provider}-{uuid4().hex[:8]}"
                staging.stage_finding(
                    task_id=task_id, worker_id=worker_id, subtask=step.subtask,
                    provider=step.provider or "?", finding=step.result or "",
                )
                result.findings.append({
                    "worker_id": worker_id, "provider": step.provider,
                    "finding": step.result, "subtask": step.subtask, "domain": step.domain,
                })
                # Fire-and-forget grade of this worker's finding (graduation ladder —
                # moves its per-domain score). Injected so swarm_loop stays grading-agnostic.
                if on_step_done is not None:
                    try:
                        on_step_done(step)
                    except Exception as e:
                        logger.debug(f"swarm: on_step_done failed (non-blocking): {e}")


async def run_swarm(
    prompt: str,
    domain: str,
    task_id: str,
    *,
    plan_fn: Callable[[str, str], Awaitable[List[Dict[str, str]]]],
    worker_candidates_fn: Callable[[str], List[str]],
    capability_map,
    client,
    review_fn: Optional[Callable[[str, List[Step]], Awaitable[Dict]]] = None,
    heartbeat_fn: Optional[Callable[[Dict], Awaitable[None]]] = None,
    on_step_done: Optional[Callable[[Step], None]] = None,
) -> SwarmResult:
    """Run the swarm loop for one task. Returns SwarmResult (findings shaped like
    delegator output so the orchestrator's existing compile step is unchanged).

    P2: steps execute in PARALLEL waves respecting `depends_on` DAG edges; the
    optional heartbeat_fn is ticked with {"phase","workers"} at plan/wave/review
    so the adapter can show a generic "Workers active (N)" while the seated model
    is idle, and its own name during plan/review.

    plan_fn / review_fn / heartbeat_fn carry the install's wiring (adapter injects
    real models + heartbeat; tests inject fakes). review_fn optional (absent = one
    pass). heartbeat_fn optional.
    """
    result = SwarmResult()

    # ── PLAN (seated model — heartbeat shows IT, not "workers") ───────────────
    await _fire_heartbeat(heartbeat_fn, "planning")
    try:
        raw_steps = await plan_fn(prompt, domain)
    except Exception as e:
        logger.warning(f"swarm: plan_fn failed ({type(e).__name__}) — aborting to caller fallback")
        result.aborted_reason = f"plan_failed:{type(e).__name__}"
        return result

    steps: List[Step] = []
    for s in (raw_steps or [])[:MAX_STEPS]:
        if isinstance(s, dict) and s.get("subtask"):
            step = Step(
                subtask=str(s["subtask"]),
                domain=str(s.get("domain") or domain),
                depends_on=[d for d in (s.get("depends_on") or []) if isinstance(d, int)],
            )
            step.id = len(steps)
            steps.append(step)
    if not steps:
        logger.info("swarm: planner produced no steps — aborting to caller fallback")
        result.aborted_reason = "empty_plan"
        return result
    result.steps = steps

    # ── EXECUTE (parallel waves) + REVIEW LOOP ────────────────────────────────
    review_iters = 0
    batch = list(steps)
    while batch:
        await _run_batch_in_waves(
            batch, result, task_id,
            worker_candidates_fn=worker_candidates_fn,
            capability_map=capability_map, client=client, heartbeat_fn=heartbeat_fn,
            on_step_done=on_step_done,
        )
        result.compiled = _compile(result.steps)

        # ── REVIEW (seated model again — heartbeat shows IT) ──────────────────
        if review_fn is None or review_iters >= MAX_REVIEW_ITERS:
            break
        await _fire_heartbeat(heartbeat_fn, "reviewing")
        try:
            verdict = await review_fn(result.compiled, result.steps)
        except Exception as e:
            logger.warning(f"swarm: review_fn failed ({type(e).__name__}) — finishing with current results")
            break
        review_iters += 1
        if verdict.get("done", True):
            break
        followups = verdict.get("followup") or []
        new_steps: List[Step] = []
        for f in followups:
            if isinstance(f, dict) and f.get("subtask"):
                if len(result.steps) >= MAX_STEPS:
                    break
                ns = Step(subtask=str(f["subtask"]), domain=str(f.get("domain") or domain))
                ns.id = len(result.steps)
                result.steps.append(ns)
                new_steps.append(ns)
        if not new_steps:
            break
        batch = new_steps
        logger.info(f"swarm: review round {review_iters} added {len(new_steps)} follow-up step(s)")

    result.review_iters = review_iters
    staging.clear_staging(task_id)
    _n_done = sum(1 for s in result.steps if s.status == "done")
    logger.info(f"swarm: task {task_id} complete — {_n_done}/{len(result.steps)} steps done, "
                f"{result.waves} wave(s), {review_iters} review round(s)")
    return result


def _compile(steps: List[Step]) -> str:
    """Merge completed step results into one compiled context block for the communicator."""
    done = [s for s in steps if s.status == "done" and s.result]
    if not done:
        return ""
    lines = []
    for i, s in enumerate(done, 1):
        lines.append(f"{i}. [{s.domain}] {s.subtask}\n   → {s.result}")
    return "\n".join(lines)
