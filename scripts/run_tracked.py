#!/usr/bin/env python3
"""run_tracked — wrap a scheduled job and record how it went, for the error-inbox sweeper.

Ported from live claude-brain (2026-10-19). # NEXUS:PORTABLE — Linux-native, zero LLM calls.

Usage (crontab):  python3 <repo>/scripts/run_tracked.py <job-id> -- <command> [args...]

Contract:
  * TRANSPARENT: the job's stdout/stderr pass straight through to whatever the cron line
    redirects to, line by line. The wrapper must never swallow a job's log.
  * Exit code is preserved.
  * Records the latest run to  $NEXUS_DATA_DIR/error-inbox/state/<job-id>.json  and appends
    one line per run to  .../state/history.jsonl  (the sweeper prunes it).
  * PATH is extended with the user-level bin dirs cron's minimal PATH lacks
    (~/.local/bin, ~/.npm-global/bin) — on the reference install a bare CLI name under cron's
    PATH failed every night for months while the job reported "healthy".
  * Shell syntax (`cd x && y`, pipes) is NOT supported — run-tracked execs argv directly.
    Put the wrapper after the `cd`:  cd /dir && python3 run_tracked.py id -- python3 x.py

Companion: scripts/nightly_sweeper.py (judges these records against the crontab).
"""
import collections
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
STATE_DIR = _DATA_DIR / "error-inbox" / "state"
TAIL_LINES = 15
EXTRA_PATH = [str(Path.home() / ".npm-global" / "bin"), str(Path.home() / ".local" / "bin"),
              "/usr/local/bin"]

# Real crash signatures only. Plain words like "failed"/"error" appear in healthy
# reports ("0 failed", "errors: 0") and must not trip this.
CRASH_RE = re.compile(
    r"^Traceback \(most recent call last\):"
    r"|^\w+(Error|Exception): "
    r"|Segmentation fault|core dumped"
    r"|: command not found$|: No such file or directory$|Permission denied$",
    re.M,
)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pump(src, dst, tail, counter):
    for raw in iter(src.readline, b""):
        try:
            dst.write(raw)
            dst.flush()
        except Exception:
            pass
        line = raw.decode("utf-8", "replace").rstrip("\n")
        tail.append(line)
        counter[0] += len(raw)
    src.close()


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2
    job_id = argv[0]
    cmd = argv[1:]
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd or not re.fullmatch(r"[A-Za-z0-9._-]+", job_id):
        print(f"run_tracked: bad usage (job_id={job_id!r}, cmd={cmd!r})", file=sys.stderr)
        return 2

    env = dict(os.environ)
    path = env.get("PATH", "/usr/bin:/bin").split(":")
    env["PATH"] = ":".join([p for p in EXTRA_PATH if p not in path] + path)

    start, t0 = _now(), time.time()
    # Start marker: lets the sweeper tell "running/hung" apart from "never fired".
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f".{job_id}.started").write_text(start)
    except Exception:
        pass
    out_tail = collections.deque(maxlen=TAIL_LINES)
    err_tail = collections.deque(maxlen=TAIL_LINES)
    out_n, err_n = [0], [0]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        threads = [
            threading.Thread(target=_pump, args=(proc.stdout, sys.stdout.buffer, out_tail, out_n)),
            threading.Thread(target=_pump, args=(proc.stderr, sys.stderr.buffer, err_tail, err_n)),
        ]
        for t in threads:
            t.start()
        rc = proc.wait()
        for t in threads:
            t.join()
    except OSError as e:  # command missing / not executable
        rc = 127
        err_tail.append(f"run_tracked: cannot exec {cmd[0]!r}: {e}")
        print(err_tail[-1], file=sys.stderr)

    out_s, err_s = "\n".join(out_tail), "\n".join(err_tail)
    crash = CRASH_RE.search(out_s) or CRASH_RE.search(err_s)
    record = {
        "job_id": job_id,
        "command": cmd,
        "start_time": start,
        "end_time": _now(),
        "duration_s": round(time.time() - t0, 2),
        "exit_code": rc,
        "has_traceback": bool(crash),
        "crash_line": crash.group(0) if crash else None,
        "stdout_bytes": out_n[0],
        "stderr_bytes": err_n[0],
        "stdout_tail": out_s,
        "stderr_tail": err_s,
    }
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_DIR / f".{job_id}.json.tmp"
        tmp.write_text(json.dumps(record, indent=2))
        tmp.replace(STATE_DIR / f"{job_id}.json")
        with open(STATE_DIR / "history.jsonl", "a") as h:
            slim = {k: record[k] for k in ("job_id", "start_time", "end_time", "exit_code", "has_traceback")}
            h.write(json.dumps(slim) + "\n")
    except Exception as e:  # recording must never change the job's outcome
        print(f"run_tracked: could not record state: {e}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
