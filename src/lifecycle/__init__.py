"""
Model Lifecycle Manager — monthly update detection for local LLM models.

Sources are plug-in adapters: each registry (Ollama, HuggingFace, etc.) implements
ModelSource. Operators add new sources in config/model_sources.yaml without code changes.

Usage:
    python scripts/model_check.py           # run check
    python scripts/model_check.py --force   # force even if checked this month
    python scripts/model_check.py --dry-run # report only, no DM
"""

from .manager import ModelLifecycleManager

__all__ = ["ModelLifecycleManager"]
