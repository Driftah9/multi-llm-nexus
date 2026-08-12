#!/usr/bin/env python3
"""
Hardware Fit Check — cron/timer entrypoint and manual runner.

Refreshes llmfit's model catalog from HuggingFace, then checks whether a
model newer or better-suited than what's currently running has shown up.
Only notifies (DM via the configured adapter) when a candidate clears the
configured quality/speed bar — never pulls or reconfigures anything.

Usage:
    python scripts/llmfit_fit_check.py               # runs if interval elapsed
    python scripts/llmfit_fit_check.py --force        # run even if checked recently
    python scripts/llmfit_fit_check.py --dry-run      # report to stdout, no DM sent
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.lifecycle.fit_check import HardwareFitChecker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("llmfit_fit_check")


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexus llmfit hardware-fit checker")
    parser.add_argument("--force", action="store_true", help="Run even if checked within the configured interval")
    parser.add_argument("--dry-run", action="store_true", help="Report to stdout only, no notification sent")
    args = parser.parse_args()

    checker = HardwareFitChecker()
    result = checker.run(force=args.force, dry_run=args.dry_run)

    if result["skipped"]:
        print("Fit check skipped — ran recently. Use --force to override.")
        return

    for e in result["errors"]:
        logger.warning(e)

    candidate = result["candidate"]
    if not candidate:
        print("No qualifying candidate found — current model still fits best.")
        return

    print(f"\nCandidate: {candidate['name']}")
    print(f"  quality {candidate['quality']:.0f} (vs {candidate['baseline_quality']:.0f} now)")
    if candidate.get("estimated_tps"):
        print(f"  estimated_tps {candidate['estimated_tps']:.1f}")
    if candidate.get("best_quant"):
        print(f"  best_quant {candidate['best_quant']}")

    if args.dry_run:
        print("(dry-run: DM not sent)")
    else:
        print("DM sent via configured notification protocol.")


if __name__ == "__main__":
    main()
