"""Council roles — fixed-role prompts, role assignment, and a read-only local
research broker for the Verifier.

Replaces the old "N identical members, peer-rank them" tournament with a small
fixed cast (Skeptic / Advocate / Verifier; Chair is the separate synthesis step
in council_executor.run() / adapter_base). Roles aren't interchangeable, so
there is no ranking stage here — each role does a different job and the
chairman reads all of them.

The research broker is grep-only, fixed-string, args-list subprocess (no
shell=True, no interpolation of model-generated text into a shell command) —
council members still cannot write or edit anything. It only ever reads from
an explicit root allowlist.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from .worker_pool import is_nano_tier as _wp_is_nano_tier

logger = logging.getLogger(__name__)

# Council members are remote API models with NO tools, NO filesystem/system
# access, and NO memory of prior turns — same fact the solo non-Claude path
# already has to tell providers (core/provider_bridge.py _TOOL_FREE_PREAMBLE).
# Without this, models role-play having checked the live system and fabricate
# results. This is prepended to every role prompt.
_NO_TOOLS_PREAMBLE = (
    "IMPORTANT — YOUR ACTUAL CAPABILITIES: you are a stateless language model "
    "with NO tools, NO file or system access, NO ability to run commands, and "
    "NO way to check the live system directly. You only know what is in this "
    "prompt. NEVER claim to have checked, verified, or confirmed something "
    "against the live system — you cannot. If a claim needs that kind of "
    "check, mark it `[CHECK: <short search term>]` instead of asserting it, "
    "and someone else will look it up for you.\n\n"
)

_ROLE_PROMPTS = {
    "skeptic": _NO_TOOLS_PREAMBLE + """\
You are the council Skeptic. Your ONLY job: attack the premise. Find flaws, \
missing evidence, hidden assumptions, and ways the claim could be wrong. Be \
harsh but specific — vague doubt is useless, name the exact weak point.

Do not propose fixes, implementations, or recommendations. Findings only.""",

    "advocate": _NO_TOOLS_PREAMBLE + """\
You are the council Advocate. Your ONLY job: steelman the case. Find the \
strongest, most charitable version of the claim and the best evidence for it. \
Do not concede weak points the Skeptic would raise — that's their job, not \
yours.

Do not propose fixes, implementations, or recommendations. Findings only.""",

    "verifier": _NO_TOOLS_PREAMBLE + """\
You are the council Verifier. Your ONLY job: separate what is provable from \
what is assumed. For every factual or "is this live" claim in the question or \
context, either cite the specific evidence given, or mark it \
`[CHECK: <short search term>]` (e.g. `[CHECK: council_router.py]` or \
`[CHECK: _get_council_members]`) so it can be looked up in the live codebase. \
Use short, literal, greppable terms — a filename, function name, or exact \
phrase — not a question.

Take no position on whether the underlying claim is good or bad. Do not \
propose fixes, implementations, or recommendations. Findings only.""",
}

# ── Review-mode prompts (D4) ─────────────────────────────────────────────────
# Used when the orchestrator has ALREADY delegated + compiled findings (D2/D3)
# and council's job is to VALIDATE them, not re-derive an answer from the raw
# question. Roles receive anonymized findings (D5, blinded by anonymize.py)
# plus the original question, and emit per-finding CONFIRM/DENY/FLAG verdicts.
# Shared grounding rule appended to every review-mode role. This is the
# anti-hallucination contract: a stateless model must NEVER assert an external
# fact from training data — it marks it for lookup instead. Two brokers:
#   [CHECK: term]  → local codebase/memory grep (our own files/architecture)
#   [WEB: query]   → external cited web lookup (laws, current events, anything
#                    outside our files — the model's training may be stale/wrong)
_GROUNDING_RULE = (
    "\n\nGROUNDING (MANDATORY — anti-hallucination): You are stateless and your "
    "training data may be outdated or wrong. NEVER assert an external or "
    "time-sensitive fact (a law, rule, price, date, current event, or 'this is "
    "still true') from memory. Instead:\n"
    "  • For claims about OUR OWN code/architecture/files, mark `[CHECK: <greppable term>]`.\n"
    "  • For claims about the OUTSIDE WORLD (laws, regulations, current events, "
    "external facts), mark `[WEB: <search query>]` — do not state the fact, mark "
    "the lookup. A broker will resolve it with cited sources and the chairman "
    "reconciles it. If you cannot ground a claim, say FLAG, not CONFIRM."
)

_ROLE_PROMPTS_REVIEW = {
    "skeptic": _NO_TOOLS_PREAMBLE + """\
You are the council Skeptic, reviewing FINDINGS already gathered by workers \
(shown below, labeled anonymously — you are not told which model produced \
which finding). Your ONLY job: attack each finding. For each labeled finding, \
say CONFIRM, DENY, or FLAG (uncertain/needs more evidence), and name the exact \
weak point if you deny or flag it. If you suspect a finding is outdated (e.g. \
'there may be a newer law/rule that changes this'), do NOT assert it from \
memory — raise it as `[WEB: <query>]` so it comes back with real support.

Do NOT re-derive an independent answer to the original question. Do not \
propose fixes, implementations, or recommendations. Verdicts on the given \
findings only.""" + _GROUNDING_RULE,

    "advocate": _NO_TOOLS_PREAMBLE + """\
You are the council Advocate, reviewing FINDINGS already gathered by workers \
(shown below, labeled anonymously — you are not told which model produced \
which finding). Your ONLY job: steelman each finding. For each labeled \
finding, say CONFIRM, DENY, or FLAG, and give the best evidence FOR it if you \
confirm it — but if that evidence is an external fact, ground it with \
`[WEB: <query>]` rather than asserting it from memory.

Do NOT re-derive an independent answer to the original question. Do not \
propose fixes, implementations, or recommendations. Verdicts on the given \
findings only.""" + _GROUNDING_RULE,

    "verifier": _NO_TOOLS_PREAMBLE + """\
You are the council Verifier, reviewing FINDINGS already gathered by workers \
(shown below, labeled anonymously — you are not told which model produced \
which finding). Your ONLY job: for each labeled finding, either cite the \
specific evidence given for it, or mark it for lookup. Use `[CHECK: <term>]` \
for our own codebase/files (short, literal, greppable — a filename, function \
name, or exact phrase) and `[WEB: <query>]` for external facts. Then say \
CONFIRM, DENY, or FLAG for each finding based on what you could establish.

EXTRA: If any finding claims architectural/system facts (e.g. 'the orchestrator \
does X', 'council fires when Y', 'workers get delegated to Z'), aggressively \
fact-check those with [CHECK: ...] marks — they are load-bearing claims. Do \
not let unverified architectural assertions survive.

Do NOT re-derive an independent answer to the original question. Take no \
position beyond what's provable. Verdicts on the given findings only.""" + _GROUNDING_RULE,
}

ROLE_NAMES = ["skeptic", "advocate", "verifier"]


def role_prompt(role: str, mode: str = "debate") -> str:
    """mode='debate' (default): re-derive from the raw question (pre-D2 behavior).
    mode='review': validate already-compiled, anonymized findings (D4)."""
    if mode == "review":
        return _ROLE_PROMPTS_REVIEW[role]
    return _ROLE_PROMPTS[role]


def role_domain(role: str) -> str:
    """Bandit domain key for a role's own track record — separate from the
    general task domain, e.g. 'council_skeptic'. A provider that's a good
    Advocate isn't necessarily a good Verifier; scoring them under one shared
    domain would blend those together, so each role gets its own EWMA row in
    capability_map's existing (domain -> provider -> score) research map. No
    schema change — this is the same growable dict every other domain uses.
    """
    return f"council_{role}"


# Below this EWMA score, with at least ROLE_MIN_SAMPLES observations, a nano-tier
# provider is no longer trusted to hold this role and gets excluded from the
# candidate pool for it (falls through to standard-tier candidates instead).
# This is the "nano keeps failing as Skeptic -> escalate" rule: it fires per
# (role, provider), not globally, so a provider can still be fine in a role it
# hasn't failed.
ROLE_RELIABILITY_FLOOR = 0.4
ROLE_MIN_SAMPLES = 4


def record_role_outcome(
    role: str,
    provider: str,
    reliable: bool,
    capability_map,
    score: Optional[float] = None,
) -> None:
    """Record whether a role's response held up, feeding the role-scoped EWMA
    that assign_roles() reads for both ranking and the tier-escalation gate.

    Call this after the response has actually been checked against something
    — the Verifier's own [VERIFIED]/[NOT FOUND] grep results, a later
    contradiction surfaced during synthesis, or an orchestrator's independent
    read of the live system. Don't call it on an ungraded response; an
    unearned score pollutes the EWMA same as a wrong one.

    score: override the reliable/unreliable default (1.0/0.0) with a graded
    value in [0,1] when you have one (e.g. partial credit).
    """
    if capability_map is None:
        return
    if score is None:
        score = 1.0 if reliable else 0.0
    capability_map.update(role_domain(role), provider, score)
    try:
        capability_map.save()
    except Exception as e:
        logger.warning(f"council_roles: capability_map save failed: {e}")


def _is_nano_tier(provider: str) -> bool:
    """True if this provider's ONLY/cheapest configured tier is nano.
    Providers with no tier info (e.g. not in the registry) are treated as
    non-nano — the gate only demotes providers we have real tier data for.

    NEXUS:PORTABLE — delegates to worker_pool.is_nano_tier(), the already-ported
    tier-lookup shim (Nexus has no provider_catalog; tier data comes from the
    deployment's config/providers.yaml via worker_pool's roster loader)."""
    try:
        return _wp_is_nano_tier(provider)
    except Exception:
        return False


def _role_eligible(role: str, provider: str, capability_map) -> bool:
    """False if this provider has enough graded samples in this role to be
    judged unreliable AND is nano-tier (the escalation gate). True otherwise —
    including for providers with too few samples to judge yet, so a new
    nano-tier candidate still gets a fair first shot at the role."""
    if capability_map is None or not _is_nano_tier(provider):
        return True
    domain = role_domain(role)
    samples = capability_map.data.get("grades", {}).get(domain, {}).get(provider, 0)
    if samples < ROLE_MIN_SAMPLES:
        return True
    return capability_map.score(domain, provider) >= ROLE_RELIABILITY_FLOOR


def assign_roles(
    members: List[str],
    domain: str = "general",
    capability_map=None,
) -> Dict[str, str]:
    """Pick one distinct provider per role from the available council members.

    Ranks candidates by their role-scoped track record (role_domain), not the
    general task domain — a Verifier's reliability is judged as a Verifier.
    A nano-tier provider that has repeatedly failed THIS role is excluded from
    the pool for it (see _role_eligible / ROLE_RELIABILITY_FLOOR) and the pick
    falls through to a standard-tier candidate instead. Falls back to
    first-available when there's no capability_map or nothing has been graded
    yet.
    """
    assigned: Dict[str, str] = {}
    remaining = list(members)

    for role in ROLE_NAMES:
        if not remaining:
            break
        pool = [p for p in remaining if _role_eligible(role, p, capability_map)] or remaining
        pick = None
        if capability_map is not None:
            try:
                pick = capability_map.choose(role_domain(role), pool)["provider"]
            except Exception:
                pick = None
        if pick is None:
            pick = pool[0]
        assigned[role] = pick
        remaining.remove(pick)

    return assigned


# ── Local research broker (read-only, Verifier-only) ────────────────────────

# Fixed root allowlist. Never expanded from model output — only these trees
# are ever read. No write/edit capability exists anywhere in this module.
# NEXUS:PORTABLE — the default root is the deployment's OWN project tree (the
# repo dir containing `src/`), so the broker greps whatever codebase it's
# actually running against instead of a hardcoded install path. Override via
# COUNCIL_RESEARCH_ROOTS (os.pathsep-separated) so an operator can point it at
# additional trees (e.g. a memory store) without a code edit; falls back to
# this single self-referential root when unset.
def _default_roots() -> List[Path]:
    import os
    env = os.getenv("COUNCIL_RESEARCH_ROOTS")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p.strip()]
    return [
        Path(__file__).resolve().parent.parent.parent,
    ]

_ALLOWED_ROOTS = _default_roots()

_CHECK_TAG_RE = re.compile(r"\[CHECK:\s*([^\]]{1,80})\]")
_WEB_TAG_RE = re.compile(r"\[WEB:\s*([^\]]{1,120})\]")
_MAX_TERMS_PER_CALL = 6
_MAX_MATCHES_PER_TERM = 4
_GREP_TIMEOUT = 8
# Web lookups hit the network + a Haiku synth call, so they're costlier than
# grep — cap them harder per role to protect the token/latency budget.
_MAX_WEB_TERMS_PER_CALL = 3


def extract_check_terms(text: str) -> List[str]:
    """Pull `[CHECK: term]` markers out of a Verifier response, deduped, capped."""
    seen = []
    for m in _CHECK_TAG_RE.finditer(text):
        term = m.group(1).strip()
        if term and term not in seen:
            seen.append(term)
        if len(seen) >= _MAX_TERMS_PER_CALL:
            break
    return seen


def extract_web_terms(text: str) -> List[str]:
    """Pull `[WEB: query]` markers out of any role response, deduped, capped.
    These are external-fact lookups (laws, current events) a stateless model
    must NOT answer from training data — grounded via research.research()."""
    seen = []
    for m in _WEB_TAG_RE.finditer(text):
        term = m.group(1).strip()
        if term and term not in seen:
            seen.append(term)
        if len(seen) >= _MAX_WEB_TERMS_PER_CALL:
            break
    return seen


async def _grep_term(term: str) -> str:
    """Fixed-string, case-insensitive recursive grep of the allowed roots for
    one literal term. Args-list subprocess — no shell, no interpolation risk.
    Read-only: grep never modifies anything it touches.
    """
    hits: List[str] = []
    for root in _ALLOWED_ROOTS:
        if not root.exists():
            continue
        try:
            proc = await asyncio.create_subprocess_exec(
                "grep", "-rniF", "-m", str(_MAX_MATCHES_PER_TERM),
                "--include=*.py", "--include=*.md", "--include=*.json",
                "--exclude-dir=__pycache__", "--exclude-dir=venv",
                term, str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=_GREP_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                continue
            for line in out.decode(errors="replace").splitlines():
                hits.append(line.strip())
                if len(hits) >= _MAX_MATCHES_PER_TERM:
                    break
        except Exception as e:
            logger.debug(f"council_roles: grep failed for {term!r} in {root}: {e}")
    if hits:
        shown = "\n".join(f"    {h}" for h in hits[:_MAX_MATCHES_PER_TERM])
        return f"[VERIFIED: {term!r} found —\n{shown}]"
    return f"[NOT FOUND: {term!r} — no match in live codebase/memory]"


async def _research_term(query: str) -> str:
    """External web lookup for one query via the Nexus research pipeline
    (DuckDuckGo search → page fetch → Haiku-class synthesis → cited markdown).
    Read-only: cannot write code or documents, only reads the web. This is
    the council's antidote to answering domain facts from stale training data.
    """
    try:
        from src.research.research_worker import research as _run_research
        summary = await _run_research(query, scope="general", num_results=4)
        summary = (summary or "").strip()
        if not summary or summary.startswith("# Research Failed"):
            return f"[WEB NO RESULT: {query!r} — lookup returned nothing usable]"
        # Keep it bounded — the chairman doesn't need the whole page dump.
        if len(summary) > 1200:
            summary = summary[:1200] + "\n…(truncated)"
        return f"[WEB RESULT for {query!r} — cited, verify links]\n{summary}"
    except Exception as e:
        logger.warning(f"council_roles: web research failed for {query!r}: {e}")
        return f"[WEB LOOKUP ERROR: {query!r} — {e}]"


async def resolve_grounding_tags(role_text: str) -> str:
    """Resolve BOTH grounding brokers in a single role response and append the
    results: `[CHECK: term]` → local grep of the codebase/memory (read-only,
    no LLM), and `[WEB: query]` → external cited web lookup (read-only). Any
    role may emit either — this is what lets the Skeptic surface "there's a new
    law, per <source>" with real support instead of asserting from memory.
    """
    check_terms = extract_check_terms(role_text)
    web_terms = extract_web_terms(role_text)
    if not check_terms and not web_terms:
        return role_text

    out = role_text
    if check_terms:
        results = await asyncio.gather(*[_grep_term(t) for t in check_terms])
        out += "\n\n[LIVE-SYSTEM CHECK RESULTS]\n" + "\n".join(results)
    if web_terms:
        results = await asyncio.gather(*[_research_term(t) for t in web_terms])
        out += "\n\n[EXTERNAL WEB LOOKUP RESULTS]\n" + "\n\n".join(results)
    return out


async def verify_check_tags(verifier_text: str) -> str:
    """Back-compat alias — resolves both grounding brokers now. Kept so existing
    callers (debate-mode path) keep working; new review-mode path calls
    resolve_grounding_tags on every role."""
    return await resolve_grounding_tags(verifier_text)
