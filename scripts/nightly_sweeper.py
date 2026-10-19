#!/usr/bin/env python3
"""nightly_sweeper — verify that everything scheduled actually ran, and ran clean.

Ported from live claude-brain (2026-10-19). # NEXUS:PORTABLE — Linux-native (crontab +
systemd), zero LLM calls; the operator DM goes through the Notifier seam.

Source of truth for WHAT should run is the live schedule itself (crontab + custom systemd
timers), never a hand-kept registry — anything scheduled is checked.

Per crontab job wrapped with run_tracked (state in $NEXUS_DATA_DIR/error-inbox/state/):
  FAILED        last run exited non-zero, or printed a crash signature (traceback etc.)
  DID_NOT_FIRE  a scheduled fire time passed (after tracking began) with no run started
  HUNG          started but never finished, for longer than HUNG_HOURS
  SILENT        ran "clean" but broke a per-job expectation (config/error-inbox-expectations.json)
  HUNG (turns)  a task_journal entry in flight > 15 min (turn-hung__<platform>)
  OK / NOT_YET_DUE
Custom systemd timers are checked natively (unit Result / exit status), and every enabled
long-running service under /etc/systemd/system must be active.
Crontab lines NOT wrapped with run_tracked are reported as coverage gaps.

Inbox: $NEXUS_DATA_DIR/error-inbox/open/<job>.json (turn crashes from core/turn_failure land here too) — one entry per job, de-duplicated (first_seen,
last_seen, occurrences). A job that comes good is marked RECOVERED, not deleted; entries are
removed only on operator request (--clear JOB | --clear-recovered), per the inbox rule.

Flags:  --notify         DM the operator (Notifier seam) if anything is open
        --dry-run        classify and print, write nothing
        --clear JOB      remove one inbox entry          --clear-recovered  remove all RECOVERED
Exit 0 = sweep completed (whatever it found). Non-zero = the sweeper itself failed — run it
from a systemd unit with OnFailure= or a run_tracked cron line so that failure is itself seen.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", _REPO / "data"))
BASE = _DATA_DIR / "error-inbox"
STATE = BASE / "state"
OPEN = BASE / "open"
SINCE_FILE = BASE / "tracking_since"
REPORT = BASE / "last-sweep.json"
EXPECT_FILE = Path(os.environ.get("ERROR_INBOX_EXPECTATIONS", _REPO / "config" / "error-inbox-expectations.json"))
TASK_JOURNAL_DIR = _DATA_DIR / "task_journal"
GRACE = timedelta(minutes=10)        # cron start lag + `sleep N &&` prefixes
HUNG_HOURS = 6
HISTORY_DAYS = 8
CUSTOM_TIMERS_EXCLUDE = re.compile(
    r"^(apt-|man-db|logrotate|e2scrub|fstrim|motd|systemd-|dpkg|fwupd|update-notifier|sysstat|"
    r"snapd|apport|ua-|plocate|esm|error-inbox-|anacron|phpsessionclean|certbot|sysstat)"
)

DRY = "--dry-run" in sys.argv
LOCAL = datetime.now().astimezone().tzinfo
NOW = datetime.now(timezone.utc)


# ── cron schedule math (local time, Debian cron semantics) ──────────────────────
NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
         "sep": 9, "oct": 10, "nov": 11, "dec": 12,
         "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
          "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
          "@hourly": "0 * * * *"}


def _field(spec, lo, hi):
    vals = set()
    for part in spec.lower().split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(NAMES.get(x, x)) for x in part.split("-"))
        else:
            a = int(NAMES.get(part, part))
            b = hi if step > 1 else a
        vals.update(range(a, b + 1, step))
    return vals


def last_fire(sched, before):
    """Latest local datetime <= before matching a 5-field cron spec (None if >400 days)."""
    m, h, dom, mon, dow = sched.split()
    M, H = _field(m, 0, 59), _field(h, 0, 23)
    D, MO = _field(dom, 1, 31), _field(mon, 1, 12)
    W = {d % 7 for d in _field(dow, 0, 7)}
    dom_star, dow_star = dom == "*", dow == "*"
    b = before.astimezone(LOCAL).replace(second=0, microsecond=0)
    day = b.date()
    for _ in range(400):
        if day.month in MO:
            dmatch, wmatch = day.day in D, (day.isoweekday() % 7) in W
            ok = (dmatch and wmatch) if (dom_star or dow_star) else (dmatch or wmatch)
            if ok:
                for hh in sorted(H, reverse=True):
                    for mm in sorted(M, reverse=True):
                        cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=LOCAL)
                        if cand <= b:
                            return cand
        day -= timedelta(days=1)
    return None


def boot_time():
    up = float(Path("/proc/uptime").read_text().split()[0])
    return NOW - timedelta(seconds=up)


# ── inputs ──────────────────────────────────────────────────────────────────────
def parse_crontab():
    txt = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    jobs, untracked = {}, []
    for line in txt.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", s):
            continue
        if s.startswith("@"):
            tok, rest = s.split(None, 1)
            sched = "@reboot" if tok == "@reboot" else MACROS.get(tok, None)
        else:
            parts = s.split(None, 5)
            if len(parts) < 6:
                continue
            sched, rest = " ".join(parts[:5]), parts[5]
        m = re.search(r"run[-_]tracked(?:\.py)?\s+([A-Za-z0-9._-]+)\s+--\s", rest)
        if not m:
            untracked.append(s)
            continue
        jobs.setdefault(m.group(1), []).append(sched)
    return jobs, untracked


def load_json(p, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def parse_ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


# ── classification ──────────────────────────────────────────────────────────────
def classify(job, scheds, since, expectations):
    st = load_json(STATE / f"{job}.json")
    started_marker = parse_ts((STATE / f".{job}.started").read_text().strip()) \
        if (STATE / f".{job}.started").exists() else None
    last_start = parse_ts(st["start_time"]) if st else None
    latest_start = max(filter(None, [last_start, started_marker]), default=None)

    expected = []
    for s in scheds:
        f = boot_time() if s == "@reboot" else (last_fire(s, NOW - GRACE) if s else None)
        if f:
            expected.append(f.astimezone(timezone.utc))
    exp = max(expected, default=None)

    if started_marker and (not last_start or started_marker > last_start):
        if NOW - started_marker > timedelta(hours=HUNG_HOURS):
            return "HUNG", f"started {started_marker:%Y-%m-%d %H:%M}Z, never finished", st
    if exp and exp >= since and (not latest_start or latest_start < exp - timedelta(minutes=1)):
        return "DID_NOT_FIRE", f"due {exp.astimezone(LOCAL):%a %m-%d %H:%M}, no run started", st
    exp_cfg = expectations.get(job, {})
    max_age_hours = exp_cfg.get("max_age_hours")

    if not st:
        # No state file. Check if job should have run by now based on max_age.
        if max_age_hours:
            # For jobs with max_age set, flag as STALE if last scheduled time was older than max_age
            f = max(filter(None, [last_fire(s, NOW - GRACE) if s else None for s in scheds]), default=None)
            # A miss before tracking began is unjudgeable; misses after it are DID_NOT_FIRE above.
            if f and f >= since:
                age = (NOW - f).total_seconds() / 3600
                if age > max_age_hours:
                    return "STALE", f"no run for {age:.0f}h (due {max_age_hours}h ago)", None
        return "NOT_YET_DUE", "no run recorded since tracking began", None
    if st["exit_code"] != 0 or st.get("has_traceback"):
        why = f"exit {st['exit_code']}"
        if st.get("crash_line"):
            why += f" — {st['crash_line'][:160]}"
        return "FAILED", why, st

    # Check staleness: if job has a max_age_hours expectation, verify it hasn't exceeded
    max_age_hours = exp_cfg.get("max_age_hours")
    if max_age_hours:
        last_run = parse_ts(st.get("start_time"))
        if last_run:
            age_hours = (NOW - last_run).total_seconds() / 3600
            if age_hours > max_age_hours:
                return "STALE", f"last run {age_hours:.0f}h ago (max {max_age_hours}h)", st
    if exp_cfg.get("require_output") and not (st.get("stdout_bytes") or st.get("stderr_bytes")):
        return "SILENT", "exit 0 but produced no output (expected output)", st
    if exp_cfg.get("must_match"):
        blob = (st.get("stdout_tail") or "") + "\n" + (st.get("stderr_tail") or "")
        if not re.search(exp_cfg["must_match"], blob, re.M):
            return "SILENT", f"exit 0 but output lacks /{exp_cfg['must_match']}/", st
    for bad in exp_cfg.get("must_not_match", []):
        hit = re.search(bad, (st.get("stdout_tail") or "") + "\n" + (st.get("stderr_tail") or ""), re.M)
        if hit:
            return "SILENT", f"exit 0 but output shows /{bad}/: {hit.group(0)[:120]}", st
    return "OK", f"last run {parse_ts(st['start_time']).astimezone(LOCAL):%m-%d %H:%M}", st


def custom_timers():
    out = subprocess.run(["systemctl", "list-timers", "--all", "--no-legend", "--plain"],
                         capture_output=True, text=True).stdout
    res = {}
    for line in out.splitlines():
        cols = line.split()
        units = [c for c in cols if c.endswith(".timer")]
        if not units:
            continue
        timer = units[0]
        if CUSTOM_TIMERS_EXCLUDE.match(timer):
            continue
        svc = cols[-1] if cols[-1].endswith(".service") else timer.replace(".timer", ".service")
        p = subprocess.run(["systemctl", "show", svc, "-p", "Result", "-p", "ExecMainStatus",
                            "-p", "ExecMainStartTimestamp"], capture_output=True, text=True).stdout
        kv = dict(l.split("=", 1) for l in p.splitlines() if "=" in l)
        name = "timer:" + timer.replace(".timer", "")
        if kv.get("Result", "success") != "success" or kv.get("ExecMainStatus", "0") not in ("0", ""):
            res[name] = ("FAILED", f"{svc} Result={kv.get('Result')} status={kv.get('ExecMainStatus')} "
                                   f"(last start {kv.get('ExecMainStartTimestamp') or 'never'})", None)
        else:
            res[name] = ("OK", f"{svc} last start {kv.get('ExecMainStartTimestamp') or 'never'}", None)
    return res


def hung_turns():
    """Scan task_journal/*.json for in-flight tasks older than 15 min.
    Returns dict of job_id -> (status, why, st) for turns that never finished."""
    res = {}
    task_dir = TASK_JOURNAL_DIR
    if not task_dir.exists():
        return res

    hung_threshold = timedelta(minutes=15)
    for task_file in task_dir.glob("*.json"):
        if task_file.name == "reply_queue.db":  # skip the DB file
            continue
        try:
            task_data = json.loads(task_file.read_text())
        except Exception:
            continue

        started_str = task_data.get("started")
        if not started_str:
            continue

        try:
            started = datetime.fromisoformat(started_str.replace("Z", "+00:00"))
        except Exception:
            continue

        age = NOW - started
        if age > hung_threshold:
            platform = task_data.get("platform", "unknown")
            job_id = f"turn-hung__{platform}"
            question = task_data.get("question", "")[:80]
            phase = task_data.get("phase", "unknown")
            detail = f"turn {task_data['task_id'][:8]} phase={phase} question={question} age={age.total_seconds()/60:.0f}m"
            # Evidence dict needs start_time for deduplication, and custom fields for display
            evidence = {
                "start_time": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "task_id": task_data.get("task_id"),
                "channel": task_data.get("channel"),
                "phase": phase,
                "question": question,
                "age_seconds": age.total_seconds(),
            }
            res[job_id] = ("HUNG", detail, evidence)
    return res


def always_on_services(expectations):
    """Every enabled long-running unit we installed (/etc/systemd/system) must be active.
    Oneshots are judged on their last Result instead. Opt-outs: expectations["_ignore_units"]."""
    ignore = set(expectations.get("_ignore_units", []))
    out = subprocess.run(["systemctl", "list-unit-files", "--type=service", "--state=enabled",
                          "--no-legend", "--plain"], capture_output=True, text=True).stdout
    res = {}
    for line in out.splitlines():
        unit = line.split()[0]
        if unit.endswith("@.service") or unit in ignore or unit.startswith("error-inbox-"):
            continue
        p = subprocess.run(["systemctl", "show", unit, "-p", "FragmentPath", "-p", "Type", "-p",
                            "ActiveState", "-p", "SubState", "-p", "Result", "-p", "ExecMainStatus",
                            "-p", "ActiveEnterTimestamp", "-p", "InactiveEnterTimestamp"],
                           capture_output=True, text=True).stdout
        kv = dict(l.split("=", 1) for l in p.splitlines() if "=" in l)
        if not kv.get("FragmentPath", "").startswith("/etc/systemd/system/"):
            continue
        name = "service:" + unit.replace(".service", "")
        if kv.get("Type") == "oneshot":
            bad = kv.get("Result") not in ("success", "")
            res[name] = (("FAILED", f"oneshot Result={kv.get('Result')} status={kv.get('ExecMainStatus')}", None)
                         if bad else ("OK", "oneshot last result success", None))
        elif kv.get("ActiveState") != "active":
            res[name] = ("FAILED", f"{kv.get('ActiveState')}/{kv.get('SubState')} since "
                                   f"{kv.get('InactiveEnterTimestamp') or '?'} (exit {kv.get('ExecMainStatus')})", None)
        else:
            res[name] = ("OK", f"active since {kv.get('ActiveEnterTimestamp')}", None)
    return res


# ── inbox ───────────────────────────────────────────────────────────────────────
PROBLEMS = {"FAILED", "DID_NOT_FIRE", "HUNG", "SILENT", "STALE"}


def update_inbox(results):
    OPEN.mkdir(parents=True, exist_ok=True)
    now = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    for job, (status, why, st) in results.items():
        f = OPEN / f"{job.replace(':', '__')}.json"
        cur = load_json(f)
        run_key = st["start_time"] if st else why
        if status in PROBLEMS:
            if cur and cur.get("status") != "RECOVERED":
                if cur.get("last_run_key") != run_key:
                    cur["occurrences"] += 1
                cur.update(status=status, detail=why, last_seen=now, last_run_key=run_key)
            else:
                cur = {"job_id": job, "status": status, "detail": why, "first_seen": now,
                       "last_seen": now, "occurrences": 1, "last_run_key": run_key,
                       "previous": cur}
            if st:
                # Extract standard run-tracked fields, plus any custom hung-turn fields
                evidence = {k: st.get(k) for k in ("exit_code", "crash_line", "stderr_tail", "stdout_tail", "command") if k in st}
                # For hung turns, include custom fields (task_id, channel, phase, question, age_seconds)
                for k in ("task_id", "channel", "phase", "question", "age_seconds"):
                    if k in st:
                        evidence[k] = st[k]
                cur["evidence"] = evidence
            f.write_text(json.dumps(cur, indent=2))
        elif cur and cur.get("status") != "RECOVERED" and status == "OK":
            cur.update(status="RECOVERED", recovered_at=now, recovered_detail=why)
            f.write_text(json.dumps(cur, indent=2))


def inbox_entries():
    return [load_json(f) for f in sorted(OPEN.glob("*.json"))] if OPEN.exists() else []


def prune_history():
    h = STATE / "history.jsonl"
    if not h.exists():
        return
    cut = (NOW - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    keep = [l for l in h.read_text().splitlines(True) if (load_json_line(l) or {}).get("start_time", "") >= cut]
    h.write_text("".join(keep))


def load_json_line(l):
    try:
        return json.loads(l)
    except Exception:
        return None


def format_dm(entries, untracked):
    open_ = [e for e in entries if e["status"] != "RECOVERED"]
    rec = [e for e in entries if e["status"] == "RECOVERED"]
    lines = [f"🔴 **Error inbox — {len(open_)} open**" if open_ else "🟢 **Error inbox — nothing open**"]
    order = ["FAILED", "DID_NOT_FIRE", "HUNG", "STALE", "SILENT"]
    for e in sorted(open_, key=lambda e: (order.index(e["status"]), e["job_id"])):
        since = e["first_seen"][:10]
        lines.append(f"- **{e['job_id']}** {e['status']} — {e['detail']} (since {since}, ×{e['occurrences']})")
    if rec:
        lines.append(f"\nRecovered, awaiting your OK to clear: {', '.join(e['job_id'] for e in rec)}")
    if untracked:
        lines.append(f"\nCoverage gap: {len(untracked)} crontab line(s) not wrapped with run_tracked.")
    lines.append(f"\nDetail: `{OPEN}` · clear: `nightly_sweeper.py --clear <job>`")
    return "\n".join(lines)


def main():
    if "--clear" in sys.argv:
        job = sys.argv[sys.argv.index("--clear") + 1]
        (OPEN / f"{job.replace(':', '__')}.json").unlink(missing_ok=True)
        print(f"cleared {job}")
        return 0
    if "--clear-recovered" in sys.argv:
        for e in inbox_entries():
            if e["status"] == "RECOVERED":
                (OPEN / f"{e['job_id'].replace(':', '__')}.json").unlink(missing_ok=True)
                print(f"cleared {e['job_id']}")
        return 0

    BASE.mkdir(parents=True, exist_ok=True)
    if not SINCE_FILE.exists() and not DRY:
        SINCE_FILE.write_text(NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    since = parse_ts(SINCE_FILE.read_text().strip()) if SINCE_FILE.exists() else NOW
    expectations = load_json(EXPECT_FILE, {}) or {}
    expectations = {k: v for k, v in expectations.items() if not k.startswith("_")}

    jobs, untracked = parse_crontab()
    # A job is only judgeable from when it first appeared in the crontab — otherwise every newly
    # added job is DID_NOT_FIRE for a fire time that predates it. Jobs that already ran under
    # tracking inherit tracking_since; brand-new ones start now.
    seen_file = STATE / ".jobs_first_seen.json"
    first_seen = load_json(seen_file, {}) or {}
    for job in jobs:
        if job not in first_seen:
            ran = (STATE / f"{job}.json").exists() or (STATE / f".{job}.started").exists()
            first_seen[job] = (since if ran else NOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not DRY:
        seen_file.write_text(json.dumps(first_seen, indent=2, sort_keys=True))
    results = {job: classify(job, scheds, max(since, parse_ts(first_seen[job])), expectations)
               for job, scheds in sorted(jobs.items())}
    results.update(custom_timers())
    results.update(always_on_services(load_json(EXPECT_FILE, {}) or {}))
    hung = hung_turns()
    results.update(hung)
    # A turn-hung entry whose turn has since finished must recover, not sit HUNG until hand-cleared.
    for f in OPEN.glob("turn-hung__*.json") if OPEN.exists() else []:
        job = f.stem
        if job not in hung:
            results[job] = ("OK", "no turn in flight past the hung threshold", None)

    counts = {}
    for job, (status, why, _) in results.items():
        counts[status] = counts.get(status, 0) + 1
        if status != "OK":
            print(f"{status:13} {job:32} {why}")
    print("summary:", json.dumps(counts), f"untracked={len(untracked)}")
    for u in untracked:
        print("UNTRACKED    ", u[:150])
    if DRY:
        return 0

    update_inbox(results)
    prune_history()
    REPORT.write_text(json.dumps({
        "swept_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "tracking_since": SINCE_FILE.read_text().strip(),
        "counts": counts, "untracked": untracked,
        "jobs": {j: {"status": s, "detail": w} for j, (s, w, _) in results.items()},
    }, indent=2))

    if "--notify" in sys.argv:
        entries = inbox_entries()
        if any(e["status"] != "RECOVERED" for e in entries) or untracked:
            sys.path.insert(0, str(_REPO))
            from src.core.notify import Notifier
            if not Notifier.from_config().send(format_dm(entries, untracked), destination="dm"):
                print("DM send FAILED", file=sys.stderr)
                return 1
            print("DM sent")
        else:
            print("nothing open — no DM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
