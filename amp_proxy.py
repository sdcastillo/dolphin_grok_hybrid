"""Proxy the guitar amp UI onto the public Funnel site at /amp/."""

import asyncio

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import RedirectResponse, Response

router = APIRouter()
AMP_HTTP = "http://127.0.0.1:8790"
AMP_WS = "ws://127.0.0.1:8790/ws"
_DROP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


async def _proxy(request: Request, path: str) -> Response:
    url = AMP_HTTP + "/" + path.lstrip("/")
    if request.url.query:
        url = url + "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                url,
                content=await request.body(),
                headers=headers,
            )
    except httpx.HTTPError as error:
        return Response(
            content=f"Guitar amp is not reachable: {error}\n",
            status_code=502,
            media_type="text/plain",
        )
    out = {k: v for k, v in upstream.headers.items() if k.lower() not in _DROP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=out)


@router.get("/amp")
async def amp_redirect():
    return RedirectResponse(url="/amp/", status_code=307)


@router.api_route(
    "/amp/",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def amp_root(request: Request):
    return await _proxy(request, "")


@router.api_route(
    "/amp/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def amp_path(path: str, request: Request):
    return await _proxy(request, path)


@router.websocket("/amp/ws")
async def amp_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        async with websockets.connect(AMP_WS, open_timeout=5, max_size=2**20) as upstream:

            async def client_to_amp():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def amp_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = {
                asyncio.create_task(client_to_amp()),
                asyncio.create_task(amp_to_client()),
            }
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except Exception:
        pass
    try:
        await websocket.close()
    except Exception:
        pass
