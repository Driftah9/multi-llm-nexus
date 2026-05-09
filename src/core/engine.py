"""
Nexus engine — the tick cycle that drives everything.
Receives messages from adapters, routes through triage and providers,
returns responses back to the originating adapter.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from .router import Router
from .session import SessionStore, Session
from .triage import Triage

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

    def __init__(self, router: Router, session_store: SessionStore, triage: Triage, config: dict):
        self.router = router
        self.sessions = session_store
        self.triage = triage
        self.config = config
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
        except Exception as e:
            logger.error(f"Processing error for session {inbound.session_id}: {e}")
            return None

    def _build_system(self, inbound: InboundMessage, session: Session) -> str:
        operator_name = self.config.get("operator_name", "Operator")
        agent_name = self.config.get("agent_name", "Nexus")
        return (
            f"You are {agent_name}, an AI assistant for {operator_name}. "
            f"Platform: {inbound.platform}. "
            f"Session: {session.session_id} ({session.message_count} messages). "
            f"Be direct and helpful."
        )
