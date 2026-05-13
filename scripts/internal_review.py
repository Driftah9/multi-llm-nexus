#!/usr/bin/env python3
"""
Internal AI reviewer — sends code/files to Groq and Cerebras for parallel review.

Usage:
  python scripts/internal_review.py src/core/engine.py
  python scripts/internal_review.py src/adapters/mattermost/adapter.py --focus "async safety, error handling"
  python scripts/internal_review.py --diff HEAD~1  # review last commit's changes

Output:
  Prints side-by-side review from each provider.
  Saves JSON result to data/reviews/<timestamp>_<file>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import aiohttp

PROJECT_ROOT = Path(__file__).parent.parent

# ── Load .env ─────────────────────────────────────────────────────────────────

def _load_env():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# ── OpenAI-compatible reviewer ────────────────────────────────────────────────

async def _review_openai(
    session: aiohttp.ClientSession,
    name: str,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
) -> dict:
    start = time.time()
    try:
        async with session.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are an expert code reviewer specializing in Python async systems, "
                            "LLM integrations, and production reliability. Be concise and specific. "
                            "Lead with the most critical findings. Format: numbered list, severity in brackets."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2048,
                "temperature": 0.2,
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return {"provider": name, "error": f"HTTP {resp.status}: {body[:200]}", "elapsed": time.time() - start}
            data = await resp.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return {
                "provider": name,
                "model": model,
                "review": text,
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "elapsed": round(time.time() - start, 2),
            }
    except Exception as e:
        return {"provider": name, "error": str(e), "elapsed": round(time.time() - start, 2)}


# ── Build review prompt ────────────────────────────────────────────────────────

def _build_prompt(content: str, filename: str, focus: str) -> str:
    focus_line = f"\n\nFocus areas: {focus}" if focus else ""
    return (
        f"Review the following Python file for bugs, async safety issues, error handling gaps, "
        f"and production reliability concerns.{focus_line}\n\n"
        f"File: {filename}\n\n"
        f"```python\n{content}\n```\n\n"
        "Provide specific findings with line references where possible. "
        "Rate each finding: [HIGH], [MEDIUM], or [LOW]. "
        "End with a one-line summary verdict."
    )


def _get_diff_content(ref: str) -> tuple[str, str]:
    result = subprocess.run(
        ["git", "diff", ref],
        capture_output=True, text=True, cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print(f"git diff failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout, f"git diff {ref}"


# ── Main ───────────────────────────────────────────────────────────────────────

async def run(files: list[str], focus: str, diff_ref: str | None, save: bool):
    _load_env()

    groq_key = os.environ.get("GROQ_API_KEY", "")
    cerebras_key = os.environ.get("CEREBRAS_API_KEY", "")

    if not groq_key and not cerebras_key:
        print("No API keys found. Set GROQ_API_KEY and/or CEREBRAS_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    reviewers = []
    if groq_key:
        reviewers.append(("Groq (Llama-8B)", "https://api.groq.com/openai/v1", groq_key, "llama-3.1-8b-instant"))
    if cerebras_key:
        reviewers.append(("Cerebras (Qwen3-235B)", "https://api.cerebras.ai/v1", cerebras_key, "qwen-3-235b-a22b-instruct-2507"))

    if diff_ref:
        content, filename = _get_diff_content(diff_ref)
        targets = [(filename, content)]
    else:
        targets = []
        for f in files:
            path = Path(f)
            if not path.exists():
                path = PROJECT_ROOT / f
            if not path.exists():
                print(f"File not found: {f}", file=sys.stderr)
                continue
            targets.append((path.name, path.read_text()))

    if not targets:
        print("No files to review.", file=sys.stderr)
        sys.exit(1)

    all_results = []

    async with aiohttp.ClientSession() as session:
        for idx, (filename, content) in enumerate(targets):
            if idx > 0:
                print(f"\n[Rate limit cooldown — waiting 65s before next file...]")
                await asyncio.sleep(65)

            print(f"\n{'='*70}")
            print(f"Reviewing: {filename}  ({len(content):,} chars)")
            print(f"{'='*70}")

            prompt = _build_prompt(content, filename, focus)
            tasks = [
                _review_openai(session, name, url, key, model, prompt)
                for name, url, key, model in reviewers
            ]
            results = await asyncio.gather(*tasks)

            for r in results:
                print(f"\n── {r['provider']} ({r.get('elapsed', 0):.1f}s) ──────────────────")
                if "error" in r:
                    print(f"ERROR: {r['error']}")
                else:
                    print(r["review"])
                    tok = r.get("input_tokens", 0) + r.get("output_tokens", 0)
                    print(f"\n[tokens used: {tok:,}]")

            all_results.extend(results)

    if save and all_results:
        out_dir = PROJECT_ROOT / "data" / "reviews"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = targets[0][0].replace("/", "_").replace(".py", "")
        out_path = out_dir / f"{ts}_{stem}.json"
        out_path.write_text(json.dumps(all_results, indent=2))
        print(f"\nSaved to {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Internal AI code reviewer (Groq + Cerebras)")
    parser.add_argument("files", nargs="*", help="Files to review")
    parser.add_argument("--focus", default="", help="Focus areas (e.g. 'async safety, error handling')")
    parser.add_argument("--diff", metavar="REF", help="Review git diff since REF (e.g. HEAD~1)")
    parser.add_argument("--no-save", action="store_true", help="Skip saving JSON output")
    args = parser.parse_args()

    if not args.files and not args.diff:
        parser.print_help()
        sys.exit(1)

    asyncio.run(run(
        files=args.files,
        focus=args.focus,
        diff_ref=args.diff,
        save=not args.no_save,
    ))


if __name__ == "__main__":
    main()
