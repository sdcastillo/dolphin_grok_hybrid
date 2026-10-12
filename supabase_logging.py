"""Supabase-backed logging for visitors and chat interactions."""

import logging
from datetime import datetime

from direct_db import execute_one, execute_write
from supabase_client import supabase

logger = logging.getLogger(__name__)


def _insert(table: str, row: dict) -> int | None:
    try:
        result = supabase.table(table).insert(row).execute()
        if result.data:
            return result.data[0].get("id")
        return None
    except Exception as exc:
        logger.warning("Supabase insert into %s failed: %s", table, exc)
        try:
            if table == "visitors":
                execute_write(
                    "INSERT INTO visitors (ip_address, user_agent, path, country, visited_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        row.get("ip_address"),
                        row.get("user_agent"),
                        row.get("path"),
                        row.get("country"),
                        row.get("visited_at") or datetime.utcnow(),
                    ),
                )
                return None
            elif table == "chat_logs":
                inserted = execute_one(
                    """
                    INSERT INTO chat_logs (
                        ip_address, user_agent, rating, prompt, response, model,
                        status, error_message, duration_ms, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        row.get("ip_address"),
                        row.get("user_agent"),
                        row.get("rating"),
                        row.get("prompt"),
                        row.get("response"),
                        row.get("model"),
                        row.get("status"),
                        row.get("error_message"),
                        row.get("duration_ms"),
                        row.get("created_at") or datetime.utcnow(),
                    ),
                )
                return inserted["id"] if inserted else None
            elif table == "tts_audio_logs":
                inserted = execute_one(
                    """
                    INSERT INTO tts_audio_logs (
                        storage_path, public_url, text_prompt, format, effects_enabled,
                        sample_rate, file_size_bytes, duration_ms, ip_address, user_agent,
                        chat_log_id, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        row.get("storage_path"),
                        row.get("public_url"),
                        row.get("text_prompt"),
                        row.get("format"),
                        row.get("effects_enabled"),
                        row.get("sample_rate"),
                        row.get("file_size_bytes"),
                        row.get("duration_ms"),
                        row.get("ip_address"),
                        row.get("user_agent"),
                        row.get("chat_log_id"),
                        row.get("created_at") or datetime.utcnow(),
                    ),
                )
                return inserted["id"] if inserted else None
        except Exception as fallback_exc:
            # Logging failures (supabase or postgres) must never break chat or main flows
            logger.warning("Postgres fallback insert into %s failed: %s", table, fallback_exc)
    return None


def log_visitor(
    ip_address: str,
    user_agent: str,
    path: str = "/",
    country: str | None = None,
) -> None:
    _insert(
        "visitors",
        {
            "ip_address": ip_address,
            "user_agent": user_agent,
            "path": path,
            "country": country,
            "visited_at": datetime.utcnow().isoformat(),
        },
    )


def log_chat(
    *,
    ip_address: str,
    user_agent: str,
    rating: str,
    prompt: str,
    model: str,
    response: str | None = None,
    status: str = "success",
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> int | None:
    return _insert(
        "chat_logs",
        {
            "ip_address": ip_address,
            "user_agent": user_agent,
            "rating": rating,
            "prompt": prompt,
            "response": response,
            "model": model,
            "status": status,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "created_at": datetime.utcnow().isoformat(),
        },
    )