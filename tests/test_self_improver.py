"""Tests for the self-improvement loop."""

import asyncio
import json
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.lifecycle.self_improver import (
    SelfImprover,
    TriageAccuracy,
    MisclassificationPattern,
    ImprovementProposal,
)


@pytest.fixture
def tmp_validation_db(tmp_path):
    """Create a temporary triage-validation DB with test data."""
    db_path = tmp_path / "test-validation.db"
    conn = sqlite3.connect(str(db_path))

    # Create schema (minimal for testing)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS triage_validations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       REAL    NOT NULL,
            tier            TEXT    NOT NULL,
            domain          TEXT,
            channel         TEXT,
            predicted_tier  TEXT,
            correct         INTEGER NOT NULL
        );
    """)

    # Insert test data
    now = datetime.now(timezone.utc).timestamp()
    recent = now - (3 * 24 * 3600)  # 3 days ago

    # Good accuracy in nano tier
    for i in range(10):
        conn.execute(
            "INSERT INTO triage_validations VALUES (NULL, ?, 'nano', 'coding', 'dev', NULL, 1)",
            (recent + i*100,),
        )

    # Misclassifications in standard tier for 'analysis' domain
    for i in range(7):
        conn.execute(
            "INSERT INTO triage_validations VALUES (NULL, ?, 'standard', 'analysis', 'dev', 'deep', 0)",
            (recent + i*100,),
        )
    # One correct classification
    conn.execute(
        "INSERT INTO triage_validations VALUES (NULL, ?, 'standard', 'analysis', 'dev', NULL, 1)",
        (recent,),
    )

    conn.commit()
    conn.close()

    return db_path


@pytest.mark.asyncio
async def test_get_recent_accuracy(tmp_validation_db, tmp_path):
    """Test accuracy retrieval."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )

    accuracy = improver.get_recent_accuracy(days=7)

    assert len(accuracy) > 0

    # nano/coding should be 100% correct
    nano_coding = [a for a in accuracy if a.tier == "nano" and a.domain == "coding"]
    assert len(nano_coding) == 1
    assert nano_coding[0].accuracy == 1.0

    # standard/analysis has more errors than correct
    std_analysis = [a for a in accuracy if a.tier == "standard" and a.domain == "analysis"]
    assert len(std_analysis) == 1
    assert std_analysis[0].accuracy < 1.0  # Some errors


def test_get_misclassifications(tmp_validation_db, tmp_path):
    """Test misclassification pattern detection."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )

    patterns = improver.get_misclassifications(days=7, min_occurrences=1)

    # Should detect the standard:analysis:dev pattern
    assert len(patterns) > 0

    # Find standard/analysis pattern
    pattern_key = None
    for key in patterns.keys():
        if "standard" in key and "analysis" in key:
            pattern_key = key
            break

    assert pattern_key is not None
    assert len(patterns[pattern_key]) > 0
    assert patterns[pattern_key][0]["predicted"] == "deep"


@pytest.mark.asyncio
async def test_generate_candidates(tmp_validation_db, tmp_path):
    """Test candidate generation."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )

    candidates = improver.generate_candidates(days=7, confidence_threshold=0.5)

    # Should have at least one candidate (the standard/analysis issue)
    assert len(candidates) > 0

    # Verify candidate structure
    for candidate in candidates:
        assert isinstance(candidate, MisclassificationPattern)
        assert candidate.pattern_id
        assert 0.0 <= candidate.confidence <= 1.0
        assert candidate.proposed_fix


@pytest.mark.asyncio
async def test_evaluate(tmp_validation_db, tmp_path):
    """Test full evaluation."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )

    proposal = await improver.evaluate(days=7)

    assert isinstance(proposal, ImprovementProposal)
    assert proposal.proposal_id
    assert proposal.generated_at
    assert proposal.evaluation_window_days == 7
    assert proposal.accuracy_summary  # Should have tier accuracy


@pytest.mark.asyncio
async def test_save_and_load_proposals(tmp_validation_db, tmp_path):
    """Test saving and loading proposals."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )

    proposal = await improver.evaluate(days=7)
    improver.save_proposal(proposal)

    # Verify file was created
    assert improver.history_path.exists()

    # Verify content
    loaded = improver.load_proposals()
    assert proposal.proposal_id in loaded
    assert loaded[proposal.proposal_id]["evaluation_window_days"] == 7


def test_proposal_serialization():
    """Test proposal serialization."""
    pattern = MisclassificationPattern(
        pattern_id="test_pattern",
        tier="standard",
        domain="coding",
        channel="dev",
        misclassifications=5,
        most_common_error="nano",
        confidence=0.85,
        proposed_fix="Route coding:dev to better provider",
    )

    proposal = ImprovementProposal(
        proposal_id="test_proposal",
        generated_at=datetime.now(timezone.utc).isoformat(),
        evaluation_window_days=7,
        high_impact_fixes=[pattern],
        accuracy_summary={"nano": 0.95, "standard": 0.80},
    )

    data = proposal.to_dict()
    assert data["proposal_id"] == "test_proposal"
    assert len(data["high_impact_fixes"]) == 1
    assert data["high_impact_fixes"][0]["pattern_id"] == "test_pattern"


@pytest.mark.asyncio
async def test_approve_and_reject_fixes(tmp_validation_db, tmp_path):
    """Test approval and rejection of fixes."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )
    
    # Generate a proposal
    proposal = await improver.evaluate(days=7)
    improver.save_proposal(proposal)
    
    if not proposal.high_impact_fixes:
        pytest.skip("No high-impact fixes in test data")
    
    # Approve a fix
    pattern_id = proposal.high_impact_fixes[0].pattern_id
    assert improver.approve_fix(proposal.proposal_id, pattern_id, "Looks good!")
    
    # Verify approval was recorded
    approved = improver.get_approved_fixes(proposal.proposal_id)
    assert len(approved) == 1
    assert approved[0]["pattern_id"] == pattern_id
    assert approved[0]["approvals"][0]["notes"] == "Looks good!"
    
    # Reject another fix if available
    if len(proposal.high_impact_fixes) > 1:
        pattern_id_2 = proposal.high_impact_fixes[1].pattern_id
        assert improver.reject_fix(proposal.proposal_id, pattern_id_2, "Not right now")
        
        rejected = improver.get_rejected_fixes(proposal.proposal_id)
        assert len(rejected) == 1
        assert rejected[0]["pattern_id"] == pattern_id_2


@pytest.mark.asyncio
async def test_approve_nonexistent_fix(tmp_validation_db, tmp_path):
    """Test that approving nonexistent fix returns False."""
    improver = SelfImprover(
        validation_db=tmp_validation_db,
        history_path=tmp_path / "history.json",
    )
    
    # Try to approve a nonexistent proposal
    result = improver.approve_fix("fake_proposal", "fake_pattern")
    assert result is False
