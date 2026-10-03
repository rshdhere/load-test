#!/usr/bin/env bash
# Runs on the VPS from the repository root after the checkout is updated
# (CI does this over SSH; see .github/workflows/ci.yml). Rebuilds what changed,
# restarts the stack and fails unless Grafana and the runner answer.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f infra/.env ]]; then
  echo "infra/.env is missing; copy infra/.env.example and fill it in first" >&2
  exit 1
fi

compose=(docker compose -f infra/compose.yaml)

"${compose[@]}" build --pull
"${compose[@]}" up -d --remove-orphans --wait --wait-timeout 300
docker image prune -f >/dev/null

check() {
  for _ in $(seq 30); do
    if curl -fsS -o /dev/null "$2"; then
      echo "ok: $1"
      return 0
    fi
    sleep 2
  done
  echo "not answering: $1 ($2)" >&2
  return 1
}

check grafana http://127.0.0.1:3001/api/health
check runner http://127.0.0.1:3002/run/

echo "deployed $(git rev-parse --short HEAD)"
