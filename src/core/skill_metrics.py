"""
Skill metrics collector — runtime instrumentation for deployed modules.

Writes to SQLite. Self-eval reads from the same DB to surface refinement candidates.
Zero-impact on the hot path: async writes, no blocking.

Ported from claude-brain. DB path is nexus-scoped (~/.local/nexus/).
"""
import sqlite3
import threading
import time
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.skill_metrics")

DEFAULT_DB_PATH = Path.home() / ".local" / "nexus" / "skill-metrics.db"


class SkillMetrics:
    """
    Thread-safe metrics collector backed by SQLite.

    Usage:
        metrics = SkillMetrics()
        metrics.record("debounce", "skip", channel="dev", user_id="u1")
        metrics.record("debounce", "pass", channel="dev", user_id="u1")
    """

    _instance: Optional["SkillMetrics"] = None
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
        self._init_db()

    def _init_db(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL    NOT NULL,
                skill    TEXT    NOT NULL,
                action   TEXT    NOT NULL,
                detail   TEXT,
                channel  TEXT,
                user_id  TEXT
            );

            CREATE TABLE IF NOT EXISTS counters (
                skill        TEXT NOT NULL,
                action       TEXT NOT NULL,
                count        INTEGER NOT NULL DEFAULT 0,
                last_updated REAL    NOT NULL,
                PRIMARY KEY (skill, action)
            );

            CREATE INDEX IF NOT EXISTS idx_events_skill_ts ON events(skill, timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
        """)
        conn.close()
        logger.info(f"Skill metrics DB: {self._db_path}")

    def record(
        self,
        skill: str,
        action: str,
        detail: Optional[str] = None,
        channel: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        """Record a skill event. Non-blocking — fires in background thread."""
        threading.Thread(
            target=self._write,
            args=(skill, action, detail, channel, user_id),
            daemon=True,
        ).start()

    def _write(
        self,
        skill: str,
        action: str,
        detail: Optional[str],
        channel: Optional[str],
        user_id: Optional[str],
    ):
        now = time.time()
        with self._write_lock:
            try:
                conn = sqlite3.connect(str(self._db_path))
                conn.execute(
                    "INSERT INTO events (timestamp, skill, action, detail, channel, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (now, skill, action, detail, channel, user_id),
                )
                conn.execute(
                    "INSERT INTO counters (skill, action, count, last_updated) "
                    "VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(skill, action) DO UPDATE SET "
                    "count = count + 1, last_updated = ?",
                    (skill, action, now, now),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Metrics write failed: {e}")

    def query_counters(self, skill: Optional[str] = None) -> list[dict]:
        """Read current counter state. Used by self-eval."""
        conn = sqlite3.connect(str(self._db_path))
        if skill:
            rows = conn.execute(
                "SELECT skill, action, count, last_updated FROM counters WHERE skill = ?",
                (skill,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT skill, action, count, last_updated FROM counters"
            ).fetchall()
        conn.close()
        return [
            {"skill": r[0], "action": r[1], "count": r[2], "last_updated": r[3]}
            for r in rows
        ]

    def query_events(
        self,
        skill: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Read recent events. Used by self-eval."""
        conn = sqlite3.connect(str(self._db_path))
        query = "SELECT timestamp, skill, action, detail, channel, user_id FROM events WHERE 1=1"
        params: list = []
        if skill:
            query += " AND skill = ?"
            params.append(skill)
        if since:
            query += " AND timestamp > ?"
            params.append(since)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [
            {
                "timestamp": r[0], "skill": r[1], "action": r[2],
                "detail": r[3], "channel": r[4], "user_id": r[5],
            }
            for r in rows
        ]

    def summary(self, days: int = 7) -> dict:
        """
        Summary report for self-eval consumption.
        Returns per-skill action counts for the last N days.
        """
        since = time.time() - (days * 86400)
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT skill, action, COUNT(*) FROM events "
            "WHERE timestamp > ? GROUP BY skill, action ORDER BY skill, action",
            (since,),
        ).fetchall()
        conn.close()

        result: dict = {}
        for skill, action, count in rows:
            if skill not in result:
                result[skill] = {}
            result[skill][action] = count
        return result
