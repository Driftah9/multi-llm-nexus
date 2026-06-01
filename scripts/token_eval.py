#!/usr/bin/env python3
"""
7-day token usage evaluator — normalized per-message metrics + week-over-week comparison.

Ported from claude-brain. Reads session JSONL files, calculates costs, compares vs baseline.
Supports optional posting to Mattermost. All paths and rates are configurable.

Usage:
    python scripts/token_eval.py                  # eval from session logs
    python scripts/token_eval.py --help           # see all options
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Default Sonnet 4.6 rates ($/MTok) — operators can override
DEFAULT_RATES = {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75}


def collect_7day_tokens(session_glob: str = "~/.claude/sessions/**/*.jsonl") -> dict:
    """Collect token usage from JSONL session files in the last 7 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "message_count": 0,
    }

    import glob
    files = glob.glob(os.path.expanduser(session_glob), recursive=True)
    for f in files:
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    ts_str = d.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts < cutoff:
                                continue
                        except Exception:
                            continue
                    msg = d.get("message", {})
                    if isinstance(msg, dict):
                        usage = msg.get("usage", {})
                        if usage and isinstance(usage, dict):
                            totals["input_tokens"] += usage.get("input_tokens", 0)
                            totals["output_tokens"] += usage.get("output_tokens", 0)
                            totals["cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)
                            totals["cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)
                            totals["message_count"] += 1
        except Exception:
            pass
    return totals


def calc_cost(tokens: dict, rates: dict) -> dict:
    """Calculate cost given token counts and rate card."""
    cost = {
        "input": round(tokens["input_tokens"] / 1e6 * rates["input"], 2),
        "output": round(tokens["output_tokens"] / 1e6 * rates["output"], 2),
        "cache_read": round(tokens["cache_read_input_tokens"] / 1e6 * rates["cache_read"], 2),
        "cache_write": round(tokens["cache_creation_input_tokens"] / 1e6 * rates["cache_write"], 2),
    }
    cost["total"] = round(sum(cost.values()), 2)
    return cost


def normalize(tokens: dict, cost: dict) -> dict:
    """Per-message efficiency metrics — volume-independent."""
    n = tokens["message_count"]
    if n == 0:
        return {
            "cost_per_msg": 0, "cache_write_per_msg": 0, "cache_read_per_msg": 0,
            "output_per_msg": 0, "cache_efficiency_ratio": 0,
        }
    cw = tokens["cache_creation_input_tokens"]
    cr = tokens["cache_read_input_tokens"]
    return {
        "cost_per_msg": round(cost["total"] / n, 4),
        "cache_write_per_msg": round(cw / n),
        "cache_read_per_msg": round(cr / n),
        "output_per_msg": round(tokens["output_tokens"] / n),
        "cache_efficiency_ratio": round(cr / cw, 2) if cw > 0 else 0,
    }


def delta(label: str, before: float, after: float, invert: bool = False) -> str:
    """Format change between two values. invert=True means higher is better."""
    if before == 0:
        return f"{label}: N/A"
    pct = ((after - before) / before) * 100
    improving = pct < 0 if not invert else pct > 0
    arrow = "▼" if pct < 0 else "▲"
    sign = "✅" if improving else "⚠️"
    return f"{sign} {arrow}{abs(pct):.1f}%"


def mm_get(url: str, path: str, token: str) -> dict:
    """GET from Mattermost API."""
    req = urllib.request.Request(
        f"{url}/api/v4{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def mm_post(url: str, path: str, token: str, payload: dict) -> dict:
    """POST to Mattermost API."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}/api/v4{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def post_to_mattermost(message: str, mm_url: str, bot_token: str, team_name: str):
    """Post message to Mattermost #town-square."""
    team = mm_get(mm_url, f"/teams/name/{team_name}", bot_token)
    channel = mm_get(mm_url, f"/teams/{team['id']}/channels/name/town-square", bot_token)
    mm_post(mm_url, "/posts", bot_token, {"channel_id": channel["id"], "message": message})
    logger.info("Posted to #town-square")


def fmt_k(n: int) -> str:
    """Format large token counts as readable shorthand."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}k"
    return str(n)


def main():
    parser = argparse.ArgumentParser(
        description="7-day token eval with baseline comparison"
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Path to baseline.json (default: ./token_baseline.json)"
    )
    parser.add_argument(
        "--session-glob",
        default="~/.claude/sessions/**/*.jsonl",
        help="Glob pattern for session JSONL files"
    )
    parser.add_argument(
        "--mattermost-url",
        default=os.environ.get("MATTERMOST_URL", ""),
        help="Mattermost URL (from MATTERMOST_URL env or --mattermost-url)"
    )
    parser.add_argument(
        "--bot-token",
        default=os.environ.get("MATTERMOST_BOT_TOKEN", ""),
        help="Mattermost bot token (from MATTERMOST_BOT_TOKEN env or --bot-token)"
    )
    parser.add_argument(
        "--team",
        default=os.environ.get("MATTERMOST_TEAM_NAME", "nexus"),
        help="Mattermost team name (default: nexus)"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write report to file (default: stdout or #town-square if bot token provided)"
    )
    parser.add_argument(
        "--rates-input",
        type=float,
        default=DEFAULT_RATES["input"],
        help="Input token rate $/MTok"
    )
    parser.add_argument(
        "--rates-output",
        type=float,
        default=DEFAULT_RATES["output"],
        help="Output token rate $/MTok"
    )
    parser.add_argument(
        "--rates-cache-read",
        type=float,
        default=DEFAULT_RATES["cache_read"],
        help="Cache read token rate $/MTok"
    )
    parser.add_argument(
        "--rates-cache-write",
        type=float,
        default=DEFAULT_RATES["cache_write"],
        help="Cache write token rate $/MTok"
    )
    args = parser.parse_args()

    # Determine baseline path
    baseline_file = args.baseline or Path("token_baseline.json")
    if not baseline_file.exists():
        logger.error(f"Baseline file not found: {baseline_file}")
        sys.exit(1)

    baseline_data = json.loads(baseline_file.read_text())
    b_tokens = baseline_data["baseline"]["tokens"]
    b_cost = baseline_data["baseline"]["cost_usd_at_sonnet_rates"]

    # Normalized baseline
    if "normalized" not in baseline_data["baseline"]:
        baseline_data["baseline"]["normalized"] = normalize(b_tokens, b_cost)
    b_norm = baseline_data["baseline"]["normalized"]

    # Last week snapshot
    last_week = baseline_data.get("last_week")

    # Collect current week
    rates = {
        "input": args.rates_input,
        "output": args.rates_output,
        "cache_read": args.rates_cache_read,
        "cache_write": args.rates_cache_write,
    }
    now_tokens = collect_7day_tokens(args.session_glob)
    now_cost = calc_cost(now_tokens, rates)
    now_norm = normalize(now_tokens, now_cost)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = now_tokens["message_count"]

    # Build report
    raw_lines = [
        f"| Messages | {b_tokens['message_count']:,} | {n:,} | {delta('', b_tokens['message_count'], n)} |",
        f"| Cache writes | {fmt_k(b_tokens['cache_creation_input_tokens'])} | {fmt_k(now_tokens['cache_creation_input_tokens'])} | {delta('', b_tokens['cache_creation_input_tokens'], now_tokens['cache_creation_input_tokens'])} |",
        f"| Cache reads | {fmt_k(b_tokens['cache_read_input_tokens'])} | {fmt_k(now_tokens['cache_read_input_tokens'])} | {delta('', b_tokens['cache_read_input_tokens'], now_tokens['cache_read_input_tokens'])} |",
        f"| Output tokens | {fmt_k(b_tokens['output_tokens'])} | {fmt_k(now_tokens['output_tokens'])} | {delta('', b_tokens['output_tokens'], now_tokens['output_tokens'])} |",
        f"| Est. total cost | ${b_cost['total']:.2f} | ${now_cost['total']:.2f} | {delta('', b_cost['total'], now_cost['total'])} |",
    ]

    eff_lines = [
        f"| Cost/message | ${b_norm['cost_per_msg']:.4f} | ${now_norm['cost_per_msg']:.4f} | {delta('', b_norm['cost_per_msg'], now_norm['cost_per_msg'])} |",
        f"| Cache writes/msg | {fmt_k(b_norm['cache_write_per_msg'])} | {fmt_k(now_norm['cache_write_per_msg'])} | {delta('', b_norm['cache_write_per_msg'], now_norm['cache_write_per_msg'])} |",
        f"| Cache reads/msg | {fmt_k(b_norm['cache_read_per_msg'])} | {fmt_k(now_norm['cache_read_per_msg'])} | {delta('', b_norm['cache_read_per_msg'], now_norm['cache_read_per_msg'])} |",
        f"| Output tokens/msg | {b_norm['output_per_msg']} | {now_norm['output_per_msg']} | {delta('', b_norm['output_per_msg'], now_norm['output_per_msg'])} |",
        f"| Cache reuse ratio | {b_norm['cache_efficiency_ratio']}x | {now_norm['cache_efficiency_ratio']}x | {delta('', b_norm['cache_efficiency_ratio'], now_norm['cache_efficiency_ratio'], invert=True)} |",
    ]

    if last_week and last_week.get("normalized"):
        lw_norm = last_week["normalized"]
        lw_date = last_week.get("date", "prior week")
        wow_lines = [
            f"| Cost/message | ${lw_norm['cost_per_msg']:.4f} | ${now_norm['cost_per_msg']:.4f} | {delta('', lw_norm['cost_per_msg'], now_norm['cost_per_msg'])} |",
            f"| Cache writes/msg | {fmt_k(lw_norm['cache_write_per_msg'])} | {fmt_k(now_norm['cache_write_per_msg'])} | {delta('', lw_norm['cache_write_per_msg'], now_norm['cache_write_per_msg'])} |",
            f"| Cache reads/msg | {fmt_k(lw_norm['cache_read_per_msg'])} | {fmt_k(now_norm['cache_read_per_msg'])} | {delta('', lw_norm['cache_read_per_msg'], now_norm['cache_read_per_msg'])} |",
            f"| Cache reuse ratio | {lw_norm['cache_efficiency_ratio']}x | {now_norm['cache_efficiency_ratio']}x | {delta('', lw_norm['cache_efficiency_ratio'], now_norm['cache_efficiency_ratio'], invert=True)} |",
        ]
        wow_section = f"""
**Week-over-week efficiency (normalized, {lw_date} → {today}):**
| Metric | Last week | This week | Direction |
|---|---|---|---|
""" + "\n".join(wow_lines)
    else:
        wow_section = "\n**Week-over-week:** No prior normalized snapshot — will compare next run."

    report = f"""## Token Eval — {today}

**Raw 7-day totals (vs baseline):**
| Metric | Baseline | This week | Change |
|---|---|---|---|
""" + "\n".join(raw_lines) + f"""

**Efficiency per message (volume-independent, vs baseline):**
| Metric | Baseline | This week | Change |
|---|---|---|---|
""" + "\n".join(eff_lines) + wow_section + f"""

_Messages this week: {n:,} | Rates: input=${rates['input']}/MTok, output=${rates['output']}/MTok, cache_read=${rates['cache_read']}/MTok, cache_write=${rates['cache_write']}/MTok._"""

    # Save current week snapshot for next WoW
    baseline_data["last_week"] = {
        "date": today,
        "tokens": now_tokens,
        "cost_usd_at_sonnet_rates": now_cost,
        "normalized": now_norm,
    }
    baseline_file.write_text(json.dumps(baseline_data, indent=2))

    # Output
    if args.output:
        args.output.write_text(report)
        logger.info(f"Report written to {args.output}")
    elif args.bot_token and args.mattermost_url:
        try:
            post_to_mattermost(report, args.mattermost_url, args.bot_token, args.team)
        except Exception as e:
            logger.error(f"Failed to post to Mattermost: {e}")
            print(report)
    else:
        print(report)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
