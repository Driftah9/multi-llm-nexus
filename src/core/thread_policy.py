"""
Thread binding policy — per-thread conversation isolation and scoping.

OpenClaw pattern: threads can be isolated (separate conversations) or
bound (share context). This module defines the policy and provides
the session key generation logic.
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("nexus.thread_policy")


@dataclass
class ThreadPolicy:
    """
    Configuration for how threads should be handled in a channel.

    Attributes:
        mode: "isolated" (each thread is separate conversation)
              "bound" (all threads share channel conversation)
        session_prefix: Prefix for session keys (e.g., "mattermost" or "discord")
        include_user_in_key: If True, session key includes user_id (more isolation)
    """

    mode: str = "isolated"  # isolated | bound
    session_prefix: str = "channel"
    include_user_in_key: bool = False


class ThreadBindingPolicy:
    """
    Manages thread session scoping for multi-adapter platforms.

    Usage:
        policy = ThreadBindingPolicy()
        policy.set_channel_policy("mm:town-square", ThreadPolicy(mode="isolated"))
        key = policy.get_session_key("mm:town-square", thread_id="abc", user_id="xyz")
        # Returns: "mm_town-square_thread_abc" (if isolated)
        # Returns: "mm_town-square" (if bound)
    """

    def __init__(self):
        """Initialize with default global policy."""
        self.global_policy = ThreadPolicy(mode="isolated")
        self.channel_policies: dict[str, ThreadPolicy] = {}

    def set_channel_policy(self, channel_id: str, policy: ThreadPolicy) -> None:
        """
        Set policy for a specific channel.

        Args:
            channel_id: Channel identifier (e.g., "mm:town-square")
            policy: ThreadPolicy to apply
        """
        self.channel_policies[channel_id] = policy
        logger.debug(f"Set thread policy for {channel_id}: mode={policy.mode}")

    def get_session_key(
        self,
        channel_id: str,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Generate a session key for a message, respecting thread policy.

        Args:
            channel_id: Channel identifier
            thread_id: Thread ID (if this message is in a thread)
            user_id: User ID (optional, for extra isolation if policy allows)

        Returns:
            Session key suitable for session store lookup
        """
        policy = self.channel_policies.get(channel_id, self.global_policy)

        base_key = f"{policy.session_prefix}_{channel_id.replace(':', '_')}"

        if not thread_id or policy.mode == "bound":
            # Bound mode: all messages in the channel share one conversation
            return base_key

        # Isolated mode: each thread gets its own session
        if policy.include_user_in_key and user_id:
            return f"{base_key}_thread_{thread_id}_user_{user_id}"
        else:
            return f"{base_key}_thread_{thread_id}"

    def should_isolate_thread(self, channel_id: str) -> bool:
        """
        Check if threads should be isolated in this channel.

        Args:
            channel_id: Channel identifier

        Returns:
            True if threads are isolated, False if bound
        """
        policy = self.channel_policies.get(channel_id, self.global_policy)
        return policy.mode == "isolated"

    def set_global_policy(self, policy: ThreadPolicy) -> None:
        """
        Set the global default policy for all channels.

        Args:
            policy: Default ThreadPolicy
        """
        self.global_policy = policy
        logger.info(f"Set global thread policy: mode={policy.mode}")


# Example usage in config/thread_binding.yaml:
"""
# Thread binding policy — per-channel session isolation

# Global default: isolated threads
global:
  mode: isolated          # "isolated" or "bound"
  session_prefix: channel
  include_user_in_key: false

# Per-channel overrides
channels:
  "mm:dev":
    mode: isolated       # Each thread is a separate conversation
  "mm:status":
    mode: bound          # All threads share the channel conversation
  "dc:bot-commands":
    mode: isolated
    include_user_in_key: true  # Extra isolation per user
"""
