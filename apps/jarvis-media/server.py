#!/usr/bin/env python3
"""Jarvis Media — minimalist local music/video player + API for Grok Bot / Dolphin Pod.

Run standalone:
  cd /home/sandcastle/JARVIS_APP/apps/jarvis-media && python3 server.py

Or mount router into JARVIS_APP main:
  from apps... no — import media_router from this file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from library import get_item, load_config, pick, scan, search

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"

router = APIRouter(prefix="/api/media", tags=["jarvis-media"])


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "path"}


@router.get("/health")
def media_health():
    items = scan()
    return {
        "ok": True,
        "app": "jarvis-media",
        "count": len(items),
        "audio": sum(1 for i in items if i["kind"] == "audio"),
        "video": sum(1 for i in items if i["kind"] == "video"),
    }


@router.get("/library")
def media_library(kind: str | None = None, refresh: bool = False):
    items = scan(force=refresh)
    if kind:
        items = [i for i in items if i["kind"] == kind]
    return {"count": len(items), "items": [_public(i) for i in items]}


@router.get("/search")
def media_search(q: str = Query("", min_length=0)):
    hits = search(q)
    return {"q": q, "count": len(hits), "items": [_public(i) for i in hits]}


@router.get("/pick")
def media_pick(tag: str | None = None, q: str | None = None):
    """Grok Bot / Dolphin Pod: pull one track by mood tag or query."""
    item = pick(tag=tag, q=q)
    if not item:
        raise HTTPException(404, "no matching media")
    return {
        "item": _public(item),
        "stream_url": item["stream_url"],
        "embed_url": item["embed_url"],
        "embed_html": (
            f'<audio controls autoplay loop src="{item["stream_url"]}" '
            f'data-title="{item["title"]}"></audio>'
            if item["kind"] == "audio"
            else f'<video controls autoplay src="{item["stream_url"]}" '
            f'data-title="{item["title"]}"></video>'
        ),
    }


@router.get("/stream/{item_id}")
def media_stream(item_id: str):
    item = get_item(item_id)
    if not item:
        raise HTTPException(404, "not found")
    path = Path(item["path"])
    if not path.is_file():
        raise HTTPException(410, "file missing")
    return FileResponse(
        path,
        media_type=item["mime"],
        filename=item["filename"],
        content_disposition_type="inline",
    )


@router.get("/embed/{item_id}", response_class=HTMLResponse)
def media_embed(item_id: str):
    item = get_item(item_id)
    if not item:
        raise HTTPException(404, "not found")
    if item["kind"] == "audio":
        body = f'<audio controls autoplay loop style="width:100%" src="/api/media/stream/{item_id}"></audio>'
    else:
        body = f'<video controls autoplay style="width:100%;max-height:80vh" src="/api/media/stream/{item_id}"></video>'
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>{item['title']}</title>
<style>body{{margin:0;background:#0b1220;color:#e8eef8;font:14px system-ui}}
main{{padding:12px}} h1{{font-size:16px;margin:0 0 8px}}</style></head>
<body><main><h1>{item['title']}</h1>{body}
<p style="opacity:.6;margin-top:8px">{', '.join(item['tags']) or 'untagged'}</p>
</main></body></html>"""


@router.post("/rescan")
def media_rescan():
    items = scan(force=True)
    return {"ok": True, "count": len(items)}


def create_app() -> FastAPI:
    app = FastAPI(title="Jarvis Media", version="0.1.0")
    app.include_router(router)

    @app.get("/", response_class=HTMLResponse)
    def player():
        index = STATIC / "index.html"
        return index.read_text(encoding="utf-8")

    @app.get("/player", response_class=HTMLResponse)
    def player_alias():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="media-static")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    host = os.getenv("JARVIS_MEDIA_HOST", cfg.get("host", "0.0.0.0"))
    port = int(os.getenv("JARVIS_MEDIA_PORT", cfg.get("port", 8010)))
    print(f"Jarvis Media on http://{host}:{port}/  API http://{host}:{port}/api/media/library")
    uvicorn.run(app, host=host, port=port, log_level="info")
