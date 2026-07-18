"""Tests for orchestration staging: per-task_id worker-finding storage
(write/read/clear), keeping temp findings off canonical memory.
"""
import json

from src.orchestration import staging


def test_stage_finding_creates_record(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)

    staging.stage_finding("task-1", "worker-a", "research", "claude", "found X")

    findings = staging.get_staged_findings("task-1")
    assert len(findings) == 1
    assert findings[0]["worker_id"] == "worker-a"
    assert findings[0]["subtask"] == "research"
    assert findings[0]["provider"] == "claude"
    assert findings[0]["finding"] == "found X"
    assert "staged_at" in findings[0]


def test_stage_finding_appends_multiple(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)

    staging.stage_finding("task-2", "worker-a", "sub-1", "claude", "finding one")
    staging.stage_finding("task-2", "worker-b", "sub-2", "gemini", "finding two")

    findings = staging.get_staged_findings("task-2")
    assert len(findings) == 2
    assert [f["worker_id"] for f in findings] == ["worker-a", "worker-b"]


def test_get_staged_findings_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)

    assert staging.get_staged_findings("nonexistent-task") == []


def test_get_staged_findings_resets_on_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)
    path = staging._path("task-3")
    path.write_text("not valid json{{{")

    assert staging.get_staged_findings("task-3") == []


def test_stage_finding_resets_corrupt_record_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)
    path = staging._path("task-4")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage")

    staging.stage_finding("task-4", "worker-a", "sub", "claude", "ok")

    record = json.loads(path.read_text())
    assert len(record["findings"]) == 1


def test_clear_staging_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)

    staging.stage_finding("task-5", "worker-a", "sub", "claude", "ok")
    assert staging._path("task-5").exists()

    staging.clear_staging("task-5")
    assert not staging._path("task-5").exists()
    assert staging.get_staged_findings("task-5") == []


def test_clear_staging_missing_task_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(staging, "STAGING_DIR", tmp_path)

    staging.clear_staging("never-existed")  # should not raise
