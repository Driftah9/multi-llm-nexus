"""
Task router — maps incoming messages to the appropriate provider.
Rules are defined in config/providers.yaml under the `routing` key.

Supports local LLM offloading: if `local_offload: true` and a nano-tier task
is detected, routes to the local provider instead of cloud.
"""
import logging
import re
from typing import Optional

from ..providers.base import BaseProvider

logger = logging.getLogger(__name__)


class Router:
    """
    Selects which provider handles a given message based on:
    1. Explicit task type (triage, code, privacy, etc.)
    2. Pattern matching against message content
    3. Tier-based routing (nano → local if available, standard/deep → cloud)
    4. Default fallback to primary provider
    """

    def __init__(self, providers: dict[str, BaseProvider], routing_config: dict):
        self.providers = providers
        self.default = routing_config.get("default", "primary")
        self.triage_provider = routing_config.get("triage", "triage")
        self.local_offload = routing_config.get("local_offload", False)
        self.local_provider = routing_config.get("local", "ollama")
        self.patterns = routing_config.get("patterns", [])
        self._compiled = [
            (re.compile(p["match"], re.IGNORECASE), p["provider"])
            for p in self.patterns
            if "match" in p and "provider" in p
        ]

        # Check if local provider is actually available
        self._local_available = self.local_provider in self.providers

    def route(self, message: str, task_type: Optional[str] = None, tier: Optional[str] = None) -> BaseProvider:
        """
        Return the provider that should handle this message.

        Args:
            message: The input message
            task_type: Optional explicit task type (triage, code, etc.)
            tier: Optional tier from triage (nano/standard/deep)
        """
        # Local LLM offloading: route nano-tier tasks to local if available
        if self.local_offload and tier == "nano" and self._local_available:
            logger.debug(f"Routing nano-tier task to local provider: {self.local_provider}")
            return self.providers[self.local_provider]

        # Explicit task type
        if task_type and task_type in self.providers:
            return self.providers[task_type]

        # Pattern matching
        for pattern, provider_name in self._compiled:
            if pattern.search(message):
                provider = self.providers.get(provider_name)
                if provider:
                    return provider

        # Default provider
        return self.providers.get(self.default) or next(iter(self.providers.values()))

    def get_triage_provider(self) -> BaseProvider:
        """Return the fast/cheap provider used for message classification."""
        return self.providers.get(self.triage_provider) or self.route("")

    def list_providers(self) -> dict[str, str]:
        return {name: repr(p) for name, p in self.providers.items()}

    def has_local_offload(self) -> bool:
        """Return whether local LLM offloading is enabled and available."""
        return self.local_offload and self._local_available
