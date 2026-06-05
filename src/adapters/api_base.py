"""
API base class for REST-based adapters (Discord, Mattermost, etc).
Handles authentication and common REST patterns.
"""
from abc import ABC, abstractmethod
from typing import Optional


class APISenderBase(ABC):
    """Base for platform API clients (Mattermost, Discord, etc)."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize client connection."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Close client connection."""
        ...

    @abstractmethod
    async def post_message(self, channel_id: str, text: str,
                           reply_to: Optional[str] = None) -> dict:
        """
        Post a message to a channel.
        Returns the message dict (containing 'id' at minimum).
        """
        ...

    @abstractmethod
    async def update_message(self, message_id: str, text: str) -> None:
        """Update an existing message."""
        ...
