"""Emergent domain discovery — classify work into operator's actual domains.

As the operator uses Nexus, the system learns what they actually work on
(not just coarse seed categories). Classifies prompts into work-type
categories (video-editing, linux-servers, financial-planning) and tracks
frequency to understand the operator's domain distribution.

Categories are WORK-TYPE labels only (lowercase-kebab, 1-3 words). NEVER
proper nouns, brand names, client names, URLs, or person names. Enforced
structurally: reject anything entity-shaped before storage.

Domain scores then guide capability routing: "who's best at video-editing
for this operator?" instead of generic "who's best at reasoning?"

NEXUS:PORTABLE — classification is generic; domain vocabulary is
operator-specific config.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

# Structural rejection: patterns that signal entity names, not category labels
_ENTITY_PATTERNS = [
    r"^[A-Z]",  # Starts with capital (proper noun red flag)
    r"@",  # Email-shaped
    r"\.",  # Domain name-shaped
    r"(?:client|customer|company|project|person|user|account|session|room|channel|team|workspace|group|org)",  # Entity-type keywords
    r"(?:2024|2025|2026|q[1-4]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",  # Temporal/date-shaped
]

_ENTITY_RE = re.compile("|".join(_ENTITY_PATTERNS))  # No IGNORECASE; labels are already lowercase-validated


def _is_category(label: str) -> bool:
    """Structural gate: is this a WORK-TYPE category or an entity name?

    Rejects anything that looks like a proper noun, brand, client name, etc.
    This prevents leaking PII into the domain registry.
    """
    if not label or len(label) > 50:
        return False
    if not re.match(r"^[a-z0-9][a-z0-9\-]*$", label):
        return False
    if _ENTITY_RE.search(label):
        return False
    return True


class DomainClassifier:
    """Classify prompts into work-type domains using a cheap provider."""

    def __init__(self, bridge=None, known_domains: Optional[List[str]] = None):
        """
        Args:
            bridge: Injected bridge for provider calls
            known_domains: Optional list of known domain labels (to bias classification)
        """
        self.bridge = bridge
        self.known_domains = set(known_domains or [])

    async def classify(self, prompt: str) -> Optional[str]:
        """Classify a prompt into a domain label.

        Returns: lowercase-kebab domain label, or None if classification failed/rejected.
        """
        if not self.bridge or not prompt:
            return None

        # Classification prompt for a cheap provider
        system = (
            "You are a work-type domain classifier. "
            "Read the user's request and respond with ONLY a lowercase-kebab domain label "
            "(e.g., 'python-scripting', 'video-editing', 'financial-analysis'). "
            "Labels are work-type CATEGORIES only, never proper nouns, brand names, or client names. "
            "If you cannot classify, respond with 'general'. "
            "Respond with the SINGLE LABEL ONLY, no explanation."
        )

        try:
            response = await self.bridge.invoke(
                "nano",  # Use cheapest tier
                prompt=f"Classify this task: {prompt[:200]}",
                system=system,
                max_tokens=20,
            )

            if not response:
                return None

            # Extract first word (should be the only output)
            label = response.strip().lower().split()[0]

            # Structural validation
            if not _is_category(label):
                logger.debug(f"domain_classifier: rejected invalid label: {label}")
                return None

            return label
        except Exception as e:
            logger.debug(f"domain_classifier: classification failed: {e}")
            return None

    def register_domain(self, label: str) -> bool:
        """Register a domain as known for future biasing."""
        if _is_category(label):
            self.known_domains.add(label)
            return True
        return False

    def get_known_domains(self) -> List[str]:
        """Return sorted list of known domains."""
        return sorted(self.known_domains)
