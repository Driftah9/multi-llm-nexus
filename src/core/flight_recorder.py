"""flight_recorder — append-only in-flight turn journal (the black box).

Records every turn's assembled prompt, each failover attempt, and every partial
chunk AS IT ARRIVES at the provider seam, so a mid-generation provider crash
(or process/host crash) leaves a complete local record up to the last token
received — nothing conversational lives only on a provider's side. Provider-
neutral by construction: it taps `ProviderChain.try_with_fallback()`, the seam
every lane in the engine already flows through. # NEXUS:PORTABLE

Design constraints:
- FAIL-OPEN: a recorder error must never break a turn. Every public function
  swallows exceptions.
- Append-only JSONL, one file per UTC day under the journal dir (see below);
  O_APPEND semantics via mode "a" (small line writes are atomic on Linux).
  Provider-crash durability needs no fsync (local process survives and
  flushes); we flush per line so a local *process* crash loses at most the
  line in transit.
- Retention: files older than FLIGHT_RECORDER_RETENTION_DAYS are removed
  opportunistically (default 14).

Journal directory resolution (in priority order):
  1. `FLIGHT_RECORDER_DIR` env override, if set.
  2. `layout.path("Logs") / "flight_recorder"` — the manifest-declared runtime
     logs home (`config/directory_layout.json`), so every install keeps this
     in the one place it already keeps everything else it logs.
  3. `Path.home() / "Logs" / "flight_recorder"` as a last-resort fallback if
     the layout manifest can't be read (fail-open — recording degrades, it
     never raises into the caller).

Reader/recovery tool: scripts/flight_recover.py
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import layout

# Chunks are appended verbatim; prompts are large but bounded per turn.
_MAX_FIELD = 200_000

_cur_day = None
_cur_base: Path | None = None
_cur_fh = None


def _base_dir() -> Path:
    """Resolve the journal directory fresh on every call (cheap, and lets an env
    override or manifest change take effect without a process restart — also
    what makes fail-open-on-bad-dir testable without reloading the module)."""
    override = os.environ.get("FLIGHT_RECORDER_DIR")
    if override:
        return Path(override)
    try:
        return layout.path("Logs") / "flight_recorder"
    except Exception:
        return Path.home() / "Logs" / "flight_recorder"


def _retention_days() -> int:
    try:
        return int(os.environ.get("FLIGHT_RECORDER_RETENTION_DAYS", "14"))
    except ValueError:
        return 14


def _fh():
    """Line-buffered handle for today's journal, rotating at UTC midnight (or
    immediately if the resolved base directory changed, e.g. env override)."""
    global _cur_day, _cur_base, _cur_fh
    base = _base_dir()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if day != _cur_day or base != _cur_base or _cur_fh is None or _cur_fh.closed:
        if _cur_fh and not _cur_fh.closed:
            try:
                _cur_fh.close()
            except Exception:
                pass
        base.mkdir(parents=True, exist_ok=True)
        _cur_fh = open(base / f"{day}.jsonl", "a", buffering=1, encoding="utf-8")
        _cur_day = day
        _cur_base = base
        _prune(base)
    return _cur_fh


def _prune(base: Path) -> None:
    try:
        cutoff = time.time() - _retention_days() * 86400
        for f in base.glob("*.jsonl"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:
        pass


def _write(ev: str, turn_id: str, **fields) -> None:
    try:
        rec = {"ts": round(time.time(), 3), "ev": ev, "turn": turn_id}
        for k, v in fields.items():
            if isinstance(v, str) and len(v) > _MAX_FIELD:
                v = v[:_MAX_FIELD] + f"…[truncated {len(v)} chars]"
            rec[k] = v
        _fh().write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def turn_start(turn_id: str, tier: str | None = None, **fields) -> None:
    """Record the turn's opening state — the provider-neutral working set.

    `fields` is deliberately open-ended: callers pass whatever context they
    have (prompt, platform, session_key, username, text, first_provider, ...).
    Anything omitted is simply absent from the record — this must work when
    called with nothing but a turn_id.
    """
    _write("turn_start", turn_id, tier=tier, **fields)


def attempt_start(turn_id: str, provider: str, model=None) -> None:
    _write("attempt_start", turn_id, provider=provider, model=model)


def chunk(turn_id: str, provider: str, text: str) -> None:
    """A partial chunk/status arrived from the provider — the in-flight data."""
    _write("chunk", turn_id, provider=provider, text=text)


def attempt_end(turn_id: str, provider: str, ok: bool, error=None,
                text_len: int = 0) -> None:
    _write("attempt_end", turn_id, provider=provider, ok=ok,
           error=(str(error)[:500] if error else None), text_len=text_len)


def turn_end(turn_id: str, ok: bool, provider=None, final_text=None) -> None:
    _write("turn_end", turn_id, ok=ok, provider=provider, final_text=final_text)
