#!/usr/bin/env bash
# Narrow Tailscale Funnel to purchase/billing/legal paths only.
# Full app stays available on the tailnet via `tailscale serve`.
#
# Sam: run this on Jarvis (password for sudo will be prompted):
#   sudo bash /home/sandcastle/JARVIS_APP/scripts/narrow-funnel.sh
#
# One-time alternative so future serve/funnel changes don't need root:
#   sudo tailscale set --operator=sandcastle
set -euo pipefail

PORT="${JARVIS_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
TS=(tailscale)
if [[ "$(id -u)" -ne 0 ]]; then
  if ! tailscale serve status >/dev/null 2>&1; then
    :
  fi
  # Prefer sudo when not already root (serve config is root-gated on this host).
  if [[ "${SKIP_SUDO:-}" != "1" ]]; then
    TS=(sudo tailscale)
  fi
fi

echo "== Current serve/funnel =="
"${TS[@]}" serve status || true
"${TS[@]}" funnel status || true

echo "== Reset broad Funnel / serve config =="
"${TS[@]}" serve reset

echo "== Tailnet-only: serve full app on :${PORT} =="
"${TS[@]}" serve --bg --yes "${PORT}"

echo "== Funnel ONLY purchase/onboarding/legal paths =="
FUNNEL_PATHS=(
  /billing/webhook
  /start
  /billing/checkout
  /billing/config
  /billing/access
  /onboarding
  /onboarding/activate
  /pay
  /privacy
  /terms
  /hub/privacy.html
  /hub/terms.html
)

for p in "${FUNNEL_PATHS[@]}"; do
  echo "  funnel ${p} -> ${BASE}${p}"
  # Public path p proxies to local same path (target includes path).
  "${TS[@]}" funnel --bg --yes --set-path="${p}" "${BASE}${p}"
done

echo "== Done. Status: =="
"${TS[@]}" serve status
"${TS[@]}" funnel status
echo
echo "Notes:"
echo "  - Public internet only reaches Funnel paths above (middleware is still the app backstop)."
echo "  - Full chat/hub/TTS on tailnet via MagicDNS serve."
echo "  - Stripe webhook + success URLs must use these Funnel paths (/start, /onboarding, /pay, …)."
