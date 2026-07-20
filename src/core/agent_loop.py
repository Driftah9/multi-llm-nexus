"""Agent loop — the tool-calling engine that turns any tools-capable provider
into an actor.

Ported DOWN from the live claude-brain executor (adapters/core/agent_loop.py,
built + live-validated 2026-07-20 on gemini-flash and groq-70b, including a
guard-refusal test). This module is the ToolRegistry's first real consumer:
before it, tool schemas had no channel into any provider (send() took no tools
argument), no code executed a returned tool_call, and requires_confirmation was
never enforced. Now:

    Message list + ToolDef schemas → provider.send(tools=…) → the provider
    parses tool_calls into ProviderResponse → confirmation gate → registry
    executes → results appended as role:"tool" Messages → repeat until the
    model answers in plain text (or max-iters / wall-clock / token budget cuts
    it off — each cut ends with ONE tool-less summarize call so a run always
    ends in text).

Safety: a ToolDef with requires_confirmation=True is executed ONLY if the
caller supplies a confirm_fn that approves it — no confirm_fn means such calls
are refused (fail-closed). The refusal text is fed back to the model as an
error tool result, and the system preamble tells it not to retry. Operators
wanting claude-brain-style guard scripts wire them in via confirm_fn (see the
live core/agent_guards.py for the reference shape).

Provider dialects are the provider's own job here (each parses tool_calls into
neutral ToolCall objects in its send()) — cleaner than the live system's single
normalizer. The openai-compatible provider type has full passthrough; others
accept-and-ignore tools until implemented.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..providers.base import BaseProvider, Message, ProviderResponse, ToolResult
from ..tools.definitions import ToolRegistry

logger = logging.getLogger("nexus.agent_loop")

_AGENT_PREAMBLE = (
    "You are an agent with access to EXACTLY the tools listed in this request — "
    "no others. Rules:\n"
    "1. Use tools to accomplish the task; do not describe what you would do.\n"
    "2. NEVER fabricate a tool result. If a tool errors, read the error and adapt.\n"
    "3. A result starting with REFUSED is a safety gate declining the action. Do "
    "NOT retry the same call — adjust your approach or report the refusal.\n"
    "4. When the task is complete (or impossible), reply in plain text with no "
    "tool calls: state what you did and the outcome.\n\n"
)


@dataclass
class AgentRunResult:
    text: str                       # final assistant text ("" on hard failure)
    iterations: int = 0
    tool_events: list = field(default_factory=list)
    termination: str = "done"       # done|max_iters|timeout|token_budget|error
    error: bool = False
    usage: dict = field(default_factory=dict)  # summed input/output tokens


def _sum_usage(total: dict, usage: dict) -> dict:
    for k in ("input_tokens", "output_tokens"):
        total[k] = total.get(k, 0) + int(usage.get(k) or 0)
    return total


async def run_agent(
    provider: BaseProvider,
    prompt: str,
    *,
    registry: ToolRegistry,
    system_prompt: str = "",
    max_iters: int = 10,
    timeout_s: float = 300.0,
    token_budget: int = 60_000,
    categories: Optional[list[str]] = None,      # limit the tool set (registry categories)
    confirm_fn: Optional[Callable[[str, dict], Awaitable[bool]]] = None,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None,
) -> AgentRunResult:
    """Drive one agentic run on `provider`. Never raises — failures come back
    as AgentRunResult(error=True) so chains above can fail over cleanly."""
    if not provider.supports_tools():
        return AgentRunResult(text=f"{provider!r} does not support tools",
                              termination="error", error=True)

    schemas = registry.schemas(categories)
    system = _AGENT_PREAMBLE + (system_prompt or "")
    messages: list[Message] = [Message(role="user", content=prompt)]
    events: list[dict] = []
    usage_total: dict = {}
    start = time.monotonic()

    async def _notify(line: str) -> None:
        if on_status:
            try:
                await on_status(line)
            except Exception:
                pass

    async def _final_summarize(reason: str, iteration: int) -> AgentRunResult:
        try:
            resp = await provider.send(
                messages + [Message(role="user", content=(
                    "Stop using tools now. Summarize what you accomplished and "
                    "what remains, in plain text."))],
                system=system)
            _sum_usage(usage_total, resp.usage)
            text = (resp.content or "").strip()
        except Exception as e:
            logger.error(f"agent_loop: summarize call failed: {e}")
            text = ""
        return AgentRunResult(text=text, iterations=iteration, tool_events=events,
                              termination=reason, error=not text, usage=usage_total)

    iteration = 0
    for iteration in range(1, max_iters + 1):
        if time.monotonic() - start > timeout_s:
            return await _final_summarize("timeout", iteration)
        if usage_total.get("input_tokens", 0) + usage_total.get("output_tokens", 0) > token_budget:
            return await _final_summarize("token_budget", iteration)

        try:
            resp: ProviderResponse = await provider.send(messages, system=system,
                                                         tools=schemas)
        except Exception as e:
            logger.error(f"agent_loop: send failed on iter {iteration}: {e}")
            return AgentRunResult(text=f"Error: provider call failed: {e}",
                                  iterations=iteration, tool_events=events,
                                  termination="error", error=True, usage=usage_total)

        _sum_usage(usage_total, resp.usage)

        if not resp.tool_calls:
            text = (resp.content or "").strip()
            return AgentRunResult(text=text, iterations=iteration, tool_events=events,
                                  termination="done", error=not text, usage=usage_total)

        # Assistant turn (with its tool_calls) enters the transcript; each call
        # gets a role:"tool" reply — providers serialize this to their wire form.
        messages.append(Message(role="assistant", content=resp.content or "",
                                tool_calls=resp.tool_calls))

        for call in resp.tool_calls:
            t0 = time.monotonic()
            event = {"tool": call.name, "refused": False, "error": False}
            tool = registry.get(call.name)

            if tool is not None and tool.requires_confirmation:
                approved = False
                if confirm_fn is not None:
                    try:
                        approved = bool(await confirm_fn(call.name, call.arguments))
                    except Exception:
                        approved = False
                if not approved:
                    # Fail-closed: no confirm hook (or denial) → refuse, tell the model.
                    event["refused"] = True
                    events.append(event)
                    messages.append(Message(role="tool", content="", tool_results=[
                        ToolResult(call_id=call.call_id, is_error=True, content=(
                            f"REFUSED: {call.name} requires operator confirmation "
                            "and none was granted. Do not retry."))]))
                    await _notify(f"⛔ {call.name}: refused (needs confirmation)")
                    continue

            result = await registry.execute(call.name, call.arguments or {})
            is_error = isinstance(result, dict) and "error" in result
            event["error"] = is_error
            event["ms"] = int((time.monotonic() - t0) * 1000)
            events.append(event)
            messages.append(Message(role="tool", content="", tool_results=[
                ToolResult(call_id=call.call_id, content=str(result),
                           is_error=is_error)]))
            await _notify(f"⚙ {call.name}")

    return await _final_summarize("max_iters", iteration)
