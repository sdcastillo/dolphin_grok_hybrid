"""Create Tailscale user invites for paid Dolphin access."""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API = "https://api.tailscale.com/api/v2"


def _cfg() -> tuple[str | None, str | None]:
    return os.getenv("TAILSCALE_API_KEY"), os.getenv("TAILSCALE_TAILNET")


def configured() -> bool:
    k, t = _cfg()
    return bool(k and t)


def create_user_invite(email: str, role: str | None = None) -> dict[str, Any]:
    """Invite email to the tailnet. Returns invite dict including inviteUrl when available."""
    api_key, tailnet = _cfg()
    if not api_key or not tailnet:
        raise RuntimeError("TAILSCALE_API_KEY and TAILSCALE_TAILNET are required")

    role = role or os.getenv("TAILSCALE_INVITE_ROLE", "member")
    url = f"{API}/tailnet/{tailnet}/user-invites"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"email": email.strip().lower(), "role": role}

    with httpx.Client(timeout=30.0) as client:
        res = client.post(url, headers=headers, json=payload)
        if res.status_code >= 400:
            # Fallback: some tenants use /invites
            alt = f"{API}/tailnet/{tailnet}/invites"
            res2 = client.post(alt, headers=headers, json={"email": email.strip().lower()})
            if res2.status_code >= 400:
                logger.error("Tailscale invite failed %s %s / alt %s %s", res.status_code, res.text, res2.status_code, res2.text)
                raise RuntimeError(f"Tailscale invite failed: {res.status_code} {res.text}")
            data = res2.json()
        else:
            data = res.json()

    # Normalize invite URL field names across API shapes
    invite_url = (
        data.get("inviteUrl")
        or data.get("url")
        or data.get("inviteURL")
        or (data.get("invite") or {}).get("url")
    )
    invite_id = data.get("id") or (data.get("invite") or {}).get("id")
    return {"raw": data, "invite_url": invite_url, "invite_id": invite_id}
