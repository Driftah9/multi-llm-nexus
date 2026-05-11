"""
Nexus engine — the tick cycle that drives everything.
Receives messages from adapters, routes through triage and providers,
returns responses back to the originating adapter.

When a message's context belongs to an orchestrator-enabled workspace,
the engine delegates to the Orchestrator for specialist dispatch.
Otherwise, it routes directly to the primary provider.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from .router import Router
from .session import SessionStore, Session
from .triage import Triage
from .orchestrator import Orchestrator

if TYPE_CHECKING:
    from ..adapters.base import BaseAdapter
    from ..providers.base import BaseProvider, Message

logger = logging.getLogger("nexus.engine")


@dataclass
class InboundMessage:
    platform: str
    channel_id: str
    session_id: str
    user_id: str
    username: str
    content: str
    metadata: dict


@dataclass
class OutboundMessage:
    platform: str
    channel_id: str
    session_id: str
    content: str
    metadata: dict


class Engine:
    """
    The core tick cycle. Adapters push InboundMessage objects in.
    Engine routes, processes, and pushes OutboundMessage objects back.
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

    def enqueue(self, message: InboundMessage):
        """Adapter calls this to hand off an inbound message."""
        self._queue.put_nowait(message)

    async def start(self):
        self._running = True
        logger.info("Nexus engine started")
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                asyncio.create_task(self._process(msg))
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Engine loop error: {e}")

    async def stop(self):
        self._running = False
        logger.info("Nexus engine stopped")

    async def _process(self, inbound: InboundMessage) -> Optional[OutboundMessage]:
        try:
            context = inbound.metadata.get("context", inbound.channel_id)

            # Check orchestrator path first
            if self.orchestrator and self.orchestrator.should_orchestrate(context):
                return await self._process_orchestrated(inbound, context)

            return await self._process_standard(inbound)
        except Exception as e:
            logger.error(f"Processing error for session {inbound.session_id}: {e}")
            return None

    async def _process_orchestrated(
        self, inbound: InboundMessage, context: str
    ) -> Optional[OutboundMessage]:
        """Delegate to orchestrator for specialist dispatch + synthesis."""
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
            logger.debug(f"Orchestrator bypassed for {context} — falling through")
            return await self._process_standard(inbound)

        specialists_label = ", ".join(result.specialists_used)
        metadata = {
            "orchestrated": True,
            "specialists": result.specialists_used,
            "synthesized": result.synthesized,
            "cost_usd": result.total_cost,
            "elapsed": result.elapsed,
        }

        logger.info(
            f"Orchestrated response: specialists=[{specialists_label}] "
            f"synthesized={result.synthesized} cost=${result.total_cost:.4f} "
            f"elapsed={result.elapsed:.1f}s"
        )

        return OutboundMessage(
            platform=inbound.platform,
            channel_id=inbound.channel_id,
            session_id=inbound.session_id,
            content=result.response,
            metadata=metadata,
        )

    async def _process_standard(self, inbound: InboundMessage) -> Optional[OutboundMessage]:
        """Standard single-session processing (no specialist dispatch)."""
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
