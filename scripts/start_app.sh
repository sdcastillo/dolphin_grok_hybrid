#!/usr/bin/env bash
set -euo pipefail

PORT="${APP_PORT:-8000}"
VENV="/root/.venv/bin/uvicorn"
APP="main:app"
CLOAK_FILE="/root/.config/cloak"
CLOAK="$(tr '[:upper:]' '[:lower:]' < "${CLOAK_FILE}" 2>/dev/null || echo on)"

if [[ "${CLOAK}" == "off" ]]; then
  HOST="127.0.0.1"
  echo "CLOAK:OFF — app backend for public nginx on http://$(hostname -I | awk '{print $1}')/"
  exec "${VENV}" "${APP}" --host "${HOST}" --port "${PORT}" --workers 1
fi

TS_IP="$(tailscale ip -4 2>/dev/null || true)"
if [[ -z "${TS_IP}" ]]; then
  echo "CLOAK:ON but Tailscale is not connected; refusing to start on a public interface." >&2
  exit 1
fi

echo "CLOAK:ON — Tailscale only: http://${TS_IP}:${PORT}"
exec "${VENV}" "${APP}" --host "${TS_IP}" --port "${PORT}" --workers 1