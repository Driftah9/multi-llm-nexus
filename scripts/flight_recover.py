#!/usr/bin/env python3
"""flight_recover — read the flight-recorder black box after a crash.

Reconstructs a turn from the append-only journal written by
src/core/flight_recorder.py: the recorded turn context, every failover
attempt, and all partial chunks received up to the moment of failure — i.e.
exactly what was in flight when a provider died.

Journal directory resolution matches src/core/flight_recorder.py:
  FLIGHT_RECORDER_DIR env override, else layout.path("Logs")/flight_recorder,
  else ~/Logs/flight_recorder as a last resort.

Usage:
  flight_recover.py                     # list recent turns (today + yesterday)
  flight_recover.py --failed            # list only failed turns
  flight_recover.py <turn_id>           # full reconstruction of one turn
  flight_recover.py <turn_id> --prompt  # dump the recorded prompt (for handoff
                                        #  to another provider)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core import layout  # noqa: E402


def _base_dir() -> Path:
    override = os.environ.get("FLIGHT_RECORDER_DIR")
    if override:
        return Path(override)
    try:
        return layout.path("Logs") / "flight_recorder"
    except Exception:
        return Path.home() / "Logs" / "flight_recorder"


BASE = _base_dir()


def _load(days=3):
    events = []
    now = datetime.now(timezone.utc)
    for d in range(days):
        f = BASE / f"{(now - timedelta(days=d)).strftime('%Y-%m-%d')}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text(errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _turns(events):
    turns = {}
    for e in events:
        turns.setdefault(e["turn"], []).append(e)
    return turns


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M:%S")


def list_turns(only_failed=False):
    turns = _turns(_load())
    rows = []
    for tid, evs in turns.items():
        start = next((e for e in evs if e["ev"] == "turn_start"), None)
        end = next((e for e in evs if e["ev"] == "turn_end"), None)
        nchunks = sum(1 for e in evs if e["ev"] == "chunk")
        ok = end["ok"] if end else None  # None = no turn_end → crashed mid-flight
        if only_failed and ok is True:
            continue
        rows.append((
            evs[0]["ts"], tid,
            "OK" if ok else ("FAILED" if ok is False else "NO-END (crash?)"),
            (start or {}).get("platform", "?"),
            ((start or {}).get("user_text") or (start or {}).get("text") or "")[:60],
            nchunks,
        ))
    rows.sort()
    for ts, tid, status, plat, text, nchunks in rows:
        print(f"{_fmt_ts(ts)}  {tid}  {status:14s} {plat:10s} chunks={nchunks:<4d} {text}")
    if not rows:
        print("no matching turns in journal window")


def show_turn(tid, prompt_only=False):
    turns = _turns(_load(days=14))
    evs = turns.get(tid)
    if not evs:
        # allow prefix match
        matches = [t for t in turns if t.startswith(tid)]
        if len(matches) == 1:
            evs = turns[matches[0]]
        else:
            print(f"turn {tid} not found ({len(matches)} prefix matches)")
            return
    start = next((e for e in evs if e["ev"] == "turn_start"), {})
    if prompt_only:
        print(start.get("prompt", "(no prompt recorded)"))
        return
    print(f"=== TURN {evs[0]['turn']} — {start.get('platform')} / "
          f"{start.get('session_key')} / user={start.get('username')}")
    print(f"user_text: {start.get('user_text') or start.get('text')}")
    print(f"tier={start.get('tier')} first_provider={start.get('first_provider')} "
          f"prompt_chars={len(start.get('prompt') or '')}")
    partials = {}
    for e in evs:
        if e["ev"] == "attempt_start":
            print(f"\n--- attempt: {e['provider']} (model={e.get('model')}) "
                  f"@ {_fmt_ts(e['ts'])}")
        elif e["ev"] == "chunk":
            partials.setdefault(e["provider"], []).append(e["text"])
        elif e["ev"] == "attempt_end":
            got = "".join(partials.get(e["provider"], []))
            print(f"    end: ok={e['ok']} text_len={e.get('text_len', 0)} "
                  f"error={e.get('error')}")
            if got:
                print(f"    in-flight received ({len(got)} chars): {got[:1500]}")
        elif e["ev"] == "turn_end":
            print(f"\n=== turn_end ok={e['ok']} winner={e.get('provider')} "
                  f"@ {_fmt_ts(e['ts'])}")
    ended = any(e["ev"] == "turn_end" for e in evs)
    if not ended:
        print("\n=== NO turn_end — process/host died mid-turn. "
              "In-flight text per provider above is everything received.")
        for prov, chunks in partials.items():
            got = "".join(chunks)
            print(f"\n[{prov}] {len(got)} chars received before cutoff:\n{got[-3000:]}")


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        list_turns()
    elif args[0] == "--failed":
        list_turns(only_failed=True)
    else:
        show_turn(args[0], prompt_only="--prompt" in args)


if __name__ == "__main__":
    main()
