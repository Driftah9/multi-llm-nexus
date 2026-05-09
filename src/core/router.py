"""
Task router — maps incoming messages to the appropriate provider.
Rules are defined in config/providers.yaml under the `routing` key.
"""
import re
from typing import Optional

from ..providers.base import BaseProvider


class Router:
    """
    Selects which provider handles a given message based on:
    1. Explicit task type (triage, code, privacy, etc.)
    2. Pattern matching against message content
    3. Default fallback to primary provider
    """

    def __init__(self, providers: dict[str, BaseProvider], routing_config: dict):
        self.providers = providers
        self.default = routing_config.get("default", "primary")
        self.triage_provider = routing_config.get("triage", "triage")
        self.patterns = routing_config.get("patterns", [])
        self._compiled = [
            (re.compile(p["match"], re.IGNORECASE), p["provider"])
            for p in self.patterns
            if "match" in p and "provider" in p
        ]

    def route(self, message: str, task_type: Optional[str] = None) -> BaseProvider:
        """Return the provider that should handle this message."""
        if task_type and task_type in self.providers:
            return self.providers[task_type]

        for pattern, provider_name in self._compiled:
            if pattern.search(message):
                provider = self.providers.get(provider_name)
                if provider:
                    return provider

        return self.providers.get(self.default) or next(iter(self.providers.values()))

    def get_triage_provider(self) -> BaseProvider:
        """Return the fast/cheap provider used for message classification."""
        return self.providers.get(self.triage_provider) or self.route("")

    def list_providers(self) -> dict[str, str]:
        return {name: repr(p) for name, p in self.providers.items()}
