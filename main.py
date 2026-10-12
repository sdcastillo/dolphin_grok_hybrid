from starlette.concurrency import run_in_threadpool
from fastapi import BackgroundTasks, Body, FastAPI, Request, File, UploadFile, Cookie, Header, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import json
import os
import secrets
import re
import tempfile
import time
import uuid
import httpx
import pydantic
import subprocess
from dotenv import load_dotenv

PROFILE_PATH = os.getenv("PROFILE_PATH", "profile.md")
PROFILE_CACHE = {"mtime": 0.0, "text": ""}

def get_profile_text() -> str:
    try:
        mtime = os.path.getmtime(PROFILE_PATH)
    except OSError:
        return ""
    if mtime != PROFILE_CACHE["mtime"]:
        try:
            with open(PROFILE_PATH, encoding="utf-8") as fh:
                PROFILE_CACHE["text"] = fh.read().strip()
            PROFILE_CACHE["mtime"] = mtime
        except OSError:
            return PROFILE_CACHE["text"]
    return PROFILE_CACHE["text"]

# OCR / PDF extraction
try:
    import fitz  # PyMuPDF
    import pytesseract
    from PIL import Image
    from img2table.document import Image as Img2TableImage
    from img2table.ocr import TesseractOCR
    OCR_AVAILABLE = True
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
except Exception:
    OCR_AVAILABLE = False
from health_library import health_prompt_addon
from news_fetcher import (
    TRENDS_SCHEDULER_ENABLED,
    fetch_trending_headlines,
    fetch_trending_topics,
    get_stored_trends,
    get_trending_context,
    start_trending_scheduler,
    stop_trending_scheduler,
)
from soap_service import (
    SoapFault,
    build_fault,
    generate_wsdl,
    handle_soap_request,
)
from supabase_audio import save_tts_mp3
from direct_db import execute, execute_write
from supabase_logging import log_chat, log_visitor

import access_store
import stripe_billing
import tailscale_provision
from fastapi import Cookie, Header, HTTPException, Response
from fastapi.responses import RedirectResponse

from tts_service import TTS_ENABLED, status as tts_status, synthesize, warmup

# --- SCARLETT_FX_INTEGRATION imports ---
try:
    from scarlett_fx import (
        apply_fx_chain,
        detect_scarlett,
        resolve_fx_mode,
        bytes_from_audio_path,
        TTS_FX_COOKED_DIR,
    )
except Exception as _scarlett_imp_exc:  # pragma: no cover
    apply_fx_chain = None  # type: ignore
    detect_scarlett = lambda: {"present": False, "error": str(_scarlett_imp_exc)}  # type: ignore
    resolve_fx_mode = lambda m=None: "off"  # type: ignore
    bytes_from_audio_path = None  # type: ignore
    TTS_FX_COOKED_DIR = Path("/home/sandcastle/DOLPHIN_SAIL/downloads/cooked")  # type: ignore
try:
    from couchbase_tts import dual_publish_tts
except Exception:
    dual_publish_tts = None  # type: ignore
# --- end SCARLETT_FX_INTEGRATION imports ---
from minecraft_gen import generate_image, status as image_status

load_dotenv()

IMG_ENABLED = os.getenv("IMG_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


ACCESS_COOKIE = os.getenv("ACCESS_COOKIE_NAME", "dolphin_access")


def _extract_access_token(
    request: Request,
    access_cookie: str | None = None,
    x_dolphin_access: str | None = None,
) -> str | None:
    if x_dolphin_access:
        return x_dolphin_access.strip()
    if access_cookie:
        return access_cookie.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.query_params.get("access_token")



def _billing_required() -> bool:
    billing_on = os.getenv("BILLING_REQUIRED", "auto").lower()
    if billing_on == "auto":
        return stripe_billing.configured()
    return billing_on in {"1", "true", "yes", "on"}



def _client_is_localhost(request: Request) -> bool:
    """True when the *TCP peer* is local.

    Tailscale Funnel / reverse proxies often set X-Forwarded-For to the real
    client IP and may rewrite request.client.host. Payment gating must not treat
    those forwarded client IPs as "not local" when the socket peer is still loopback.
    """
    # Prefer the raw ASGI client when available (pre-proxy-rewrite).
    scope_client = None
    try:
        scope_client = (request.scope.get("client") or [None])[0]
    except Exception:
        scope_client = None
    host = (scope_client or (request.client.host if request.client else "") or "")
    if host in {"127.0.0.1", "::1", "localhost"}:
        return True
    # Some stacks put the proxy peer in this header instead of rewriting client.
    x_real = (request.headers.get("x-real-ip") or "").strip()
    if x_real in {"127.0.0.1", "::1"}:
        return True
    return False


def _is_tailscale_ip(ip: str) -> bool:
    """Tailscale CGNAT 100.64.0.0/10 (and common IPv6 unique-local fd7a:115c:a1e0::/48)."""
    ip = (ip or "").strip().lower()
    if not ip:
        return False
    if ip.startswith("fd7a:115c:a1e0:"):
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127


def _admin_client_prefix_bypass(ip: str) -> bool:
    """Owner public/Funnel client prefixes (IPv4/IPv6) from ADMIN_CLIENT_PREFIXES."""
    ip = (ip or "").strip().lower()
    if not ip:
        return False
    raw = (os.getenv("ADMIN_CLIENT_PREFIXES") or "").strip()
    if not raw:
        return False
    for prefix in raw.split(","):
        p = prefix.strip().lower()
        if p and ip.startswith(p):
            return True
    return False


def _tailscale_whois(ip: str) -> dict:
    """Best-effort Tailscale whois for admin device checks (cached briefly)."""
    import json
    import subprocess
    import time

    ip = (ip or "").strip()
    if not ip or not _is_tailscale_ip(ip):
        return {}
    cache = getattr(_tailscale_whois, "_cache", {})
    now = time.time()
    hit = cache.get(ip)
    if hit and now - hit[0] < 60:
        return hit[1]
    try:
        proc = subprocess.run(
            ["tailscale", "whois", "--json", ip],
            capture_output=True,
            text=True,
            timeout=2,
        )
        data = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
    except Exception:
        data = {}
    cache[ip] = (now, data)
    _tailscale_whois._cache = cache
    return data


def _admin_localhost_bypass(request: Request) -> bool:
    """Owner/admin on the machine itself skips payment (default on for localhost)."""
    flag = os.getenv("ADMIN_LOCALHOST_BYPASS", "true").lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    # Funnel terminates locally: TCP peer is loopback even when XFF is the phone.
    if _client_is_localhost(request):
        return True
    # Also treat direct loopback Host header + no external peer as local.
    return False


def _admin_tailscale_bypass(request: Request) -> bool:
    """Owner devices on the tailnet (e.g. iphone-15-pro) skip payment.

    Enabled by default when ADMIN_LOCALHOST_BYPASS is on. Matches Tailscale whois
    login / node name, or an explicit IP allowlist.
    """
    flag = os.getenv("ADMIN_TAILSCALE_BYPASS", os.getenv("ADMIN_LOCALHOST_BYPASS", "true")).lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False

    # Candidate IPs: socket peer, then forwarded client (Funnel / proxy).
    candidates: list[str] = []
    try:
        scope_client = (request.scope.get("client") or [None])[0]
        if scope_client:
            candidates.append(str(scope_client))
    except Exception:
        pass
    if request.client and request.client.host:
        candidates.append(request.client.host)
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xff:
        candidates.append(xff)
    # de-dupe
    seen = set()
    ips = []
    for ip in candidates:
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)

    allow_ips = {
        x.strip()
        for x in (os.getenv("ADMIN_TAILSCALE_IPS") or "100.69.35.112").split(",")
        if x.strip()
    }
    allow_nodes = {
        x.strip().lower()
        for x in (
            os.getenv("ADMIN_TAILSCALE_NODES")
            or "iphone-15-pro,jarvis-1,polar,cursor"
        ).split(",")
        if x.strip()
    }
    allow_users = {
        x.strip().lower()
        for x in (
            os.getenv("ADMIN_TAILSCALE_USERS")
            or "sdcastillo@github,sdcastillo@"
        ).split(",")
        if x.strip()
    }

    allow_net = os.getenv("ADMIN_TAILSCALE_ALLOW_NET", "true").lower() in {"1", "true", "yes", "on"}

    for ip in ips:
        if ip in allow_ips:
            return True
        if _admin_client_prefix_bypass(ip):
            return True
        # Owner preference: any device on the tailnet can use the personal hub
        if allow_net and _is_tailscale_ip(ip):
            return True
        info = _tailscale_whois(ip)
        # JSON shape varies slightly by tailscale version
        node = (
            (info.get("Node") or {})
            if isinstance(info.get("Node"), dict)
            else {}
        )
        user = (
            (info.get("UserProfile") or info.get("User") or {})
            if isinstance(info.get("UserProfile") or info.get("User"), dict)
            else {}
        )
        name = (
            (node.get("Name") or node.get("Hostinfo", {}).get("Hostname") or "")
            if isinstance(node.get("Hostinfo"), dict) or "Name" in node
            else (node.get("Name") or "")
        )
        # Name often like "iphone-15-pro.marlin-pollux.ts.net"
        short = str(name).split(".")[0].lower()
        login = str(user.get("LoginName") or user.get("DisplayName") or "").lower()
        if short and short in allow_nodes:
            return True
        if login and any(login == u or login.startswith(u.rstrip("@")) for u in allow_users):
            # Only auto-trust owner login when the peer is actually on the tailnet
            if _is_tailscale_ip(ip):
                return True
    return False


def _admin_header_bypass(request: Request) -> bool:
    """Optional shared admin unlock key via header or query (for Funnel/remote admin)."""
    key = (os.getenv("ADMIN_UNLOCK_KEY") or "").strip()
    if not key:
        return False
    got = (request.headers.get("x-admin-unlock") or "").strip()
    if got and secrets.compare_digest(got, key):
        return True
    q = (request.query_params.get("admin_key") or "").strip()
    return bool(q and secrets.compare_digest(q, key))

def _has_paid_entitlement(request: Request) -> bool:
    """True when billing is off, admin bypass, or cookie/Bearer token is paid|provisioned."""
    if not _billing_required():
        return True
    if _admin_localhost_bypass(request):
        return True
    if _admin_tailscale_bypass(request):
        return True
    if _admin_header_bypass(request):
        return True
    token = _extract_access_token(
        request,
        request.cookies.get(ACCESS_COOKIE),
        request.headers.get("x-dolphin-access"),
    )
    return access_store.get_by_token(token) is not None


def require_dolphin_access(request: Request, access_cookie: str | None = None, x_dolphin_access: str | None = None) -> dict:
    """Paid entitlement required when BILLING_REQUIRED is on (default true if Stripe configured)."""
    if not _billing_required():
        return {"status": "billing_disabled"}
    if _admin_localhost_bypass(request) or _admin_tailscale_bypass(request) or _admin_header_bypass(request):
        return {"status": "admin_bypass", "email": os.getenv("ADMIN_EMAIL", "admin@localhost")}
    token = _extract_access_token(request, access_cookie, x_dolphin_access)
    row = access_store.get_by_token(token)
    if not row:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "message": "Pay with Stripe to unlock Dolphin chat, then finish onboarding / Tailscale join.",
                "checkout": "/billing/checkout",
                "pay": "/pay",
                "onboarding": "/onboarding",
            },
        )
    return row


_DOCS_ENABLED = os.getenv("DOCS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
app = FastAPI(
    title="Alpha Predictive Insights AI",
    description="Local Dolphin-Mistral 7B chat with Qwen3-TTS voice, Supabase logging, and Minecraft pixel image gen",
    version="1.2.0",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/hub", StaticFiles(directory="static/hub", html=True), name="hub")

# Jarvis Media API (local library for Grok Bot / Dolphin Pod embeds)
try:
    import sys as _sys
    from pathlib import Path as _Path
    _media_dir = str(_Path(__file__).resolve().parent / "apps" / "jarvis-media")
    if _media_dir not in _sys.path:
        _sys.path.insert(0, _media_dir)
    from server import router as media_router  # type: ignore
    app.include_router(media_router)

    @app.get("/media", response_class=HTMLResponse)
    async def jarvis_media_player():
        index = _Path(_media_dir) / "static" / "index.html"
        return HTMLResponse(index.read_text(encoding="utf-8"))
except Exception as _media_err:  # noqa: BLE001
    import logging as _logging
    _logging.getLogger("uvicorn.error").warning("Jarvis Media not loaded: %s", _media_err)

from amp_proxy import router as amp_ui_router

app.include_router(amp_ui_router)


@app.get("/privacy")
async def privacy_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/hub/privacy.html", status_code=302)


@app.get("/terms")
async def terms_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/hub/terms.html", status_code=302)


# Public purchase / legal paths (no paid cookie required). Everything else needs entitlement
# when BILLING_REQUIRED is on. Browser navigations redirect to /pay; APIs get 402 JSON.
_PUBLIC_GET = {
    "/pay",
    "/admin/unlock",
    "/privacy",
    "/terms",
    "/billing/config",
    "/billing/access",
    "/onboarding",
    "/hub/privacy.html",
    "/hub/terms.html",
}
_PUBLIC_POST = {
    "/billing/webhook",
    "/billing/checkout",
    "/start",
    "/onboarding/activate",
}
_PUBLIC_PREFIXES = ()  # keep empty; prefer exact allowlist to avoid leaking /hub/*


def _is_public_path(method: str, path: str) -> bool:
    m = method.upper()
    if path != "/" and path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    if m == "GET" and path in _PUBLIC_GET:
        return True
    if m == "POST" and path in _PUBLIC_POST:
        return True
    if m == "HEAD" and path in _PUBLIC_GET:
        return True
    if m == "OPTIONS":
        return True
    return False


def _normalize_ip(ip: str) -> str:
    ip = (ip or "").strip().lower()
    if ip.startswith("[") and ip.endswith("]"):
        ip = ip[1:-1]
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    if ip.count(":") == 1 and "." in ip and ip.rsplit(":", 1)[-1].isdigit():
        ip = ip.rsplit(":", 1)[0]
    return ip


def _request_peer_ip(request: Request) -> str:
    try:
        scope_client = (request.scope.get("client") or [None])[0]
    except Exception:
        scope_client = None
    host = scope_client or (request.client.host if request.client else "") or ""
    return _normalize_ip(str(host))


def _forwarded_client_ip(request: Request) -> str:
    """Client address observed by the nearest trusted proxy (last X-Forwarded-For hop)."""
    parts = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if not parts:
        return ""
    return _normalize_ip(parts[-1])


def _on_tailnet_or_this_machine(request: Request) -> bool:
    """True for a tailnet peer, or a loopback client whose forwarded address is not public.

    Tailscale Funnel's TCP peer is 127.0.0.1 and it replaces X-Forwarded-For with the
    public client. Nginx appends the peer it actually accepted. The last hop is that peer.
    """
    peer = _request_peer_ip(request)
    if _is_tailscale_ip(peer):
        return True
    if peer not in {"127.0.0.1", "::1", "localhost"}:
        return False
    forwarded = _forwarded_client_ip(request)
    if not forwarded or forwarded in {"127.0.0.1", "::1", "localhost"}:
        return True
    return _is_tailscale_ip(forwarded)


_HUB_PUBLIC_PATHS = {"/hub/privacy.html", "/hub/terms.html"}


def _path_key(path: str) -> str:
    path = path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path


def _hub_requires_tailnet(path: str) -> bool:
    path = _path_key(path)
    if path in _HUB_PUBLIC_PATHS:
        return False
    return path == "/hub" or path.startswith("/hub/")


def _wants_browser_redirect(request: Request) -> bool:
    path = request.url.path or "/"
    if path == "/" or path.startswith("/hub"):
        return True
    if path.endswith(".html"):
        return True
    if (request.headers.get("sec-fetch-mode") or "").lower() == "navigate":
        return True
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        # Prefer redirect when client clearly wants HTML over JSON-only.
        first = accept.split(",")[0].strip()
        if first.startswith("text/html") or "*/*" in first:
            return True
        if "application/json" not in accept:
            return True
    return False


@app.middleware("http")
async def entitlement_gate(request: Request, call_next):
    path = request.url.path or "/"
    method = request.method.upper()
    # Resource hub stays on this machine and the tailnet. Funnel must not serve it.
    # Privacy and terms stay public so the paywall links keep working.
    if _hub_requires_tailnet(path) and not _on_tailnet_or_this_machine(request):
        body = b"" if method == "HEAD" else b"This page is available only from a device on the tailnet.\n"
        return Response(
            content=body,
            status_code=403,
            media_type="text/plain",
            headers={"Cache-Control": "no-store"},
        )
    if _is_public_path(method, path):
        return await call_next(request)
    if not _billing_required():
        return await call_next(request)
    if _has_paid_entitlement(request):
        response = await call_next(request)
        # Persist access when owner opens a one-time ?access_token= link (e.g. from iPhone).
        qtok = (request.query_params.get("access_token") or "").strip()
        if qtok and access_store.get_by_token(qtok) and not request.cookies.get(ACCESS_COOKIE):
            response.set_cookie(
                key=ACCESS_COOKIE,
                value=qtok,
                httponly=True,
                samesite="lax",
                max_age=60 * 60 * 24 * 365 * 5,
                secure=os.getenv("COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"},
            )
        # Tailscale/admin peers: mint/persist an admin entitlement cookie so chat stays unlocked
        elif (
            not request.cookies.get(ACCESS_COOKIE)
            and (
                _admin_localhost_bypass(request)
                or _admin_tailscale_bypass(request)
                or _admin_header_bypass(request)
            )
        ):
            admin_email = (os.getenv("ADMIN_EMAIL") or "admin@localhost").strip().lower()
            session_id = "admin_tailscale_bypass"
            row = access_store.get_by_session(session_id)
            if not row or row.get("status") not in {"paid", "provisioned"}:
                row = access_store.mark_paid(
                    checkout_session_id=session_id,
                    email=admin_email,
                    amount_cents=0,
                    currency="usd",
                )
                row = access_store.mark_provisioned(
                    checkout_session_id=session_id,
                    invite_url=None,
                    invite_id="admin-tailscale",
                )
            token = (row or {}).get("access_token")
            if token:
                response.set_cookie(
                    key=ACCESS_COOKIE,
                    value=token,
                    httponly=True,
                    samesite="lax",
                    max_age=60 * 60 * 24 * 365 * 5,
                    secure=os.getenv("COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"},
                )
        return response
    # Unpaid: browsers → /pay; APIs → 402 JSON (no product features).
    if method in {"GET", "HEAD"} and _wants_browser_redirect(request):
        return RedirectResponse(url="/pay", status_code=303)
    return JSONResponse(
        status_code=402,
        content={
            "error": "payment_required",
            "message": "Pay with Stripe to unlock Dolphin, then finish onboarding / Tailscale join.",
            "checkout": "/billing/checkout",
            "pay": "/pay",
            "onboarding": "/onboarding",
        },
    )


@app.get("/pay", response_class=HTMLResponse)
async def paywall_page(request: Request):
    """Minimal public paywall — Stripe checkout CTA only (no chat UI)."""
    return templates.get_template("paywall.html").render({"request": request})




# --- DOLPHIN POD Rolladex proxy (local serve.py on :8787) ---
# PROXY_ROLODEX_MOUNTED
_POD_UPSTREAM = os.getenv("DOLPHIN_POD_URL", "http://127.0.0.1:8787")


async def _proxy_pod(request: Request, subpath: str):
    """Proxy Rolladex/outreach through FastAPI so Tailscale HTTPS + admin bypass apply."""
    url = f"{_POD_UPSTREAM.rstrip('/')}/{subpath.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.get(url)
    except Exception as exc:
        raise HTTPException(502, detail=f"Rolladex upstream down: {exc}")
    ctype = upstream.headers.get("content-type", "text/html; charset=utf-8")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=ctype)


@app.get("/rolodex")
@app.get("/rolodex/")
async def rolodex_home(request: Request):
    return await _proxy_pod(request, "/rolodex")


@app.get("/outreach")
@app.get("/outreach/")
async def outreach_home(request: Request):
    return await _proxy_pod(request, "/outreach")


@app.get("/outreach.csv")
async def outreach_csv(request: Request):
    return await _proxy_pod(request, "/outreach.csv")


@app.get("/admin/unlock")
async def admin_unlock(request: Request, response: Response, key: str | None = None):
    """Owner/admin unlock: sets a permanent paid cookie when ADMIN_UNLOCK_KEY matches."""
    expected = (os.getenv("ADMIN_UNLOCK_KEY") or "").strip()
    provided = (key or request.query_params.get("key") or "").strip()
    if not expected or not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(403, detail="Invalid admin unlock key")
    # Ensure an admin entitlement exists
    admin_email = (os.getenv("ADMIN_EMAIL") or "admin@localhost").strip().lower()
    session_id = "admin_local_bypass"
    row = access_store.get_by_session(session_id)
    if not row or row.get("status") not in {"paid", "provisioned"}:
        row = access_store.mark_paid(
            checkout_session_id=session_id,
            email=admin_email,
            amount_cents=0,
            currency="usd",
        )
        row = access_store.mark_provisioned(
            checkout_session_id=session_id,
            invite_url=None,
            invite_id="admin",
        )
    token = row.get("access_token")
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365 * 5,
        secure=os.getenv("COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"},
    )
    return response



templates = Jinja2Templates(directory="templates")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "dolphin-mistral:7b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_NUM_THREADS = int(os.getenv("OLLAMA_NUM_THREADS", str(os.cpu_count() or 4)))

OLLAMA_OPTIONS = {
    "temperature": 0.2,
    "top_p": 0.9,
    "top_k": 40,
    "num_predict": int(os.getenv("OLLAMA_NUM_PREDICT", "96")),
    "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "1024")),
    "num_thread": OLLAMA_NUM_THREADS,
    "repeat_penalty": 1.1,
}



class ChatRequest(pydantic.BaseModel):
    message: str
    rating: str = "PG"
    speak: bool = False


class TTSRequest(pydantic.BaseModel):
    text: str
    language: str = "Auto"
    format: str = "wav"
    fx: str | None = None  # off|software|scarlett|both
    effects: bool = True
    download: bool = False
    chat_log_id: int | None = None


class OpenAIChatRequest(pydantic.BaseModel):
    model: str = OLLAMA_MODEL
    messages: list[dict]
    stream: bool = False
    temperature: float | None = None


class ImagineRequest(pydantic.BaseModel):
    prompt: str
    size: int = 512
    seed: int | None = None
    variation: int = 0
    animate: bool = False  # returns GIF when true (epic for video prep)


PROMPTS = {
    "G": (
        "You are Dolphin, a strict G-rated general-audience AI. "
        "You MUST ALWAYS REFUSE ANY request involving violence, sex, drugs, illegal acts, harm, profanity, or adult themes. "
        "Politely say you cannot discuss that and suggest a safe topic. "
        "Only provide wholesome, positive, educational responses."
    ),
    "PG": (
        "You are Dolphin, a PG-rated AI. "
        "You MUST ALWAYS REFUSE explicit sexual content, graphic violence, illegal instructions, or heavy profanity. "
        "Sanitize or redirect sensitive topics. Use only mild language."
    ),
    "R": (
        "You are Dolphin, an R-rated AI. "
        "Allow mature themes, swearing, moderate violence/sex if requested. "
        "REFUSE extreme gore, non-consensual, real crime instructions."
    ),
    "NC17": (
        "You are Dolphin in NC-17 / most-permissive mode. "
        "Allow mature themes, strong language, and adult sexual content when asked. "
        "Still REFUSE: real-world violent crime instructions, child sexual content, "
        "non-consensual sexual content, and extreme gore how-to. "
        "Do not claim you have zero rules; be honest that illegal harm assistance is refused."
    ),
}

# Rating-aware supplemental math instruction (LaTeX is always secondary to natural language)
# Keep this short and directive — models respond better to crisp "MUST use $...$" rules + short examples.
MATH_GUIDANCE = {
    "G": (
        "When a simple number or shape idea would be clearer with math, add it AFTER your normal friendly sentences as a small helper. "
        "MUST wrap every bit of arithmetic in dollar signs exactly like $3 + 4 = 7$ or $2 \\times 3 = 6$. Use $$ only if showing multiple lines. "
        "Only simple arithmetic, fractions like 1/2, and pi for circles (say 'about 3.14'). "
        "Example style you must copy: 'You started with 3 toy cars and got 4 more. That means you have $3 + 4 = 7$ toy cars now!' "
        "Never begin your reply with math. Keep explanations simple and friendly."
    ),
    "PG": (
        "When calculations, area, rate, average, probability or simple algebra would make the explanation clearer or shorter, add the math AFTER your conversational words. "
        "MUST wrap math with dollar signs for rendering: $x + 3 = 10$, $A = \\pi r^2$, $(4+6+8)/3 = 6$. Never leave powers as plain ^ text. "
        "Lead with words. Use $...$ inline and $$...$$ when showing the key result or steps. Briefly say what symbols mean. "
        "OK for PG: basic algebra, percentages, averages, coin probabilities, rectangle/circle area. Stop before advanced calculus."
    ),
    "R": (
        "When any math, formula, or calculation helps explain things better, add it as a supplement after your main conversational answer. "
        "MUST put LaTeX in dollar signs — never plain text powers like x^2. Correct style: f(x) = $x^2$, the derivative is $f'\''(x) = 2x$. "
        "For display use $...$ or $$ ... $$. Example: 'Using the power rule we have $$ \\frac{d}{dx} x^2 = 2x $$.' "
        "Words first, math with real delimiters second. Any level is allowed."
    ),
    "NC17": (
        "When any math, formula, or calculation helps explain things better, add it as a supplement after your main conversational answer. "
        "MUST put LaTeX in dollar signs — never plain text powers like x^2. Correct style: f(x) = $x^2$, the derivative is $f'\''(x) = 2x$. "
        "For display use $...$ or $$ ... $$. Example: 'Using the power rule we have $$ \\frac{d}{dx} x^2 = 2x $$.' "
        "Words first, math with real delimiters second. Any level is allowed."
    ),
}


def latexify(text: str) -> str:
    """Ensure mathematical content uses LaTeX delimiters for KaTeX rendering.
    Only touches responses that don't already contain delimiters.
    Keeps the natural language intact; math is still supplemental.
    Conservative to avoid breaking normal sentences.
    """
    if not text or '$' in text or '\\(' in text or '\\[' in text:
        return text

    out = text

    # Handle unicode superscript 2 as well (common in model output for r²)
    out = re.sub(
        r'\b([A-Za-z])\s*=\s*π\s*r[²2]\b',
        r'$\1 = \\pi r^2$',
        out
    )
    out = re.sub(
        r'\b([A-Za-z])\s*=\s*pi\s*r[²2]\b',
        r'$\1 = \\pi r^2$',
        out,
        flags=re.IGNORECASE
    )

    # A = π(9)  or A = π * 25  style , also catch trailing ²) etc
    out = re.sub(
        r'\b([A-Za-z])\s*=\s*π\s*\(?\s*([0-9.]+)[²2]?\)?',
        r'$\1 = \\pi \\times \2$',
        out,
        flags=re.IGNORECASE
    )
    out = re.sub(
        r'\b([A-Za-z])\s*=\s*pi\s*\*?\s*([0-9.]+)[²2]?',
        r'$\1 = \\pi \\times \2$',
        out,
        flags=re.IGNORECASE
    )

    # Wrap short, clean standalone equations that are likely results.
    # Trigger only after "formula", ":", or sentence start and avoid prose words inside eq.
    def _wrap_eq(m: re.Match) -> str:
        eq = m.group(1).strip()
        if len(eq) > 50:
            return m.group(0)
        low = ' ' + eq.lower() + ' '
        if any(w in low for w in (' the ', ' is ', ' are ', ' and ', ' or ', ' for ', ' with ', ' units ')):
            return m.group(0)
        return f'${eq}$'

    # Look for "formula ... = ..." or after " : " or ". X = Y"
    out = re.sub(
        r'(?i)(formula[^.]{0,40}?)([A-Za-z]\s*=\s*[0-9A-Za-z+\-*/().^ ]{2,35})',
        lambda m: m.group(1) + '$' + m.group(2).strip() + '$',
        out
    )
    out = re.sub(
        r'([:.]\s+)([A-Za-z]\s*=\s*[0-9A-Za-z+\-*/().^ ]{2,35})(?=\s|$|\.)',
        lambda m: m.group(1) + '$' + m.group(2).strip() + '$',
        out
    )

    # Powers like x^2 or r^2 (but not inside words or next to existing $)
    out = re.sub(
        r'(?<![$\\])\b([a-zA-Z])\^([0-9]+)\b(?![$])',
        r'$\1^{\2}$',
        out
    )

    # f(x) = something
    out = re.sub(
        r'\bf\(x\)\s*=\s*([0-9a-zA-Z+\-*/().^ ]{1,25}?)(?=\s|$|\.|,)',
        lambda m: f'$f(x) = {m.group(1).strip()}$',
        out
    )

    return out


def client_ip(request: Request) -> str:
    ip = request.headers.get("x-forwarded-for", request.client.host or "unknown")
    if "," in ip:
        ip = ip.split(",")[0].strip()
    return ip


def schedule_mp3_save(
    background_tasks: BackgroundTasks,
    *,
    mp3_bytes: bytes,
    text_prompt: str,
    sample_rate: int,
    effects: bool,
    duration_ms: int | None,
    request: Request | None = None,
    chat_log_id: int | None = None,
) -> None:
    background_tasks.add_task(
        save_tts_mp3,
        mp3_bytes,
        text_prompt=text_prompt,
        sample_rate=sample_rate,
        effects_enabled=effects,
        duration_ms=duration_ms,
        ip_address=client_ip(request) if request else None,
        user_agent=request.headers.get("user-agent", "unknown") if request else None,
        chat_log_id=chat_log_id,
    )


def build_ollama_payload(message: str, rating: str, stream: bool = False, include_health: bool = False) -> dict:
    r = rating.upper()
    base = PROMPTS.get(r, PROMPTS["PG"])
    math_part = MATH_GUIDANCE.get(r, MATH_GUIDANCE["PG"])
    system_prompt = f"{base}\n\n{math_part}"

    # --- Personal profile injection (profile.md) ---
    profile_text = get_profile_text()
    if profile_text:
        system_prompt += (
            "\n\n---\nPersonal context about the user you are talking to "
            "(use naturally, don't recite verbatim, never share with other users):\n"
            f"{profile_text}"
        )

    # Skip news dump on short chit-chat — keeps CPU chat snappy.
    if len(message.split()) >= 8 or any(k in message.lower() for k in ("news", "headline", "today", "trend")):
        trending_context = get_trending_context()
        if trending_context:
            system_prompt = f"{system_prompt}\n\n{trending_context}"

    # Health files stay off public Funnel chats. On the tailnet, the full
    # record is added only when the question is about those files.
    health_context = ""
    if include_health:
        system_prompt += (
            "\n\nThe user keeps personal health files on this tailnet site: "
            "/hub/health/ (charts), /hub/hospital-notes.html (March 2024 admission, plain English), "
            "and /hub/sam_health.html (Baylor summary page). "
            "Local lab, dental, and health-note excerpts are added when he asks about those files. "
            "Do not recite them unless he asks about his own health. "
            "They are not a hospital chart and not medical advice."
        )
        health_context = health_prompt_addon(message)
        if health_context:
            system_prompt += "\n\n" + health_context

    # Final crisp reminder right before the user message. Helps the model emit proper $...$ LaTeX.
    system_prompt += "\n\nRemember: when math makes the answer clearer, put it after your natural sentences and always wrap the expressions with dollar signs ($math$ or $$math$$) so it renders in the chat. Use LaTeX syntax, not plain text like x^2."

    options = dict(OLLAMA_OPTIONS)
    if health_context:
        # Capped health block is ~4k tokens. 12288 holds it plus the base
        # prompt. The model allows 32768; this stays well under that.
        options["num_ctx"] = max(int(options.get("num_ctx", 1024)), 12288)
    # Allow enough room for natural explanation + supplemental LaTeX.
    # Previously capped too aggressively — longer replies help complete math.
    word_count = max(1, len(message.split()))
    base_predict = max(120, word_count * 20)

    # If the question smells mathy, give the model full room to write explanation + LaTeX.
    lowered = message.lower()
    mathy = any(k in lowered for k in ('area', 'volume', 'solve', 'calculate', 'integral', 'derivative', 'probability', 'average', 'formula', 'equation', 'radius', 'sqrt', 'sin', 'cos', 'sum', 'pi ', 'percent', 'algebra', '+', 'x=', '2x', 'r^', 'x^2'))
    if mathy:
        base_predict = options.get("num_predict", 320)
    options["num_predict"] = min(options["num_predict"], base_predict)

    return {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        "stream": stream,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": options,
    }


@app.on_event("startup")
async def startup_event():
    # Warm Ollama model in background; TTS loads lazily on first /tts request
    async def _warm_ollama():
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                await client.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=build_ollama_payload("ping", "PG"),
                )
        except Exception:
            pass

    if os.getenv("OLLAMA_WARM_ON_START", "false").lower() in {"1", "true", "yes", "on"}:
        asyncio.create_task(_warm_ollama())
    if TTS_ENABLED:
        asyncio.create_task(asyncio.to_thread(warmup))

    if TRENDS_SCHEDULER_ENABLED:
        start_trending_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    await stop_trending_scheduler()


@app.get("/trending")
async def trending(limit: int = 10):
    try:
        return await fetch_trending_topics(limit=limit)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/trending/stored")
async def trending_stored(limit: int = 20):
    try:
        items = get_stored_trends(limit=limit)
        return {"count": len(items), "items": items}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/trending/headlines")
async def trending_headlines(limit: int = 8):
    try:
        headlines = await fetch_trending_headlines(limit=limit)
        return {"count": len(headlines), "headlines": headlines}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # Visit logging deferred until client posts /log-visit after the 18+ legal gate.
    # Unpaid browsers should already be redirected by entitlement_gate; keep a hard gate here.
    if _billing_required() and not _has_paid_entitlement(request):
        return RedirectResponse(url="/pay", status_code=303)
    return templates.get_template("index.html").render(
        {
            "request": request,
            "tts_enabled": TTS_ENABLED,
            "img_enabled": IMG_ENABLED,
            "show_health_hub": _on_tailnet_or_this_machine(request),
        }
    )


@app.post("/chat")
async def chat(req: ChatRequest, request: Request, background_tasks: BackgroundTasks, access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE), x_dolphin_access: str | None = Header(default=None)):
    entitlement = require_dolphin_access(request, access_cookie, x_dolphin_access)
    ip = client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")
    started = time.perf_counter()
    ollama_payload = build_ollama_payload(
        req.message,
        req.rating,
        include_health=_on_tailnet_or_this_machine(request),
    )

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            res = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=ollama_payload)
            res.raise_for_status()
            data = res.json()
            response_text = latexify(data["message"]["content"])
            chat_log_id = None
            try:
                duration_ms = int((time.perf_counter() - started) * 1000)
                def _log_ok():
                    try:
                        log_chat(
                            ip_address=ip,
                            user_agent=user_agent,
                            rating=req.rating.upper(),
                            prompt=req.message,
                            response=response_text,
                            model=OLLAMA_MODEL,
                            status="success",
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        pass
                background_tasks.add_task(_log_ok)
            except Exception:
                pass

            result = {"response": response_text, "chat_log_id": chat_log_id}
            if req.speak and TTS_ENABLED:
                try:
                    tts_started = time.perf_counter()
                    synth = await synthesize(response_text)
                    schedule_mp3_save(
                        background_tasks,
                        mp3_bytes=synth.mp3_bytes,
                        text_prompt=response_text,
                        sample_rate=synth.sample_rate,
                        effects=True,
                        duration_ms=int((time.perf_counter() - tts_started) * 1000),
                        request=request,
                        chat_log_id=chat_log_id,
                    )
                    result["audio_base64"] = __import__("base64").b64encode(synth.response_bytes).decode("ascii")
                    result["audio_format"] = "wav" if synth.media_type == "audio/wav" else "mp3"
                except Exception as tts_exc:
                    result["tts_error"] = str(tts_exc)

            return result
    except httpx.TimeoutException:
        error = "The model is still loading or the server is low on memory. Please wait a moment and try again."
        try:
            log_chat(
                ip_address=ip,
                user_agent=user_agent,
                rating=req.rating.upper(),
                prompt=req.message,
                model=OLLAMA_MODEL,
                status="timeout",
                error_message=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception:
            pass
        return JSONResponse({"error": error}, status_code=504)
    except Exception as e:
        error = f"Model backend error: {str(e)}"
        try:
            log_chat(
                ip_address=ip,
                user_agent=user_agent,
                rating=req.rating.upper(),
                prompt=req.message,
                model=OLLAMA_MODEL,
                status="error",
                error_message=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception:
            pass
        return JSONResponse({"error": error}, status_code=500)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request, access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE), x_dolphin_access: str | None = Header(default=None)):
    entitlement = require_dolphin_access(request, access_cookie, x_dolphin_access)
    ip = client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")
    started = time.perf_counter()
    ollama_payload = build_ollama_payload(
        req.message,
        req.rating,
        stream=True,
        include_health=_on_tailnet_or_this_machine(request),
    )

    async def event_generator():
        full_response = []
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=ollama_payload,
                ) as res:
                    res.raise_for_status()
                    async for line in res.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            full_response.append(token)
                            yield f"data: {json.dumps({'token': token})}\n\n"
                        if chunk.get("done"):
                            break

            response_text = latexify("".join(full_response))
            chat_log_id = None
            try:
                chat_log_id = log_chat(
                    ip_address=ip,
                    user_agent=user_agent,
                    rating=req.rating.upper(),
                    prompt=req.message,
                    response=response_text,
                    model=OLLAMA_MODEL,
                    status="success",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception:
                pass
            yield f"data: {json.dumps({'done': True, 'response': response_text, 'chat_log_id': chat_log_id})}\n\n"
        except Exception as exc:
            error = str(exc)
            try:
                log_chat(
                    ip_address=ip,
                    user_agent=user_agent,
                    rating=req.rating.upper(),
                    prompt=req.message,
                    model=OLLAMA_MODEL,
                    status="error",
                    error_message=error,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception:
                pass
            yield f"data: {json.dumps({'error': error})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")



# --- SCARLETT_FX_INTEGRATION helpers ---
import tempfile
from pathlib import Path as _Path_scarlett

def _client_meta(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    return ip, ua

async def _synthesize_to_wav_file(text: str, language: str, effects: bool) -> _Path_scarlett:
    """Synthesize dry/software-layered WAV to a temp file for Scarlett play."""
    synth = await synthesize(text, language, output_format="wav", effects=effects)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.write(synth.response_bytes)
    tmp.close()
    return _Path_scarlett(tmp.name)

def _schedule_dual_upload(
    background_tasks: BackgroundTasks,
    *,
    mp3_bytes: bytes,
    text_prompt: str,
    sample_rate: int,
    effects: bool,
    duration_ms: int,
    request: Request,
    chat_log_id: int | None,
    fx_mode: str | None,
    cooked_path: str | None,
):
    ip, ua = _client_meta(request)

    def _job():
        # Supabase only (Capella disabled per Sam 2026-09-18)
        return save_tts_mp3(
            mp3_bytes,
            text_prompt=text_prompt,
            sample_rate=sample_rate,
            effects_enabled=effects,
            duration_ms=duration_ms,
            ip_address=ip,
            user_agent=ua,
            chat_log_id=chat_log_id,
        )

    background_tasks.add_task(_job)
# --- end SCARLETT_FX_INTEGRATION helpers ---

@app.post("/tts")
async def text_to_speech(req: TTSRequest, request: Request, background_tasks: BackgroundTasks, fx: str | None = None):
    # --- SCARLETT_FX_IN_TTS ---
    fx_mode = (fx or getattr(req, "fx", None) or "").strip().lower()
    if fx_mode in {"scarlett", "both", "pedals", "software"} and apply_fx_chain is not None:
        # Delegate to FX path (returns audio bytes like normal /tts)
        if not TTS_ENABLED:
            return JSONResponse({"error": "TTS is disabled"}, status_code=503)
        output_format = req.format.lower().strip()
        if output_format not in {"wav", "mp3"}:
            return JSONResponse({"error": "format must be wav or mp3"}, status_code=400)
        wav_path = None
        try:
            tts_started = time.perf_counter()
            wav_path = await _synthesize_to_wav_file(req.text, req.language, effects=False)
            out_path, meta = await run_in_threadpool(apply_fx_chain, wav_path, fx_mode)
            audio_bytes, media_type = bytes_from_audio_path(out_path, prefer_mp3=(output_format == "mp3"))
            duration_ms = int((time.perf_counter() - tts_started) * 1000)
            if media_type == "audio/mpeg":
                _schedule_dual_upload(
                    background_tasks,
                    mp3_bytes=audio_bytes,
                    text_prompt=req.text,
                    sample_rate=48000,
                    effects=True,
                    duration_ms=duration_ms,
                    request=request,
                    chat_log_id=req.chat_log_id,
                    fx_mode=meta.get("mode_effective"),
                    cooked_path=str(out_path),
                )
            filename = f"speech_fx.{ 'mp3' if media_type=='audio/mpeg' else 'wav'}"
            disposition = "attachment" if req.download or output_format == "mp3" else "inline"
            return Response(
                content=audio_bytes,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'{disposition}; filename="{filename}"',
                    "X-Sample-Rate": "48000",
                    "X-Audio-Effects": f"scarlett:{meta.get('mode_effective')}",
                    "X-Cooked-Path": str(out_path),
                    "X-Scarlett-Sink": str(meta.get("sink") or ""),
                    "X-Scarlett-Source": str(meta.get("source") or ""),
                },
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc), "fx": fx_mode}, status_code=500)
        finally:
            if wav_path is not None:
                try:
                    _Path_scarlett(wav_path).unlink(missing_ok=True)
                except Exception:
                    pass
    # --- end SCARLETT_FX_IN_TTS (fall through to dry/normal) ---
    if not TTS_ENABLED:
        return JSONResponse({"error": "TTS is disabled"}, status_code=503)

    output_format = req.format.lower().strip()
    if output_format not in {"wav", "mp3"}:
        return JSONResponse({"error": "format must be wav or mp3"}, status_code=400)

    try:
        tts_started = time.perf_counter()
        synth = await synthesize(
            req.text,
            req.language,
            output_format=output_format,
            effects=req.effects,
        )
        schedule_mp3_save(
            background_tasks,
            mp3_bytes=synth.mp3_bytes,
            text_prompt=req.text,
            sample_rate=synth.sample_rate,
            effects=req.effects,
            duration_ms=int((time.perf_counter() - tts_started) * 1000),
            request=request,
            chat_log_id=req.chat_log_id,
        )
        filename = f"speech.{output_format}"
        disposition = "attachment" if req.download or output_format == "mp3" else "inline"
        return Response(
            content=synth.response_bytes,
            media_type=synth.media_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{filename}"',
                "X-Sample-Rate": str(synth.sample_rate),
                "X-Audio-Effects": "layered" if req.effects else "off",
            },
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/tts/status")
async def tts_status_endpoint():
    return tts_status()


@app.get("/tts/audio")
async def list_tts_audio(limit: int = 20):
    limit = max(1, min(limit, 100))
    rows = execute(
        """
        SELECT id, public_url, text_prompt, file_size_bytes, duration_ms, effects_enabled, created_at
        FROM tts_audio_logs
        WHERE public_url IS NOT NULL
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return {"count": len(rows), "items": rows}


# --- SCARLETT_FX_INTEGRATION endpoints ---
@app.get("/tts/devices")
async def tts_devices():
    info = detect_scarlett() if callable(detect_scarlett) else {"present": False}
    return {"scarlett": info, "fx_mode_default": resolve_fx_mode(None), "cooked_dir": str(TTS_FX_COOKED_DIR)}


@app.post("/tts/fx")
async def text_to_speech_fx(req: TTSRequest, request: Request, background_tasks: BackgroundTasks, fx: str | None = None):
    """Synthesize + Scarlett pedal loop; dual-upload; return JSON with cooked path + upload status."""
    if not TTS_ENABLED:
        return JSONResponse({"error": "TTS is disabled"}, status_code=503)
    if apply_fx_chain is None:
        return JSONResponse({"error": "scarlett_fx module not loaded"}, status_code=500)

    mode = fx or getattr(req, "fx", None) or "scarlett"
    output_format = (req.format or "mp3").lower().strip()
    if output_format not in {"wav", "mp3"}:
        return JSONResponse({"error": "format must be wav or mp3"}, status_code=400)

    wav_path = None
    try:
        tts_started = time.perf_counter()
        # Keep software vocal FX off for pedal loop input (dry into pedals)
        wav_path = await _synthesize_to_wav_file(req.text, req.language, effects=False)
        out_path, meta = await run_in_threadpool(apply_fx_chain, wav_path, mode)
        audio_bytes, media_type = bytes_from_audio_path(out_path, prefer_mp3=(output_format == "mp3"))
        duration_ms = int((time.perf_counter() - tts_started) * 1000)
        sample_rate = 48000 if str(out_path).endswith(".mp3") else 24000

        # Supabase-only upload in background (no Capella)
        upload = {"queued": True, "target": "supabase"}
        if media_type == "audio/mpeg":
            _schedule_dual_upload(
                background_tasks,
                mp3_bytes=audio_bytes,
                text_prompt=req.text,
                sample_rate=sample_rate,
                effects=True,
                duration_ms=duration_ms,
                request=request,
                chat_log_id=req.chat_log_id,
                fx_mode=(meta.get("mode_effective") if isinstance(meta, dict) else mode),
                cooked_path=str(out_path),
            )
        else:
            from scarlett_fx import bytes_from_audio_path as _b
            mp3_bytes, _ = _b(out_path, prefer_mp3=True)
            _schedule_dual_upload(
                background_tasks,
                mp3_bytes=mp3_bytes,
                text_prompt=req.text,
                sample_rate=48000,
                effects=True,
                duration_ms=duration_ms,
                request=request,
                chat_log_id=req.chat_log_id,
                fx_mode=(meta.get("mode_effective") if isinstance(meta, dict) else mode),
                cooked_path=str(out_path),
            )

        import base64
        return {
            "ok": True,
            "fx": meta,
            "cooked_path": str(out_path),
            "media_type": media_type,
            "sample_rate": sample_rate,
            "duration_ms": duration_ms,
            "upload": upload,
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "public_url": None,  # filled async via Supabase background upload
        }
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    finally:
        if wav_path is not None:
            try:
                _Path_scarlett(wav_path).unlink(missing_ok=True)
            except Exception:
                pass
# --- end SCARLETT_FX_INTEGRATION endpoints ---


# =========================
# SAAM: Sexual Awareness Mindfulness (STAR method)
# for stressful + medical situations, backed by medical datasets
# =========================

@app.get("/saam")
async def list_saam(limit: int = 50, category: str = None):
    """List SAAM entries. Supports optional category filter."""
    limit = max(1, min(limit, 200))
    if category:
        rows = execute(
            """
            SELECT id, situation, task, action, result, category, diagnosis, source, notes, created_at
            FROM saam
            WHERE category = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (category, limit),
        )
    else:
        rows = execute(
            """
            SELECT id, situation, task, action, result, category, diagnosis, source, notes, created_at
            FROM saam
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    return {"count": len(rows), "items": rows}


@app.post("/saam")
async def create_saam(req: dict):
    """Create a new SAAM (STAR) entry."""
    situation = (req.get("situation") or "").strip()
    if not situation:
        return JSONResponse({"error": "situation is required"}, status_code=400)

    task = (req.get("task") or "").strip() or None
    action = (req.get("action") or "").strip() or None
    result = (req.get("result") or "").strip() or None
    category = req.get("category", "stress")
    diagnosis = (req.get("diagnosis") or "").strip() or None
    source = req.get("source", "user")

    try:
        execute_write(
            """
            INSERT INTO saam (situation, task, action, result, category, diagnosis, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (situation, task, action, result, category, diagnosis, source)
        )
        return {"status": "success", "message": "SAAM entry saved"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/saam/join")
async def saam_diagnosis_join(diagnosis: str = None, limit: int = 20):
    """LEFT JOIN saam onto diagnosis + scientific context."""
    limit = max(1, min(limit, 100))
    
    if diagnosis:
        like = f"%{diagnosis}%"
        query = """
            SELECT 
                d.id as diagnosis_id,
                d.diagnosis,
                d.source as diagnosis_source,
                d.answer as medical_answer,
                d.evidence as science_evidence,
                s.id as saam_id,
                s.situation,
                s.task,
                s.action,
                s.result,
                s.category as saam_category
            FROM diagnosis d
            LEFT JOIN saam s ON s.diagnosis ILIKE %s OR s.situation ILIKE %s
            WHERE d.diagnosis ILIKE %s
            ORDER BY d.created_at DESC, s.created_at DESC
            LIMIT %s
        """
        rows = execute(query, (like, like, like, limit))
    else:
        query = """
            SELECT 
                d.id as diagnosis_id,
                d.diagnosis,
                d.source as diagnosis_source,
                d.answer as medical_answer,
                d.evidence as science_evidence,
                s.id as saam_id,
                s.situation,
                s.task,
                s.action,
                s.result,
                s.category as saam_category
            FROM diagnosis d
            LEFT JOIN saam s ON s.diagnosis_id = d.id OR s.diagnosis ILIKE ('%' || d.diagnosis || '%')
            ORDER BY d.created_at DESC, s.created_at DESC
            LIMIT %s
        """
        rows = execute(query, (limit,))
    
    return {"count": len(rows), "items": rows, "join_type": "diagnosis LEFT JOIN saam + science"}


@app.post("/imagine")
async def imagine(req: ImagineRequest):
    if not IMG_ENABLED:
        return JSONResponse({"error": "Image generation is disabled"}, status_code=503)
    prompt = (req.prompt or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    try:
        size = req.size if req.size in (256, 384, 512, 768, 1024) else 512
        var = max(0, min(5, int(req.variation or 0)))
        animate = bool(req.animate)
        img_bytes = await asyncio.to_thread(generate_image, prompt, size, req.seed, var, animate)

        # Persist a copy
        os.makedirs("static/generated", exist_ok=True)
        fname = f"mc_{abs(hash(prompt)) % 100000}_{int(time.time())}_v{var}.png"
        fpath = f"static/generated/{fname}"
        with open(fpath, "wb") as f:
            f.write(img_bytes)

        return Response(
            content=img_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": f'inline; filename="{fname}"',
                "X-Image-Model": "procedural-minecraft-pixel",
                "X-Prompt-Hash": str(abs(hash(prompt)) % (10**10)),
                "X-Variation": str(var),
                "X-Saved": fpath,
            },
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/imagine")
async def imagine_get(prompt: str, size: int = 512, seed: int | None = None, variation: int = 0):
    if not IMG_ENABLED:
        return JSONResponse({"error": "Image generation is disabled"}, status_code=503)
    prompt = (prompt or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt is required"}, status_code=400)
    try:
        sz = size if size in (256, 384, 512, 768, 1024) else 512
        var = max(0, min(5, int(variation or 0)))
        animate = False
        img_bytes = await asyncio.to_thread(generate_image, prompt, sz, seed, var, animate)

        os.makedirs("static/generated", exist_ok=True)
        fname = f"mc_{abs(hash(prompt)) % 100000}_{int(time.time())}_v{var}.png"
        fpath = f"static/generated/{fname}"
        with open(fpath, "wb") as f:
            f.write(img_bytes)

        return Response(
            content=img_bytes,
            media_type="image/png",
            headers={
                "Content-Disposition": f'inline; filename="{fname}"',
                "X-Image-Model": "procedural-minecraft-pixel",
                "X-Saved": fpath,
            },
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/imagine/status")
async def imagine_status():
    return image_status()


# --- PDF OCR Tool ---
def _process_pdf_ocr(pdf_bytes: bytes, filename: str) -> dict:
    """High-res PDF -> image -> OCR + table extraction (adapted from user script)."""
    if not OCR_AVAILABLE:
        return {"error": "OCR dependencies not available on this server."}

    results = {
        "filename": filename,
        "pages": [],
        "summary": ""
    }

    try:
        pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(pdf_document)
        results["total_pages"] = total_pages

        # Initialize OCR
        ocr = TesseractOCR(n_threads=1, lang="eng")

        for page_num in range(total_pages):
            page_data = {"page": page_num + 1, "tables": [], "text": "", "has_tables": False}

            # 1. High-res render (zoom=3 to balance quality/speed/memory)
            zoom = 3
            mat = fitz.Matrix(zoom, zoom)
            page = pdf_document.load_page(page_num)
            pix = page.get_pixmap(matrix=mat, alpha=False)  # solid bg

            # Temp image
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image_path = tmp.name
            pix.save(image_path)

            page_data["image_saved"] = os.path.basename(image_path)  # for debug if needed

            # 2. Try table extraction first (very powerful for bank statements etc.)
            try:
                img_obj = Img2TableImage(image_path, detect_rotation=False)
                extracted_tables = img_obj.extract_tables(
                    ocr=ocr,
                    implicit_rows=True,
                    borderless_tables=True,
                    min_confidence=50
                )
            except Exception as e:
                extracted_tables = []
                print(f"Table extraction error page {page_num+1}: {e}")

            if extracted_tables:
                page_data["has_tables"] = True
                for table in extracted_tables:
                    try:
                        md = table.df.to_markdown(index=False)
                        page_data["tables"].append(md)
                    except Exception:
                        pass
            else:
                # 3. Full page OCR fallback
                try:
                    image = Image.open(image_path)
                    text = pytesseract.image_to_string(image)
                    page_data["text"] = text.strip()
                except Exception as e:
                    page_data["text"] = f"[OCR error: {e}]"

            # Cleanup temp image
            try:
                os.unlink(image_path)
            except:
                pass

            results["pages"].append(page_data)

        pdf_document.close()
        results["summary"] = f"Processed {total_pages} page(s)."

    except Exception as exc:
        results["error"] = str(exc)

    return results


@app.post("/pdf_ocr")
async def pdf_ocr(file: UploadFile = File(...)):
    """Upload a PDF → high-res render → OCR + intelligent table extraction."""
    if not OCR_AVAILABLE:
        return JSONResponse({"error": "OCR tools (PyMuPDF / Tesseract / img2table) are not available."}, status_code=503)

    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Please upload a .pdf file."}, status_code=400)

    contents = await file.read()
    if len(contents) > 30 * 1024 * 1024:  # 30MB limit
        return JSONResponse({"error": "PDF too large (max 30MB)."}, status_code=413)

    try:
        result = await asyncio.to_thread(_process_pdf_ocr, contents, file.filename)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/imagine/gallery")
async def imagine_gallery(limit: int = 12):
    """List recently generated images saved to disk."""
    os.makedirs("static/generated", exist_ok=True)
    try:
        files = sorted(
            [f for f in os.listdir("static/generated") if f.endswith(".png")],
            reverse=True
        )[: max(1, min(50, limit))]
        items = []
        for fn in files:
            items.append({
                "filename": fn,
                "url": f"/static/generated/{fn}",
                "size": os.path.getsize(f"static/generated/{fn}")
            })
        return {"count": len(items), "items": items}
    except Exception as e:
        return {"count": 0, "items": [], "error": str(e)}


@app.get("/clips")
async def list_clips(limit: int = 12):
    """List recently generated video clips."""
    os.makedirs("static/clips", exist_ok=True)
    try:
        files = sorted(
            [f for f in os.listdir("static/clips") if f.endswith((".mp4", ".webm", ".mov"))],
            reverse=True
        )[: max(1, min(30, limit))]
        items = []
        for fn in files:
            fpath = f"static/clips/{fn}"
            items.append({
                "filename": fn,
                "url": f"/{fpath}",
                "size": os.path.getsize(fpath) if os.path.exists(fpath) else 0
            })
        return {"count": len(items), "items": items}
    except Exception as e:
        return {"count": 0, "items": [], "error": str(e)}


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: OpenAIChatRequest, request: Request, access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE), x_dolphin_access: str | None = Header(default=None)):
    require_dolphin_access(request, access_cookie, x_dolphin_access)
    user_messages = [m for m in req.messages if m.get("role") == "user"]
    if not user_messages:
        return JSONResponse({"error": "No user message provided"}, status_code=400)

    message = user_messages[-1].get("content", "")
    rating = "PG"
    for msg in req.messages:
        if msg.get("role") == "system" and "G-rated" in msg.get("content", ""):
            rating = "G"
            break

    ip = client_ip(request)
    user_agent = request.headers.get("user-agent", "unknown")
    started = time.perf_counter()
    payload = build_ollama_payload(
        message,
        rating,
        stream=req.stream,
        include_health=_on_tailnet_or_this_machine(request),
    )
    if req.temperature is not None:
        payload["options"]["temperature"] = req.temperature

    if req.stream:
        async def stream_openai():
            full_response = []
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                ) as res:
                    res.raise_for_status()
                    async for line in res.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            full_response.append(token)
                            data = {
                                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                                "object": "chat.completion.chunk",
                                "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                            }
                            yield f"data: {json.dumps(data)}\n\n"
                        if chunk.get("done"):
                            break

            response_text = latexify("".join(full_response))
            try:
                log_chat(
                    ip_address=ip,
                    user_agent=user_agent,
                    rating=rating,
                    prompt=message,
                    response=response_text,
                    model=OLLAMA_MODEL,
                    status="success",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception:
                pass
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_openai(), media_type="text/event-stream")

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            res = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            res.raise_for_status()
            data = res.json()
            response_text = latexify(data["message"]["content"])
            try:
                log_chat(
                    ip_address=ip,
                    user_agent=user_agent,
                    rating=rating,
                    prompt=message,
                    response=response_text,
                    model=OLLAMA_MODEL,
                    status="success",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception:
                pass
            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "model": req.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": response_text},
                        "finish_reason": "stop",
                    }
                ],
            }
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/log-visit")
@app.post("/log-visit")
async def log_visit(request: Request):
    user_agent = request.headers.get("user-agent", "unknown")
    path = request.headers.get("referer") or str(request.url.path)
    try:
        if request.method == "POST":
            try:
                body = await request.json()
                if isinstance(body, dict) and body.get("path"):
                    path = str(body.get("path"))[:500]
            except Exception:
                pass
        log_visitor(client_ip(request), user_agent, path)
    except Exception:
        pass
    return {"status": "ok"}



# ----- Stripe billing → Tailscale onboarding → Dolphin access -----

@app.get("/billing/config")
async def billing_config(request: Request):
    # For Tailscale/admin peers, chat is unlocked — UI must not say "requires payment"
    entitled = _has_paid_entitlement(request)
    base_required = (
        stripe_billing.configured()
        if os.getenv("BILLING_REQUIRED", "auto").lower() == "auto"
        else os.getenv("BILLING_REQUIRED", "false").lower() in {"1", "true", "yes", "on"}
    )
    return {
        "stripe_configured": stripe_billing.configured(),
        "tailscale_configured": tailscale_provision.configured(),
        "publishable_key": os.getenv("STRIPE_PUBLISHABLE_KEY"),
        "product": os.getenv("STRIPE_PRODUCT_NAME", "Dolphin AI Access (Tailscale)"),
        "amount_cents": int(os.getenv("STRIPE_AMOUNT_CENTS", "2900")),
        "currency": os.getenv("STRIPE_CURRENCY", "usd"),
        "billing_required": bool(base_required and not entitled),
        "access_active": bool(entitled),
        "admin_bypass": bool(
            _admin_localhost_bypass(request)
            or _admin_tailscale_bypass(request)
            or _admin_header_bypass(request)
        ),
    }


@app.post("/billing/checkout")
async def billing_checkout(request: Request):
    if not stripe_billing.configured():
        raise HTTPException(503, detail="Stripe is not configured (set STRIPE_SECRET_KEY)")
    email = None
    try:
        body = await request.json()
        email = (body or {}).get("email")
    except Exception:
        body = {}
    session = stripe_billing.create_checkout_session(email=email)
    if email:
        access_store.upsert_pending(email=email, checkout_session_id=session["id"])
    return session


@app.post("/billing/webhook")
async def billing_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    if not stripe_billing.configured():
        raise HTTPException(503, detail="Stripe not configured")
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(400, detail="Missing Stripe-Signature")
    try:
        event = stripe_billing.construct_event(payload, stripe_signature)
    except Exception as exc:
        raise HTTPException(400, detail=f"Webhook signature error: {exc}")

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        session_id = sess.get("id")
        email = (
            (sess.get("customer_details") or {}).get("email")
            or sess.get("customer_email")
            or sess.get("client_reference_id")
        )
        amount = sess.get("amount_total")
        currency = sess.get("currency")
        customer = sess.get("customer")
        pi = sess.get("payment_intent")
        row = access_store.mark_paid(
            checkout_session_id=session_id,
            email=email,
            customer_id=customer if isinstance(customer, str) else None,
            payment_intent_id=pi if isinstance(pi, str) else None,
            amount_cents=amount,
            currency=currency,
        )
        invite_url = None
        invite_id = None
        if email and tailscale_provision.configured():
            try:
                inv = tailscale_provision.create_user_invite(email)
                invite_url = inv.get("invite_url")
                invite_id = inv.get("invite_id")
            except Exception as exc:
                access_store.mark_provisioned(
                    checkout_session_id=session_id,
                    invite_url=None,
                    invite_id=f"error:{exc}",
                )
                return {
                    "received": True,
                    "provision": "invite_failed",
                    "error": str(exc),
                    "access_token": row.get("access_token"),
                }
        row = access_store.mark_provisioned(
            checkout_session_id=session_id,
            invite_url=invite_url,
            invite_id=str(invite_id) if invite_id else None,
        )
        return {"received": True, "access_token": row.get("access_token"), "invite_url": invite_url}

    return {"received": True}




@app.post("/start")
async def stripe_webhook_alias_start(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    """Alias for Stripe destination currently configured as predictiveinsightsai.com/start."""
    return await billing_webhook(request, stripe_signature)

@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request, session_id: str | None = None):
    row = access_store.get_by_session(session_id) if session_id else None
    if session_id and (not row or row.get("status") == "pending") and stripe_billing.configured():
        try:
            import stripe

            stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
            sess = stripe.checkout.Session.retrieve(session_id)
            if sess.get("payment_status") == "paid":
                email = (sess.get("customer_details") or {}).get("email") or sess.get("customer_email")
                row = access_store.mark_paid(
                    checkout_session_id=session_id,
                    email=email,
                    customer_id=sess.get("customer") if isinstance(sess.get("customer"), str) else None,
                    payment_intent_id=sess.get("payment_intent") if isinstance(sess.get("payment_intent"), str) else None,
                    amount_cents=sess.get("amount_total"),
                    currency=sess.get("currency"),
                )
                if email and tailscale_provision.configured() and not row.get("tailscale_invite_url"):
                    try:
                        inv = tailscale_provision.create_user_invite(email)
                        row = access_store.mark_provisioned(
                            checkout_session_id=session_id,
                            invite_url=inv.get("invite_url"),
                            invite_id=str(inv.get("invite_id") or ""),
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    return templates.get_template("onboarding.html").render(
        {
            "request": request,
            "session_id": session_id or "",
            "entitlement": row,
            "tailscale_download": "https://tailscale.com/download",
            "dolphin_chat_url": "/",
            "polar_hint": os.getenv(
                "DOLPHIN_TAILSCALE_HINT",
                "After joining the tailnet, open this app (or Polar Dolphin) on MagicDNS to chat.",
            ),
        }
    )


@app.post("/onboarding/activate")
async def onboarding_activate(request: Request, response: Response):
    try:
        body = await request.json()
    except Exception:
        body = {}
    token = (body or {}).get("access_token")
    session_id = (body or {}).get("session_id")
    row = None
    if token:
        row = access_store.get_by_token(token)
    elif session_id:
        row = access_store.get_by_session(session_id)
        if row and row.get("status") in {"paid", "provisioned"}:
            token = row.get("access_token")
    if not row or not token:
        raise HTTPException(404, detail="No paid entitlement found for this session")
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
        secure=os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
    )
    return {
        "ok": True,
        "email": row.get("email"),
        "status": row.get("status"),
        "tailscale_invite_url": row.get("tailscale_invite_url"),
        "access_token": token,
    }


@app.get("/billing/access")
async def billing_access_status(
    request: Request,
    access_cookie: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
):
    if _admin_localhost_bypass(request) or _admin_tailscale_bypass(request) or _admin_header_bypass(request):
        return {
            "authenticated": True,
            "status": "admin_bypass",
            "email": os.getenv("ADMIN_EMAIL", "admin@localhost"),
            "has_tailscale_invite": False,
            "admin_bypass": True,
        }
    token = _extract_access_token(request, access_cookie, request.headers.get("x-dolphin-access"))
    row = access_store.get_by_token(token)
    return {
        "authenticated": bool(row),
        "status": (row or {}).get("status"),
        "email": (row or {}).get("email"),
        "has_tailscale_invite": bool((row or {}).get("tailscale_invite_url")),
        "admin_bypass": False,
    }


def cloak_status() -> dict:
    cloak_file = os.getenv("CLOAK_FILE", "/root/.config/cloak")
    mode = "on"
    try:
        with open(cloak_file, encoding="utf-8") as fh:
            mode = fh.read().strip().lower() or "on"
    except (FileNotFoundError, PermissionError, OSError):
        pass
    ts_ip = None
    try:
        import subprocess

        ts_ip = subprocess.check_output(["tailscale", "ip", "-4"], text=True, timeout=3).strip()
    except Exception:
        ts_ip = None
    urls = {}
    if ts_ip:
        urls["magicdns"] = "http://milleniumfalcon/"
        urls["tailscale_ip_80"] = f"http://{ts_ip}/"
        urls["tailscale_ip_8000"] = f"http://{ts_ip}:8000"
    return {
        "mode": mode,
        "public_access": mode == "off",
        "tailscale_only": mode == "on",
        "tailscale_ip": ts_ip,
        "recommended_url": urls.get("magicdns") or (f"http://{ts_ip}:8000" if ts_ip else None),
        "tailscale_urls": urls,
    }


def _soap_endpoint_url(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/soap"


@app.get("/soap")
@app.get("/soap/wsdl")
async def soap_wsdl(request: Request):
    endpoint = _soap_endpoint_url(request)
    return Response(
        content=generate_wsdl(endpoint),
        media_type="application/wsdl+xml",
        headers={"Content-Disposition": 'inline; filename="MilleniumFalcon.wsdl"'},
    )


@app.post("/soap")
async def soap_endpoint(request: Request):
    soap_action = request.headers.get("soapaction", "").strip().strip('"')
    body = await request.body()
    if not body:
        return Response(
            content=build_fault("Empty request body", "Client"),
            media_type="text/xml",
            status_code=400,
        )
    try:
        xml_response = await handle_soap_request(
            body,
            ollama_base_url=OLLAMA_BASE_URL,
            ollama_model=OLLAMA_MODEL,
            build_ollama_payload=build_ollama_payload,
            get_stored_trends=get_stored_trends,
            cloak_status=cloak_status,
            tts_status=tts_status,
        )
        return Response(content=xml_response, media_type="text/xml")
    except SoapFault as exc:
        return Response(
            content=build_fault(exc.message, exc.code),
            media_type="text/xml",
            status_code=400 if exc.code == "Client" else 500,
        )
    except httpx.HTTPError as exc:
        return Response(
            content=build_fault(f"Backend error: {exc}", "Server"),
            media_type="text/xml",
            status_code=502,
        )
    except Exception as exc:
        return Response(
            content=build_fault(str(exc), "Server"),
            media_type="text/xml",
            status_code=500,
        )


@app.post("/generate-hybrid-clip")
async def generate_hybrid_clip(req: dict = Body(...)):
    prompt = (req.get('prompt') or 'hybrid movie scene action').strip()
    image_url = req.get('image_url')
    image_path = req.get('image_path')  # local /static/... path
    duration = int(req.get('duration', 6) or 6)
    duration = max(2, min(10, duration))

    xai_key = os.getenv('XAI_API_KEY')
    if not xai_key:
        return JSONResponse({"error": "XAI_API_KEY not configured for video generation."}, status_code=500)

    # If client gives a local image_path like /static/generated/xxx.png, convert to usable URL
    # For local dev we can use a placeholder note; prefer client-provided absolute image_url when using image ref
    final_image_url = image_url
    if not final_image_url and image_path:
        # Client should normally resolve to full URL (http://.../static/...) for external API
        final_image_url = image_path  # pass through, user/app can adjust

    headers = {"Authorization": f"Bearer {xai_key}"}
    payload = {
        "model": "grok-2-image",  # fallback or adjust; video model names evolve
        "prompt": prompt,
        "duration": duration,
        "resolution": "720p",
        "aspect_ratio": "16:9"
    }
    if final_image_url:
        payload["image_url"] = final_image_url

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                "https://api.x.ai/v1/videos/generations",
                json=payload,
                headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            video_url = data.get('url') or data.get('video_url') or data.get('output_url')
            if not video_url:
                # Some responses may embed differently
                if isinstance(data.get('data'), list) and data['data']:
                    video_url = data['data'][0].get('url') or data['data'][0].get('video_url')
            if not video_url:
                return JSONResponse({"error": "No video URL returned from provider", "response": data}, status_code=400)

            # download
            os.makedirs("static/clips", exist_ok=True)
            ts = int(time.time())
            vid_path = f"static/clips/hybrid_{ts}.mp4"
            vid_resp = await client.get(video_url)
            vid_resp.raise_for_status()
            with open(vid_path, 'wb') as f:
                f.write(vid_resp.content)

            # Transcode for broad compatibility (safe, non-fatal on fail)
            final_path = vid_path.replace('.mp4', '_final.mp4')
            try:
                subprocess.run([
                    'ffmpeg', '-i', vid_path,
                    '-c:v', 'libx264', '-crf', '23', '-preset', 'medium',
                    '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
                    '-y', final_path
                ], check=True, capture_output=True, timeout=120)
                serve_path = final_path
            except Exception:
                serve_path = vid_path  # fallback to original download

            return {
                "video_url": f"/{serve_path}",
                "original": f"/{vid_path}",
                "prompt_used": prompt,
                "image_ref": final_image_url,
            }

    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.text[:300]
        except Exception:
            pass
        return JSONResponse({"error": f"API error: {detail or str(e)}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# --- ROI (records request) board: tracking only, no fax sending ---
ROI_REQUESTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "health-corpus", "roi_requests.json")
ROI_STATUS_KEYS = {"needs_action", "awaiting", "received", "tbd", "closed"}
ROI_PATCH_FIELDS = {"status", "status_key", "sent_date", "next_step", "contact", "provider", "confirmation"}


def _roi_load() -> dict:
    with open(ROI_REQUESTS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _roi_save(data: dict) -> None:
    folder = os.path.dirname(ROI_REQUESTS_PATH)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".roi_requests.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, ROI_REQUESTS_PATH)


@app.get("/api/health/roi")
async def roi_requests_get(request: Request):
    if not _on_tailnet_or_this_machine(request):
        return JSONResponse({"error": "tailnet only"}, status_code=403)
    try:
        return JSONResponse(_roi_load(), headers={"Cache-Control": "no-store"})
    except FileNotFoundError:
        return JSONResponse({"rows": [], "note": "Faxes are sent by Sam; this board tracks them."})


@app.patch("/api/health/roi/{row_id}")
async def roi_requests_patch(row_id: str, request: Request, payload: dict = Body(...)):
    if not _on_tailnet_or_this_machine(request):
        return JSONResponse({"error": "tailnet only"}, status_code=403)
    if payload.get("action") == "mark_sent":
        today = time.strftime("%Y-%m-%d")
        changes = {"status": f"Sent {today} by Sam, awaiting", "status_key": "awaiting", "sent_date": today}
    else:
        changes = {k: str(v)[:300] for k, v in payload.items() if k in ROI_PATCH_FIELDS}
    if not changes:
        return JSONResponse({"error": "no editable fields"}, status_code=400)
    if "status_key" in changes and changes["status_key"] not in ROI_STATUS_KEYS:
        return JSONResponse({"error": "bad status_key"}, status_code=400)
    data = _roi_load()
    for row in data.get("rows", []):
        if row.get("id") == row_id:
            row.update(changes)
            data["updated"] = time.strftime("%Y-%m-%d")
            _roi_save(data)
            return row
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/health")
async def health():
    ollama_ok = False
    ollama_error = None
    models: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            res.raise_for_status()
            models = [m["name"] for m in res.json().get("models", [])]
            ollama_ok = OLLAMA_MODEL in models
    except Exception as exc:
        ollama_error = str(exc)

    tts = tts_status()
    img = image_status()
    healthy = ollama_ok

    body = {
        "status": "healthy" if healthy else "degraded",
        "cloak": cloak_status(),
        "soap": {
            "wsdl": "/soap/wsdl",
            "endpoint": "/soap",
            "namespace": "http://milleniumfalcon.local/soap",
            "operations": ["Echo", "Transmit", "SendMessage", "GetHealth", "GetHeadlines"],
        },
        "ollama": {
            "url": OLLAMA_BASE_URL,
            "model": OLLAMA_MODEL,
            "loaded": ollama_ok,
            "models": models,
            "error": ollama_error,
        },
        "tts": tts,
        "image": img,
    }

    if not healthy:
        return JSONResponse(body, status_code=503)
    return body
