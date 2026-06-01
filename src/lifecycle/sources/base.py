"""
Abstract base for model source adapters.

Each source knows how to:
  - fetch the current upstream digest for a model
  - confirm a specific quant exists in that version
  - produce the shell command to pull/update
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VersionInfo:
    """Upstream version snapshot for one model."""
    digest: str                    # Canonical version fingerprint (SHA, digest hash, etc.)
    quant_confirmed: bool = True   # False if requested quant not found in this version
    last_modified: str = ""        # ISO date string if available
    url: str = ""                  # Source URL for reference
    metadata: dict = field(default_factory=dict)


class ModelSource(ABC):
    """
    Adapter interface for a model registry.

    Implement this to add a new source (LM Studio, Civit.AI, etc.).
    Register the type string in sources/__init__.py SOURCE_REGISTRY.
    """

    def __init__(self, config: dict):
        self.config = config
        self.source_id = config.get("id", "")
        self.endpoint = config.get("endpoint", "")

    @abstractmethod
    def fetch_version(self, model_id: str, quant: Optional[str] = None) -> Optional[VersionInfo]:
        """
        Return current upstream VersionInfo for model_id.
        Returns None if the model is not found on this source.
        """

    @abstractmethod
    def pull_command(self, model_id: str, quant: Optional[str] = None) -> str:
        """Shell command the operator should run to update this model."""

    @classmethod
    def from_config(cls, config: dict) -> "ModelSource":
        return cls(config)
