"""
Nexus Engine — the tick cycle that drives everything.

Operates in two modes:

  ACTIVE MODE (during conversation):
    Process message queue, check triggers, route through triage/providers.
    Burns tokens. Auto-transitions to STANDBY after idle_timeout.

  STANDBY MODE (idle):
    Linux watchers monitor for events (zero tokens).
    When a wake trigger fires or a message is enqueued, transitions to ACTIVE.

  ACTIVE → STANDBY: No activity for idle_timeout seconds
  STANDBY → ACTIVE: Wake trigger received, or adapter enqueues a message
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

from .router import Router
from .session import SessionStore, Session
from .triage import Triage
from .orchestrator import Orchestrator
from .watchers import TriggerListener, WakeEvent, WakeReason, WatcherConfig

if TYPE_CHECKING:
    from ..adapters.base import BaseAdapter
    from ..providers.base import BaseProvider, Message

logger = logging.getLogger("nexus.engine")


class EngineMode(Enum):
    ACTIVE = "active"
    STANDBY = "standby"
    STARTING = "starting"
    STOPPING = "stopping"


@dataclass
class TickResult:
    tick_number: int
    messages_processed: int
    tasks_checked: int
    actions_taken: list[str]
    effort_level: str  # "idle", "low", "medium", "high"
    mode: str = "active"


@dataclass
class InboundMessage:
    platform: str
    channel_id: str
    session_id: str
    user_id: str
    username: str
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass
class OutboundMessage:
    platform: str
    channel_id: str
    session_id: str
    content: str
    metadata: dict = field(default_factory=dict)


class Engine:
    """
    Hybrid tick-cycle engine. Adapters push InboundMessage objects via enqueue().
    Engine routes, processes, and returns OutboundMessage objects via callbacks.
    """

    def __init__(
        self,
        router: Router,
        session_store: SessionStore,
        triage: Triage,
        config: dict,
        orchestrator: Optional[Orchestrator] = None,
    ):
        self.router = router
        self.sessions = session_store
        self.triage = triage
        self.config = config
        self.orchestrator = orchestrator

        self._queue: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self._running = False
        self.mode = EngineMode.STARTING
        self.tick_count = 0

        # Hybrid mode config
        self.tick_interval = config.get("tick_interval", 30)
        self.idle_timeout = config.get("idle_timeout", 300)
        self.last_activity = time.time()

        # Watcher infrastructure
        watcher_config = WatcherConfig(config)
        self.trigger_listener = TriggerListener(watcher_config)

        # Adapter callbacks — adapters register themselves so engine can push responses
        self._response_handlers: dict[str, callable] = {}

        # Space registry reference — set by main.py after construction
        self.space_registry = None

        # Observability
        self.mode_transitions: list[dict] = []

    def register_response_handler(self, platform: str, handler: callable):
        """Adapter registers a callback to receive outbound messages."""
        self._response_handlers[platform] = handler

    def enqueue(self, message: InboundMessage):
        """Adapter calls this to hand off an inbound message. Also activates the engine."""
        self._queue.put_nowait(message)
        self.last_activity = time.time()
        if self.mode == EngineMode.STANDBY:
            self._set_mode(EngineMode.ACTIVE)

    async def start(self):
        self._running = True
        self._set_mode(EngineMode.STANDBY)
        logger.info("Nexus engine started (STANDBY)")

        while self._running:
            try:
                if self.mode == EngineMode.STANDBY:
                    await self._standby_loop()
                elif self.mode == EngineMode.ACTIVE:
                    await self._active_loop()
            except Exception as e:
                logger.error(f"Engine loop error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        self._set_mode(EngineMode.STOPPING)
        self._running = False
        logger.info("Nexus engine stopped")

    # ── Standby Loop ─────────────────────────────────────────────

    async def _standby_loop(self):
        """Zero-token wait. Check triggers and queue for wake signals."""
        # Check queue first — adapter may have pushed a message
        if not self._queue.empty():
            self._set_mode(EngineMode.ACTIVE)
            return

        # Poll trigger file (blocks up to 60s)
        events = await self.trigger_listener.wait_for_event(timeout=60.0)
        if events:
            self._set_mode(EngineMode.ACTIVE)
            await self._process_wake_events(events)
            return

        # Also check if a message arrived while we were polling
        if not self._queue.empty():
            self._set_mode(EngineMode.ACTIVE)

    async def _process_wake_events(self, events: list[WakeEvent]):
        events.sort(key=lambda e: e.priority)
        for event in events:
            self.last_activity = time.time()
            logger.info(f"Wake event: {event.reason.value} from {event.source} — {event.summary}")

    # ── Active Loop ──────────────────────────────────────────────

    async def _active_loop(self):
        """Process messages and check triggers. Auto-idle after timeout."""
        result = await self._tick()

        # Check idle timeout → transition to standby
        idle_seconds = time.time() - self.last_activity
        if idle_seconds >= self.idle_timeout and result.effort_level == "idle":
            logger.info(f"Idle for {idle_seconds:.0f}s — transitioning to STANDBY")
            self._set_mode(EngineMode.STANDBY)
            return

        await asyncio.sleep(self.tick_interval)

    async def _tick(self) -> TickResult:
        self.tick_count += 1
        messages_processed = 0
        actions = []

        # Drain the message queue
        while not self._queue.empty():
            try:
                msg = self._queue.get_nowait()
                outbound = await self._process(msg)
                if outbound:
                    await self._deliver(outbound)
                    messages_processed += 1
                    actions.append(f"replied:{msg.platform}:{msg.channel_id}")
            except Exception as e:
                logger.error(f"Message processing error: {e}")

        # Check for pending wake events from watchers
        pending = self.trigger_listener.read_events()
        if pending:
            self.last_activity = time.time()
            await self._process_wake_events(pending)
            actions.append(f"wake_events:{len(pending)}")

        effort = "idle" if messages_processed == 0 and not pending else "medium"
        if messages_processed > 0:
            self.last_activity = time.time()

        return TickResult(
            tick_number=self.tick_count,
            messages_processed=messages_processed,
            tasks_checked=len(pending),
            actions_taken=actions,
            effort_level=effort,
            mode=self.mode.value,
        )

    # ── Message Processing ───────────────────────────────────────

    async def _process(self, inbound: InboundMessage) -> Optional[OutboundMessage]:
        try:
            context = inbound.metadata.get("context", inbound.channel_id)

            if self.orchestrator and self.orchestrator.should_orchestrate(context):
                return await self._process_orchestrated(inbound, context)

            return await self._process_standard(inbound)
        except Exception as e:
            logger.error(f"Processing error for session {inbound.session_id}: {e}")
            return None

    async def _process_orchestrated(
        self, inbound: InboundMessage, context: str
    ) -> Optional[OutboundMessage]:
        workspace_name = self.orchestrator.get_workspace_for_display(context) or context
        operator_context = self._build_operator_context(inbound)

        logger.info(f"Orchestrating in workspace '{workspace_name}' for {context}")

        result = await self.orchestrator.dispatch(
            message=inbound.content,
            context=context,
            session_key=inbound.session_id,
            operator_context=operator_context,
        )

        if result.bypassed:
            return await self._process_standard(inbound)

        return OutboundMessage(
            platform=inbound.platform,
            channel_id=inbound.channel_id,
            session_id=inbound.session_id,
            content=result.response,
            metadata={
                "orchestrated": True,
                "specialists": result.specialists_used,
                "synthesized": result.synthesized,
                "cost_usd": result.total_cost,
                "elapsed": result.elapsed,
            },
        )

    async def _process_standard(self, inbound: InboundMessage) -> Optional[OutboundMessage]:
        triage_result = await self.triage.classify(inbound.content)
        session = self.sessions.get_or_create(
            session_id=inbound.session_id,
            platform=inbound.platform,
            channel_id=inbound.channel_id,
            provider_name=self.router.default,
        )
        provider = self.router.route(inbound.content, triage_result.task_type)

        from ..providers.base import Message
        messages = [Message(role="user", content=inbound.content)]
        system = self._build_system(inbound, session)

        logger.debug(f"Routing to {provider} (task={triage_result.task_type})")
        response = await provider.send(messages, system=system)

        self.sessions.update(session)

        return OutboundMessage(
            platform=inbound.platform,
            channel_id=inbound.channel_id,
            session_id=inbound.session_id,
            content=response.content,
            metadata={"provider": repr(provider), "task_type": triage_result.task_type},
        )

    async def _deliver(self, outbound: OutboundMessage):
        handler = self._response_handlers.get(outbound.platform)
        if handler:
            try:
                await handler(outbound)
            except Exception as e:
                logger.error(f"Delivery failed for {outbound.platform}: {e}")
        else:
            logger.warning(f"No response handler for platform: {outbound.platform}")

    # ── Helpers ──────────────────────────────────────────────────

    def _build_system(self, inbound: InboundMessage, session: Session) -> str:
        operator_name = self.config.get("operator_name", "Operator")
        agent_name = self.config.get("agent_name", "Nexus")
        return (
            f"You are {agent_name}, an AI assistant for {operator_name}. "
            f"Platform: {inbound.platform}. "
            f"Session: {session.session_id} ({session.message_count} messages). "
            f"Be direct and helpful."
        )

    def _build_operator_context(self, inbound: InboundMessage) -> str:
        operator_name = self.config.get("operator_name", "Operator")
        return (
            f"Operator: {operator_name}. "
            f"Platform: {inbound.platform}. "
            f"User: {inbound.username}."
        )

    # ── Mode Management ──────────────────────────────────────────

    def _set_mode(self, new_mode: EngineMode):
        old_mode = self.mode
        self.mode = new_mode
        self.mode_transitions.append({
            "from": old_mode.value,
            "to": new_mode.value,
            "timestamp": time.time(),
            "tick_count": self.tick_count,
        })
        if len(self.mode_transitions) > 100:
            self.mode_transitions = self.mode_transitions[-100:]
        if old_mode != new_mode:
            logger.info(f"Engine mode: {old_mode.value} → {new_mode.value}")

    def get_status(self) -> dict:
        return {
            "mode": self.mode.value,
            "tick_count": self.tick_count,
            "running": self._running,
            "idle_seconds": time.time() - self.last_activity,
            "queue_size": self._queue.qsize(),
            "transitions": len(self.mode_transitions),
        }
