"""
Triage validation — records classification decisions and outcome signals.

Every auto-triage decision is stored. Operator overrides and quick resets
write outcome signals. Self-eval reads this DB to surface accuracy trends,
misclassification patterns, and per-channel routing candidates.

Ported from claude-brain (mattermost-daemon/src/triage_validator.py).
Generalized: tier names (nano/standard/deep/apex) instead of model names.
DB path is configurable so operators can place it alongside other data.

Extensions (PW-8 convergence):
- Domain/stakes/complexity tracking per decision
- Turn facts: provider, model, failover_hops, council, error
- Re-ask detection via note_incoming (negation or >60% token overlap)
- Apex tier support
- Legacy DB migration (archive pre-2026-06 schemas)
"""

import hashlib
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.triage_validator")

DEFAULT_DB_PATH = Path.home() / ".local" / "nexus" / "triage-validation.db"

OUTCOME_UNKNOWN = None
OUTCOME_CORRECT = "correct"
OUTCOME_UNDER = "under"
OUTCOME_OVER = "over"
OUTCOME_DISSATISFIED = "dissatisfied"

# !new/!reset within this window after a response = dissatisfaction signal
SHORT_RESET_WINDOW = 45  # seconds
# a follow-up inside this window is eligible to count as a re-ask
REASK_WINDOW = 300  # seconds

# Higher rank = more capable tier
_TIER_RANK = {"nano": 0, "standard": 1, "deep": 2, "apex": 3}

# model-lock commands → the tier they imply (for override signals)
MODEL_TO_TIER = {"haiku": "nano", "sonnet": "standard", "opus": "deep"}

_NEGATION_OPENERS = re.compile(
    r"^\s*(no[,.\s]|nope|wrong|that'?s (not|wrong)|not what|incorrect|"
    r"try again|still (wrong|not)|that didn'?t)", re.IGNORECASE)


def _rank(tier: str) -> int:
    return _TIER_RANK.get((tier or "").lower(), 1)


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()[:400]))


class TriageValidator:
    """
    Thread-safe triage decision recorder backed by SQLite.

    Singleton — one instance per process regardless of how many adapters are running.

    Usage:
        v = TriageValidator()

        decision_id = v.record_decision(
            channel="dev-channel",
            message_hash=v.hash_message(raw_message),
            classified_tier=triage.tier,
            classified_effort=triage.effort,
        )
        v.record_response(decision_id, response_length=len(text), channel="dev-channel")

        # When operator issues !deep / !standard / !nano while NOT already locked:
        v.record_override(channel, from_tier="standard", to_tier="deep")

        # When operator issues !new / !reset:
        v.record_reset(channel)

        # When operator returns to !auto:
        v.record_auto_released(channel)
    """

    _instance: Optional["TriageValidator"] = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Optional[Path] = None):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: Optional[Path] = None):
        if self._initialized:
            return
        self._initialized = True
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._write_lock = threading.Lock()
        self._last_decision: dict[str, int] = {}
        self._last_decision_tier: dict[str, str] = {}
        self._last_response_ts: dict[str, float] = {}
        self._last_msg: dict[str, tuple[float, set]] = {}  # channel → (ts, tokens)
        self._init_db()

    def _init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # Legacy migration: the pre-2026-06 daemon wrote model-name columns
        # (classified_model). Archive that file untouched and start fresh —
        # the old rows describe a retired routing scheme.
        if self._db_path.exists():
            try:
                conn = sqlite3.connect(str(self._db_path))
                cols = [r[1] for r in conn.execute("PRAGMA table_info(decisions)")]
                conn.close()
                if cols and "classified_tier" not in cols:
                    legacy = self._db_path.with_suffix(
                        f".legacy-{time.strftime('%Y%m%d')}.db")
                    self._db_path.rename(legacy)
                    logger.info(f"triage_validator: legacy DB archived → {legacy}")
            except Exception as e:
                logger.warning(f"triage_validator: legacy check failed: {e}")
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS decisions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp          REAL    NOT NULL,
                platform           TEXT,
                channel            TEXT    NOT NULL,
                user_id            TEXT,
                message_hash       TEXT    NOT NULL,
                classified_tier    TEXT    NOT NULL,
                classified_effort  TEXT    NOT NULL,
                domain             TEXT,
                stakes             REAL,
                complexity         REAL,
                provider           TEXT,
                model              TEXT,
                failover_hops      INTEGER,
                council            INTEGER,
                error              INTEGER,
                response_length    INTEGER,
                response_ms        REAL,
                outcome            TEXT
            );

            CREATE TABLE IF NOT EXISTS outcome_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   REAL    NOT NULL,
                channel     TEXT    NOT NULL,
                signal_type TEXT    NOT NULL,
                from_tier   TEXT,
                to_tier     TEXT,
                decision_id INTEGER REFERENCES decisions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_decisions_channel_ts
                ON decisions(channel, timestamp);
            CREATE INDEX IF NOT EXISTS idx_decisions_outcome
                ON decisions(outcome);
            CREATE INDEX IF NOT EXISTS idx_signals_ts
                ON outcome_signals(timestamp);
        """)
        conn.close()
        logger.info(f"Triage validation DB: {self._db_path}")

    # ── Recording ─────────────────────────────────────────────────────────

    def record_decision(
        self,
        channel: str,
        message_hash: str,
        classified_tier: str,
        classified_effort: str,
        user_id: Optional[str] = None,
        platform: Optional[str] = None,
        domain: Optional[str] = None,
        stakes: Optional[float] = None,
        complexity: Optional[float] = None,
    ) -> int:
        """Record an auto-triage classification. Returns the decision ID."""
        now = time.time()
        with self._write_lock:
            try:
                conn = sqlite3.connect(str(self._db_path))
                cur = conn.execute(
                    "INSERT INTO decisions (timestamp, platform, channel, user_id, "
                    "message_hash, classified_tier, classified_effort, domain, "
                    "stakes, complexity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (now, platform, channel, user_id, message_hash,
                     classified_tier, classified_effort, domain, stakes, complexity),
                )
                decision_id = cur.lastrowid
                conn.commit()
                conn.close()
                self._last_decision[channel] = decision_id
                self._last_decision_tier[channel] = classified_tier
                return decision_id
            except Exception as e:
                logger.error(f"Triage validator write failed: {e}")
                return -1

    def record_response(
        self,
        decision_id: int,
        response_length: int,
        channel: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        failover_hops: Optional[int] = None,
        council: Optional[bool] = None,
        error: Optional[bool] = None,
    ):
        """Attach turn facts after the reply lands. response_ms is computed
        from the decision's own timestamp (classify→deliver wall time)."""
        now = time.time()
        if channel:
            self._last_response_ts[channel] = now
        if decision_id is None or decision_id < 0:
            return

        def _write():
            with self._write_lock:
                try:
                    conn = sqlite3.connect(str(self._db_path))
                    conn.execute(
                        "UPDATE decisions SET response_length = ?, "
                        "provider = ?, model = ?, failover_hops = ?, "
                        "council = ?, error = ? WHERE id = ?",
                        (response_length, provider, model, failover_hops,
                         council, error, decision_id),
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Triage validator response update failed: {e}")

        threading.Thread(target=_write, daemon=True).start()

    def record_override(self, channel: str, to_tier: str,
                        from_tier: Optional[str] = None):
        """A tier/model lock issued while NOT already locked — the operator is
        correcting auto-triage. from_tier defaults to the channel's last
        auto-classified tier."""
        from_tier = from_tier or self._last_decision_tier.get(channel)
        if not from_tier:
            return
        from_rank, to_rank = _rank(from_tier), _rank(to_tier)
        if from_rank == to_rank:
            return
        signal_type = "override_up" if to_rank > from_rank else "override_down"
        outcome = OUTCOME_UNDER if to_rank > from_rank else OUTCOME_OVER
        decision_id = self._last_decision.get(channel)

        def _write():
            now = time.time()
            with self._write_lock:
                try:
                    conn = sqlite3.connect(str(self._db_path))
                    conn.execute(
                        "INSERT INTO outcome_signals "
                        "(timestamp, channel, signal_type, from_tier, to_tier, decision_id) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (now, channel, signal_type, from_tier, to_tier, decision_id),
                    )
                    if decision_id and decision_id > 0:
                        conn.execute(
                            "UPDATE decisions SET outcome = ? WHERE id = ? AND outcome IS NULL",
                            (outcome, decision_id),
                        )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Triage validator override write failed: {e}")

        threading.Thread(target=_write, daemon=True).start()

    def record_reset(self, channel: str):
        """!new/!reset — dissatisfaction only when within SHORT_RESET_WINDOW
        of the last response (a quick wipe right after an answer)."""
        last_response = self._last_response_ts.get(channel)
        if last_response is None or (time.time() - last_response) > SHORT_RESET_WINDOW:
            return
        decision_id = self._last_decision.get(channel)
        self._signal(channel, "quick_reset", decision_id, OUTCOME_DISSATISFIED)

    def record_auto_released(self, channel: str):
        """!auto after an override — weak positive close on the last decision."""
        decision_id = self._last_decision.get(channel)
        if not decision_id or decision_id < 0:
            return

        def _write():
            with self._write_lock:
                try:
                    conn = sqlite3.connect(str(self._db_path))
                    conn.execute(
                        "UPDATE decisions SET outcome = ? WHERE id = ? "
                        "AND outcome IS NULL", (OUTCOME_CORRECT, decision_id))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Triage validator auto-release write failed: {e}")

        threading.Thread(target=_write, daemon=True).start()

    def note_incoming(self, channel: str, text: str):
        """Call on EVERY non-command message BEFORE recording its decision.
        Detects re-asks: a follow-up within REASK_WINDOW that opens with a
        negation or is a near-duplicate of the previous message → the
        PREVIOUS decision is marked dissatisfied."""
        now = time.time()
        prev = self._last_msg.get(channel)
        cur_tokens = _tokens(text)
        self._last_msg[channel] = (now, cur_tokens)
        if prev is None or not text:
            return
        prev_ts, prev_tokens = prev
        if (now - prev_ts) > REASK_WINDOW:
            return
        decision_id = self._last_decision.get(channel)
        if not decision_id or decision_id < 0:
            return
        negation = bool(_NEGATION_OPENERS.match(text))
        overlap = 0.0
        if prev_tokens and cur_tokens:
            overlap = len(prev_tokens & cur_tokens) / len(prev_tokens | cur_tokens)
        if negation or overlap > 0.6:
            self._signal(channel, "re_ask", decision_id, OUTCOME_DISSATISFIED)

    def _signal(self, channel: str, signal_type: str,
                decision_id: Optional[int], outcome: Optional[str]):
        def _write():
            now = time.time()
            with self._write_lock:
                try:
                    conn = sqlite3.connect(str(self._db_path))
                    conn.execute(
                        "INSERT INTO outcome_signals (timestamp, channel, "
                        "signal_type, decision_id) VALUES (?, ?, ?, ?)",
                        (now, channel, signal_type, decision_id))
                    if outcome and decision_id and decision_id > 0:
                        conn.execute(
                            "UPDATE decisions SET outcome = ? WHERE id = ? "
                            "AND outcome IS NULL", (outcome, decision_id))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Triage validator: {signal_type} write failed: {e}")

        threading.Thread(target=_write, daemon=True).start()

    # ── Queries (for self-eval) ────────────────────────────────────────────

    @staticmethod
    def hash_message(message: str) -> str:
        """SHA-256 hash of first 300 chars — correlates without storing raw messages."""
        return hashlib.sha256(message[:300].encode()).hexdigest()[:16]

    def summary(self, days: int = 14) -> dict:
        """
        Accuracy summary for self-eval. Returns counts by outcome, per-tier
        misclassification breakdown, and channels with high override rates.
        """
        since = time.time() - (days * 86400)
        conn = sqlite3.connect(str(self._db_path))

        total = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE timestamp > ?", (since,)
        ).fetchone()[0]

        by_outcome = conn.execute(
            "SELECT outcome, COUNT(*) FROM decisions WHERE timestamp > ? GROUP BY outcome",
            (since,),
        ).fetchall()

        overrides = conn.execute(
            "SELECT signal_type, COUNT(*) FROM outcome_signals "
            "WHERE timestamp > ? GROUP BY signal_type",
            (since,),
        ).fetchall()

        hot_channels = conn.execute(
            "SELECT channel, COUNT(*) as n FROM outcome_signals "
            "WHERE timestamp > ? GROUP BY channel ORDER BY n DESC LIMIT 10",
            (since,),
        ).fetchall()

        tier_misses = conn.execute(
            "SELECT classified_tier, COUNT(*) FROM decisions "
            "WHERE timestamp > ? AND outcome IN ('under', 'over') "
            "GROUP BY classified_tier ORDER BY COUNT(*) DESC",
            (since,),
        ).fetchall()

        conn.close()

        return {
            "days": days,
            "total_decisions": total,
            "by_outcome": {row[0] or "unknown": row[1] for row in by_outcome},
            "override_signals": {row[0]: row[1] for row in overrides},
            "hot_channels": [{"channel": r[0], "signals": r[1]} for r in hot_channels],
            "tier_misses": [{"tier": r[0], "misses": r[1]} for r in tier_misses],
        }
