"""Database connection and initialization for SQLite.

Usage:
    from database import get_connection, init_db

    init_db()
    with get_connection() as conn:
        conn.execute("SELECT 1")
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Resolve database path
# Supports:
#   - Absolute or relative path
#   - sqlite:/// prefix (common DATABASE_URL style)
DEFAULT_DB_PATH = "data/db.sqlite"


def _resolve_db_path() -> str:
    raw = os.getenv("DATABASE_URL", DEFAULT_DB_PATH)

    if raw.startswith("sqlite:///"):
        raw = raw[len("sqlite:///") :]
    elif raw.startswith("sqlite://"):
        raw = raw[len("sqlite://") :]

    # Make absolute if relative
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


DB_PATH = _resolve_db_path()


def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with row factory set to dict-like rows."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys and WAL for better concurrency
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def db_session():
    """Context manager that yields a connection and commits/rollbacks automatically."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema (idempotent)."""
    schema = """
    CREATE TABLE IF NOT EXISTS migrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Example tables - replace or extend as needed for your app
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        status TEXT DEFAULT 'active' CHECK(status IN ('active', 'archived', 'deleted')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id);
    CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
    """

    with db_session() as conn:
        conn.executescript(schema)

    print(f"Database initialized at: {DB_PATH}")


def execute(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Convenience helper for SELECT queries."""
    with get_connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


def execute_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
    """Convenience helper for SELECT ... LIMIT 1."""
    with get_connection() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def execute_write(query: str, params: tuple = ()) -> int:
    """Run INSERT/UPDATE/DELETE and return lastrowid or rowcount."""
    with db_session() as conn:
        cur = conn.execute(query, params)
        return cur.lastrowid or conn.total_changes


if __name__ == "__main__":
    init_db()
    print("\nConnection test:")
    rows = execute("SELECT name FROM sqlite_master WHERE type='table';")
    for row in rows:
        print(f"  - {row['name']}")
    print("\nDatabase connection successful!")
