"""Optional personal health notes for tailnet chats.

The addon stays empty unless HEALTH_CONTEXT_FILE points at a note the operator
placed on this machine. Nothing medical is bundled with the repository.
"""

from __future__ import annotations

import os
from pathlib import Path

_HINTS = (
    "health",
    "lab",
    "labs",
    "dental",
    "hospital",
    "chart",
    "medication",
    "diagnosis",
    "symptom",
)


def health_prompt_addon(message: str) -> str:
    text = (message or "").lower()
    if not any(word in text for word in _HINTS):
        return ""
    raw = (os.getenv("HEALTH_CONTEXT_FILE") or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if not path.is_file():
        return ""
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not body:
        return ""
    clipped = body[:4000]
    return (
        "Local health notes the user asked about. Use them only for this question. "
        "They are not a hospital chart and not medical advice.\n"
        f"{clipped}"
    )
