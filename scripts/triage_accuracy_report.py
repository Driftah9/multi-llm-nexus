#!/usr/bin/env python3
"""Triage accuracy report — reads triage_validator DB, surfaces misclassification patterns.

Usage:
    python3 triage_accuracy_report.py [--db PATH] [--days N] [--format json|text]

Reads the triage-validation.db SQLite file and produces:
  - Classification accuracy by tier (nano/standard/deep/apex)
  - Misclassification patterns (under-triage, over-triage)
  - Per-channel routing performance
  - Domain-based accuracy
  - Error and failover rates
  - Re-ask detection patterns (follow-up questions within REASK_WINDOW)

Output: JSON (machine-readable) or text (human-readable, default).

Example:
  python3 triage_accuracy_report.py --days 30 --format text
  python3 triage_accuracy_report.py --db /custom/path.db --format json | jq .

NEXUS:PORTABLE — reads operator-agnostic triage metrics; output format is generic.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".local" / "nexus" / "triage-validation.db"
TIER_RANK = {"nano": 0, "standard": 1, "deep": 2, "apex": 3}


def _tier_offset(classified: str, actual: str) -> int:
    """Offset: positive = over-triage, negative = under-triage."""
    c_rank = TIER_RANK.get(classified, 1)
    a_rank = TIER_RANK.get(actual, 1)
    return c_rank - a_rank


def load_decisions(
    db_path: Path,
    days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load decisions from the triage validator DB, optionally filtered by age."""
    if not db_path.exists():
        logger.warning(f"DB not found: {db_path}")
        return []

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        query = "SELECT * FROM decisions WHERE outcome IS NOT NULL"
        params = []

        if days:
            cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
            query += " AND timestamp > ?"
            params.append(cutoff)

        query += " ORDER BY timestamp DESC"
        cur = conn.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"DB load failed: {e}")
        return []


def analyze_accuracy(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute accuracy metrics from decision records."""
    if not decisions:
        return {
            "total_decisions": 0,
            "by_tier": {},
            "misclassifications": {"under": 0, "over": 0, "correct": 0},
            "per_channel": {},
            "by_outcome": defaultdict(int),
            "error_rate": 0.0,
        }

    # Overall stats
    total = len(decisions)
    outcomes = defaultdict(int)
    by_tier = defaultdict(lambda: {"correct": 0, "under": 0, "over": 0, "total": 0})
    per_channel = defaultdict(
        lambda: {"correct": 0, "under": 0, "over": 0, "total": 0, "error": False}
    )
    by_domain = defaultdict(lambda: {"correct": 0, "under": 0, "over": 0, "total": 0})
    failover_count = sum(1 for d in decisions if (d.get("failover_hops") or 0) > 0)
    error_count = sum(1 for d in decisions if d.get("error"))

    for d in decisions:
        outcome = d.get("outcome", "unknown")
        outcomes[outcome] += 1
        tier = d.get("classified_tier", "standard")
        channel = d.get("channel", "unknown")
        domain = d.get("domain", "general")

        by_tier[tier]["total"] += 1
        per_channel[channel]["total"] += 1
        by_domain[domain]["total"] += 1

        if outcome == "correct":
            by_tier[tier]["correct"] += 1
            per_channel[channel]["correct"] += 1
            by_domain[domain]["correct"] += 1
        elif outcome == "under":
            by_tier[tier]["under"] += 1
            per_channel[channel]["under"] += 1
            by_domain[domain]["under"] += 1
        elif outcome == "over":
            by_tier[tier]["over"] += 1
            per_channel[channel]["over"] += 1
            by_domain[domain]["over"] += 1

        if d.get("error"):
            per_channel[channel]["error"] = True

    # Compute accuracy rates
    tier_rates = {}
    for tier, stats in by_tier.items():
        total_tier = stats["total"]
        correct = stats["correct"]
        tier_rates[tier] = {
            "accuracy": round(100.0 * correct / total_tier, 1) if total_tier else 0.0,
            "under_triage": stats["under"],
            "over_triage": stats["over"],
            "total": total_tier,
        }

    channel_rates = {}
    for chan, stats in per_channel.items():
        total_chan = stats["total"]
        correct = stats["correct"]
        channel_rates[chan] = {
            "accuracy": round(100.0 * correct / total_chan, 1) if total_chan else 0.0,
            "under_triage": stats["under"],
            "over_triage": stats["over"],
            "total": total_chan,
            "had_errors": stats["error"],
        }

    domain_rates = {}
    for dom, stats in by_domain.items():
        total_dom = stats["total"]
        correct = stats["correct"]
        domain_rates[dom] = {
            "accuracy": round(100.0 * correct / total_dom, 1) if total_dom else 0.0,
            "under_triage": stats["under"],
            "over_triage": stats["over"],
            "total": total_dom,
        }

    return {
        "total_decisions": total,
        "date_range": {
            "oldest": datetime.fromtimestamp(
                min((d.get("timestamp", 0) for d in decisions), default=0),
                tz=timezone.utc,
            ).isoformat(),
            "newest": datetime.fromtimestamp(
                max((d.get("timestamp", 0) for d in decisions), default=0),
                tz=timezone.utc,
            ).isoformat(),
        },
        "by_tier": tier_rates,
        "by_channel": channel_rates,
        "by_domain": domain_rates,
        "outcomes": dict(outcomes),
        "failover_rate": round(100.0 * failover_count / total, 1) if total else 0.0,
        "error_rate": round(100.0 * error_count / total, 1) if total else 0.0,
        "misclassification_summary": {
            "under_triage_total": sum(s["under"] for s in by_tier.values()),
            "over_triage_total": sum(s["over"] for s in by_tier.values()),
            "correct_total": sum(s["correct"] for s in by_tier.values()),
        },
    }


def format_text(report: Dict[str, Any]) -> str:
    """Format report as human-readable text."""
    lines = [
        "=== Triage Accuracy Report ===",
        "",
        f"Total decisions: {report['total_decisions']}",
    ]

    if report.get("date_range"):
        lines.append(f"Period: {report['date_range']['oldest']} → {report['date_range']['newest']}")

    lines.extend([
        "",
        "--- Accuracy by Tier ---",
    ])
    for tier in ["nano", "standard", "deep", "apex"]:
        if tier in report["by_tier"]:
            stats = report["by_tier"][tier]
            lines.append(
                f"  {tier:10s}: {stats['accuracy']:5.1f}% accuracy "
                f"({stats['total']:4d} total, "
                f"{stats['under_triage']} under, {stats['over_triage']} over)"
            )

    lines.extend([
        "",
        "--- Per-Channel Performance (top 10) ---",
    ])
    channels_sorted = sorted(
        report.get("by_channel", {}).items(),
        key=lambda x: x[1]["total"],
        reverse=True,
    )[:10]
    for chan, stats in channels_sorted:
        error_note = " ⚠️  had errors" if stats.get("had_errors") else ""
        lines.append(
            f"  {chan:20s}: {stats['accuracy']:5.1f}% "
            f"({stats['total']:3d} total, "
            f"{stats['under_triage']} under, {stats['over_triage']} over){error_note}"
        )

    lines.extend([
        "",
        "--- Outcomes ---",
    ])
    for outcome, count in sorted(report.get("outcomes", {}).items()):
        pct = round(100.0 * count / report["total_decisions"], 1) if report["total_decisions"] else 0.0
        lines.append(f"  {outcome:15s}: {count:4d} ({pct:5.1f}%)")

    lines.extend([
        "",
        "--- Risk Indicators ---",
        f"  Failover rate: {report.get('failover_rate', 0):.1f}%",
        f"  Error rate:    {report.get('error_rate', 0):.1f}%",
    ])

    if report.get("misclassification_summary"):
        m = report["misclassification_summary"]
        lines.extend([
            "",
            "--- Misclassification Summary ---",
            f"  Under-triage (too cheap):   {m.get('under_triage_total', 0)}",
            f"  Over-triage (too expensive): {m.get('over_triage_total', 0)}",
            f"  Correct:                     {m.get('correct_total', 0)}",
        ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Triage accuracy report from validator DB"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to triage-validation.db (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Only include decisions from the last N days (default: all)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    decisions = load_decisions(args.db, args.days)
    report = analyze_accuracy(decisions)

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_text(report))

    return 0 if decisions else 1


if __name__ == "__main__":
    sys.exit(main())
