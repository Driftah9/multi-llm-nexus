"""Council executor — fixed-role fan-out, live-system check, synthesize.

Given a RoutePlan from council_router, this module:
  1. Assigns 3 of the available members to fixed roles: Skeptic, Advocate, Verifier
  2. Fans out the user prompt to those 3 roles in parallel (role-specific prompts)
  3. Resolves grounding tags on every role: `[CHECK: term]` → read-only local grep
     of the deployment's own code/memory; `[WEB: query]` → cited external lookup
     (no LLM call for grep, no write/edit capability anywhere)
  4. Returns a CouncilResult with the synthesis_prompt for the chairman
     (chairman synthesis happens in the caller/bridge — not here)

The chairman is NEVER called here. This module is pure council work; the caller
routes synthesis_prompt to the chairman after run() returns.

Previously this ran an N-member "answer then peer-rank" tournament (anonymize.py
+ aggregate_rankings). That assumed members were interchangeable, which breaks
once roles have different jobs (a Skeptic and an Advocate aren't competing on
the same task, so ranking them is meaningless) and it fanned out to every
eligible provider twice (~30 calls). Fixed roles cut that to ~4 calls and match
what the roles are actually for. anonymize.py / aggregate_rankings remain in the
tree (blinding compiled findings in review mode still uses anonymize); the
tournament ranking path is simply no longer wired into run().

Ported from the live claude-brain fixed-role council. NEXUS:PORTABLE — the
role model + grounding brokers are generic; provider ids come from providers.yaml.
Design: multi-llm-orchestration.md §1–§4, §8
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .anonymize import anonymize, deanonymize
from .capability_map import CapabilityMap
from .council_roles import assign_roles, role_prompt, resolve_grounding_tags
from .council_router import RoutePlan
from .council_judge import grade_council_roles
from .council_session import CouncilSession
from .providers import ProviderClient

logger = logging.getLogger(__name__)

# Phrases that indicate a role has drifted into architect/builder territory.
# Responses containing these get their off-scope lines stripped before the
# chairman sees them — roles report findings, they don't propose builds.
_SCOPE_VIOLATION_PATTERNS = [
    r"(?i)(i can wire|i can implement|let me implement|let me create|i will create|i'll create)",
    r"(?i)(you should build|you should add|you should configure|we could build|we should build)",
    r"(?i)(here'?s? (how|a) (to|plan|system|approach|implementation|solution|fix))",
    r"(?i)(happy to (help|implement|wire|build|set up))",
    r"(?i)(next step[s]? (is|are|would be))",
    r"(?i)(i'?ll? (write|draft|scaffold|set up|wire))",
]

# Synthesis prompt template. Chairman receives this after all 3 roles respond.
# SURFACE-don't-BLEND: where the council agrees, state the consensus; where it
# conflicts, present BOTH positions with the tradeoff — never silently average
# disagreement into one smooth answer.
_SYNTHESIS_PROMPT = """\
You are the chairman synthesizing a role-based council of independent AI responses.

Original question:
{user_prompt}

The council's perspectives:

{role_block}

Produce one authoritative answer, but do NOT blend disagreement away:
- Where the roles AGREE, state it directly as the consensus.
- Where they CONFLICT or diverge on a material point, SURFACE both positions and
  the tradeoff between them — name the disagreement, don't silently pick one or
  average them into mush. If the evidence favours one side, say so and why.
- The Verifier's [VERIFIED]/[NOT FOUND] results are live-codebase ground truth —
  treat them as authoritative over any unverified claim from the Skeptic or
  Advocate, and flag anything neither role nor the live check could establish.

Lead with the recommendation/answer, then the supporting analysis. Do not mention
the council process — speak as a single authoritative voice."""

# Review-mode synthesis prompt (D4). Used when compiled_findings was supplied —
# roles validated already-delegated findings instead of re-deriving an answer.
_REVIEW_SYNTHESIS_PROMPT = """\
You are the chairman reconciling a council's validation of already-gathered findings.

Original question:
{user_prompt}

Findings that were delegated to workers and compiled (labeled anonymously A/B/C \
so the council could not be biased by which model produced which):

{findings_block}

The council's verdicts on those findings:

{role_block}

Produce one authoritative answer built FROM THE VALIDATED FINDINGS, but do NOT
blend disagreement away:
- Where the council CONFIRMS a finding, treat it as established and use it.
- Where the council DENIES a finding, discard it — do not present it as fact.
- Where the council FLAGS a finding (uncertain), say so explicitly rather than
  asserting it either way.
- Where roles CONFLICT on the same finding, SURFACE both verdicts and the
  reasoning — don't silently pick one.

Lead with the recommendation/answer, then the supporting analysis. Do not mention
the council process or the anonymized labels — speak as a single authoritative voice."""


@dataclass
class CouncilResult:
    synthesis_prompt: str                    # pass to chairman
    raw_responses: Dict[str, str]            # provider -> role response text
    roles: Dict[str, str]                    # role name -> provider assigned to it
    top_provider: str                        # verifier's provider (the fact-checked role)
    failed_providers: List[str] = field(default_factory=list)
    peer_review_skipped: bool = True         # always True now — roles aren't ranked against each other
    rankings: List[Dict] = field(default_factory=list)   # unused, kept for callers that still read it
    label_map: Dict[str, str] = field(default_factory=dict)  # unused, kept for callers that still read it
    session_path: str = ""                   # council_session_<ts>.json audit record (empty on fallback)
    # Deferred-judge context (set when run(defer_judge=True)): the caller fires
    # grade_council_roles AFTER the chairman replies, grading against the real
    # synthesis instead of the synthesis prompt. No chairman output → no grade.
    judge_deferred: bool = False
    question: str = ""                       # original user prompt (for the deferred judge)
    complexity: float = 0.5                  # triage complexity (for judge model selection)
    session: Optional[object] = None         # CouncilSession recorder handle


async def run(
    plan: RoutePlan,
    user_prompt: str,
    system_prompt: str = "",
    domain: str = "general",
    capability_map: Optional[CapabilityMap] = None,
    timeout: float = 60.0,
    complexity: float = 0.5,
    compiled_findings: Optional[List[Dict[str, str]]] = None,
    defer_judge: bool = False,
) -> CouncilResult:
    """Execute the role-based council flow for a given RoutePlan.

    Args:
        plan:              RoutePlan with mode="council", chairman, members
        user_prompt:       the user's message (already context-expanded if needed)
        system_prompt:     optional system context passed to all roles
        domain:            domain string, used to pick the best provider per role
        capability_map:    optional CapabilityMap; used for role assignment
        timeout:           per-call timeout in seconds
        complexity:        task complexity [0,1] for judge model selection
        compiled_findings: (D2-D5) list of {"provider", "finding"} dicts staged by
                           orchestration/delegator.py. When supplied, council
                           switches to REVIEW mode: findings are blinded by source
                           (anonymize.py) and roles validate them (CONFIRM/DENY/
                           FLAG) instead of re-deriving an answer. When None, falls
                           back to debate-from-scratch.
        defer_judge:       when True the judge is NOT spawned here; the caller fires
                           grade_council_roles after the chairman actually replies,
                           using the real chairman output as reference.

    Returns:
        CouncilResult — pass .synthesis_prompt to the chairman.
    """
    client = ProviderClient(timeout=timeout)
    members = [m for m in plan.members if _member_available(m, client)]

    if not members:
        logger.warning("council_executor: no available members, falling back to empty council")
        return _empty_result(user_prompt)

    review_mode = bool(compiled_findings)

    # ── Step 1: Assign Skeptic / Advocate / Verifier to distinct members ────
    roles = assign_roles(members, domain=domain, capability_map=capability_map)
    if not roles:
        logger.warning("council_executor: role assignment produced nothing, falling back")
        return _empty_result(user_prompt)
    logger.info(f"council_executor: roles assigned: {roles} (review_mode={review_mode})")

    # ── Step 1b (D5): blind compiled findings by source before roles see them.
    findings_block = ""
    label_to_provider: Dict[str, str] = {}
    if review_mode:
        anon_input = [
            {"provider": f["provider"], "response": f["finding"]}
            for f in compiled_findings
        ]
        findings_block, label_to_provider = anonymize(anon_input)

    # ── Step 2: Fan out to each role in parallel, role-specific prompt ──────
    base_system = (system_prompt + "\n\n") if system_prompt else ""
    role_mode = "review" if review_mode else "debate"
    role_input = (
        f"Original question:\n{user_prompt}\n\nFindings to review:\n{findings_block}"
        if review_mode else user_prompt
    )
    calls = {
        role: client.complete(provider, role_input, system=base_system + role_prompt(role, mode=role_mode))
        for role, provider in roles.items()
    }
    import asyncio as _asyncio
    raw_results = await _asyncio.gather(*calls.values(), return_exceptions=True)

    successes: Dict[str, str] = {}   # role -> text
    failures: List[str] = []         # providers that failed
    all_violations: Dict[str, List[str]] = {}  # role -> stripped snippets (for the session record)
    for (role, provider), result in zip(roles.items(), raw_results):
        if isinstance(result, Exception):
            logger.warning(f"council_executor: {role} ({provider}) failed: {result}")
            failures.append(provider)
        else:
            clamped, violations = _clamp_member_output(str(result), provider)
            if violations:
                logger.warning(f"council_executor: {role} ({provider}) scope violations stripped: {violations}")
                all_violations[role] = violations
            successes[role] = clamped

    if not successes:
        logger.error("council_executor: all roles failed, returning empty result")
        return _empty_result(user_prompt, failed=failures)

    # ── Step 3: Resolve grounding tags on EVERY role, not just the Verifier.
    #    `[CHECK: term]` → local grep; `[WEB: query]` → cited external lookup.
    #    All read-only, no writes. ───────────────────────────────────────────
    import asyncio as _asyncio_ground
    _ground_roles = list(successes.keys())
    _ground_results = await _asyncio_ground.gather(
        *[resolve_grounding_tags(successes[r]) for r in _ground_roles]
    )
    for _r, _grounded in zip(_ground_roles, _ground_results):
        successes[_r] = _grounded

    # ── Step 4: Build synthesis prompt, labeled by role (roles are NOT
    #    anonymized — the chairman needs to know which role said what; only the
    #    FINDINGS were blinded) ─────────────────────────────────────────────
    role_block = "\n\n".join(
        f"**{role.title()}**:\n{text}" for role, text in successes.items()
    )
    if review_mode:
        synthesis_prompt = _REVIEW_SYNTHESIS_PROMPT.format(
            user_prompt=user_prompt,
            findings_block=findings_block,
            role_block=role_block,
        )
    else:
        synthesis_prompt = _SYNTHESIS_PROMPT.format(
            user_prompt=user_prompt,
            role_block=role_block,
        )

    raw_responses = {roles[role]: text for role, text in successes.items()}
    top_provider = roles.get("verifier") or next(iter(raw_responses))

    # ── Session record: persist this run before the judge fires. ────────────
    session = CouncilSession(
        question=user_prompt,
        domain=domain,
        complexity=complexity,
        review_mode=review_mode,
    )
    session.record_council(
        roles={role: roles[role] for role in successes},
        role_responses=successes,
        scope_violations=all_violations,
        failed_providers=failures,
        synthesis_prompt=synthesis_prompt,
        finding_attribution=dict(label_to_provider) if review_mode else None,
    )

    result = CouncilResult(
        synthesis_prompt=synthesis_prompt,
        raw_responses=raw_responses,
        roles={role: roles[role] for role in successes},
        top_provider=top_provider,
        failed_providers=failures,
        label_map=dict(label_to_provider) if review_mode else {},
        session_path=str(session.path),
        judge_deferred=defer_judge,
        question=user_prompt,
        complexity=complexity,
        session=session,
    )

    # De-anonymize for attribution logging only (D5) — never surfaced to roles/chairman.
    if review_mode and label_to_provider:
        attributed = {label: deanonymize(label_to_provider, label) for label in label_to_provider}
        logger.debug(f"council_executor: reviewed findings attribution: {attributed}")

    # ── Async judge: grade Skeptic/Advocate (fire-and-forget) ──────────────
    if not defer_judge:
        try:
            import asyncio as _asyncio
            _asyncio.create_task(
                grade_council_roles(
                    question=user_prompt,
                    role_responses=successes,     # {role: text, ...}
                    role_providers=roles,         # {role: provider, ...}
                    synthesis=synthesis_prompt,
                    complexity=complexity,
                    enable_fable=False,
                    session=session,
                )
            )
        except Exception as e:
            logger.debug(f"council_executor: judge spawn failed (non-blocking): {e}")

    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _member_available(provider: str, client: ProviderClient) -> bool:
    """True if the provider is in the registry and has a key configured."""
    if provider not in client.registry:
        logger.debug(f"council_executor: {provider} not in registry, skipping")
        return False
    spec = client.registry[provider]
    if spec.api_key_env is None:
        return True   # local provider, no key needed
    import os
    return bool(os.getenv(spec.api_key_env))


def _clamp_member_output(text: str, provider: str) -> tuple:
    """Strip scope violations from a member's Stage 1 response.

    Council members must return findings only. Sentences containing architect/
    builder language are removed before the response reaches the chairman.

    Returns (clamped_text, list_of_violation_snippets).
    """
    import re as _re
    violations = []
    lines = text.splitlines()
    clean_lines = []
    for line in lines:
        matched = False
        for pattern in _SCOPE_VIOLATION_PATTERNS:
            if _re.search(pattern, line):
                violations.append(line.strip()[:120])
                matched = True
                break
        if not matched:
            clean_lines.append(line)
    return "\n".join(clean_lines).strip(), violations


def _empty_result(user_prompt: str, failed: List[str] = None) -> CouncilResult:
    """Fallback result when all members fail — chairman gets the raw prompt."""
    return CouncilResult(
        synthesis_prompt=user_prompt,
        raw_responses={},
        roles={},
        top_provider="",
        failed_providers=failed or [],
    )
