"""Council judge — async, fire-and-forget grading of Skeptic/Advocate responses
against the Chairman's synthesis.

The judge compares each role's findings against what the Chairman actually
concluded and flags hallucinations or weak attacks:
  - Skeptic: Did the Chairman agree the objections were real? Valid attack?
  - Advocate: Did the Chairman accept the steelman? Valid argument?

Runs after chairman synthesis (async, doesn't block user reply). Grades feed
back into council_roles.record_role_outcome() → role-scoped EWMA → assign_roles()
for the tier-escalation gate.

Judge model selection:
  - Complexity ≤ 0.4 (low-stakes): standard-tier judge (fast, sufficient for
    fact-checking)
  - Complexity 0.4–0.7 (medium/high): high-tier judge (stronger reasoning for
    argument eval)
  - Nexus ships with no built-in "fable"/premium-subscription tier — see
    `_FABLE_JUDGE` below.

Design: similar to capability_grader.py (shadow-grade + feedback loop), but
judges council roles instead of local LLMs, and uses chairman synthesis as the
reference instead of an online provider's answer.

# NEXUS:PORTABLE — the multi-candidate failover design (ordered list, advance
# past a failing/unavailable judge) is the portable fix; only the concrete
# roster below is install-specific.
"""

import asyncio
import json
import logging
import os
import re
from typing import Optional

from .capability_map import CapabilityMap
from .council_roles import record_role_outcome, role_domain
from .providers import ProviderClient

logger = logging.getLogger(__name__)

# Judge model candidates, best-first. First one AVAILABLE in the registry wins.
# NEXUS:OPERATOR — these ids are THIS install's `providers.yaml` top-level
# provider ids (no Claude/Anthropic entry: Nexus's OpenAI-compatible registry
# has no Claude id to put here). A different install overrides via the
# COUNCIL_JUDGES_HIGH / COUNCIL_JUDGES_STANDARD env vars (comma-separated
# provider ids, best-first); unset falls back to these constants.
_DEFAULT_JUDGES_HIGH = ["sambanova", "openrouter", "google_gemini", "cerebras", "mistral"]
_DEFAULT_JUDGES_STANDARD = ["github_models", "google_gemini", "groq", "mistral", "cerebras"]


def _judges_from_env(env_var: str, default: list) -> list:
    raw = os.environ.get(env_var)
    if not raw:
        return list(default)
    ids = [p.strip() for p in raw.split(",") if p.strip()]
    return ids or list(default)


_JUDGES_BY_TIER = {
    "high": _judges_from_env("COUNCIL_JUDGES_HIGH", _DEFAULT_JUDGES_HIGH),
    "standard": _judges_from_env("COUNCIL_JUDGES_STANDARD", _DEFAULT_JUDGES_STANDARD),
}

# Fable5 (Claude subscription-CLI tier) for very specific high-precision
# situations, if available in the registry. Nexus has no fable/subscription
# tier by default — the Nexus provider registry is OpenAI-compatible-only, so
# there is no id to put here. Kept as "" (never matches `available`) rather
# than removed so `enable_fable=True` stays a harmless no-op instead of an
# error; a Nexus install that wires up its own fable-equivalent provider can
# set this to that provider's id.
_FABLE_JUDGE = ""


def _judge_candidates(
    complexity: float,
    enable_fable: bool = False,
    available: Optional[set] = None,
) -> list:
    """Ordered judge candidates (best-first) filtered to available providers.

    Returns a LIST so the caller can advance to the next judge when one fails
    (a single 400/429/timeout must not kill grading — that's how the judge sat
    silently dead: it picked a retired/unavailable model and gave up). None
    available = legacy head-of-list.
    """
    tier = "high" if complexity >= 0.5 else "standard"
    candidates = list(_JUDGES_BY_TIER.get(tier, []))
    if enable_fable and complexity > 0.8 and _FABLE_JUDGE:
        candidates.insert(0, _FABLE_JUDGE)
    if available is None:
        return candidates[:1]
    return [c for c in candidates if c in available]


def _pick_judge(complexity: float, enable_fable: bool = False, available: Optional[set] = None):
    """Back-compat single-pick (first available candidate) — kept for existing tests."""
    cands = _judge_candidates(complexity, enable_fable, available)
    return cands[0] if cands else None


_GRADE_SKEPTIC_PROMPT = """You are grading a Skeptic's attack for validity.

ORIGINAL QUESTION:
{question}

SKEPTIC'S OBJECTIONS:
{skeptic_response}

CHAIRMAN'S SYNTHESIS (the final decision):
{synthesis}

Grade the Skeptic. Reply with ONLY a JSON object, no prose:
{{"score": 0.0-1.0, "hallucinated": true|false}}

- score = how much the Skeptic's objections were real/valid. Did the Chairman
  agree they were problems? If the Chairman dismissed them, score low. If the
  Chairman treated them as serious constraints, score high.
- hallucinated = true if the Skeptic fabricated or wildly overstated objections
  not supported by the question or Chairman's analysis. False if the objections
  were real, even if the Chairman ultimately proceeded anyway."""

_GRADE_ADVOCATE_PROMPT = """You are grading an Advocate's steelman for merit.

ORIGINAL QUESTION:
{question}

ADVOCATE'S CASE (steelman):
{advocate_response}

CHAIRMAN'S SYNTHESIS (the final decision):
{synthesis}

Grade the Advocate. Reply with ONLY a JSON object, no prose:
{{"score": 0.0-1.0, "hallucinated": true|false}}

- score = how much the Advocate's steelman was valid/useful. Did the Chairman
  adopt parts of it? If the Chairman ignored it, score low. If the Chairman
  incorporated the strongest points, score high.
- hallucinated = true if the Advocate invented merit or distorted facts to
  make the case sound better. False if the steelman was honest, even if
  ultimately unpersuasive."""


class CouncilJudge:
    """Async judge for council role grading; fires after chairman synthesis."""

    def __init__(self):
        self._client: Optional[ProviderClient] = None
        self._cm: Optional[CapabilityMap] = None

    def _lazy(self):
        if self._client is None:
            self._client = ProviderClient(timeout=40.0)
        if self._cm is None:
            self._cm = CapabilityMap()
        return self._client, self._cm

    async def maybe_grade(
        self,
        question: str,
        role: str,
        role_response: str,
        role_provider: str,
        synthesis: str,
        complexity: float = 0.5,
        enable_fable: bool = False,
        session=None,
    ) -> Optional[dict]:
        """Sampled grade of a council role's response. Safe to fire-and-forget.

        Args:
            question: original user prompt
            role: "skeptic" or "advocate"
            role_response: the role's answer
            role_provider: which provider gave this role's response
            synthesis: the Chairman's final synthesis
            complexity: task complexity [0, 1] to guide judge selection
            enable_fable: if True, try the fable/premium judge for very
                high-complexity cases (no-op on installs with none configured)
            session: optional CouncilSession — verdict + EWMA delta persisted there

        Returns:
            {"score": 0-1, "hallucinated": bool} or None if grading skipped.
            On success, calls record_role_outcome() to update role-reliability.
        """
        if role not in ("skeptic", "advocate"):
            return None
        if not question or not role_response or not synthesis:
            return None

        client, cm = self._lazy()
        candidates = _judge_candidates(complexity, enable_fable, available=set(client.available()))
        if not candidates:
            # WARNING, not debug — a judge that can't run means role-reliability
            # accumulates nothing (this hid a 15-day silent no-op once already).
            logger.warning("council_judge: no judge candidate available — grading skipped")
            return None

        prompt_template = _GRADE_SKEPTIC_PROMPT if role == "skeptic" else _GRADE_ADVOCATE_PROMPT
        prompt = prompt_template.format(
            question=question[:1500],
            skeptic_response=role_response[:2000] if role == "skeptic" else "",
            advocate_response=role_response[:2000] if role == "advocate" else "",
            synthesis=synthesis[:2000],
        )

        # Try judges in order; a failed/unparseable one advances to the next.
        verdict = None
        judge = None
        for cand in candidates:
            try:
                verdict_raw = await asyncio.wait_for(client.complete(cand, prompt), timeout=30)
            except asyncio.TimeoutError:
                logger.info(f"council_judge: judge {cand} timed out — next")
                continue
            except Exception as e:
                logger.info(f"council_judge: judge {cand} failed ({type(e).__name__}) — next")
                continue
            v = self._parse_verdict(verdict_raw)
            if v is None:
                logger.info(f"council_judge: judge {cand} unparseable — next")
                continue
            verdict, judge = v, cand
            break

        if verdict is None:
            logger.warning(f"council_judge: all {len(candidates)} judge candidate(s) failed — grading skipped")
            return None

        ewma_before = cm.score(role_domain(role), role_provider)
        record_role_outcome(
            role=role,
            provider=role_provider,
            reliable=(verdict["score"] >= 0.5),
            capability_map=cm,
            score=verdict["score"],
        )
        ewma_after = cm.score(role_domain(role), role_provider)
        if session is not None:
            session.record_judge_verdict(
                role=role,
                provider=role_provider,
                verdict=verdict,
                judge_model=judge,
                ewma_before=ewma_before,
                ewma_after=ewma_after,
            )
        logger.info(
            f"council_judge: {role} ({role_provider}) → score={verdict['score']} "
            f"hallucinated={verdict['hallucinated']} (judge={judge})"
        )
        return verdict

    @staticmethod
    def _parse_verdict(raw: str) -> Optional[dict]:
        """Extract score + hallucinated from judge's JSON response."""
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        obj = None
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r'\{.*?\}', raw, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    return None
        if not isinstance(obj, dict) or "score" not in obj:
            return None
        try:
            score = max(0.0, min(1.0, float(obj["score"])))
        except (TypeError, ValueError):
            return None
        return {"score": score, "hallucinated": bool(obj.get("hallucinated", False))}


# Singleton for the async judge, shared across calls.
_judge = CouncilJudge()


async def grade_council_roles(
    question: str,
    role_responses: dict,  # {role: text, ...}
    role_providers: dict,  # {role: provider, ...}
    synthesis: str,
    complexity: float = 0.5,
    enable_fable: bool = False,
    session=None,
) -> None:
    """Grade all council roles (Skeptic/Advocate) asynchronously after synthesis.

    Fires all grades in parallel (not serialized). Doesn't block and doesn't
    return — grades feed directly into capability_map (and, when a
    CouncilSession is supplied, into that run's persistent session record).
    Designed for fire-and-forget from council_executor.run() after returning
    CouncilResult to the chairman.
    """
    tasks = []
    for role in ("skeptic", "advocate"):
        if role in role_responses and role in role_providers:
            tasks.append(
                _judge.maybe_grade(
                    question=question,
                    role=role,
                    role_response=role_responses[role],
                    role_provider=role_providers[role],
                    synthesis=synthesis,
                    complexity=complexity,
                    enable_fable=enable_fable,
                    session=session,
                )
            )
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
