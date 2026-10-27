"""Paid-access rows for Stripe checkout and Tailscale onboarding.

Stored in SQLite (`ACCESS_DB_PATH`, default `data/access.sqlite`). A token
lookup returns a row only after the checkout is paid or provisioned.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_ACTIVE = {"paid", "provisioned"}


def _path() -> Path:
    raw = os.getenv("ACCESS_DB_PATH", "data/access.sqlite")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entitlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            checkout_session_id TEXT UNIQUE,
            access_token TEXT UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            amount_cents INTEGER,
            currency TEXT,
            customer_id TEXT,
            payment_intent_id TEXT,
            tailscale_invite_url TEXT,
            tailscale_invite_id TEXT,
            updated_at TEXT
        )
        """
    )
    return conn


def _row(row: sqlite3.Row | None) -> dict | None:
    return {k: row[k] for k in row.keys()} if row else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_pending(*, email: str | None, checkout_session_id: str) -> dict:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO entitlements (email, checkout_session_id, status, updated_at)
            VALUES (?, ?, 'pending', ?)
            ON CONFLICT(checkout_session_id) DO UPDATE SET
                email = COALESCE(excluded.email, entitlements.email),
                updated_at = excluded.updated_at
            """,
            (email, checkout_session_id, _now()),
        )
        conn.commit()
        found = conn.execute(
            "SELECT * FROM entitlements WHERE checkout_session_id = ?",
            (checkout_session_id,),
        ).fetchone()
        return _row(found) or {}
    finally:
        conn.close()


def get_by_session(session_id: str | None) -> dict | None:
    if not session_id:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM entitlements WHERE checkout_session_id = ?",
            (session_id,),
        ).fetchone()
        return _row(row)
    finally:
        conn.close()


def get_by_token(token: str | None) -> dict | None:
    if not token:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM entitlements WHERE access_token = ?",
            (token,),
        ).fetchone()
        data = _row(row)
        if not data or data.get("status") not in _ACTIVE:
            return None
        return data
    finally:
        conn.close()


def mark_paid(
    *,
    checkout_session_id: str,
    email: str | None = None,
    customer_id: str | None = None,
    payment_intent_id: str | None = None,
    amount_cents: int | None = None,
    currency: str | None = None,
) -> dict:
    conn = _conn()
    try:
        existing = conn.execute(
            "SELECT access_token FROM entitlements WHERE checkout_session_id = ?",
            (checkout_session_id,),
        ).fetchone()
        token = (existing["access_token"] if existing and existing["access_token"] else secrets.token_urlsafe(24))
        conn.execute(
            """
            INSERT INTO entitlements (
                email, checkout_session_id, access_token, status,
                amount_cents, currency, customer_id, payment_intent_id, updated_at
            ) VALUES (?, ?, ?, 'paid', ?, ?, ?, ?, ?)
            ON CONFLICT(checkout_session_id) DO UPDATE SET
                email = COALESCE(excluded.email, entitlements.email),
                access_token = COALESCE(entitlements.access_token, excluded.access_token),
                status = 'paid',
                amount_cents = COALESCE(excluded.amount_cents, entitlements.amount_cents),
                currency = COALESCE(excluded.currency, entitlements.currency),
                customer_id = COALESCE(excluded.customer_id, entitlements.customer_id),
                payment_intent_id = COALESCE(excluded.payment_intent_id, entitlements.payment_intent_id),
                updated_at = excluded.updated_at
            """,
            (
                email,
                checkout_session_id,
                token,
                amount_cents,
                currency,
                customer_id,
                payment_intent_id,
                _now(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM entitlements WHERE checkout_session_id = ?",
            (checkout_session_id,),
        ).fetchone()
        return _row(row) or {}
    finally:
        conn.close()


def mark_provisioned(
    *,
    checkout_session_id: str,
    invite_url: str | None,
    invite_id: str | None,
) -> dict:
    conn = _conn()
    try:
        conn.execute(
            """
            UPDATE entitlements
            SET status = 'provisioned',
                tailscale_invite_url = ?,
                tailscale_invite_id = ?,
                updated_at = ?
            WHERE checkout_session_id = ?
            """,
            (invite_url, invite_id, _now(), checkout_session_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM entitlements WHERE checkout_session_id = ?",
            (checkout_session_id,),
        ).fetchone()
        return _row(row) or {}
    finally:
        conn.close()
