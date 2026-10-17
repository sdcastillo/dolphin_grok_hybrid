#!/usr/bin/env bash
set -euo pipefail

PORT="${APP_PORT:-8000}"
VENV="/root/.venv/bin/uvicorn"
APP="main:app"

TS_IP="$(tailscale ip -4 2>/dev/null || true)"
if [[ -z "${TS_IP}" ]]; then
  echo "Tailscale is not connected; refusing to expose the app publicly." >&2
  exit 1
fi

echo "Starting app on Tailscale only: http://${TS_IP}:${PORT}"
exec "${VENV}" "${APP}" --host "${TS_IP}" --port "${PORT}" --workers 1