#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

mkdir -p "${ROOT_DIR}/data/logs"

"${ROOT_DIR}/scripts/worker-up.sh"

for _ in $(seq 1 30); do
  if docker exec discord-browser-worker python3 - <<'PY' >/dev/null 2>&1
import urllib.request
urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2)
PY
  then
    exec docker exec discord-browser-worker python3 /app/automation.py cold-start-ghost-show
  fi
  sleep 2
done

echo "browser worker did not become ready in time" >&2
exit 1
