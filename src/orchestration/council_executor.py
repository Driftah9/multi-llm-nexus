"""Council executor — fan-out, anonymize, peer-review, synthesize.

Given a RoutePlan with mode="council", this module:
  1. Fans out the user prompt to all council members in parallel
  2. Anonymizes responses (strips provider identity → labels A/B/C)
  3. Sends anonymized responses back to each member for peer ranking
  4. Aggregates rankings via EWMA-weighted Borda count
  5. Returns a CouncilResult with the synthesis_prompt for the chairman

The chairman (primary orchestrator) is NEVER called here. The caller routes
synthesis_prompt back through the main bridge after run() returns.

Design principle: SURFACE, don't BLEND. Where the council agrees, state
consensus. Where it conflicts, present BOTH positions with the tradeoff —
never silently average disagreement into one smooth answer.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .anonymize import anonymize, aggregate_rankings
from .capability_map import CapabilityMap
from .council_router import RoutePlan
from .providers import ProviderClient

logger = logging.getLogger(__name__)

# Max council member responses to include in synthesis prompt.
MAX_SYNTHESIS_RESPONSES = 3

_RANK_PROMPT = """\
Below are {n} responses to the same question, labeled {labels}.
Rank them from BEST to WORST on accuracy, clarity, and completeness.

{anon_block}

Reply with ONLY the ranking, one label per line, best first. Example:
Response B
Response A
Response C"""

_SYNTHESIS_PROMPT = """\
You are the chairman synthesizing a council of independent AI responses.

Original question:
{user_prompt}

The following {n} independent responses were ranked by peer review (best to worst):

{ranked_block}

Produce one authoritative answer, but do NOT blend disagreement away:
- Where the responses AGREE, state it directly as the consensus.
- Where they CONFLICT or diverge on a material point, SURFACE both positions and
  the tradeoff between them — name the disagreement, don't silently pick one or
  average them into mush. If the evidence favours one side, say so and why.
- Flag anything no response could establish as an open question.

Lead with the recommendation/answer, then the supporting analysis. Do not mention
the ranking process or label the responses — speak as a single authoritative voice."""


@dataclass
class CouncilResult:
    synthesis_prompt: str                     # pass to chairman
    raw_responses: Dict[str, str]             # provider -> first-pass response text
    rankings: List[Dict]                      # [{provider, average_rank, votes}]
    label_map: Dict[str, str]                 # "Response A" -> provider name
    top_provider: str                         # winner (lowest average_rank)
    failed_providers: List[str] = field(default_factory=list)
    peer_review_skipped: bool = False         # True if <2 members responded


async def run(
    plan: RoutePlan,
    user_prompt: str,
    system_prompt: str = "",
    domain: str = "general",
    capability_map: Optional[CapabilityMap] = None,
    timeout: float = 60.0,
) -> CouncilResult:
    """Execute the council flow for a given RoutePlan.

    Args:
        plan:           RoutePlan with mode="council", chairman, members
        user_prompt:    the user's message (already context-expanded if needed)
        system_prompt:  optional system context passed to all members
        domain:         domain string for capability_map updates
        capability_map: optional CapabilityMap; updated in-place if provided
        timeout:        per-call timeout in seconds

    Returns:
        CouncilResult — pass .synthesis_prompt to the chairman.
    """
    client = ProviderClient(timeout=timeout)
    members = [m for m in plan.members if _member_available(m, client)]

    if not members:
        logger.warning("council_executor: no available members, falling back to empty council")
        return _empty_result(user_prompt)

    # ── Step 1: Fan out to all members in parallel ─────────────────────────
    logger.info(f"council_executor: fanning out to {len(members)} members: {members}")
    raw = await client.fan_out(members, user_prompt)

    successes: Dict[str, str] = {}
    failures: List[str] = []
    for provider, result in raw.items():
        if isinstance(result, Exception):
            logger.warning(f"council_executor: {provider} failed: {result}")
            failures.append(provider)
        else:
            successes[provider] = str(result)

    if not successes:
        logger.error("council_executor: all members failed, returning empty result")
        return _empty_result(user_prompt, failed=failures)

    # ── Step 2: Anonymize ──────────────────────────────────────────────────
    response_list = [{"provider": p, "response": r} for p, r in successes.items()]
    anon_block, label_map = anonymize(response_list)

    # ── Step 3: Peer review ────────────────────────────────────────────────
    rankings: List[Dict] = []
    if len(successes) >= 2:
        label_list = list(label_map.keys())
        labels_str = ", ".join(label_list)
        rank_prompt = _RANK_PROMPT.format(
            n=len(successes),
            labels=labels_str,
            anon_block=anon_block,
        )
        rank_raw = await client.fan_out(list(successes.keys()), rank_prompt)

        parsed: List[List[str]] = []
        for provider, result in rank_raw.items():
            if isinstance(result, Exception):
                logger.debug(f"council_executor: {provider} failed peer review: {result}")
                continue
            ranking = _parse_ranking(str(result), label_list)
            if ranking:
                parsed.append(ranking)

        if parsed:
            rankings = aggregate_rankings(parsed, label_map)
            logger.info(f"council_executor: aggregate rankings: {rankings}")
    else:
        logger.info("council_executor: only 1 member responded, skipping peer review")

    # ── Step 4: Build synthesis prompt ─────────────────────────────────────
    top_providers = _top_providers(rankings, successes, MAX_SYNTHESIS_RESPONSES)
    top_provider = top_providers[0] if top_providers else list(successes.keys())[0]

    ranked_block = _build_ranked_block(top_providers, successes, rankings, label_map)
    synthesis_prompt = _SYNTHESIS_PROMPT.format(
        user_prompt=user_prompt,
        n=len(top_providers),
        ranked_block=ranked_block,
    )

    # ── Step 5: Update capability_map ──────────────────────────────────────
    if capability_map is not None and rankings:
        _update_capability_map(capability_map, rankings, domain)

    return CouncilResult(
        synthesis_prompt=synthesis_prompt,
        raw_responses=successes,
        rankings=rankings,
        label_map=label_map,
        top_provider=top_provider,
        failed_providers=failures,
        peer_review_skipped=(len(successes) < 2),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _member_available(provider: str, client: ProviderClient) -> bool:
    if provider not in client.registry:
        logger.debug(f"council_executor: {provider} not in registry, skipping")
        return False
    spec = client.registry[provider]
    if spec.api_key_env is None:
        return True   # local provider, no key needed
    import os
    return bool(os.getenv(spec.api_key_env))


def _parse_ranking(text: str, valid_labels: List[str]) -> List[str]:
    found = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        for label in valid_labels:
            if label in line and label not in seen:
                found.append(label)
                seen.add(label)
    if not found:
        positions = {label: text.find(label) for label in valid_labels if label in text}
        found = [label for label, _ in sorted(positions.items(), key=lambda x: x[1]) if x[1] >= 0]
    return found


def _top_providers(
    rankings: List[Dict],
    successes: Dict[str, str],
    n: int,
) -> List[str]:
    if rankings:
        ordered = [r["provider"] for r in rankings if r["provider"] in successes]
    else:
        ordered = list(successes.keys())
    return ordered[:n]


def _build_ranked_block(
    ordered_providers: List[str],
    successes: Dict[str, str],
    rankings: List[Dict],
    label_map: Dict[str, str],
) -> str:
    provider_to_label = {v: k for k, v in label_map.items()}
    blocks = []
    for rank, provider in enumerate(ordered_providers, start=1):
        label = provider_to_label.get(provider, provider)
        blocks.append(f"#{rank} ({label}):\n{successes[provider]}")
    return "\n\n".join(blocks)


def _update_capability_map(
    cm: CapabilityMap,
    rankings: List[Dict],
    domain: str,
) -> None:
    n = len(rankings)
    if n == 0:
        return
    for i, entry in enumerate(rankings):
        score = 1.0 - (i / max(1, n - 1)) if n > 1 else 0.7
        cm.update(domain, entry["provider"], score)
    try:
        cm.save()
    except Exception as e:
        logger.warning(f"council_executor: capability_map save failed: {e}")


def _empty_result(user_prompt: str, failed: List[str] = None) -> CouncilResult:
    return CouncilResult(
        synthesis_prompt=user_prompt,
        raw_responses={},
        rankings=[],
        label_map={},
        top_provider="",
        failed_providers=failed or [],
        peer_review_skipped=True,
    )
