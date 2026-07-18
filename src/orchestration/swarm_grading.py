"""Swarm worker grading (P2.5b) — the climb/sink engine of the graduation ladder.

Without this, a worker's per-domain score never moves from swarm participation, so
nothing can climb toward qualification. Here, after a worker completes a step, its
finding is SAMPLED-graded for standalone quality by a capable judge, and the verdict
feeds:
  - capability_map.record_grade(provider, domain, score) → moves the qualification
    score the P2.5a gate reads (models earn/lose demanding-role standing from evidence);
  - provider_status lifecycle (record_call + shadow agreement → auto-promote unknown→known).

Sampled (1-in-N) and concurrency-capped so grading never dominates the token budget —
the same discipline as capability_grader. Fire-and-forget: a grading failure never
touches the swarm's result.

# NEXUS:PORTABLE — mechanism is generic; the judge roster is the install seam.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Sampling + concurrency (env-tunable). Grading is insurance for the ladder, not
# every call — keep it cheap.
SAMPLE_RATE = float(os.environ.get("SWARM_GRADE_SAMPLE_RATE", "0.5"))
_MAX_INFLIGHT = int(os.environ.get("SWARM_GRADE_MAX_INFLIGHT", "2"))

# Judges that can score a finding, best-first (available ones win).
# NEXUS:OPERATOR — this roster is THIS install's top-level provider ids (see
# config/providers.yaml): github_models, google_gemini, groq, sambanova, mistral.
# Another install sets SWARM_GRADE_JUDGES to its own capable graders.
# NEXUS:PORTABLE = the grade-one-finding-against-a-rubric mechanism, not the
# specific provider ids.
_JUDGES = [j for j in (os.environ.get("SWARM_GRADE_JUDGES")
           or "github_models,google_gemini,groq,sambanova,mistral").split(",") if j.strip()]

_GRADE_PROMPT = """You are grading a worker AI's response to a delegated sub-task.

SUB-TASK ({domain}):
{subtask}

WORKER RESPONSE:
{finding}

Grade the response on quality for this sub-task. Reply with ONLY a JSON object, no prose:
{{"score": 0.0-1.0, "hallucinated": true|false}}
- score = how correct, complete, and on-task the response is (0=useless, 1=excellent).
- hallucinated = true if it invented specifics or asserted unverifiable claims as fact."""


class SwarmGrader:
    def __init__(self, rng: Optional[random.Random] = None):
        self.rng = rng or random.Random()
        self._client = None
        self._cm = None
        self._inflight = 0

    def _lazy(self):
        if self._client is None:
            from .providers import ProviderClient
            self._client = ProviderClient(timeout=40.0)
        if self._cm is None:
            from .capability_map import CapabilityMap
            self._cm = CapabilityMap()
        return self._client, self._cm

    @staticmethod
    def _parse(raw: str) -> Optional[dict]:
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        obj = None
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{.*?\}", raw, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    return None
        if not isinstance(obj, dict) or "score" not in obj:
            return None
        try:
            return {"score": max(0.0, min(1.0, float(obj["score"]))),
                    "hallucinated": bool(obj.get("hallucinated", False))}
        except (TypeError, ValueError):
            return None

    async def maybe_grade(self, subtask: str, domain: str, provider: str, finding: str) -> Optional[dict]:
        """Sampled, concurrency-capped grade of one worker finding. Fire-and-forget."""
        if not subtask or not finding or not provider:
            return None
        if self.rng.random() > SAMPLE_RATE:
            return None
        if self._inflight >= _MAX_INFLIGHT:
            return None
        self._inflight += 1
        try:
            return await self._grade(subtask, domain, provider, finding)
        except Exception as e:
            logger.debug(f"swarm_grading: failed ({type(e).__name__}) — non-blocking")
            return None
        finally:
            self._inflight -= 1

    async def _grade(self, subtask, domain, provider, finding) -> Optional[dict]:
        client, cm = self._lazy()
        avail = set(client.available())
        # don't let a provider grade its own finding
        judge = next((j for j in _JUDGES if j in avail and j != provider), None)
        if not judge:
            return None
        raw = await asyncio.wait_for(
            client.complete(judge, _GRADE_PROMPT.format(
                domain=domain, subtask=subtask[:1200], finding=finding[:2000])),
            timeout=30)
        verdict = self._parse(raw)
        if verdict is None:
            return None

        before = cm.score(domain, provider)
        cm.record_grade(provider, domain, verdict["score"], verdict["hallucinated"])
        cm.save()
        after = cm.score(domain, provider)

        # Trust-ladder lifecycle: count the call + feed shadow agreement (a
        # "good" grade = agreement with the trusted judge). Auto-promotes unknown→known.
        try:
            from .provider_status import get_store
            st = get_store()
            st.record_call(provider)
            st.record_shadow(provider, agreed=(verdict["score"] >= 0.5))
        except Exception as e:
            logger.debug(f"swarm_grading: provider_status update skipped ({e})")

        logger.info(f"swarm_grading: {provider} on '{domain}' → score={verdict['score']} "
                    f"(EWMA {before:.2f}→{after:.2f}, judge={judge})")
        return verdict


# Module singleton — shared across swarm runs.
_grader = SwarmGrader()


def grade_finding_bg(subtask: str, domain: str, provider: str, finding: str) -> None:
    """Fire-and-forget scheduling of one worker-finding grade. Safe to call from
    inside the swarm loop — never awaits, never raises."""
    try:
        asyncio.create_task(_grader.maybe_grade(subtask, domain, provider, finding))
    except RuntimeError:
        # no running loop (e.g. unit context) — skip silently
        pass
