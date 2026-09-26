"""Domain Registry — persistent storage of operator's domain frequencies.

Tracks which work-type domains the operator engages with, ranked by
frequency. Used to understand operator's actual work distribution and
guide capability routing (swarm asks "who's best at X for THIS operator?").

Storage: SQLite at data/domain_registry.db
Schema: domains (domain, frequency, last_seen)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".local" / "nexus" / "domain_registry.db"


class DomainRegistry:
    """SQLite-backed domain frequency tracker."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS domains (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                domain          TEXT    NOT NULL UNIQUE,
                frequency       INTEGER NOT NULL DEFAULT 0,
                first_seen      REAL    NOT NULL,
                last_seen       REAL    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_domains_frequency
                ON domains(frequency DESC);
        """)
        conn.close()

    def record(self, domain: str) -> None:
        """Record an observation of a domain (increment frequency)."""
        now = time.time()
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute(
                "INSERT INTO domains (domain, frequency, first_seen, last_seen) "
                "VALUES (?, 1, ?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET "
                "frequency = frequency + 1, last_seen = ?",
                (domain, now, now, now),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"domain_registry: record failed: {e}")

    def get_distribution(self, limit: int = 20) -> List[Tuple[str, int]]:
        """Get top N domains by frequency.

        Returns: [(domain, frequency), ...] sorted by frequency descending.
        """
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT domain, frequency FROM domains "
                "ORDER BY frequency DESC LIMIT ?",
                (limit,),
            )
            results = [(r["domain"], r["frequency"]) for r in cur.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.error(f"domain_registry: get_distribution failed: {e}")
            return []

    def get_frequency(self, domain: str) -> int:
        """Get frequency count for one domain."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.execute(
                "SELECT frequency FROM domains WHERE domain = ?",
                (domain,),
            )
            row = cur.fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"domain_registry: get_frequency failed: {e}")
            return 0

    def all_domains(self) -> List[str]:
        """Get all registered domains."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.execute("SELECT domain FROM domains ORDER BY frequency DESC")
            results = [row[0] for row in cur.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.error(f"domain_registry: all_domains failed: {e}")
            return []

    def to_dict(self) -> Dict[str, int]:
        """Export as {domain: frequency} dict (for JSON export/analysis)."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.execute("SELECT domain, frequency FROM domains ORDER BY frequency DESC")
            result = {row[0]: row[1] for row in cur.fetchall()}
            conn.close()
            return result
        except Exception as e:
            logger.error(f"domain_registry: to_dict failed: {e}")
            return {}
