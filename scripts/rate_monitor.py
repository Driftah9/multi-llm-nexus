#!/usr/bin/env python3
"""
Rate limit monitor for free-tier LLM providers.

Tracks daily token/request usage per provider in data/rate_limits.json.
Called by NexusBridge after every successful API call (via hook), or run
standalone to print current usage + limits.

Usage:
  python scripts/rate_monitor.py               # print current usage
  python scripts/rate_monitor.py --reset        # reset all daily counters
  python scripts/rate_monitor.py --notify       # post summary to Mattermost

Standalone import usage (from bridge/adapters):
  from scripts.rate_monitor import record_usage, get_usage_summary
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
USAGE_FILE = PROJECT_ROOT / "data" / "rate_limits.json"

# ── Known free-tier limits ────────────────────────────────────────────────────
# Values are conservative daily limits. Update as quotas change.
FREE_TIER_LIMITS = {
    "groq": {
        "display": "Groq (Llama-8B)",
        "requests_per_day": 14_400,      # 600/min * 24h (conservative)
        "requests_per_minute": 600,
        "tokens_per_day": 500_000,
        "tokens_per_minute": 6_000,
    },
    "cerebras": {
        "display": "Cerebras (Qwen3-235B)",
        "requests_per_day": 14_400,
        "requests_per_minute": 30,
        "tokens_per_day": 1_000_000,     # 1M tokens/day
        "tokens_per_minute": 60_000,
    },
    "gemini": {
        "display": "Gemini Flash (Free)",
        "requests_per_day": 1_500,       # free tier: 1500 RPD
        "requests_per_minute": 15,       # ⚠ TIGHT: 15 RPM — primary 429 cause
        "tokens_per_day": 1_000_000,
        "tokens_per_minute": 1_000_000,
    },
}

# ── Persistence ───────────────────────────────────────────────────────────────

def _load() -> dict:
    if not USAGE_FILE.exists():
        return {}
    try:
        return json.loads(USAGE_FILE.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps(data, indent=2))


def _today() -> str:
    return date.today().isoformat()


def _get_day_bucket(data: dict, provider: str) -> dict:
    today = _today()
    if provider not in data:
        data[provider] = {}
    if today not in data[provider]:
        data[provider][today] = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
    return data[provider][today]


# ── Public API ────────────────────────────────────────────────────────────────

def record_usage(provider: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Record one API call for a provider. Thread-unsafe (single process only)."""
    data = _load()
    bucket = _get_day_bucket(data, provider)
    bucket["requests"] += 1
    bucket["input_tokens"] += input_tokens
    bucket["output_tokens"] += output_tokens
    _save(data)


def get_usage_summary(provider: str | None = None) -> list[dict]:
    """Return today's usage for all (or one) provider(s)."""
    data = _load()
    today = _today()
    results = []

    for pname, limits in FREE_TIER_LIMITS.items():
        if provider and pname != provider:
            continue
        bucket = data.get(pname, {}).get(today, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
        total_tokens = bucket["input_tokens"] + bucket["output_tokens"]

        req_pct = round(bucket["requests"] / limits["requests_per_day"] * 100, 1)
        tok_pct = round(total_tokens / limits["tokens_per_day"] * 100, 1)

        results.append({
            "provider": pname,
            "display": limits["display"],
            "date": today,
            "requests": bucket["requests"],
            "requests_limit": limits["requests_per_day"],
            "requests_pct": req_pct,
            "total_tokens": total_tokens,
            "tokens_limit": limits["tokens_per_day"],
            "tokens_pct": tok_pct,
            "input_tokens": bucket["input_tokens"],
            "output_tokens": bucket["output_tokens"],
            "warning": req_pct >= 80 or tok_pct >= 80,
        })

    return results


def format_summary_text(rows: list[dict]) -> str:
    lines = [f"**Free-tier usage — {_today()}**\n"]
    for r in rows:
        warn = " ⚠️" if r["warning"] else ""
        lines.append(
            f"**{r['display']}**{warn}\n"
            f"  Requests: {r['requests']:,} / {r['requests_limit']:,} ({r['requests_pct']}%)\n"
            f"  Tokens:   {r['total_tokens']:,} / {r['tokens_limit']:,} ({r['tokens_pct']}%)"
        )
    return "\n\n".join(lines)


# ── Mattermost notify ─────────────────────────────────────────────────────────

def _notify_mattermost(text: str) -> None:
    """Post to Mattermost via the central notify script if available."""
    notify = PROJECT_ROOT.parent / "scripts" / "notify.sh"
    if notify.exists():
        import subprocess
        subprocess.run([str(notify), "multi-llm-nexus", text], check=False)
        return

    # Fallback: read .env and post directly
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print(text)
        return

    cfg: dict = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cfg[k.strip()] = v.strip().strip('"').strip("'")

    mm_url = cfg.get("MATTERMOST_URL", "")
    mm_token = cfg.get("MATTERMOST_TOKEN", "")
    mm_channel = cfg.get("RATE_MONITOR_CHANNEL", "multi-llm-nexus")

    if not mm_url or not mm_token:
        print(text)
        return

    import urllib.request
    payload = json.dumps({"channel_id": mm_channel, "message": text}).encode()
    req = urllib.request.Request(
        f"{mm_url}/api/v4/posts",
        data=payload,
        headers={"Authorization": f"Bearer {mm_token}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"Notify failed: {e}\n{text}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Rate limit monitor for free-tier LLM providers")
    parser.add_argument("--reset", action="store_true", help="Reset all usage counters")
    parser.add_argument("--notify", action="store_true", help="Post summary to Mattermost")
    parser.add_argument("--provider", help="Filter to one provider")
    parser.add_argument("--record", metavar="PROVIDER:IN:OUT",
                        help="Record usage (e.g. groq:100:50)")
    args = parser.parse_args()

    if args.reset:
        _save({})
        print("Usage counters reset.")
        return

    if args.record:
        try:
            parts = args.record.split(":")
            pname = parts[0]
            inp = int(parts[1]) if len(parts) > 1 else 0
            out = int(parts[2]) if len(parts) > 2 else 0
            record_usage(pname, inp, out)
            print(f"Recorded: {pname} +{inp}in +{out}out")
        except Exception as e:
            print(f"Invalid format: {e}")
        return

    rows = get_usage_summary(args.provider)
    text = format_summary_text(rows)
    print(text)

    if args.notify:
        _notify_mattermost(text)

    # Exit with non-zero if any provider is at/above warning threshold
    if any(r["warning"] for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
