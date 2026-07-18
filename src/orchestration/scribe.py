"""Scribe lane — provider-neutral LLM completions for the platform's internal chores.

The "house model" lane: distillation, fact extraction, checkpoint compaction, the
swarm planner floor and grading rubric calls are text-in/text-out and never face
the user, so they must not burn (or depend on) the primary/deep provider. This
module gives every scribe job one call that walks a configurable backend chain.

Ported DOWN from the live claude-brain `core/scribe.py`. The live install's default
chain ends at a `claude --model haiku` CLI backstop — that is operator-specific and
NOT agnostic. Nexus already runs an OpenAI-compatible endpoint that does tier
routing (`nexus-nano`), so the agnostic default backstop is **Nexus itself**: a
scribe job posts to the local Nexus API asking for the nano tier, and Nexus picks
whatever cheap provider the operator configured. No provider is hardcoded.

NEXUS:PORTABLE — the mechanism (ordered backend chain, stdlib-only so bare hook
  scripts can import it, cheapest-capable-lane-first degrading toward a backstop)
  is general architecture. Any install points SCRIBE_BACKENDS at what it has.
NEXUS:OPERATOR — the DEFAULT chain ("nexus") routes to this deployment's own nano
  tier. Add a local "ollama" lane by setting SCRIBE_OLLAMA_MODEL, or reorder via
  SCRIBE_BACKENDS. Local models are opt-in until a capable one is configured.

Env knobs (all optional; read fresh per call):
  SCRIBE_BACKENDS       comma order, e.g. "ollama,nexus" (default "nexus")
  NEXUS_SCRIBE_URL      default http://127.0.0.1:8080/v1/chat/completions
  NEXUS_SCRIBE_MODEL    tier model asked of the local Nexus API (default "nexus-nano")
  SCRIBE_OLLAMA_URL     default http://localhost:11434/v1/chat/completions
  SCRIBE_OLLAMA_MODEL   model name for the ollama lane; unset -> lane skipped
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_NEXUS_DEFAULT = "http://127.0.0.1:8080/v1/chat/completions"
_OLLAMA_DEFAULT = "http://localhost:11434/v1/chat/completions"


def _backends() -> list[str]:
    raw = os.environ.get("SCRIBE_BACKENDS") or "nexus"
    return [b.strip().lower() for b in raw.split(",") if b.strip()]


def _post_openai(url: str, prompt: str, system: str, model: str | None,
                 max_tokens: int, timeout: float, api_key: str | None = None) -> str:
    """Minimal OpenAI-compatible chat call, stdlib only. Returns text or ""."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body: dict = {"messages": messages, "max_tokens": max_tokens}
    if model:
        body["model"] = model
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return text.strip()


def complete(prompt: str, *, system: str = "", max_tokens: int = 900,
             timeout: float = 60.0, backends: list[str] | None = None) -> tuple[str, str]:
    """Run a scribe completion through the backend chain.

    Returns (text, backend_used); ("", "") when every lane failed. Never raises —
    scribe jobs are best-effort and must not take their callers down.
    """
    for backend in (backends if backends is not None else _backends()):
        try:
            if backend == "ollama":
                model = os.environ.get("SCRIBE_OLLAMA_MODEL", "")
                if not model:
                    continue  # lane not configured on this install
                url = os.environ.get("SCRIBE_OLLAMA_URL", _OLLAMA_DEFAULT)
                text = _post_openai(url, prompt, system, model, max_tokens, timeout)
            elif backend == "nexus":
                url = os.environ.get("NEXUS_SCRIBE_URL", _NEXUS_DEFAULT)
                model = os.environ.get("NEXUS_SCRIBE_MODEL", "nexus-nano")
                # the local Nexus API adapter requires a bearer token (adapters.yaml
                # openai_api.api_key); default matches the shipped "nexus" placeholder.
                key = os.environ.get("NEXUS_API_KEY") or os.environ.get("NEXUS_SCRIBE_KEY") or "nexus"
                text = _post_openai(url, prompt, system, model, max_tokens, timeout, api_key=key)
            else:
                logger.warning(f"scribe: unknown backend '{backend}' skipped")
                continue
            if text:
                return text, backend
            logger.info(f"scribe: backend '{backend}' returned empty, trying next")
        except Exception as e:
            logger.info(f"scribe: backend '{backend}' failed ({e}), trying next")
    return "", ""


async def complete_async(prompt: str, *, system: str = "", max_tokens: int = 900,
                         timeout: float = 60.0,
                         backends: list[str] | None = None) -> tuple[str, str]:
    """Async wrapper (thread offload — the sync path is blocking urllib)."""
    return await asyncio.to_thread(
        complete, prompt, system=system, max_tokens=max_tokens,
        timeout=timeout, backends=backends)
