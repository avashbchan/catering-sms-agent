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
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS order_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_summaries_phone ON order_summaries(phone_number, id)"
        )


def add_message(phone_number: str, role: str, content: str) -> None:
    with _write_lock, _cursor() as cur:
        cur.execute(
            "INSERT INTO messages (phone_number, role, content, created_at) VALUES (?, ?, ?, ?)",
            (phone_number, role, content, datetime.now(timezone.utc).isoformat()),
        )


def get_recent_history(phone_number: str, limit: int = 12) -> list[dict]:
    """Return the last `limit` messages for this phone number, oldest first.

    This is the LLM's per-turn conversational context and is intentionally
    lead-boundary-agnostic - it just trims to the most recent messages. The
    lead email / order extraction use get_transcript_since_last_lead instead,
    which scopes to the current lead (see below).
    """
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


def add_order_summary(phone_number: str, summary_json: str) -> None:
    """Log the raw extracted OrderSummary for a lead, for later debugging if a summary looks wrong."""
    with _write_lock, _cursor() as cur:
        cur.execute(
            "INSERT INTO order_summaries (phone_number, summary_json, created_at) VALUES (?, ?, ?)",
            (phone_number, summary_json, datetime.now(timezone.utc).isoformat()),
        )


def get_full_transcript(phone_number: str) -> list[dict]:
    """Return the complete conversation history for this phone number, oldest
    first, each item as {"role", "content", "created_at"}.

    This is the unscoped "entire history" query. The staff lead email / order
    extraction use get_transcript_since_last_lead instead (scoped to the current
    lead); this remains available if anything ever needs the full history.
    """
    with _cursor() as cur:
        cur.execute(
            "SELECT role, content, created_at FROM messages WHERE phone_number = ? ORDER BY id ASC",
            (phone_number,),
        )
        return [
            {"role": row["role"], "content": row["content"], "created_at": row["created_at"]}
            for row in cur.fetchall()
        ]


def get_transcript_since_last_lead(phone_number: str) -> list[dict]:
    """Messages since this phone number's last submitted lead, oldest first,
    each item as {"role", "content", "created_at"}.

    Falls back to the full history if no prior lead exists (nothing to exclude).
    Used for the lead email / order-summary extraction so a repeat customer's
    old conversation doesn't bleed into a new lead - unlike get_recent_history
    (the LLM's per-turn context), which intentionally ignores lead boundaries.

    The boundary is the newest order_summaries.created_at for this number. Both
    that and messages.created_at are ISO-8601 UTC strings from the same
    datetime.now(timezone.utc).isoformat() pattern, so a plain string ">"
    comparison orders correctly - no schema change or migration needed, and the
    existing idx_order_summaries_phone / idx_messages_phone indexes cover both
    queries. (created_at is also returned so the transcript PDF can show
    per-message timestamps.)
    """
    with _cursor() as cur:
        cur.execute(
            "SELECT created_at FROM order_summaries WHERE phone_number = ? ORDER BY id DESC LIMIT 1",
            (phone_number,),
        )
        last_lead = cur.fetchone()

        if last_lead is None:
            cur.execute(
                "SELECT role, content, created_at FROM messages WHERE phone_number = ? ORDER BY id ASC",
                (phone_number,),
            )
        else:
            cur.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE phone_number = ? AND created_at > ? ORDER BY id ASC",
                (phone_number, last_lead["created_at"]),
            )
        return [
            {"role": row["role"], "content": row["content"], "created_at": row["created_at"]}
            for row in cur.fetchall()
        ]
