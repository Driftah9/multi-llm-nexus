"""Worker delegation (D2) — the orchestrator's fan-out to cheap workers.

This is the piece that dispatches LLM subtasks to nano/standard workers and
collects findings. Delegation authority lives HERE, called by the
orchestrator (task owner) — never by the routing layer and never by council
(council only validates, see council_executor.py).

Workers are stateless, tool-free, and NEVER canonical-memory writers (D-009 +
single-writer principle). Their output is staged (orchestration/staging.py),
not written straight to memory or returned straight to the user.

NEXUS:PORTABLE — ported from live claude-brain's orchestration/delegator.py.
Worker selection is delegated entirely to worker_pool.worker_candidates(),
which already encodes cheapest-first tier ordering + voice/communicator
reservation from config/providers.yaml (the Nexus analog of live's
TIER_ROSTER walk + claude-* exclusion). This module does not re-derive a
roster.
"""

import logging
from typing import Dict, List
from uuid import uuid4

from .providers import ProviderClient
from .staging import stage_finding
from .worker_pool import DEFAULT_WORKER_COUNT, worker_candidates

logger = logging.getLogger(__name__)

# Non-Claude/OpenAI-compatible providers have NO tools, NO file/system access,
# NO session. But the caller's system prompt may be written assuming a
# tool-using persona, so small models will role-play having them and
# fabricate results. This preamble is prepended to whatever system prompt
# workers receive and hard-overrides any tool/persona claims downstream of it.
# NEXUS:PORTABLE — inlined from live's core/provider_bridge._TOOL_FREE_PREAMBLE;
# Nexus has no equivalent shared constant yet, so it's localized here rather
# than invented into a new shared module.
_TOOL_FREE_PREAMBLE = (
    "IMPORTANT — YOUR ACTUAL CAPABILITIES: You are a stateless language model with "
    "NO tools, NO file or system access, NO ability to run commands, browse, or "
    "check live state, and NO memory of prior turns. Any tools, integrations, or "
    "persona described later in this prompt do NOT apply to you — ignore them. "
    "NEVER claim to call a tool, NEVER invent tool output, and NEVER report on "
    "system/live state as if you checked it. If answering correctly would require "
    "a tool, live data, or system state you cannot access, say so plainly and stop. "
    "Answer only from your own general knowledge.\n\n"
)

_WORKER_SYSTEM_PREAMBLE = _TOOL_FREE_PREAMBLE + (
    "You are a WORKER handling one delegated sub-task for an orchestrator. "
    "Report your finding plainly and concisely. Do not address the end user, "
    "do not offer to do more, and do not claim to have delegated to or "
    "consulted anyone else — you are a single stateless call.\n\n"
)


async def delegate(
    prompt: str,
    domain: str,
    task_id: str,
    worker_count: int = DEFAULT_WORKER_COUNT,
    timeout: float = 45.0,
) -> List[Dict[str, str]]:
    """Fan the task out to `worker_count` cheap workers, stage their findings.

    Returns the list of staged finding records (also persisted via
    orchestration/staging.py, keyed by task_id, so a crashed/resumed
    orchestrator can re-read them instead of re-running workers).
    """
    client = ProviderClient(timeout=timeout)
    candidates = worker_candidates(client.available(), worker_count)

    if not candidates:
        logger.info("delegator: no eligible worker providers online, skipping delegation")
        return []

    logger.info(f"delegator: dispatching task {task_id} to workers {candidates}")

    results = await client.fan_out(candidates, prompt, system=_WORKER_SYSTEM_PREAMBLE)

    findings: List[Dict[str, str]] = []
    for provider, result in results.items():
        worker_id = f"{provider}-{uuid4().hex[:8]}"
        if isinstance(result, Exception):
            logger.warning(f"delegator: worker {provider} failed: {result}")
            continue
        text = str(result).strip()
        stage_finding(
            task_id=task_id,
            worker_id=worker_id,
            subtask=prompt,
            provider=provider,
            finding=text,
        )
        findings.append({
            "worker_id": worker_id,
            "provider": provider,
            "finding": text,
        })

    if not findings:
        logger.warning(f"delegator: all workers failed for task {task_id}")

    return findings
