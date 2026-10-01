#!/usr/bin/env sh
set -eu

script="${1:?usage: grafana.sh <load|break> [k6 args...]}"
shift

export K6_PROMETHEUS_RW_SERVER_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://localhost:9090/api/v1/write}"
export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(50),p(90),p(95),p(99),avg,min,max}"
export K6_PROMETHEUS_RW_PUSH_INTERVAL="${K6_PROMETHEUS_RW_PUSH_INTERVAL:-1s}"

testid="${TESTID:-$script-$(date +%Y%m%d-%H%M%S)}"
echo "grafana testid: $testid (http://localhost:3001)"

exec k6 run -o experimental-prometheus-rw --tag "testid=$testid" "$@" "src/$script.ts"
