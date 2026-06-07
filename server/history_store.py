"""Translation history — SQLite-backed persistent storage.

Replaces localStorage for batch tasks. Supports:
- Per-user history (userid, hardcoded 'epubTranslator' for now)
- Date-based querying
- Duplicate detection by (userid, filename)
- Upsert: one record per user+filename
"""

import os
import sqlite3
import time
from datetime import datetime, timezone
from threading import Lock

DB_PATH = os.path.join(os.path.dirname(__file__), "translation_history.db")
_lock = Lock()

DEFAULT_USERID = "epubTranslator"


# ── Internal helpers ──────────────────────────────────────────────────

def _now_iso() -> str:
    """Today's date as YYYY-MM-DD (local time)."""
    return datetime.now().strftime("%Y-%m-%d")


def _now_ts() -> float:
    """Current Unix timestamp (seconds, not ms)."""
    return time.time()


def _connect() -> sqlite3.Connection:
    """Open a connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ────────────────────────────────────────────────────────────

def init_db():
    """Create tables and indexes if they don't exist. Idempotent."""
    with _lock, _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS translation_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                userid        TEXT NOT NULL DEFAULT 'epubTranslator',
                filename      TEXT NOT NULL,
                file_type     TEXT DEFAULT '',
                file_size     INTEGER DEFAULT 0,
                target_lang   TEXT DEFAULT 'zh-CN',
                bilingual     INTEGER DEFAULT 1,
                status        TEXT DEFAULT 'waiting',
                step          TEXT DEFAULT '',
                file_progress INTEGER DEFAULT 0,
                file_total    INTEGER DEFAULT 0,
                output        TEXT DEFAULT NULL,
                error         TEXT DEFAULT NULL,
                start_time    REAL DEFAULT 0,
                created_date  TEXT NOT NULL,
                created_at    REAL NOT NULL,
                updated_at    REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_hist_user_date
                ON translation_history(userid, created_date);
            CREATE INDEX IF NOT EXISTS idx_hist_user_file
                ON translation_history(userid, filename);
        """)


# ── Public API ────────────────────────────────────────────────────────

def check_done(userid: str, filename: str) -> dict | None:
    """Check if a file was already translated successfully.

    Returns dict with keys: date, output, target_lang — or None if not found.
    """
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT created_date, output, target_lang, bilingual "
            "FROM translation_history "
            "WHERE userid=? AND filename=? AND status='done' "
            "ORDER BY updated_at DESC LIMIT 1",
            (userid, filename)
        ).fetchone()
    if row:
        return {"date": row["created_date"], "output": row["output"],
                "target_lang": row["target_lang"], "bilingual": bool(row["bilingual"])}
    return None


def upsert_task(userid: str, filename: str, **fields):
    """Insert or update a translation record. Keys on (userid, filename).

    Recognised fields: file_type, file_size, target_lang, bilingual,
    status, step, file_progress, file_total, output, error, start_time.
    """
    now = _now_ts()
    today = _now_iso()

    allowed = {"file_type", "file_size", "target_lang", "bilingual",
               "status", "step", "file_progress", "file_total",
               "output", "error", "start_time"}
    values = {k: fields[k] for k in allowed if k in fields}
    values["userid"] = userid
    values["filename"] = filename
    values["updated_at"] = now

    with _lock, _connect() as conn:
        existing = conn.execute(
            "SELECT id, created_date, created_at FROM translation_history "
            "WHERE userid=? AND filename=?",
            (userid, filename)
        ).fetchone()

        if existing:
            # Update
            set_clause = ", ".join(f"{k}=?" for k in values)
            params = list(values.values()) + [userid, filename]
            conn.execute(
                f"UPDATE translation_history SET {set_clause} "
                "WHERE userid=? AND filename=?",
                params
            )
        else:
            # Insert
            values["created_date"] = today
            values["created_at"] = now
            cols = ", ".join(values.keys())
            placeholders = ", ".join("?" * len(values))
            conn.execute(
                f"INSERT INTO translation_history ({cols}) VALUES ({placeholders})",
                list(values.values())
            )


def query_tasks(userid: str, date: str = None) -> list[dict]:
    """Query tasks.

    Args:
        userid: User ID.
        date: If provided, only tasks from this date (YYYY-MM-DD).
              If None, returns today's tasks plus any unfinished from
              previous days (the default batch page view).

    Returns:
        List of task dicts.
    """
    with _lock, _connect() as conn:
        if date:
            rows = conn.execute(
                "SELECT * FROM translation_history "
                "WHERE userid=? AND created_date=? "
                "ORDER BY created_at DESC",
                (userid, date)
            ).fetchall()
        else:
            # Default: today's tasks + unfinished from any date
            today = _now_iso()
            rows = conn.execute(
                "SELECT * FROM translation_history "
                "WHERE userid=? AND ("
                "  created_date=? "
                "  OR status IN ('waiting','translating','loading','queued','stopped')"
                ") ORDER BY created_at DESC",
                (userid, today)
            ).fetchall()

    return [_row_to_dict(r) for r in rows]


def list_dates(userid: str) -> list[str]:
    """Return all distinct dates (YYYY-MM-DD) having records, newest first."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT created_date FROM translation_history "
            "WHERE userid=? ORDER BY created_date DESC",
            (userid,)
        ).fetchall()
    return [r["created_date"] for r in rows]


def search_tasks(userid: str, query: str) -> list[dict]:
    """Search tasks by filename (LIKE %query%)."""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM translation_history "
            "WHERE userid=? AND filename LIKE ? "
            "ORDER BY updated_at DESC LIMIT 50",
            (userid, f"%{query}%")
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_task(userid: str, filename: str):
    """Remove a single task record."""
    with _lock, _connect() as conn:
        conn.execute(
            "DELETE FROM translation_history WHERE userid=? AND filename=?",
            (userid, filename)
        )


def delete_all(userid: str):
    """Remove all records for a user (cleanup / reset)."""
    with _lock, _connect() as conn:
        conn.execute(
            "DELETE FROM translation_history WHERE userid=?",
            (userid,)
        )


# ── Helpers ───────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "filename": row["filename"],
        "file_type": row["file_type"],
        "file_size": row["file_size"],
        "target_lang": row["target_lang"],
        "bilingual": bool(row["bilingual"]),
        "status": row["status"],
        "step": row["step"],
        "file_progress": row["file_progress"],
        "file_total": row["file_total"],
        "output": row["output"],
        "error": row["error"],
        "start_time": row["start_time"],
        "created_date": row["created_date"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
