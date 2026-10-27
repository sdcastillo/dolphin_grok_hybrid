"""Postgres when DATABASE_URL is a postgres URL, otherwise a local SQLite file.

Callers write SQL with `%s` placeholders (psycopg style). SQLite queries are
rewritten to `?`, and ILIKE becomes LIKE.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS visitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT,
    user_agent TEXT,
    path TEXT,
    country TEXT,
    visited_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip_address TEXT,
    user_agent TEXT,
    rating TEXT,
    prompt TEXT,
    response TEXT,
    model TEXT,
    status TEXT,
    error_message TEXT,
    duration_ms INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS tts_audio_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    storage_path TEXT,
    public_url TEXT,
    text_prompt TEXT,
    format TEXT,
    effects_enabled INTEGER,
    sample_rate INTEGER,
    file_size_bytes INTEGER,
    duration_ms INTEGER,
    ip_address TEXT,
    user_agent TEXT,
    chat_log_id INTEGER,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS saam (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    situation TEXT NOT NULL,
    task TEXT,
    action TEXT,
    result TEXT,
    category TEXT,
    diagnosis TEXT,
    source TEXT,
    notes TEXT,
    diagnosis_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS diagnosis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis TEXT,
    source TEXT,
    answer TEXT,
    evidence TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS news_trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    tweet_volume INTEGER,
    fetched_at TEXT,
    summary TEXT,
    link TEXT,
    published TEXT,
    source TEXT NOT NULL DEFAULT 'rss',
    UNIQUE(topic, source)
);
"""


def _postgres_url() -> str | None:
    raw = (os.getenv("DATABASE_URL") or "").strip()
    if raw.startswith(("postgres://", "postgresql://")):
        return raw
    return None


def _sqlite_path() -> Path:
    raw = (os.getenv("DATABASE_URL") or "data/jarvis.sqlite").strip()
    for prefix in ("sqlite:///", "sqlite://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _adapt_sqlite(sql: str) -> str:
    sql = re.sub(r"\bILIKE\b", "LIKE", sql, flags=re.IGNORECASE)
    return sql.replace("%s", "?")


def _connect():
    url = _postgres_url()
    if url:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(url, row_factory=dict_row), "postgres"
    conn = sqlite3.connect(_sqlite_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn, "sqlite"


def _rows(cursor) -> list[dict[str, Any]]:
    fetched = cursor.fetchall() if cursor.description else []
    out = []
    for row in fetched or []:
        if isinstance(row, sqlite3.Row):
            out.append({k: row[k] for k in row.keys()})
        elif isinstance(row, dict):
            out.append(row)
        else:
            out.append(dict(row))
    return out


def execute(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn, kind = _connect()
    try:
        sql = query if kind == "postgres" else _adapt_sqlite(query)
        cur = conn.execute(sql, params)
        rows = _rows(cur)
        conn.commit()
        return rows
    finally:
        conn.close()


def execute_one(query: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = execute(query, params)
    return rows[0] if rows else None


def execute_write(query: str, params: tuple = ()) -> int:
    conn, kind = _connect()
    try:
        sql = query if kind == "postgres" else _adapt_sqlite(query)
        cur = conn.execute(sql, params)
        conn.commit()
        if kind == "sqlite":
            return int(cur.lastrowid or 0)
        return int(getattr(cur, "rowcount", 0) or 0)
    finally:
        conn.close()
