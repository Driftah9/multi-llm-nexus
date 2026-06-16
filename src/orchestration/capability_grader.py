"""Capability grader — strong online providers grade weak/local models.

The calibration loop behind the capability map's "company record":
- Online provider answers the user (primary path)
- SAME query shadow-runs on a local LLM (sampled, not every turn)
- An online judge grades the local's answer against the reference answer
- Grade feeds capability_map: per-domain task strength + hallucination rate

Over time, the router learns which question-types each local can actually handle.
Locals earn their way off the bench by consistently grading well.

Async + sampled by design: never blocks the user reply, never saturates CPU-bound
local models (one shadow at a time, only a fraction of turns).

Judge selection reads from $NEXUS_GRADER_JUDGES env var (comma-separated list)
or falls back to the built-in priority list.
"""

import asyncio
import json
import logging
import os
import random
import re

from .providers import ProviderClient
from .capability_map import CapabilityMap, DEFAULT_PATH

logger = logging.getLogger(__name__)

# Local model to calibrate. Override with NEXUS_GRADER_LOCAL env var.
_DEFAULT_LOCAL = os.environ.get("NEXUS_GRADER_LOCAL", "ollama")

# Online judges priority list. First available with a configured key wins.
# Override with NEXUS_GRADER_JUDGES=gemini,groq,cerebras (comma-separated).
_DEFAULT_JUDGES = ["gemini", "groq", "cerebras", "mistral"]
_JUDGE_CANDIDATES = [
    j.strip()
    for j in os.environ.get("NEXUS_GRADER_JUDGES", ",".join(_DEFAULT_JUDGES)).split(",")
    if j.strip()
]

_GRADE_PROMPT = """You are grading a student AI's answer against a reference answer.

QUESTION:
{q}

REFERENCE ANSWER (treat as correct):
{ref}

STUDENT ANSWER:
{ans}

Grade the student answer. Reply with ONLY a JSON object, no prose:
{{"score": 0.0-1.0, "hallucinated": true|false}}
- score = how well the student matches the reference on accuracy AND completeness.
- hallucinated = true if the student invented specifics not supported by the reference."""


class CapabilityGrader:
    """Shadow-grades a local model using an online judge; records to capability_map."""

    def __init__(
        self,
        sample_rate: float = 0.34,
        local: str = _DEFAULT_LOCAL,
        rng: random.Random | None = None,
        capability_map_path=DEFAULT_PATH,
    ):
        self.sample_rate = sample_rate
        self.local = local
        self.rng = rng or random.Random()
        self._capability_map_path = capability_map_path
        self._client: ProviderClient | None = None
        self._cm: CapabilityMap | None = None
        self._inflight = 0
        self._max_inflight = 1   # CPU-bound local — never run two shadows at once

    def _lazy(self):
        if self._client is None:
            self._client = ProviderClient(timeout=40.0)
        if self._cm is None:
            self._cm = CapabilityMap(self._capability_map_path)
        return self._client, self._cm

    async def maybe_grade(
        self,
        question: str,
        reference_answer: str,
        domain: str = "general",
    ) -> dict | None:
        """Sampled + concurrency-capped. Safe to fire-and-forget. None if skipped."""
        if not question or not reference_answer:
            return None
        if self.rng.random() > self.sample_rate:
            return None
        if self._inflight >= self._max_inflight:
            logger.debug("grader: shadow already in flight — skipping")
            return None
        self._inflight += 1
        try:
            return await self._grade(question, reference_answer, domain)
        except Exception as e:
            logger.warning(f"grader: failed: {type(e).__name__}: {e}")
            return None
        finally:
            self._inflight -= 1

    async def _grade(
        self, question: str, reference_answer: str, domain: str
    ) -> dict | None:
        client, cm = self._lazy()

        # 1) Shadow-run the local model on the same question
        try:
            local_ans = await asyncio.wait_for(
                client.complete(self.local, question), timeout=35
            )
        except Exception as e:
            logger.info(f"grader: local {self.local} unavailable ({type(e).__name__}) — skip")
            return None
        if not local_ans.strip():
            return None

        # 2) Pick an available online judge
        available = client.available()
        judge = next((j for j in _JUDGE_CANDIDATES if j in available), None)
        if not judge:
            logger.info("grader: no online judge available — skip")
            return None

        # 3) Judge grades local answer against the reference
        verdict_raw = await asyncio.wait_for(
            client.complete(judge, _GRADE_PROMPT.format(
                q=question[:1500], ref=reference_answer[:2000], ans=local_ans[:2000]
            )),
            timeout=30,
        )
        verdict = self._parse_verdict(verdict_raw)
        if verdict is None:
            logger.info(f"grader: judge {judge} returned unparseable verdict — skip")
            return None

        prof = cm.record_grade(self.local, domain, verdict["score"], verdict["hallucinated"])
        cm.save()
        logger.info(
            f"grader: {self.local} on '{domain}' → score={verdict['score']} "
            f"hallucinated={verdict['hallucinated']} (judge={judge}, samples={prof['samples']})"
        )
        return prof

    @staticmethod
    def _parse_verdict(raw: str) -> dict | None:
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
