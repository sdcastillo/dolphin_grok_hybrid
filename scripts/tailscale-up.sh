#!/usr/bin/env bash
set -euo pipefail

AUTHKEY_FILE="/root/.config/tailscale/authkey"
if [[ ! -f "${AUTHKEY_FILE}" ]]; then
  echo "Missing ${AUTHKEY_FILE}. Add a reusable auth key from:" >&2
  echo "https://login.tailscale.com/admin/settings/keys" >&2
  exit 1
fi

AUTHKEY="$(tr -d '[:space:]' < "${AUTHKEY_FILE}")"
# Tag requires an auth key generated with tag:integrity in the admin console.
TAG="${TAILSCALE_TAG:-}"
ARGS=(--auth-key="${AUTHKEY}" --hostname=MilleniumFalcon --ssh --accept-routes)
if [[ -n "${TAG}" ]]; then
  ARGS+=(--advertise-tags="tag:${TAG}")
fi
tailscale up --reset "${ARGS[@]}"
echo "Tailscale IP: $(tailscale ip -4)"
echo "Access the app at:"
echo "  MagicDNS: http://milleniumfalcon/   (preferred when cloak is on)"
echo "  Direct:   http://$(tailscale ip -4):${APP_PORT:-8000}"