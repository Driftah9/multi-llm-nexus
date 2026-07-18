"""Swarm loop ↔ Nexus wiring (P1).

swarm_loop.py is pure mechanism with injected fns. THIS module supplies Nexus's
real wiring and exposes one entry point the orchestrator calls:

    findings = await run_swarm_delegation(prompt, domain, task_id)

Returns findings in the SAME shape as delegator.delegate() (provider/finding
dicts) so the orchestrator's existing compile step is unchanged. On any abort
(planner empty/failed) returns [] → the orchestrator falls back to the normal
single-shot delegate, so enabling the flag can never leave a task stranded.

Planner + reviewer run through the SCRIBE chain (Nexus nano endpoint by default,
cheap, provider-agnostic) — the whole point is to keep the expensive model out of
the grunt work. Workers come from the same nano/standard pool the delegator uses;
capability_map.choose ranks them per step domain.

# NEXUS:PORTABLE — mechanism is generic; scribe + worker pool are the install seams.
"""

from __future__ import annotations

import json
import logging
import os as _os
import random
import re
from typing import Dict, List

from . import scribe
from .capability_map import CapabilityMap, DEMANDING_DOMAINS, PROBE_RATE
from .worker_pool import worker_candidates
from .providers import ProviderClient
from . import swarm_loop

logger = logging.getLogger(__name__)
_RNG = random.Random()

_PLAN_MAX = 6   # hard cap on planner-proposed steps (swarm_loop also caps)

_PLANNER_SYSTEM = (
    "You are a task PLANNER for an AI orchestrator. Break the user's task into a "
    "list of 2-5 INDEPENDENT sub-steps a cheap worker model can each do WITHOUT "
    "tools (reasoning/research/writing/analysis only — no file or system access). "
    "Return ONLY a JSON ARRAY (top-level array, NOT an object, NOT a single step), "
    "no prose, no code fences. Each element is "
    '{"subtask": "<one concrete self-contained instruction>", "domain": '
    '"coding|research|writing|reasoning|analysis|admin|general"}.\n'
    "EXAMPLE — for 'Compare Postgres, MySQL and MongoDB on speed, cost and scale, "
    "then recommend one', return:\n"
    '[{"subtask":"Analyze query speed and latency of Postgres, MySQL and MongoDB",'
    '"domain":"analysis"},'
    '{"subtask":"Compare licensing, hosting and maintenance cost of the three",'
    '"domain":"analysis"},'
    '{"subtask":"Assess horizontal and vertical scaling of each","domain":"analysis"},'
    '{"subtask":"Recommend one database given the speed/cost/scale findings",'
    '"domain":"reasoning"}]\n'
    "If the task is genuinely atomic (cannot be usefully split), return []."
)

# Planning is a DEMANDING role: a bad plan wastes every worker call under it, so
# only a QUALIFIED reasoner may plan ([[capability-graduation-ladder]]). The floor
# is the Nexus nano tier (via scribe's "nexus" lane — cheap, one call, its own
# routing picks a capable-enough model). The fallback is NOT a blind fan-out; it is
# ONLY a free reasoner that has EARNED demanding-role standing (capability_map.
# qualified). As free reasoners prove themselves they earn planning duty; until then
# the nano floor.
# NEXUS:OPERATOR — the nano-floor lane is this install's; another install sets
#   SWARM_PLANNER_BACKENDS (scribe backend order) to whatever it trusts to plan.
_PLANNER_FLOOR_BACKENDS = (_os.environ.get("SWARM_PLANNER_BACKENDS") or "nexus").split(",")

_REVIEWER_SYSTEM = (
    "You are a task REVIEWER. Given the compiled results of worker sub-steps, decide "
    "if the task is COMPLETE or needs a few follow-up steps. Return ONLY a JSON object, "
    'no prose: {"done": true|false, "followup": [{"subtask":"...","domain":"..."}]}. '
    "Keep followups minimal (0-2). Prefer done:true unless a clear gap remains."
)


def _extract_json(raw: str):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r'(\[.*\]|\{.*\})', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# Free-tier models return the step list in many shapes. Accept them all:
#   [ {...}, ... ]                          — the requested form
#   {"steps"|"plan"|"tasks"|"subtasks": [...]}  — common wrapper
# and per-step, pull the instruction from any of these keys, the domain from any
# of these (default to the task domain).
_LIST_KEYS = ("steps", "plan", "tasks", "subtasks", "items")
_SUBTASK_KEYS = ("subtask", "task", "action", "instruction", "description", "step", "name")
_DOMAIN_KEYS = ("domain", "type", "category")


def _coerce_steps(obj, default_domain: str) -> List[Dict[str, str]]:
    # Unwrap a {"steps": [...]}-style object to its list.
    if isinstance(obj, dict):
        unwrapped = None
        for k in _LIST_KEYS:
            if isinstance(obj.get(k), list):
                unwrapped = obj[k]
                break
        if unwrapped is not None:
            obj = unwrapped
        elif any(k in obj for k in _SUBTASK_KEYS):
            # The dict IS a single step (model returned one object, not an array).
            obj = [obj]
    if not isinstance(obj, list):
        return []
    out: List[Dict[str, str]] = []
    for s in obj:
        if not isinstance(s, dict):
            # bare string step, e.g. ["do X", "do Y"]
            if isinstance(s, str) and s.strip():
                out.append({"subtask": s.strip(), "domain": default_domain})
            continue
        subtask = None
        for k in _SUBTASK_KEYS:
            v = s.get(k)
            if isinstance(v, str) and v.strip():
                subtask = v.strip()
                break
        if not subtask:
            continue
        # some models split action+details; append details for a fuller instruction
        details = s.get("details") or s.get("detail")
        if isinstance(details, str) and details.strip():
            subtask = f"{subtask} — {details.strip()}"
        dom = default_domain
        for k in _DOMAIN_KEYS:
            v = s.get(k)
            if isinstance(v, str) and v.strip():
                dom = v.strip().lower()
                break
        step = {"subtask": subtask, "domain": dom}
        deps = s.get("depends_on")
        if isinstance(deps, list):
            step["depends_on"] = [d for d in deps if isinstance(d, int)]
        out.append(step)
    return out


# Planners rarely emit depends_on, so a final "synthesize/recommend/compare all"
# step lands in the same parallel wave as the analyses and runs BLIND. Detect that
# shape and make it depend on every prior step → a proper 2-wave DAG.
_SYNTH_RE = re.compile(
    r"(?i)\b(synthesi|recommend|conclude|final|overall|based on (the|these|all)|"
    r"compare (the|all|these)|pick one|choose one|decide which|rank the)\b")


def _add_synthesis_deps(steps: List[Dict[str, str]]) -> None:
    if len(steps) < 2:
        return
    last = steps[-1]
    if last.get("depends_on"):
        return
    if _SYNTH_RE.search(last.get("subtask", "")):
        last["depends_on"] = list(range(len(steps) - 1))


async def _planner_call(prompt: str) -> tuple:
    """Get a raw plan from a QUALIFIED planner: nano floor first, then any free
    reasoner that has earned demanding-role standing. Returns (text, source)."""
    # 1. Nano floor (scribe "nexus" lane) — cheap, one call.
    text, backend = await scribe.complete_async(
        f"Task:\n{prompt}", system=_PLANNER_SYSTEM, max_tokens=500,
        backends=_PLANNER_FLOOR_BACKENDS,
    )
    if text:
        return text, backend
    # 2. Only free reasoners that have PROVEN reasoning capability may plan.
    try:
        client = ProviderClient(timeout=45.0)
        cm = CapabilityMap()
        earned = cm.qualified("reasoning", list(client.available()))
        for prov in earned:
            try:
                out = await client.complete(prov, f"Task:\n{prompt}", system=_PLANNER_SYSTEM)
                if out:
                    return out, prov
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"swarm_wiring: qualified-reasoner planner lookup failed: {e}")
    return "", ""


async def _plan_fn(prompt: str, domain: str) -> List[Dict[str, str]]:
    # One retry is cheap insurance against a flaky single-step/garbage plan.
    last_head = ""
    for attempt in (1, 2):
        text, source = await _planner_call(prompt)
        steps = _coerce_steps(_extract_json(text), domain)[:_PLAN_MAX]
        _add_synthesis_deps(steps)
        if len(steps) >= 2:
            logger.info(f"swarm_wiring: planned {len(steps)} step(s) via {source} (attempt {attempt})")
            return steps
        last_head = (text or "")[:120]
        if steps and attempt == 2:
            logger.info(f"swarm_wiring: planned 1 step via {source} (attempt {attempt})")
            return steps
    logger.info(f"swarm_wiring: no qualified planner produced a usable plan after 2 tries "
                f"(raw_head={last_head!r}) — atomic task, falling back to delegate")
    return []


async def _review_fn(compiled: str, steps) -> Dict:
    text, _ = await scribe.complete_async(
        f"Compiled results:\n{compiled}", system=_REVIEWER_SYSTEM, max_tokens=400,
    )
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return {"done": True}
    return {"done": bool(obj.get("done", True)), "followup": obj.get("followup") or []}


def _make_worker_candidates_fn(client: ProviderClient, capability_map=None):
    # Full worker pool (cheapest-first, voice-reserved, key-present) — same
    # eligibility rule as the delegator, so swarm + single-shot draw the same pool.
    pool = worker_candidates(client.available(), max_workers=99)
    cm = capability_map

    def _fn(domain: str) -> List[str]:
        # DEMANDING domains (coding/reasoning/analysis): prefer PROVEN workers. If any
        # have earned standing, restrict to them (unproven models earn on easier steps
        # first — cold-start-low). If NONE are proven yet, fall back to the full pool
        # so the domain isn't starved and unknowns still get a graded shot.
        if cm is not None and domain in DEMANDING_DOMAINS:
            proven = cm.qualified(domain, list(pool))
            if proven:
                # probe: occasionally admit a near-qualified CANDIDATE alongside the
                # proven pool so it can earn its way over the bar on a real graded step.
                probes = cm.probe_candidates(domain, [p for p in pool if p not in proven])
                if probes and _RNG.random() < PROBE_RATE:
                    chosen = probes[0]
                    logger.info(f"swarm_wiring: probing candidate {chosen} on demanding '{domain}'")
                    return proven + [chosen]
                return proven
        return list(pool)
    return _fn


def make_heartbeat_fn(heartbeat):
    """Build the swarm→heartbeat callback (P2). Generic 'Workers active (N)' while
    workers run (seated model idle = not burning tokens); phase-only ticks during
    plan/review. Returns None if no heartbeat — swarm_loop treats None as a no-op.

    NEXUS: this uses set_worker/set_phase on the heartbeat manager. If a Nexus
    HeartbeatManager exposes different method names, adapt here at step-15 wiring —
    calls are guarded with getattr so a missing method degrades to a no-op rather
    than raising inside the swarm.
    """
    if heartbeat is None:
        return None

    async def _hb(state: Dict) -> None:
        phase = state.get("phase")
        set_worker = getattr(heartbeat, "set_worker", None)
        set_phase = getattr(heartbeat, "set_phase", None)
        if phase == "working" and set_worker:
            n = int(state.get("workers", 0))
            await set_worker("Workers", f"{n} active", None)
            if set_phase:
                await set_phase("working")
        elif phase in ("planning", "reviewing") and set_phase:
            await set_phase(phase)
    return _hb


async def run_swarm_delegation(
    prompt: str, domain: str, task_id: str, *, heartbeat_fn=None,
) -> List[Dict[str, str]]:
    """Entry point for the orchestrator. Returns delegator-shaped findings, or []
    to signal 'fall back to the normal single-shot delegate'."""
    client = ProviderClient(timeout=45.0)
    cm = CapabilityMap()
    worker_fn = _make_worker_candidates_fn(client, capability_map=cm)
    if not worker_fn(domain):
        logger.info("swarm_wiring: no worker providers available — falling back")
        return []

    # Grade each worker's finding (sampled, fire-and-forget) → moves its per-domain
    # score, powering the graduation ladder. swarm_loop stays grading-agnostic.
    from .swarm_grading import grade_finding_bg

    def _grade_step(step) -> None:
        grade_finding_bg(step.subtask, step.domain, step.provider or "", step.result or "")

    result = await swarm_loop.run_swarm(
        prompt, domain, task_id,
        plan_fn=_plan_fn,
        worker_candidates_fn=worker_fn,
        capability_map=cm,
        client=client,
        review_fn=_review_fn,
        heartbeat_fn=heartbeat_fn,
        on_step_done=_grade_step,
    )
    if result.aborted_reason or not result.findings:
        logger.info(f"swarm_wiring: swarm produced no findings "
                    f"(reason={result.aborted_reason or 'none'}) — falling back")
        return []
    logger.info(f"swarm_wiring: swarm delivered {len(result.findings)} finding(s) "
                f"from {len(result.steps)} step(s)")
    return result.findings
