"""Council session record — persistent per-run audit file for the role council.

Before this module, a council run left NOTHING behind: the Judge updated the
role-scoped EWMA in capability_map and the evidence was discarded. That blocked
every downstream consumer that needs longitudinal council history — contradiction
flagging, the weight feedback loop, and orchestrator drift detection (comparing
synthesis vs role consensus over time).

One JSON file per council run in $NEXUS_DATA_DIR/council_sessions/:

    council_session_<YYYYmmdd-HHMMSS>-<suffix>.json

Written in two phases:
  1. council_executor.run() writes the base record (roles, providers, grounded
     role responses, review-mode attribution, failures, synthesis prompt).
  2. The async Judge updates the same file with per-role verdicts and the
     EWMA before/after delta it applied to capability_map.

The `chairman_output` field is intentionally null in phase 1-2: chairman
synthesis happens downstream in the failover executor, which has no council
awareness today. Filling it is the documented seam for the next slice.

Retention: the directory is pruned to MAX_SESSIONS files on every write —
oldest deleted first. No unbounded growth.

Storage: $NEXUS_DATA_DIR/council_sessions/  (default: data/council_sessions/)

# NEXUS:PORTABLE — no operator paths beyond the data dir anchor.
"""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
SESSIONS_DIR = _DATA_DIR / "council_sessions"
MAX_SESSIONS = 200          # retention cap, oldest pruned first
_TEXT_CAP = 8000            # per-field cap so one verbose role can't bloat the file

SCHEMA_VERSION = 1


def _clip(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    return text if len(text) <= _TEXT_CAP else text[:_TEXT_CAP] + "…[clipped]"


class CouncilSession:
    """Recorder for one council run. Create in run(), hand to the Judge.

    All writes are atomic (tmp + os.replace) and every method is exception-safe:
    a recorder failure must never break the council path it observes.
    """

    def __init__(
        self,
        question: str,
        domain: str,
        complexity: float,
        review_mode: bool,
        sessions_dir: Optional[Path] = None,
    ):
        # sessions_dir is DI for tests (tmp_path); defaults to the module-level
        # NEXUS_DATA_DIR-derived location for real runs.
        self.sessions_dir = Path(sessions_dir) if sessions_dir is not None else SESSIONS_DIR
        ts = time.strftime("%Y%m%d-%H%M%S")
        self.session_id = f"council_session_{ts}-{uuid.uuid4().hex[:6]}"
        self.path = self.sessions_dir / f"{self.session_id}.json"
        self._record: Dict = {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "question": _clip(question),
            "domain": domain,
            "complexity": complexity,
            "review_mode": review_mode,
            "roles": {},                 # role -> provider
            "role_responses": {},        # role -> grounded text (post scope-clamp)
            "scope_violations": {},      # role -> stripped-line snippets
            "failed_providers": [],
            "finding_attribution": {},   # review mode: blinded label -> provider
            "synthesis_prompt": None,    # what the chairman received
            "chairman_output": None,     # seam: filled by a future adapter_base hook
            "judge": {},                 # role -> verdict + EWMA delta
        }

    # ── Phase 1: base record from council_executor ────────────────────────────

    def record_council(
        self,
        roles: Dict[str, str],
        role_responses: Dict[str, str],
        scope_violations: Dict[str, List[str]],
        failed_providers: List[str],
        synthesis_prompt: str,
        finding_attribution: Optional[Dict[str, str]] = None,
    ) -> None:
        try:
            self._record["roles"] = dict(roles)
            self._record["role_responses"] = {
                r: _clip(t) for r, t in role_responses.items()
            }
            self._record["scope_violations"] = {
                r: v for r, v in (scope_violations or {}).items() if v
            }
            self._record["failed_providers"] = list(failed_providers)
            self._record["synthesis_prompt"] = _clip(synthesis_prompt)
            if finding_attribution:
                self._record["finding_attribution"] = dict(finding_attribution)
            self._write()
        except Exception as e:  # never let the recorder break the council
            logger.warning(f"council_session: record_council failed: {e}")

    # ── Phase 1b: chairman's actual output (post-reply) ───────────────────────

    def record_chairman_output(self, text: str) -> None:
        try:
            self._record["chairman_output"] = _clip(text)
            self._write()
        except Exception as e:
            logger.warning(f"council_session: record_chairman_output failed: {e}")

    # ── Phase 2: judge verdicts (async, after synthesis) ──────────────────────

    def record_judge_verdict(
        self,
        role: str,
        provider: str,
        verdict: Dict,
        judge_model: Optional[str] = None,
        ewma_before: Optional[float] = None,
        ewma_after: Optional[float] = None,
    ) -> None:
        try:
            self._record["judge"][role] = {
                "provider": provider,
                "score": verdict.get("score"),
                "hallucinated": verdict.get("hallucinated"),
                "judge_model": judge_model,
                "ewma_before": ewma_before,
                "ewma_after": ewma_after,
                "graded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            self._write()
        except Exception as e:
            logger.warning(f"council_session: record_judge_verdict failed: {e}")

    # ── Internals ─────────────────────────────────────────────────────────────

    def _write(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._record, indent=2, ensure_ascii=False))
        os.replace(tmp, self.path)
        _prune(self.sessions_dir)


def _prune(sessions_dir: Path = SESSIONS_DIR, max_files: int = MAX_SESSIONS) -> None:
    """Keep the newest max_files session records; delete the oldest beyond it."""
    try:
        files = sorted(
            sessions_dir.glob("council_session_*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        for old in files[:-max_files] if len(files) > max_files else []:
            old.unlink(missing_ok=True)
    except Exception as e:
        logger.debug(f"council_session: prune skipped: {e}")


def list_sessions(limit: int = 50, sessions_dir: Path = SESSIONS_DIR) -> List[Path]:
    """Newest-first session record paths (for drift detection / reports)."""
    try:
        return sorted(
            sessions_dir.glob("council_session_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
    except Exception:
        return []
