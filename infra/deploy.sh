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
# compose.yaml passes it to the runner, which reports it as o11y_build_info
GIT_COMMIT=$(git rev-parse --short HEAD)
export GIT_COMMIT

# Fail before touching anything if another program already holds a host port
# the stack publishes; otherwise nginx can end up proxying to that program.
taken=$(
  "${compose[@]}" config --format json | python3 -c '
import json, subprocess, sys

compose = sys.argv[1:]
config = json.load(sys.stdin)

# Ports this stack already publishes (from an earlier deploy) are fine
out = subprocess.run(compose + ["ps", "--format", "json"], capture_output=True, text=True).stdout.strip()
rows = json.loads(out) if out.startswith("[") else [json.loads(line) for line in out.splitlines() if line]
ours = {p["PublishedPort"] for row in rows for p in row.get("Publishers") or [] if p.get("PublishedPort")}

listening = subprocess.run(["ss", "-Hltn"], capture_output=True, text=True).stdout
used = {int(line.split()[3].rsplit(":", 1)[1]) for line in listening.splitlines()}

for name, service in config["services"].items():
    for port in service.get("ports") or []:
        published = int(port["published"])
        if published in used and published not in ours:
            print(f"  {name}: {published}")
' "${compose[@]}"
)
if [[ -n "$taken" ]]; then
  printf 'host ports already in use by something outside this stack:\n%s\n' "$taken" >&2
  echo "free them or change the published ports in infra/compose.yaml (and infra/nginx/o11y.conf)" >&2
  exit 1
fi

"${compose[@]}" build --pull
"${compose[@]}" up -d --remove-orphans --wait --wait-timeout 300
docker image prune -f >/dev/null

# Config files are bind-mounted, so `up` does not restart anything when only
# they change. Prometheus and blackbox reload on SIGHUP; Grafana re-reads
# dashboards by itself but alert rules only through its admin API.
hup_at=$(date +%s)
"${compose[@]}" kill -s SIGHUP prometheus blackbox >/dev/null
env_value() { sed -n "s/^$1=\([^[:space:]#]*\).*/\1/p" infra/.env | tail -1; }
curl -fsS -o /dev/null -X POST -u "$(env_value GRAFANA_ADMIN_USER):$(env_value GRAFANA_ADMIN_PASSWORD)" \
  http://127.0.0.1:3120/api/admin/provisioning/alerting/reload
echo "reloaded: prometheus, blackbox, grafana alerting"

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

check grafana http://127.0.0.1:3120/api/health
check runner http://127.0.0.1:3121/run/

# SIGHUP reloads in the background; wait for a successful reload newer than the signal
reloaded() {
  curl -fsS http://127.0.0.1:9090/api/v1/status/runtimeinfo | python3 -c '
import datetime, json, re, sys
info = json.load(sys.stdin)["data"]
at = datetime.datetime.fromisoformat(re.sub(r"\.\d+", "", info["lastConfigTime"]).replace("Z", "+00:00"))
sys.exit(0 if info["reloadConfigSuccess"] and at.timestamp() >= int(sys.argv[1]) else 1)
' "$hup_at"
}
for _ in $(seq 15); do
  reloaded && break
  sleep 2
done
if reloaded; then
  echo "ok: prometheus config"
else
  echo "prometheus did not load its new config; see: docker compose -f infra/compose.yaml logs prometheus" >&2
  exit 1
fi

echo "deployed $GIT_COMMIT"
