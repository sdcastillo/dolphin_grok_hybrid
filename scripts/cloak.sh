#!/usr/bin/env bash
set -euo pipefail

CLOAK_FILE="/root/.config/cloak"
NGINX_ENABLED="/etc/nginx/sites-enabled/aloha"
NGINX_AVAILABLE="/etc/nginx/sites-available/aloha"
NGINX_PUBLIC="/etc/nginx/sites-available/aloha-public"
NGINX_CLOAKED="/etc/nginx/sites-available/aloha-cloaked"

usage() {
  echo "Usage: cloak.sh {on|off|status}"
  echo "  on   — Tailscale devices only (MilleniumFalcon private)"
  echo "  off  — Public access via Hetzner IP on port 80"
}

require_tailscale() {
  local ts_ip4 ts_ip6
  ts_ip4="$(tailscale ip -4 2>/dev/null || true)"
  ts_ip6="$(tailscale ip -6 2>/dev/null || true)"
  if [[ -z "${ts_ip4}" && -z "${ts_ip6}" ]]; then
    echo "Tailscale is not connected. Run /root/scripts/tailscale-up.sh first." >&2
    exit 1
  fi
  echo "${ts_ip4}|${ts_ip6}"
}

apply_nginx() {
  local mode="$1"
  local ts_ip4="$2"
  local ts_ip6="$3"

  if [[ "${mode}" == "on" ]]; then
    sed -e "s/__TAILSCALE_IP4__/${ts_ip4}/g" \
        -e "s/__TAILSCALE_IP6__/${ts_ip6}/g" \
        "${NGINX_CLOAKED}" > "${NGINX_AVAILABLE}"
  else
    cp "${NGINX_PUBLIC}" "${NGINX_AVAILABLE}"
  fi

  ln -sfn "${NGINX_AVAILABLE}" "${NGINX_ENABLED}"
  nginx -t
  systemctl restart nginx
}

set_cloak() {
  local mode="$1"
  local ts_ip

  case "${mode}" in
    on|off) ;;
    *) usage; exit 1 ;;
  esac

  ts_combo="$(require_tailscale)"
  ts_ip4="${ts_combo%%|*}"
  ts_ip6="${ts_combo#*|}"
  echo "${mode}" > "${CLOAK_FILE}"
  chmod 600 "${CLOAK_FILE}"
  systemctl restart alpha-ai.service
  sleep 2
  apply_nginx "${mode}" "${ts_ip4}" "${ts_ip6}"

  if [[ "${mode}" == "on" ]]; then
    echo "CLOAK:ON — public gate closed"
    echo "  MagicDNS (recommended):  http://milleniumfalcon/"
    echo "  Tailscale IP (port 80 via nginx): http://${ts_ip4}/"
    echo "  Direct backend: http://${ts_ip4}:8000  or  http://milleniumfalcon:8000"
    if [[ -n "${ts_ip6}" ]]; then
      echo "  (also via IPv6): http://[${ts_ip6}]/"
    fi
  else
    local pub_ip
    pub_ip="$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1 | head -1)"
    echo "CLOAK:OFF — public access enabled"
    echo "  Public:    http://${pub_ip}/"
    echo "  Tailscale: http://${ts_ip4}:8000"
  fi
}

status() {
  local mode ts_ip4 ts_ip6 pub_ip
  mode="$(tr '[:upper:]' '[:lower:]' < "${CLOAK_FILE}" 2>/dev/null || echo unknown)"
  ts_ip4="$(tailscale ip -4 2>/dev/null || echo n/a)"
  ts_ip6="$(tailscale ip -6 2>/dev/null || echo n/a)"
  pub_ip="$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || echo n/a)"
  echo "CLOAK:${mode^^}"
  echo "Tailscale IPv4: ${ts_ip4}"
  echo "Tailscale IPv6: ${ts_ip6}"
  echo "Public IP:      ${pub_ip}"
  if [[ "${mode}" == "on" ]]; then
    echo "Access: Tailscale devices only"
  elif [[ "${mode}" == "off" ]]; then
    echo "Access: Public + Tailscale"
  fi
}

case "${1:-}" in
  on) set_cloak on ;;
  off) set_cloak off ;;
  status) status ;;
  *) usage; exit 1 ;;
esac