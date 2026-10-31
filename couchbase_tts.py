"""Upsert TTS audio log rows to Couchbase Capella dolphin.public.tts_audio_logs."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("couchbase_tts")

CB_CONN = os.getenv("CB_CONNECTION", "")
CB_USER = os.getenv("CB_USER", "grok_xai")
CB_BUCKET = os.getenv("CB_BUCKET", "dolphin")
CB_SCOPE = os.getenv("CB_SCOPE", "public")
CB_COLLECTION = os.getenv("CB_TTS_COLLECTION", "tts_audio_logs")
CB_PASS_FILE = Path(
    os.getenv(
        "CB_PASS_FILE",
        "/home/sandcastle/JARVIS_APP/.cb_pass",
    )
)
# Also check sibling deploy / legacy box path
_FALLBACK_PASS = [
    Path(__file__).resolve().parent / ".cb_pass",
    Path("/home/sandcastle/DOLPHIN_SAIL/.cb_pass"),
    Path("/workspace/couchbase-migrate/.cb_pass"),
]

_cluster = None


def _load_password() -> str:
    env = (os.getenv("CB_PASSWORD") or "").strip()
    if env:
        return env
    for p in [CB_PASS_FILE, *_FALLBACK_PASS]:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8").strip()
        except Exception:
            continue
    raise FileNotFoundError("Couchbase password not found (CB_PASSWORD / .cb_pass)")


def _connect():
    global _cluster
    if _cluster is not None:
        return _cluster
    if not CB_CONN:
        raise RuntimeError("CB_CONNECTION is not set (couchbases://<your-capella-host>)")
    from couchbase.auth import PasswordAuthenticator
    from couchbase.cluster import Cluster
    from couchbase.options import ClusterOptions

    auth = PasswordAuthenticator(CB_USER, _load_password())
    options = ClusterOptions(auth)
    options.apply_profile("wan_development")
    cluster = Cluster(CB_CONN, options)
    cluster.wait_until_ready(timedelta(seconds=30))
    _cluster = cluster
    return cluster


def upsert_tts_audio_log(row: dict[str, Any]) -> dict[str, Any]:
    """Upsert one tts_audio_logs document. Key = str(id) or storage_path."""
    try:
        cluster = _connect()
        coll = cluster.bucket(CB_BUCKET).scope(CB_SCOPE).collection(CB_COLLECTION)
        key = str(row.get("id") or row.get("storage_path") or row.get("public_url") or "")
        if not key:
            return {"ok": False, "error": "missing id/storage_path"}
        coll.upsert(key, row)
        return {"ok": True, "key": key, "bucket": CB_BUCKET, "scope": CB_SCOPE, "collection": CB_COLLECTION}
    except Exception as exc:
        logger.warning("Couchbase tts_audio_logs upsert failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def dual_publish_tts(
    *,
    mp3_bytes: bytes,
    text_prompt: str,
    sample_rate: int,
    effects_enabled: bool = True,
    duration_ms: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    chat_log_id: int | None = None,
    fx_mode: str | None = None,
    cooked_path: str | None = None,
    save_tts_mp3_fn=None,
) -> dict[str, Any]:
    """Upload to Supabase (via existing save_tts_mp3) then Capella.

    save_tts_mp3_fn should be JARVIS_APP.supabase_audio.save_tts_mp3
    """
    result: dict[str, Any] = {"supabase": None, "couchbase": None, "fx_mode": fx_mode, "cooked_path": cooked_path}

    sb = None
    if save_tts_mp3_fn and mp3_bytes:
        try:
            sb = save_tts_mp3_fn(
                mp3_bytes,
                text_prompt=text_prompt,
                sample_rate=sample_rate,
                effects_enabled=effects_enabled,
                duration_ms=duration_ms,
                ip_address=ip_address,
                user_agent=user_agent,
                chat_log_id=chat_log_id,
            )
            result["supabase"] = sb
        except Exception as exc:
            result["supabase"] = {"saved": False, "error": str(exc)}

    # Build Capella doc from supabase row + FX metadata
    row: dict[str, Any] = {}
    if isinstance(sb, dict):
        row.update({k: v for k, v in sb.items() if k not in {"error"}})
        if sb.get("id") is not None:
            row["id"] = sb["id"]
        if sb.get("storage_path"):
            row["storage_path"] = sb["storage_path"]
        if sb.get("public_url"):
            row["public_url"] = sb["public_url"]
        if sb.get("file_size_bytes"):
            row["file_size_bytes"] = sb["file_size_bytes"]
    row.setdefault("text_prompt", (text_prompt or "")[:2000])
    row.setdefault("format", "mp3")
    row.setdefault("effects_enabled", effects_enabled)
    row.setdefault("sample_rate", sample_rate)
    row.setdefault("duration_ms", duration_ms)
    row.setdefault("file_size_bytes", len(mp3_bytes) if mp3_bytes else None)
    row.setdefault("chat_log_id", chat_log_id)
    if fx_mode:
        row["fx_mode"] = fx_mode
    if cooked_path:
        row["cooked_path"] = cooked_path
    if not row.get("id") and row.get("storage_path"):
        # Capella still accepts storage_path as key
        pass
    if row.get("id") is not None or row.get("storage_path") or row.get("public_url"):
        result["couchbase"] = upsert_tts_audio_log(row)
    else:
        # No supabase id — still try Capella with a synthetic key from cooked path
        if cooked_path:
            row["id"] = Path(cooked_path).stem
            result["couchbase"] = upsert_tts_audio_log(row)
        else:
            result["couchbase"] = {"ok": False, "error": "no key for Capella upsert"}

    return result
