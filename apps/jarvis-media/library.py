"""Jarvis Media — scan local music/video files into a small catalog."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".aiff", ".aif"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {"library_roots": [], "tags": {}, "pick_defaults": {}}


def _id_for(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]


def _kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    return None


def _tags_for(stem: str, cfg: dict[str, Any]) -> list[str]:
    tags_map = cfg.get("tags") or {}
    key = stem.lower().replace(" ", "-")
    out: list[str] = []
    for k, vals in tags_map.items():
        if k.lower() in key or key in k.lower():
            out.extend(vals)
    # de-dupe preserve order
    seen = set()
    clean = []
    for t in out:
        if t not in seen:
            seen.add(t)
            clean.append(t)
    return clean


_cache: dict[str, Any] = {"built_at": 0.0, "items": [], "by_id": {}}


def scan(force: bool = False) -> list[dict[str, Any]]:
    now = time.time()
    if not force and _cache["items"] and now - _cache["built_at"] < 30:
        return _cache["items"]

    cfg = load_config()
    items: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    seen_names: set[str] = set()

    for root in cfg.get("library_roots") or []:
        base = Path(os.path.expanduser(root))
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            kind = _kind(path)
            if not kind:
                continue
            # skip duplicate basenames (e.g. staging vs library copy)
            key = path.name.lower()
            if key in seen_names and "media-library" not in str(path):
                # prefer media-library copy; if we already have non-library, replace later
                existing = next((i for i in items if i["filename"].lower() == key), None)
                if existing and "media-library" in existing["path"]:
                    continue
            mid = _id_for(path)
            stem = path.stem
            item = {
                "id": mid,
                "title": stem.replace("-", " ").replace("_", " ").title(),
                "filename": path.name,
                "stem": stem,
                "kind": kind,
                "ext": path.suffix.lower(),
                "bytes": path.stat().st_size,
                "path": str(path.resolve()),
                "mime": mimetypes.guess_type(str(path))[0]
                or ("audio/mpeg" if kind == "audio" else "video/mp4"),
                "tags": _tags_for(stem, cfg),
                "stream_url": f"/api/media/stream/{mid}",
                "embed_url": f"/api/media/embed/{mid}",
            }
            # replace weaker duplicate
            if key in seen_names:
                items = [i for i in items if i["filename"].lower() != key]
            seen_names.add(key)
            items.append(item)
            by_id[mid] = item

    items.sort(key=lambda x: (x["kind"], x["title"].lower()))
    _cache["items"] = items
    _cache["by_id"] = by_id
    _cache["built_at"] = now
    return items


def get_item(item_id: str) -> dict[str, Any] | None:
    scan()
    return _cache["by_id"].get(item_id)


def search(q: str) -> list[dict[str, Any]]:
    q = (q or "").strip().lower()
    items = scan()
    if not q:
        return items
    out = []
    for i in items:
        hay = " ".join([i["title"], i["filename"], i["stem"], " ".join(i["tags"])]).lower()
        if q in hay:
            out.append(i)
    return out


def pick(tag: str | None = None, q: str | None = None) -> dict[str, Any] | None:
    cfg = load_config()
    items = scan()
    if q:
        hits = search(q)
        return hits[0] if hits else None
    if tag:
        tag_l = tag.lower().strip()
        defaults = cfg.get("pick_defaults") or {}
        preferred = defaults.get(tag_l)
        if preferred:
            for i in items:
                if preferred.lower() in i["stem"].lower():
                    return i
        tagged = [i for i in items if tag_l in [t.lower() for t in i["tags"]]]
        if tagged:
            return tagged[0]
        # fuzzy title
        fuzzy = search(tag_l)
        return fuzzy[0] if fuzzy else None
    return items[0] if items else None
