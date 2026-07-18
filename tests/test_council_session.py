"""Council session — per-run JSON audit trail: atomic writes, retention, exception-safety."""
import json

from src.orchestration.council_session import CouncilSession, list_sessions, MAX_SESSIONS


def _make(tmp_path, **kwargs):
    defaults = dict(question="what's our runway?", domain="finance", complexity=0.4, review_mode=False)
    defaults.update(kwargs)
    return CouncilSession(sessions_dir=tmp_path, **defaults)


def test_record_council_writes_a_file(tmp_path):
    sess = _make(tmp_path)
    sess.record_council(
        roles={"planner": "claude", "reviewer": "gemini"},
        role_responses={"planner": "burn rate is stable", "reviewer": "agree"},
        scope_violations={},
        failed_providers=[],
        synthesis_prompt="synthesize the above",
    )
    assert sess.path.exists()
    data = json.loads(sess.path.read_text())
    assert data["roles"] == {"planner": "claude", "reviewer": "gemini"}
    assert data["role_responses"]["planner"] == "burn rate is stable"
    assert data["synthesis_prompt"] == "synthesize the above"
    assert data["chairman_output"] is None
    assert data["judge"] == {}


def test_chairman_output_and_judge_verdict_update_same_file(tmp_path):
    sess = _make(tmp_path)
    sess.record_council(
        roles={"planner": "claude"},
        role_responses={"planner": "resp"},
        scope_violations={},
        failed_providers=[],
        synthesis_prompt="prompt",
    )
    sess.record_chairman_output("final synthesized answer")
    sess.record_judge_verdict(
        role="planner",
        provider="claude",
        verdict={"score": 0.9, "hallucinated": False},
        judge_model="judge-model",
        ewma_before=0.5,
        ewma_after=0.58,
    )

    # only one file on disk — same session updated in place, not a new file per phase
    files = list(tmp_path.glob("council_session_*.json"))
    assert len(files) == 1

    data = json.loads(sess.path.read_text())
    assert data["chairman_output"] == "final synthesized answer"
    assert data["judge"]["planner"]["provider"] == "claude"
    assert data["judge"]["planner"]["score"] == 0.9
    assert data["judge"]["planner"]["ewma_before"] == 0.5
    assert data["judge"]["planner"]["ewma_after"] == 0.58


def test_retention_caps_file_count(tmp_path):
    # write more than MAX_SESSIONS records to the same dir and confirm pruning kicks in
    extra = 5
    for _ in range(MAX_SESSIONS + extra):
        sess = _make(tmp_path)
        sess.record_council(
            roles={"planner": "claude"},
            role_responses={"planner": "r"},
            scope_violations={},
            failed_providers=[],
            synthesis_prompt="p",
        )

    files = list(tmp_path.glob("council_session_*.json"))
    assert len(files) == MAX_SESSIONS

    newest = list_sessions(limit=MAX_SESSIONS, sessions_dir=tmp_path)
    assert len(newest) == MAX_SESSIONS


def test_atomic_write_leaves_no_partial_files(tmp_path):
    sess = _make(tmp_path)
    sess.record_council(
        roles={"planner": "claude"},
        role_responses={"planner": "r"},
        scope_violations={},
        failed_providers=[],
        synthesis_prompt="p",
    )
    # the .tmp staging file must be renamed away, never left behind
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
    assert sess.path.exists()
    # and the file that does exist must be valid, complete JSON (no torn write)
    json.loads(sess.path.read_text())


def test_bad_input_does_not_crash(tmp_path):
    sess = _make(tmp_path)
    # role_responses values that can't be clipped like strings (None) must not raise
    sess.record_council(
        roles={"planner": "claude"},
        role_responses={"planner": None},
        scope_violations=None,
        failed_providers=[],
        synthesis_prompt=None,
    )
    # record_judge_verdict with a verdict missing expected keys must not raise
    sess.record_judge_verdict(role="planner", provider="claude", verdict={})
    # record_chairman_output with a non-string should be caught, not propagate
    sess.record_chairman_output(12345)  # len() on an int inside _clip -> caught by try/except

    # session object is still usable; whatever last succeeded is on disk (or nothing, but no crash)
    assert sess.session_id.startswith("council_session_")
