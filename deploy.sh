#!/usr/bin/env bash
# Bringt den lokalen Stand auf den Bot-Host und baut dort neu.
#
# Warum es dieses Skript gibt: Auf dem Bot-Host liegt eine Kopie ohne Git --
# kein Remote, kein Pull. Zwischen einem Commit hier und dem Bot im Funknetz
# lag bisher Handarbeit, und am 19.08.2026 ist genau das passiert: Feature
# committed, Tests gruen, Bot im Netz antwortete weiter mit dem Stand von
# gestern.
#
#   ./deploy.sh            spielt ein und baut
#   ./deploy.sh --dry-run  zeigt nur, was sich aendern wuerde
set -euo pipefail
cd "$(dirname "$0")"

HOST="${MESHBOT_HOST:-host-node-01}"
ZIEL="${MESHBOT_PATH:-meshbot}"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY="--dry-run"

# Nur der Code. data/ bleibt drueben unangetastet -- dort liegen Meldungen und
# der Zustand des laufenden Bots, die es hier gar nicht gibt.
# Ohne Schraegstrich am Ende: rsync legt den Ordner an, statt seinen Inhalt in
# das Zielwurzelverzeichnis zu kippen.
PFADE=(meshbot tools requirements.txt Dockerfile docker-compose.yml)

if [ -z "$DRY" ] && ! .venv/bin/python -m pytest -q >/dev/null 2>&1; then
  echo "Tests rot -- kein Deploy. Erst './.venv/bin/python -m pytest' ansehen." >&2
  exit 1
fi

echo "→ ${HOST}:${ZIEL}"
rsync -a --itemize-changes $DRY \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
  "${PFADE[@]}" "achildrenmile@${HOST}:${ZIEL}/"

if [ -n "$DRY" ]; then
  echo "(dry-run, nichts gebaut)"
  exit 0
fi

ssh "achildrenmile@${HOST}" "cd ${ZIEL} && docker compose up -d --build" >/dev/null
echo "gebaut, warte auf healthy…"

# Ein gestarteter Container ist kein laufender Bot. Ohne dieses Warten meldet
# das Skript Erfolg, waehrend der Healthcheck noch aussteht.
for _ in $(seq 30); do
  status=$(ssh "achildrenmile@${HOST}" "docker inspect meshbot --format '{{.State.Health.Status}}'" 2>/dev/null || echo "?")
  case "$status" in
    healthy) echo "meshbot healthy"; exit 0 ;;
    unhealthy) echo "meshbot UNHEALTHY -- 'docker logs meshbot' ansehen" >&2; exit 1 ;;
  esac
  sleep 2
done
echo "Timeout beim Warten auf healthy -- 'docker logs meshbot' ansehen" >&2
exit 1
