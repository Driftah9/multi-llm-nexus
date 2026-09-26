#!/usr/bin/env python3
"""Self-Improvement Job — daily cron task for generating routing improvement proposals.

Runs evaluation, generates candidates, saves proposal to history, optionally notifies operator.

Event-gated: only runs evaluation (mechanical, no tokens). Notifications only if improvements found.

Usage:
    python3 scripts/improvement_job.py [--days N] [--notify] [--output FORMAT]
"""

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from lifecycle.self_improver import SelfImprover


async def run_improvement_job(
    days: int = 7,
    notify: bool = False,
    output_format: str = "json",
) -> int:
    """Run the self-improvement evaluation job.

    Args:
        days: Evaluation window (default 7)
        notify: Whether to notify operator of improvements (default False)
        output_format: Output format ('json' or 'text')

    Returns:
        0 if successful, 1 on error
    """
    logger.info(f"improvement_job: starting (days={days}, notify={notify})")

    improver = SelfImprover()

    try:
        proposal = await improver.evaluate(days=days)
        improver.save_proposal(proposal)

        if output_format == "json":
            print(json.dumps(proposal.to_dict(), indent=2))
        else:
            _print_text_report(proposal)

        # Count fixes
        total_fixes = len(proposal.high_impact_fixes) + len(proposal.medium_impact_fixes)
        logger.info(f"improvement_job: found {total_fixes} improvement opportunity(ies)")

        if notify and total_fixes > 0:
            await _send_notification(proposal)

        return 0
    except Exception as e:
        logger.error(f"improvement_job: failed: {e}")
        return 1


def _print_text_report(proposal) -> None:
    """Print proposal as human-readable text."""
    from datetime import datetime

    gen_time = datetime.fromisoformat(proposal.generated_at).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*70}")
    print(f"Improvement Proposal: {proposal.proposal_id}")
    print(f"Generated: {gen_time} | Evaluation window: {proposal.evaluation_window_days} days")
    print(f"{'='*70}\n")

    print("📊 Accuracy Summary:")
    for tier, acc in sorted(proposal.accuracy_summary.items()):
        pct = acc * 100
        indicator = "✅" if acc >= 0.90 else "⚠️" if acc >= 0.75 else "❌"
        print(f"  {indicator} {tier:10} {pct:5.1f}%")

    if proposal.high_impact_fixes:
        print(f"\n🔴 High-Impact Fixes ({len(proposal.high_impact_fixes)}):")
        for fix in proposal.high_impact_fixes:
            print(f"  • {fix.pattern_id}")
            print(f"    Domain: {fix.domain or 'any'} | Channel: {fix.channel or 'any'}")
            print(f"    Misclassifications: {fix.misclassifications} (confidence: {fix.confidence*100:.0f}%)")
            print(f"    → {fix.proposed_fix}")

    if proposal.medium_impact_fixes:
        print(f"\n🟡 Medium-Impact Fixes ({len(proposal.medium_impact_fixes)}):")
        for fix in proposal.medium_impact_fixes:
            print(f"  • {fix.pattern_id}")
            print(f"    Domain: {fix.domain or 'any'} | Channel: {fix.channel or 'any'}")
            print(f"    Misclassifications: {fix.misclassifications} (confidence: {fix.confidence*100:.0f}%)")
            print(f"    → {fix.proposed_fix}")

    if not proposal.high_impact_fixes and not proposal.medium_impact_fixes:
        print("\n✅ No misclassification patterns found — routing looks good!")

    print(f"\n{'='*70}\n")


async def _send_notification(proposal) -> None:
    """Send proposal to operator (via Notifier)."""
    try:
        from core.notify import Notifier

        notifier = Notifier.from_config()
        total = len(proposal.high_impact_fixes) + len(proposal.medium_impact_fixes)
        message = (
            f"🤖 **Routing Improvement Proposal**\n"
            f"ID: `{proposal.proposal_id}`\n"
            f"Found {total} improvement opportunity(ies) in the last {proposal.evaluation_window_days} days.\n\n"
            f"View proposal: `improvement_history.json`"
        )
        await notifier.send(message)
        logger.info("improvement_job: notification sent")
    except Exception as e:
        logger.warning(f"improvement_job: notification failed: {e}")


async def main():
    parser = argparse.ArgumentParser(description="Self-improvement job")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Evaluation window in days (default: 7)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send notification to operator if improvements found",
    )
    parser.add_argument(
        "--output",
        choices=["json", "text"],
        default="json",
        help="Output format (default: json)",
    )

    args = parser.parse_args()
    return await run_improvement_job(
        days=args.days,
        notify=args.notify,
        output_format=args.output,
    )


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
