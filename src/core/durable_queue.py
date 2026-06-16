"""Durable reply queue using SQLite.

Ensures outbound messages survive adapter crashes — enqueue before posting,
mark delivered after. On restart, any pending entries are drained first.

DB location: $NEXUS_DATA_DIR/reply_queue.db  (default: data/reply_queue.db)
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
DB_PATH = _DATA_DIR / "reply_queue.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reply_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT NOT NULL,
            platform    TEXT NOT NULL,
            channel     TEXT NOT NULL,
            thread_id   TEXT,
            text        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            delivered_at TEXT,
            status      TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    conn.close()


def enqueue(
    task_id: str,
    platform: str,
    channel: str,
    text: str,
    thread_id: Optional[str] = None,
) -> int:
    """Enqueue a reply for delivery. Returns queue entry ID."""
    _init_db()
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO reply_queue
           (task_id, platform, channel, thread_id, text, created_at, status)
           VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
        (task_id, platform, channel, thread_id, text,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    logger.info(f"Enqueued reply {row_id} for task {task_id}")
    return row_id


def dequeue_pending() -> list[dict]:
    """Return all pending entries, oldest first."""
    _init_db()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM reply_queue WHERE status = 'pending' ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_delivered(queue_id: int) -> None:
    """Mark a queue entry as delivered."""
    _init_db()
    conn = _get_conn()
    conn.execute(
        "UPDATE reply_queue SET status = 'delivered', delivered_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), queue_id),
    )
    conn.commit()
    conn.close()
    logger.debug(f"Marked queue entry {queue_id} as delivered")


def clear_old_delivered(hours: int = 24) -> None:
    """Delete delivered entries older than `hours`."""
    _init_db()
    conn = _get_conn()
    cur = conn.execute(
        """DELETE FROM reply_queue
           WHERE status = 'delivered'
           AND datetime(delivered_at) < datetime('now', '-' || ? || ' hours')""",
        (hours,),
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        logger.info(f"Cleared {deleted} old delivered entries from durable queue")
