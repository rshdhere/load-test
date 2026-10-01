# bench

k6 scripts that load-test the servers in [`../servers`](../servers). Every server implements the same contract ([`../servers/openapi.json`](../servers/openapi.json)), so any script can run against any server.

## Layout

```
bench/
├── k6/
│   ├── grafana.sh         runs a script with live metrics streamed to Prometheus
│   └── src/
│       ├── load.ts        constant request rate for a fixed duration
│       ├── break.ts       ramps 0 → 200 → 500 → 1000 → 0 VUs to find the breaking point
│       ├── pick-live.ts   finds a live server via /api/v1/health before the test starts
│       └── summary.ts     prints a short summary and saves each run to ../results
├── grafana/               Prometheus + Grafana stack with the k6 dashboard preloaded
└── results/               one JSON file per run
```

## Running

Start one server (they all listen on port 3000 by default), then:

```sh
cd bench/k6
npm install                      # editor types only; k6 itself needs nothing

npm run load                     # 100 req/s for 30s
RATE=5000 DURATION=60s npm run load
npm run break
```

Each server's start command lives in its own folder (`npm start`, `cargo run --release`, `go build`, `gunicorn`, ...).

## Settings

| Variable      | Used by      | Default          | Meaning                                              |
| ------------- | ------------ | ---------------- | ---------------------------------------------------- |
| `URL`         | both         | -                | Exact URL to hit; skips port discovery               |
| `HOST`        | both         | `localhost`      | Host to probe when `URL` is not set                  |
| `PORTS`       | both         | `3000`           | Comma-separated ports to probe; first live one wins  |
| `ENCODING`    | both         | `identity`       | `Accept-Encoding` header sent with each request      |
| `TIMEOUT`     | both         | `8s`             | Per-request timeout                                  |
| `RATE`        | `load.ts`    | `100`            | Requests per second                                  |
| `DURATION`    | `load.ts`    | `30s`            | How long to hold the rate                            |
| `RESULTS_DIR` | both         | `../results`     | Where run summaries are written (relative to cwd)    |

## Results

Every run writes `results/<UTC timestamp>_<script>_<server>.json`, where the server name comes from the target's health response. Each file records:

- the script, server, URL, and settings used
- total requests, requests per second, failure rate, and dropped iterations
- latency avg / min / med / p90 / p95 / p99 / max in milliseconds
- each threshold and whether it passed

Commit the runs worth keeping so results can be compared across servers and over time. A quick side-by-side:

```sh
jq -r '[.server, .script, (.reqPerSec|floor), .latencyMs["p(99)"], .failedRate] | @tsv' results/*.json
```

## Live dashboard (Grafana)

`grafana/` runs Prometheus and Grafana in Docker. k6 pushes metrics to Prometheus while the test runs, and Grafana shows them on the official [k6 Prometheus dashboard](https://grafana.com/grafana/dashboards/19665).

```sh
docker compose -f bench/grafana/compose.yaml up -d    # once; keeps running

cd bench/k6
npm run load:grafana             # same settings as `npm run load`
npm run break:grafana
TESTID=django-break npm run break:grafana
```

Open <http://localhost:3001> and pick the run from the **testid** dropdown at the top of the dashboard. Each run gets a `testid` of `<script>-<timestamp>` unless `TESTID` is set, so name runs after the server you are testing to find them later.

| Service    | URL                     | Notes                                                       |
| ---------- | ----------------------- | ----------------------------------------------------------- |
| Grafana    | <http://localhost:3001> | Anonymous viewing; log in as `admin` / `admin` to edit      |
| Prometheus | <http://localhost:9090> | Accepts k6 remote write; keeps 30 days of data              |

Grafana uses port 3001 because every server defaults to 3000. `grafana.sh` sends p50/p90/p95/p99/avg/min/max latency, pushing every second; override any `K6_PROMETHEUS_RW_*` variable to change that. The JSON summaries in `results/` are still written as usual.

Stop the stack with `docker compose -f bench/grafana/compose.yaml down`; add `-v` to also delete the stored metrics and Grafana state.

## Fair comparisons

- Run one server at a time, on the same machine, with nothing else heavy running.
- Use release builds: `cargo build --release` for Rust, a built binary for Go.
- The Rust, Go, and Python servers read `WORKERS` (defaulting to one per CPU core); keep it the same across runs. The Bun and Node-based servers run as a single process.
- Run k6 on a different machine from the server when possible, since k6 competes for the same CPU.
