"""run_tracked + nightly_sweeper — the error inbox (ported from live claude-brain 2026-10-19)."""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── run_tracked (real subprocess, real child) ────────────────────────────────

def _track(tmp_path, job, *cmd):
    env = dict(os.environ, NEXUS_DATA_DIR=str(tmp_path))
    return subprocess.run([sys.executable, str(REPO / "scripts" / "run_tracked.py"), job, "--", *cmd],
                          capture_output=True, text=True, env=env)


def test_run_tracked_passes_output_through_and_records(tmp_path):
    r = _track(tmp_path, "hello-job", sys.executable, "-c", "print('hello'); import sys; print('warn', file=sys.stderr)")
    assert r.returncode == 0 and r.stdout.strip() == "hello" and "warn" in r.stderr
    st = json.loads((tmp_path / "error-inbox" / "state" / "hello-job.json").read_text())
    assert st["exit_code"] == 0 and not st["has_traceback"] and st["stdout_tail"] == "hello"
    hist = (tmp_path / "error-inbox" / "state" / "history.jsonl").read_text().splitlines()
    assert json.loads(hist[-1])["job_id"] == "hello-job"


def test_run_tracked_preserves_exit_code_and_flags_traceback(tmp_path):
    r = _track(tmp_path, "crashy", sys.executable, "-c", "raise ValueError('nope')")
    assert r.returncode == 1 and "ValueError: nope" in r.stderr
    st = json.loads((tmp_path / "error-inbox" / "state" / "crashy.json").read_text())
    assert st["exit_code"] == 1 and st["has_traceback"]


def test_run_tracked_exit_zero_with_traceback_is_still_flagged(tmp_path):
    code = "import traceback\ntry:\n    1/0\nexcept Exception:\n    traceback.print_exc()\n"
    _track(tmp_path, "swallowed", sys.executable, "-c", code)
    st = json.loads((tmp_path / "error-inbox" / "state" / "swallowed.json").read_text())
    assert st["exit_code"] == 0 and st["has_traceback"]


def test_run_tracked_missing_command_is_127(tmp_path):
    r = _track(tmp_path, "ghost", "definitely-not-a-real-binary-xyz")
    assert r.returncode == 127


def test_run_tracked_bad_job_id(tmp_path):
    assert _track(tmp_path, "bad id!", "true").returncode == 2


# ── nightly_sweeper ──────────────────────────────────────────────────────────

@pytest.fixture
def sw(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path))
    mod = _load("_nightly_sweeper_test", "nightly_sweeper.py")
    monkeypatch.setattr(mod, "EXPECT_FILE", tmp_path / "expect.json")
    mod.STATE.mkdir(parents=True, exist_ok=True)
    return mod


def _state(sw, job, start, exit_code=0, tb=False, out="ok"):
    (sw.STATE / f"{job}.json").write_text(json.dumps({
        "job_id": job, "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "exit_code": exit_code,
        "has_traceback": tb, "crash_line": "ValueError: x" if tb else None,
        "stdout_bytes": len(out), "stderr_bytes": 0, "stdout_tail": out, "stderr_tail": ""}))


def test_paths_follow_data_dir(sw, tmp_path):
    assert sw.BASE == tmp_path / "error-inbox" and sw.TASK_JOURNAL_DIR == tmp_path / "task_journal"


def test_last_fire_cron_math(sw):
    before = datetime(2026, 10, 19, 12, 30, tzinfo=sw.LOCAL)
    assert sw.last_fire("0 6 * * *", before) == datetime(2026, 10, 19, 6, 0, tzinfo=sw.LOCAL)
    assert sw.last_fire("*/15 * * * *", before) == datetime(2026, 10, 19, 12, 30, tzinfo=sw.LOCAL)
    assert sw.last_fire("0 3 * * 0", before).weekday() == 6            # Sunday
    assert sw.last_fire("0 0 1 * *", before) == datetime(2026, 10, 1, 0, 0, tzinfo=sw.LOCAL)


def test_classify_states(sw):
    since = sw.NOW - timedelta(days=3)
    recent = sw.NOW - timedelta(minutes=2)    # inside the 10-min grace of a */5 job
    _state(sw, "good", recent)
    assert sw.classify("good", ["*/5 * * * *"], since, {})[0] == "OK"
    _state(sw, "broke", recent, exit_code=1)
    assert sw.classify("broke", ["*/5 * * * *"], since, {})[0] == "FAILED"
    _state(sw, "tb", recent, tb=True)
    assert sw.classify("tb", ["*/5 * * * *"], since, {})[0] == "FAILED"
    _state(sw, "quiet", recent, out="")
    assert sw.classify("quiet", ["*/5 * * * *"], since, {"quiet": {"require_output": True}})[0] == "SILENT"
    _state(sw, "pattern", recent, out="0 files copied")
    assert sw.classify("pattern", ["*/5 * * * *"], since,
                       {"pattern": {"must_not_match": ["0 files copied"]}})[0] == "SILENT"
    # Hourly job whose last run was a day ago → a fire time passed with no run
    _state(sw, "stalled", sw.NOW - timedelta(days=1))
    assert sw.classify("stalled", ["0 * * * *"], since, {})[0] == "DID_NOT_FIRE"


def test_classify_hung_from_start_marker(sw):
    (sw.STATE / ".longjob.started").write_text(
        (sw.NOW - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert sw.classify("longjob", [""], sw.NOW - timedelta(days=3), {})[0] == "HUNG"


def test_inbox_dedupes_and_recovers(sw):
    st = {"start_time": "2026-10-19T01:00:00Z", "exit_code": 1}
    sw.update_inbox({"job-a": ("FAILED", "exit 1", st)})
    sw.update_inbox({"job-a": ("FAILED", "exit 1", st)})                    # same run → no bump
    e = json.loads((sw.OPEN / "job-a.json").read_text())
    assert e["occurrences"] == 1
    sw.update_inbox({"job-a": ("FAILED", "exit 1", dict(st, start_time="2026-10-19T02:00:00Z"))})
    assert json.loads((sw.OPEN / "job-a.json").read_text())["occurrences"] == 2
    sw.update_inbox({"job-a": ("OK", "last run fine", None)})
    assert json.loads((sw.OPEN / "job-a.json").read_text())["status"] == "RECOVERED"


def test_crontab_parsing_tracks_both_spellings_and_reports_gaps(sw, monkeypatch):
    tab = ("SHELL=/bin/bash\n# comment\n"
           "0 6 * * * python3 /x/scripts/run_tracked.py daily-job -- python3 job.py\n"
           "*/5 * * * * run-tracked legacy-job -- ./thing\n"
           "@daily /usr/bin/untracked-thing\n")
    monkeypatch.setattr(sw.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=tab, stderr=""))
    jobs, untracked = sw.parse_crontab()
    assert jobs == {"daily-job": ["0 6 * * *"], "legacy-job": ["*/5 * * * *"]}
    assert untracked == ["@daily /usr/bin/untracked-thing"]


def test_hung_turns_read_nexus_task_journal(sw):
    sw.TASK_JOURNAL_DIR.mkdir(parents=True)
    (sw.TASK_JOURNAL_DIR / "t1.json").write_text(json.dumps({
        "task_id": "abcdef123456", "platform": "mattermost", "channel": "general",
        "question": "why is the sky blue", "phase": "dispatch",
        "started": (sw.NOW - timedelta(minutes=40)).isoformat()}))
    res = sw.hung_turns()
    assert list(res) == ["turn-hung__mattermost"] and res["turn-hung__mattermost"][0] == "HUNG"


def test_turn_crash_records_show_in_the_sweeper_dm(sw, tmp_path, monkeypatch):
    from src.core import turn_failure
    monkeypatch.setattr(turn_failure, "OPEN_DIR", sw.OPEN)
    turn_failure.record("telegram", "tg_1", "dispatch", RuntimeError("boom"))
    dm = sw.format_dm(sw.inbox_entries(), [])
    assert "turn-crash__telegram" in dm and "1 open" in dm


def test_main_end_to_end_with_stubbed_host(sw, monkeypatch, capsys):
    monkeypatch.setattr(sw, "parse_crontab", lambda: ({"good": ["*/5 * * * *"], "broke": ["*/5 * * * *"]}, []))
    monkeypatch.setattr(sw, "custom_timers", lambda: {})
    monkeypatch.setattr(sw, "always_on_services", lambda exp: {"service:web": ("FAILED", "inactive/dead", None)})
    recent = sw.NOW - timedelta(minutes=2)
    _state(sw, "good", recent)
    _state(sw, "broke", recent, exit_code=2)
    sent = []

    class _N:
        def send(self, msg, destination=None, **kw):
            sent.append((msg, destination))
            return True
    import src.core.notify as notify
    monkeypatch.setattr(notify.Notifier, "from_config", classmethod(lambda cls, *a, **k: _N()))
    monkeypatch.setattr(sw.sys, "argv", ["nightly_sweeper.py", "--notify"])
    assert sw.main() == 0
    report = json.loads(sw.REPORT.read_text())
    assert report["jobs"]["broke"]["status"] == "FAILED" and report["jobs"]["good"]["status"] == "OK"
    assert len(sent) == 1 and sent[0][1] == "dm" and "**broke** FAILED" in sent[0][0]
    assert "service:web" in sent[0][0]
