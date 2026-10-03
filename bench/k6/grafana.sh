#!/usr/bin/env sh
set -eu

script="${1:?usage: grafana.sh <load|break> [k6 args...]}"
shift

export K6_PROMETHEUS_RW_SERVER_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://localhost:9090/api/v1/write}"
export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(50),p(90),p(95),p(99),avg,min,max}"
export K6_PROMETHEUS_RW_PUSH_INTERVAL="${K6_PROMETHEUS_RW_PUSH_INTERVAL:-1s}"

if [ -n "${SERVER:-}" ] && [ -z "${URL:-}" ]; then
  port="$(node -e '
    const s = require("../../infra/servers.json").find((s) => s.name === process.argv[1])
    if (!s) { console.error(`unknown SERVER "${process.argv[1]}"`); process.exit(1) }
    console.log(s.port)' "$SERVER")"
  export URL="http://${HOST:-localhost}:$port/api/v1/health"
fi

server=unknown
if [ -n "${URL:-}" ]; then
  candidates="$URL"
else
  candidates=""
  for port in $(echo "${PORTS:-3000}" | tr ',' ' '); do
    candidates="$candidates http://${HOST:-localhost}:$port/api/v1/health"
  done
fi
for url in $candidates; do
  name="$(curl -fs --max-time 2 "$url" | sed -n 's/.*"server": *"\([^"]*\)".*/\1/p')" || continue
  if [ -n "$name" ]; then
    server="$name"
    break
  fi
done

testid="${TESTID:-$server-$script-$(date +%Y%m%d-%H%M%S)}"
echo "grafana: server=$server testid=$testid (http://localhost:3001)"

exec k6 run -o experimental-prometheus-rw \
  --tag "testid=$testid" \
  --tag "server=$server" \
  --tag "script=$script" \
  "$@" "src/$script.ts"
