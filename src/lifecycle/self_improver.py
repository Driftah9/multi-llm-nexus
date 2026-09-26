"""Self-Improvement Loop — evaluates routing decisions, generates improvement candidates.

Closes the feedback cycle: observe triage/routing performance via validation DB,
identify misclassification patterns, propose routing improvements, learn from
operator decisions.

Phases:
  1. EVAL — read triage-validation.db, analyze accuracy by tier/domain/channel
  2. CANDIDATES — identify high-confidence improvement opportunities
  3. PROPOSAL — format for operator review (via Notifier)
  4. LEARNING — operator approves/rejects, system updates routing rules

Design: zero LLM tokens until a proposal is actually generated and approved.
Evaluation is mechanical (SQL aggregations). Proposal generation uses cheap provider
(nano tier) to synthesize findings into a readable summary.

NEXUS:PORTABLE — evaluation logic is generic; routing rules are operator config.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

import os
_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_CONFIG_DIR = Path(os.environ.get("NEXUS_CONFIG_DIR", Path(__file__).parent.parent.parent / "config"))

TRIAGE_VALIDATION_DB = Path.home() / ".local" / "nexus" / "triage-validation.db"
IMPROVEMENT_HISTORY_PATH = _DATA_DIR / "improvement_history.json"


@dataclass
class TriageAccuracy:
    """Accuracy metrics for a triage classification."""
    tier: str
    domain: Optional[str]
    channel: Optional[str]
    total: int
    correct: int
    accuracy: float = field(default=0.0, init=False)  # correct / total (calculated)

    def __post_init__(self):
        if self.total > 0:
            object.__setattr__(self, 'accuracy', round(self.correct / self.total, 3))
        else:
            object.__setattr__(self, 'accuracy', 0.0)


@dataclass
class MisclassificationPattern:
    """A recurring misclassification that could be improved."""
    pattern_id: str
    tier: str
    domain: Optional[str]
    channel: Optional[str]
    misclassifications: int
    most_common_error: str  # What it was incorrectly classified as
    confidence: float  # 0.0-1.0: how confident this pattern is real (not noise)
    proposed_fix: str  # What to change (e.g., "route this domain to provider X")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "tier": self.tier,
            "domain": self.domain,
            "channel": self.channel,
            "misclassifications": self.misclassifications,
            "most_common_error": self.most_common_error,
            "confidence": self.confidence,
            "proposed_fix": self.proposed_fix,
        }


@dataclass
class ImprovementProposal:
    """Improvement proposal ready for operator review."""
    proposal_id: str
    generated_at: str  # ISO timestamp
    evaluation_window_days: int
    high_impact_fixes: List[MisclassificationPattern] = field(default_factory=list)
    medium_impact_fixes: List[MisclassificationPattern] = field(default_factory=list)
    accuracy_summary: Dict[str, float] = field(default_factory=dict)  # tier -> accuracy

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "generated_at": self.generated_at,
            "evaluation_window_days": self.evaluation_window_days,
            "high_impact_fixes": [p.to_dict() for p in self.high_impact_fixes],
            "medium_impact_fixes": [p.to_dict() for p in self.medium_impact_fixes],
            "accuracy_summary": self.accuracy_summary,
        }


class SelfImprover:
    """Evaluates routing decisions and proposes improvements."""

    def __init__(
        self,
        validation_db: Path = TRIAGE_VALIDATION_DB,
        history_path: Path = IMPROVEMENT_HISTORY_PATH,
    ):
        self.validation_db = Path(validation_db)
        self.history_path = Path(history_path)

    def get_recent_accuracy(self, days: int = 7) -> List[TriageAccuracy]:
        """Get accuracy metrics for the last N days.

        Returns list of TriageAccuracy sorted by most common tiers/domains/channels.
        """
        if not self.validation_db.exists():
            logger.warning(f"triage-validation.db not found: {self.validation_db}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ts = cutoff.timestamp()

        try:
            conn = sqlite3.connect(str(self.validation_db))
            conn.row_factory = sqlite3.Row

            # Query: accuracy by (tier, domain, channel)
            cur = conn.execute("""
                SELECT
                    tier,
                    domain,
                    channel,
                    COUNT(*) as total,
                    SUM(CASE WHEN correct = 1 THEN 1 ELSE 0 END) as correct
                FROM triage_validations
                WHERE timestamp > ?
                GROUP BY tier, domain, channel
                ORDER BY total DESC
            """, (cutoff_ts,))

            results = []
            for row in cur.fetchall():
                results.append(TriageAccuracy(
                    tier=row["tier"],
                    domain=row["domain"],
                    channel=row["channel"],
                    total=row["total"],
                    correct=row["correct"],
                ))

            conn.close()
            return results
        except Exception as e:
            logger.error(f"self_improver: get_recent_accuracy failed: {e}")
            return []

    def get_misclassifications(self, days: int = 7, min_occurrences: int = 3) -> Dict[str, List[Dict]]:
        """Get misclassification patterns for the last N days.

        Groups errors by (tier, domain, channel) and identifies most common
        mis-classifications per group.

        Returns: {pattern_key: [errors, ...]}
        """
        if not self.validation_db.exists():
            return {}

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ts = cutoff.timestamp()

        try:
            conn = sqlite3.connect(str(self.validation_db))
            conn.row_factory = sqlite3.Row

            # Query: errors grouped by (tier, domain, channel) → most common predicted value
            cur = conn.execute("""
                SELECT
                    tier,
                    domain,
                    channel,
                    predicted_tier as predicted,
                    COUNT(*) as error_count
                FROM triage_validations
                WHERE timestamp > ? AND correct = 0
                GROUP BY tier, domain, channel, predicted_tier
                HAVING error_count >= ?
                ORDER BY error_count DESC
            """, (cutoff_ts, min_occurrences))

            patterns = {}
            for row in cur.fetchall():
                key = f"{row['tier']}:{row['domain'] or 'any'}:{row['channel'] or 'any'}"
                if key not in patterns:
                    patterns[key] = []
                patterns[key].append({
                    "predicted": row["predicted"],
                    "error_count": row["error_count"],
                })

            conn.close()
            return patterns
        except Exception as e:
            logger.error(f"self_improver: get_misclassifications failed: {e}")
            return {}

    def generate_candidates(self, days: int = 7, confidence_threshold: float = 0.65) -> List[MisclassificationPattern]:
        """Generate improvement candidates from accuracy data.

        High-confidence = consistently missed in the same way (>65% of errors are the same).
        These become routing improvement proposals.
        """
        candidates = []
        patterns = self.get_misclassifications(days=days, min_occurrences=3)

        for pattern_key, errors in patterns.items():
            tier, domain, channel = pattern_key.split(":")
            if not errors:
                continue

            total_errors = sum(e["error_count"] for e in errors)
            most_common = max(errors, key=lambda e: e["error_count"])
            confidence = most_common["error_count"] / total_errors if total_errors > 0 else 0.0

            if confidence < confidence_threshold:
                continue

            pattern_id = f"fix_{tier}_{domain}_{channel}_{datetime.now(timezone.utc).strftime('%s')}"
            candidate = MisclassificationPattern(
                pattern_id=pattern_id,
                tier=tier if tier != "any" else None,
                domain=domain if domain != "any" else None,
                channel=channel if channel != "any" else None,
                misclassifications=most_common["error_count"],
                most_common_error=most_common["predicted"],
                confidence=round(confidence, 3),
                proposed_fix=self._propose_fix(tier, domain, channel, most_common["predicted"]),
            )
            candidates.append(candidate)

        candidates.sort(key=lambda c: c.misclassifications, reverse=True)
        return candidates

    def _propose_fix(self, tier: str, domain: str, channel: str, predicted: str) -> str:
        """Generate a human-readable fix proposal."""
        if domain and domain != "any":
            if predicted and predicted != "any":
                return f"Domain '{domain}' keeps mis-classifying to tier '{predicted}' — verify routing rules or retrain triage"
            else:
                return f"Domain '{domain}' has high misclassification rate — review triage patterns"
        elif channel and channel != "any":
            if predicted and predicted != "any":
                return f"Channel '{channel}' consistently mis-routes to tier '{predicted}' — check channel-specific context"
            else:
                return f"Channel '{channel}' has triage issues — may need specialized context"
        else:
            return f"Tier '{tier}' has general misclassification issues — verify provider health or routing logic"

    async def evaluate(self, days: int = 7) -> ImprovementProposal:
        """Run a full evaluation and generate a proposal.

        Returns an ImprovementProposal ready for operator review.
        """
        accuracy_by_tier = {}
        for acc in self.get_recent_accuracy(days=days):
            if acc.tier not in accuracy_by_tier:
                accuracy_by_tier[acc.tier] = []
            accuracy_by_tier[acc.tier].append(acc.accuracy)

        # Aggregate per-tier accuracy
        tier_accuracy = {}
        for tier, accs in accuracy_by_tier.items():
            tier_accuracy[tier] = round(sum(accs) / len(accs), 3) if accs else 0.0

        # Generate candidates
        candidates = self.generate_candidates(days=days)

        # Partition by impact
        high_impact = [c for c in candidates if c.confidence >= 0.80 and c.misclassifications >= 5]
        medium_impact = [c for c in candidates if not (c.confidence >= 0.80 and c.misclassifications >= 5)]

        proposal = ImprovementProposal(
            proposal_id=f"proposal_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            evaluation_window_days=days,
            high_impact_fixes=high_impact[:10],  # Cap at top 10
            medium_impact_fixes=medium_impact[:5],  # Cap at top 5
            accuracy_summary=tier_accuracy,
        )

        logger.info(f"self_improver: generated proposal {proposal.proposal_id} with {len(high_impact)} high-impact, {len(medium_impact)} medium-impact fixes")
        return proposal

    def save_proposal(self, proposal: ImprovementProposal) -> None:
        """Save proposal to history file."""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            history = {}
            if self.history_path.exists():
                with open(self.history_path) as f:
                    history = json.load(f)

            history[proposal.proposal_id] = proposal.to_dict()

            with open(self.history_path, "w") as f:
                json.dump(history, f, indent=2, sort_keys=True)

            logger.info(f"self_improver: saved proposal {proposal.proposal_id}")
        except Exception as e:
            logger.error(f"self_improver: save_proposal failed: {e}")

    def load_proposals(self) -> Dict[str, ImprovementProposal]:
        """Load all saved proposals."""
        if not self.history_path.exists():
            return {}

        try:
            with open(self.history_path) as f:
                history = json.load(f)
            return history
        except Exception as e:
            logger.error(f"self_improver: load_proposals failed: {e}")
            return {}
