"""Upload TTS MP3 files to Supabase Storage and log metadata in Postgres."""

from __future__ import annotations

import os
import uuid
import logging
from datetime import datetime, timezone

from direct_db import execute_one, execute_write
from supabase_client import SUPABASE_URL, supabase

logger = logging.getLogger(__name__)

TTS_AUDIO_BUCKET = os.getenv("TTS_AUDIO_BUCKET", "tts-audio")
TTS_AUDIO_SAVE_ENABLED = os.getenv("TTS_AUDIO_SAVE_ENABLED", "true").lower() in {
    "1", "true", "yes", "on",
}


def _public_url(storage_path: str) -> str:
    base = SUPABASE_URL.rstrip("/")
    return f"{base}/storage/v1/object/public/{TTS_AUDIO_BUCKET}/{storage_path}"


def _storage_path() -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y/%m/%d}/{uuid.uuid4().hex}.mp3"


def _insert_row(row: dict) -> int | None:
    try:
        result = supabase.table("tts_audio_logs").insert(row).execute()
        if result.data:
            return result.data[0].get("id")
        logger.warning("tts_audio_logs insert returned no row")
    except Exception as exc:
        logger.warning("Supabase tts_audio_logs insert failed: %s", exc)

    try:
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
                row.get("created_at") or datetime.now(timezone.utc),
            ),
        )
        return inserted["id"] if inserted else None
    except Exception as exc:
        # Logging failures must never break main chat/TTS flows
        logger.warning("Postgres fallback tts_audio_logs insert failed: %s", exc)
        return None


def save_tts_mp3(
    mp3_bytes: bytes,
    *,
    text_prompt: str,
    sample_rate: int,
    effects_enabled: bool = True,
    duration_ms: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    chat_log_id: int | None = None,
) -> dict | None:
    """Upload MP3 to Supabase Storage and record metadata. Returns log row summary."""
    if not TTS_AUDIO_SAVE_ENABLED or not mp3_bytes:
        return None

    storage_path = _storage_path()
    public_url = _public_url(storage_path)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        supabase.storage.from_(TTS_AUDIO_BUCKET).upload(
            storage_path,
            mp3_bytes,
            {"content-type": "audio/mpeg", "upsert": "false"},
        )
    except Exception as exc:
        logger.warning("Supabase storage upload failed: %s", exc)
        # Still log metadata if upload fails so we can debug permissions.
        row_id = _insert_row(
            {
                "storage_path": storage_path,
                "public_url": None,
                "text_prompt": text_prompt[:2000],
                "format": "mp3",
                "effects_enabled": effects_enabled,
                "sample_rate": sample_rate,
                "file_size_bytes": len(mp3_bytes),
                "duration_ms": duration_ms,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "chat_log_id": chat_log_id,
                "created_at": created_at,
            }
        )
        return {
            "id": row_id,
            "saved": False,
            "error": str(exc),
            "storage_path": storage_path,
        }

    row_id = _insert_row(
        {
            "storage_path": storage_path,
            "public_url": public_url,
            "text_prompt": text_prompt[:2000],
            "format": "mp3",
            "effects_enabled": effects_enabled,
            "sample_rate": sample_rate,
            "file_size_bytes": len(mp3_bytes),
            "duration_ms": duration_ms,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "chat_log_id": chat_log_id,
            "created_at": created_at,
        }
    )

    return {
        "id": row_id,
        "saved": True,
        "public_url": public_url,
        "storage_path": storage_path,
        "file_size_bytes": len(mp3_bytes),
    }