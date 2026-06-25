"""Decoupled resume orchestrator — store-independent task resumption.

A background task scans open checkpoints; when a checkpoint's owning provider has recovered
(e.g. a session/quota window reset), ONE resumer (arbitrated by the council lease) continues
the interrupted task under that provider and delivers the result, then clears the checkpoint.

Single-writer-by-construction: the lease ensures exactly one resumer acts; the fencing token
is re-checked right before delivery, so a resumer that lost the lease mid-resume aborts
instead of double-delivering (split-brain guard).

AGNOSTIC by injection — no adapter/harness coupling. The caller supplies three callables:
  invoke_fn(provider, prompt)  -> result-with .text / .error (await-able)
  deliver_fn(meta, text)       -> deliver the resumed answer (await-able)
  is_recovered_fn(provider)    -> bool: has the owning provider recovered?
Plus a capability gate: the resumer only runs where REQUIREMENT is met (≥2 executors +
shared store); on the floor it stays dark.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .council_lease import CouncilLease, REQUIREMENT, shared_store_available
from .council_checkpoint import CheckpointStore, Checkpoint
from .capability_gate import SystemCapabilities, evaluate

logger = logging.getLogger(__name__)

SCAN_INTERVAL_S = 60
STARTUP_DELAY_S = 45
STALE_AFTER_S = 90    # a checkpoint must be idle this long before a resumer touches it


class CouncilResumer:
    def __init__(self, invoke_fn, deliver_fn, is_recovered_fn, *,
                 platform: str = "", capable_executors: int = 1, interval: int = SCAN_INTERVAL_S):
        self._invoke = invoke_fn
        self._deliver = deliver_fn
        self._recovered = is_recovered_fn
        self._platform = platform
        self._capable_executors = capable_executors
        self.interval = interval
        self._task = None
        self._store = CheckpointStore()
        self._lease = CouncilLease(
            f"resumer@{platform.lower() or 'node'}", ttl_ms=300_000,
            namespace=f"resumer:{platform.lower() or 'node'}",
        )

    def gate(self):
        """Activation gate: ≥2 capable executors AND a reachable shared store."""
        sysc = SystemCapabilities(
            capable_executors=self._capable_executors,
            shared_state=shared_store_available(),
        )
        return evaluate(REQUIREMENT, sysc)

    def start(self) -> None:
        g = self.gate()
        if not g.active:
            logger.info(f"council_resumer: {g.reason} — not starting")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("council_resumer: started")

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("council_resumer: stopped")

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_S)
        while True:
            try:
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"council_resumer: scan error: {e}")
            await asyncio.sleep(self.interval)

    async def _scan(self) -> None:
        ready = [cp for cp in await asyncio.to_thread(self._store.list_open)
                 if self._is_resumable(cp)]
        if not ready:
            return
        token = await asyncio.to_thread(self._lease.acquire)
        if token is None:
            return   # another resumer holds the lease
        try:
            for cp in ready:
                await self._resume_one(cp, token)
        finally:
            await asyncio.to_thread(self._lease.release)

    def _is_resumable(self, cp: Checkpoint) -> bool:
        if self._platform and (cp.meta or {}).get("platform") != self._platform:
            return False
        try:
            idle = (datetime.now(timezone.utc) - datetime.fromisoformat(cp.updated_at)).total_seconds()
        except Exception:
            idle = 1e9
        if idle < STALE_AFTER_S:
            return False
        try:
            return bool(self._recovered(cp.orchestrator))
        except Exception:
            return False

    async def _resume_one(self, cp: Checkpoint, token: int) -> None:
        await asyncio.to_thread(self._lease.renew)
        if await asyncio.to_thread(self._lease.is_fenced_out, token):
            return
        result = await self._invoke(cp.orchestrator, self._continuation_prompt(cp))
        if result is None or getattr(result, "error", False):
            return   # leave the checkpoint for a later retry
        # re-check fencing immediately before committing (split-brain guard)
        if await asyncio.to_thread(self._lease.is_fenced_out, token):
            return
        await self._deliver(cp.meta or {}, getattr(result, "text", str(result)))
        await asyncio.to_thread(self._store.delete, cp.task_id)
        logger.info(f"council_resumer: resumed + delivered {cp.task_id} on {cp.orchestrator}")

    @staticmethod
    def _continuation_prompt(cp: Checkpoint) -> str:
        parts = []
        if cp.partial_result:
            parts.append(f"Previous progress:\n{cp.partial_result}")
        parts.append(f"Task: {cp.original_message}")
        parts.append(f"Instruction: {cp.next_step or 'continue'}. Provide the complete result.")
        return "\n\n".join(parts)
