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
env_value() { sed -n "s/^$1=\([^[:space:]#]*\).*/\1/p" infra/.env | tail -1; }
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

# Alert notifications: provisioned only when infra/.env has a webhook URL, since
# Grafana refuses a contact point without one. Without it, reset the policy so
# a previously configured channel stops receiving.
notify=infra/grafana/provisioning/alerting/notifications.yaml
webhook_type=$(env_value ALERT_WEBHOOK_TYPE)
webhook_type=${webhook_type:-discord}
case $webhook_type in
  discord | slack | teams | googlechat | webhook) ;;
  *) echo "ALERT_WEBHOOK_TYPE must be discord, slack, teams, googlechat or webhook, got '$webhook_type'" >&2; exit 1 ;;
esac
if [[ -n "$(env_value ALERT_WEBHOOK_URL)" ]]; then
  cat > "$notify" <<YAML
# Written by infra/deploy.sh from infra/.env; do not edit.
apiVersion: 1
contactPoints:
  - orgId: 1
    name: o11y notifications
    receivers:
      - uid: o11y-notify
        type: $webhook_type
        settings:
          url: \$__env{ALERT_WEBHOOK_URL}
muteTimes:
  - orgId: 1
    name: always
    time_intervals:
      - weekdays: ["sunday:saturday"]
policies:
  - orgId: 1
    receiver: o11y notifications
    group_by: [grafana_folder, alertname, server]
    group_wait: 30s
    group_interval: 5m
    repeat_interval: 4h
    routes:
      # Visitor load tests trip these on purpose; they stay on the dashboards
      - receiver: o11y notifications
        object_matchers: [["kind", "=", "load-test"]]
        mute_time_intervals: [always]
YAML
  echo "alert notifications: $webhook_type"
else
  printf '%s\n' "# Written by infra/deploy.sh: no ALERT_WEBHOOK_URL, so no notifications." \
    "apiVersion: 1" "resetPolicies: [1]" > "$notify"
  echo "alert notifications: off (set ALERT_WEBHOOK_URL in infra/.env)"
fi

# CI builds and pushes every image, so a deploy from CI only pulls them
# (compose.yaml reads IMAGE_REGISTRY and IMAGE_TAG). Run by hand without them,
# the images are built here instead, which takes long on a small VPS.
if [[ -n "${IMAGE_TAG:-}" ]]; then
  export IMAGE_REGISTRY IMAGE_TAG
  "${compose[@]}" pull --quiet
  "${compose[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 300
  # Drop this stack's images from earlier deploys, and nothing else on the machine
  docker images --format '{{.Repository}}:{{.Tag}}' \
    | grep "^${IMAGE_REGISTRY}/" | grep -v ":${IMAGE_TAG}\$" | xargs -r docker rmi >/dev/null 2>&1 || true
else
  "${compose[@]}" build --pull
  "${compose[@]}" up -d --remove-orphans --wait --wait-timeout 300
fi
docker image prune -f >/dev/null

# Config files are bind-mounted, so `up` does not restart anything when only
# they change. Prometheus, blackbox and Alloy reload on SIGHUP; Grafana re-reads
# dashboards by itself but alert rules only through its admin API.
grafana_auth="$(env_value GRAFANA_ADMIN_USER):$(env_value GRAFANA_ADMIN_PASSWORD)"
hup_at=$(date +%s)
"${compose[@]}" kill -s SIGHUP prometheus blackbox alloy >/dev/null
curl -fsS -o /dev/null -X POST -u "$grafana_auth" http://127.0.0.1:3120/api/admin/provisioning/alerting/reload
echo "reloaded: prometheus, blackbox, alloy, grafana alerting"

# Loki, Tempo and Beyla only read their config at startup: restart each one
# whose config file changed (a checkout sets its mtime) since it started.
restart=()
for pair in loki:infra/loki/loki.yaml tempo:infra/tempo/tempo.yaml beyla:infra/beyla/beyla.yaml; do
  service=${pair%%:*} config=${pair#*:}
  started=$(docker inspect -f '{{.State.StartedAt}}' "$("${compose[@]}" ps -q "$service")")
  if (( $(stat -c %Y "$config") >= $(date -d "$started" +%s) )); then
    restart+=("$service")
  fi
done
if (( ${#restart[@]} )); then
  "${compose[@]}" restart "${restart[@]}" >/dev/null
  echo "restarted for new config: ${restart[*]}"
fi

check() {
  for _ in $(seq 30); do
    if curl -fsS -o /dev/null "${@:2}"; then
      echo "ok: $1"
      return 0
    fi
    sleep 2
  done
  echo "not answering: $1 (${*: -1})" >&2
  return 1
}

check grafana http://127.0.0.1:3120/api/health
check runner http://127.0.0.1:3121/run/
# Loki and Tempo publish no ports; ask Grafana whether it reaches them
check loki -u "$grafana_auth" http://127.0.0.1:3120/api/datasources/uid/loki/health
check tempo -u "$grafana_auth" http://127.0.0.1:3120/api/datasources/uid/tempo/health

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

# Beyla exits on a bad config and Docker keeps restarting it, so a quick look
# can catch it running. Give it time, then require it up and scraped.
sleep 15
beyla_state=$(docker inspect -f '{{.State.Running}} {{.State.Restarting}}' "$("${compose[@]}" ps -q beyla)")
if [[ $beyla_state == "true false" ]] \
  && curl -fsS 'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22beyla%22%7D' | grep -q ',"1"\]'; then
  echo "ok: beyla"
else
  echo "beyla is not running; see: docker compose -f infra/compose.yaml logs beyla" >&2
  exit 1
fi

echo "deployed $GIT_COMMIT"
