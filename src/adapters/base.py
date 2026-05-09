"""
Base adapter — all platform connectors implement this interface.
Adapters are responsible for:
  - Connecting to the platform
  - Listening for inbound messages
  - Formatting and sending outbound messages
  - Platform-specific formatting (markdown, limits, threading)
"""
from abc import ABC, abstractmethod
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.engine import InboundMessage, OutboundMessage


class BaseAdapter(ABC):
    def __init__(self, config: dict, on_message: Callable):
        self.config = config
        self.on_message = on_message  # callback to engine.enqueue()
        self.platform_name = "unknown"

    @abstractmethod
    async def connect(self):
        """Establish connection to the platform."""
        ...

    @abstractmethod
    async def listen(self):
        """Start listening for inbound messages. Calls on_message() for each."""
        ...

    @abstractmethod
    async def send(self, message: "OutboundMessage"):
        """Send a response back to the platform."""
        ...

    @abstractmethod
    async def disconnect(self):
        """Gracefully disconnect."""
        ...

    def format_outbound(self, content: str) -> str:
        """Apply platform-specific formatting. Override in each adapter."""
        return content

    def truncate(self, content: str, max_len: int) -> str:
        if len(content) <= max_len:
            return content
        return content[:max_len - 20] + "\n...[truncated]"
