#!/usr/bin/env python3
"""
Model Lifecycle Check — cron entrypoint and manual runner.

Usage:
    python scripts/model_check.py               # monthly check (skips if ran this month)
    python scripts/model_check.py --force        # run even if checked this month
    python scripts/model_check.py --dry-run      # report to stdout, no DM sent
    python scripts/model_check.py --state        # show current state and exit
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.lifecycle import ModelLifecycleManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("model_check")


def cmd_state(manager: ModelLifecycleManager) -> None:
    manager._load_config()
    manager._load_state()
    state = manager._state
    last = state.get("last_check", "never")
    models = state.get("models", {})
    print(f"Last check: {last}")
    print(f"Tracked models in state: {len(models)}")
    for key, info in models.items():
        source, model = key.split("::", 1)
        quant = f" [{info['quant']}]" if info.get("quant") else ""
        digest = info.get("digest", "")[:12]
        print(f"  {model}{quant} ({source}) @ {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexus model lifecycle checker")
    parser.add_argument("--force", action="store_true", help="Run even if already checked this month")
    parser.add_argument("--dry-run", action="store_true", help="Report to stdout only, no notification sent")
    parser.add_argument("--state", action="store_true", help="Show current state and exit")
    args = parser.parse_args()

    manager = ModelLifecycleManager()

    if args.state:
        cmd_state(manager)
        return

    try:
        result = manager.run(force=args.force, dry_run=args.dry_run)
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    if result["skipped"]:
        print("Check skipped — already ran this month. Use --force to override.")
        return

    updates = result["updates"]
    errors = result["errors"]

    if errors:
        for e in errors:
            logger.warning(e)

    if not updates:
        print("All tracked models are current.")
        return

    print(f"\n{len(updates)} update(s) available:\n")
    for u in updates:
        quant_str = f" [{u['quant']}]" if u['quant'] else ""
        print(f"  {u['model_id']}{quant_str}  ({u['source']})")
        print(f"    {u['reason']}")
        print(f"    {u['old_digest']} → {u['new_digest']}")
        print(f"    Run: {u['pull_command']}")
        if u.get("url"):
            print(f"    Ref: {u['url']}")
        print()

    if args.dry_run:
        print("(dry-run: DM not sent)")
    else:
        print("DM sent via configured notification protocol.")


if __name__ == "__main__":
    main()
