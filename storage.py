"""
SQLite-backed conversation history, keyed by customer phone number.

Using SQLite (rather than in-memory) means history survives app restarts
and is shared correctly across multiple worker processes (e.g. gunicorn
with several workers all pointed at the same DATABASE_PATH file).
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from config import config

# SQLite connections aren't shared safely across threads; keep one
# connection per thread and serialize writes with a lock.
_local = threading.local()
_write_lock = threading.Lock()


def _get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


@contextmanager
def _cursor():
    conn = _get_connection()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    finally:
        cur.close()


def init_db() -> None:
    with _write_lock, _cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone_number, id)"
        )


def add_message(phone_number: str, role: str, content: str) -> None:
    with _write_lock, _cursor() as cur:
        cur.execute(
            "INSERT INTO messages (phone_number, role, content, created_at) VALUES (?, ?, ?, ?)",
            (phone_number, role, content, datetime.now(timezone.utc).isoformat()),
        )


def get_recent_history(phone_number: str, limit: int = 12) -> list[dict]:
    """Return the last `limit` messages for this phone number, oldest first."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT role, content FROM (
                SELECT role, content, id FROM messages
                WHERE phone_number = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (phone_number, limit),
        )
        return [{"role": row["role"], "content": row["content"]} for row in cur.fetchall()]


def get_full_transcript(phone_number: str) -> list[dict]:
    """Return the complete conversation history for this phone number, oldest first.

    Used for the transcript appended to the staff lead email — unlike
    get_recent_history, this is not trimmed.
    """
    with _cursor() as cur:
        cur.execute(
            "SELECT role, content FROM messages WHERE phone_number = ? ORDER BY id ASC",
            (phone_number,),
        )
        return [{"role": row["role"], "content": row["content"]} for row in cur.fetchall()]
