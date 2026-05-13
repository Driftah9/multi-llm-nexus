"""
Review Gate — selective trigger for external code review.

Determines whether a changeset warrants external review from challenger providers.
Prevents review spam on small iterative changes while ensuring high-risk changes
get validated before commit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("nexus.review_gate")


class ReviewTrigger(Enum):
    """When to trigger external review."""
    SKIP = "skip"                  # No review needed
    SUGGEST = "suggest"            # Offer review, don't force
    AUTO = "auto"                  # Run review automatically


@dataclass
class ChangeScope:
    """Metrics about code changes in the current session."""
    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0
    core_modules_touched: list[str] = None
    is_commit_point: bool = False
    is_test_change: bool = False
    is_security_change: bool = False
    touched_providers: bool = False
    touched_adapters: bool = False

    def __post_init__(self):
        if self.core_modules_touched is None:
            self.core_modules_touched = []

    @property
    def total_lines_changed(self) -> int:
        return self.lines_added + self.lines_deleted

    @property
    def is_multi_file(self) -> bool:
        return self.files_changed >= 3

    @property
    def is_core_touched(self) -> bool:
        return len(self.core_modules_touched) > 0


class ReviewGate:
    """
    Decides whether a changeset should trigger external review.

    Thresholds are tuned for the free-tier provider model:
    - Groq: fast classification (nano-tier)
    - Cerebras: slow but thorough (standard-tier)
    - Goal: minimize API calls while catching real problems
    """

    # Core modules — any touch warrants attention
    CORE_MODULES = {
        "engine.py",
        "bridge.py",
        "nexus_bridge.py",
        "orchestrator.py",
        "provider_chain.py",
        "router.py",
        "chain_builder.py",
    }

    # Adapter modules — any touch is risky
    ADAPTER_MODULES = {
        "adapter.py",  # base
        "mattermost/adapter.py",
        "discord/adapter.py",
        "telegram/adapter.py",
    }

    # Provider modules — failover logic is critical
    PROVIDER_MODULES = {
        "providers/base.py",
        "providers/gemini.py",
        "providers/openai.py",
        "providers/anthropic.py",
    }

    def __init__(self):
        self.last_review_commit: Optional[str] = None
        self.session_changes = ChangeScope()

    def analyze(self, scope: ChangeScope) -> ReviewTrigger:
        """
        Classify a changeset and return the review trigger level.

        Args:
            scope: Metrics about the code changes

        Returns:
            ReviewTrigger.SKIP, ReviewTrigger.SUGGEST, or ReviewTrigger.AUTO
        """

        # HIGH-RISK: Always review
        if scope.is_security_change:
            logger.info("Security-related change detected — AUTO triggering review")
            return ReviewTrigger.AUTO

        if scope.is_commit_point and scope.core_modules_touched:
            logger.info(
                f"Commit point with {len(scope.core_modules_touched)} core module(s) touched — AUTO triggering review"
            )
            return ReviewTrigger.AUTO

        if scope.touched_providers:
            logger.info("Provider changes detected — AUTO triggering review")
            return ReviewTrigger.AUTO

        if scope.is_test_change and scope.is_commit_point:
            logger.info("Test suite changes at commit point — AUTO triggering review")
            return ReviewTrigger.AUTO

        # MEDIUM-RISK: Suggest review
        if scope.core_modules_touched:
            logger.info(
                f"Core module(s) touched ({scope.core_modules_touched}) — SUGGEST review"
            )
            return ReviewTrigger.SUGGEST

        if scope.touched_adapters:
            logger.info("Adapter changes detected — SUGGEST review")
            return ReviewTrigger.SUGGEST

        if scope.is_multi_file and scope.total_lines_changed > 100:
            logger.info(
                f"Multi-file change ({scope.files_changed} files, {scope.total_lines_changed} lines) — SUGGEST review"
            )
            return ReviewTrigger.SUGGEST

        # LOW-RISK: Skip
        if scope.total_lines_changed > 500 and not scope.is_commit_point:
            logger.debug(
                f"Large change ({scope.total_lines_changed} lines) but not at commit point — SKIP for now"
            )
            return ReviewTrigger.SKIP

        logger.debug(
            f"Small/routine change ({scope.files_changed} files, {scope.total_lines_changed} lines) — SKIP"
        )
        return ReviewTrigger.SKIP

    @staticmethod
    def classify_files(file_paths: list[str]) -> tuple[list[str], bool, bool, bool]:
        """
        Classify file changes and identify risk categories.

        Args:
            file_paths: List of changed file paths

        Returns:
            (core_modules, is_test_change, is_security_change, is_provider_change)
        """
        core_modules = []
        is_test = False
        is_security = False
        is_provider = False

        for path in file_paths:
            # Normalize path separators
            normalized = path.replace("\\", "/")

            if any(core in normalized for core in ReviewGate.CORE_MODULES):
                core_modules.append(path)

            if any(adapter in normalized for adapter in ReviewGate.ADAPTER_MODULES):
                core_modules.append(path)

            if any(prov in normalized for prov in ReviewGate.PROVIDER_MODULES):
                is_provider = True

            if "test" in normalized.lower():
                is_test = True

            if any(
                sec in normalized.lower()
                for sec in ["auth", "secret", "token", "key", "password", "credential"]
            ):
                is_security = True

        return core_modules, is_test, is_security, is_provider

    def suggestion_message(self, scope: ChangeScope, trigger: ReviewTrigger) -> str:
        """Generate a human-readable review suggestion."""
        if trigger == ReviewTrigger.SKIP:
            return None

        if trigger == ReviewTrigger.AUTO:
            reason = ""
            if scope.is_security_change:
                reason = "security-sensitive changes"
            elif scope.touched_providers:
                reason = "provider/failover logic touched"
            elif scope.core_modules_touched:
                reason = f"core module(s) touched: {', '.join(scope.core_modules_touched)}"
            elif scope.is_commit_point:
                reason = "about to commit"

            return (
                f"**Review recommended before commit** ({reason})\n"
                f"Run: `python scripts/internal_review.py {' '.join(scope.core_modules_touched or ['.'])} --notify`"
            )

        if trigger == ReviewTrigger.SUGGEST:
            return (
                f"**Consider getting a second opinion** on these changes.\n"
                f"Run: `python scripts/internal_review.py <file> --focus \"areas of concern\"`"
            )

        return None
