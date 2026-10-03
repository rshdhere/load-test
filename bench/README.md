# bench

k6 scripts that load-test the servers in [`../servers`](../servers). Every server implements the same contract ([`../servers/openapi.json`](../servers/openapi.json)), so any script can run against any server.

## Layout

```
bench/
├── k6/
│   ├── grafana.sh         runs a script with live metrics streamed to Prometheus (see ../infra)
│   └── src/
│       ├── load.ts        constant request rate for a fixed duration
│       ├── break.ts       ramps 0 → 200 → 500 → 1000 → 0 VUs to find the breaking point
│       ├── pick-live.ts   finds a live server via /api/v1/health before the test starts
│       └── summary.ts     prints a short summary and saves each run to ../results
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
| `SERVER`      | `grafana.sh` | -                | Server name from `infra/servers.json`; sets `URL` to its container port |
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

The Grafana stack lives in [`../infra`](../infra/README.md) and also runs all 13 servers in containers on fixed ports (3101–3113). With it up, stream any run to Grafana by naming the server:

```sh
docker compose -f infra/compose.yaml up -d     # from the repo root; see infra/README.md

cd bench/k6
SERVER=axum npm run load:grafana
SERVER=django RATE=500 npm run load:grafana
SERVER=fiber TESTID=fiber-gc-tuned npm run break:grafana
```

`grafana.sh` looks the port up in `infra/servers.json`, reads the server's name from its health endpoint, and tags every metric with `server`, `script` and `testid` (default `<server>-<script>-<timestamp>`). `URL`, `HOST` and `PORTS` still work for servers started by hand. Watch the run in **Load Testing → Live Load Test** at <http://localhost:8080>; afterwards it appears on the server's own dashboard and in **Fleet → Run Comparison**.

## Fair comparisons

- Run one server at a time, on the same machine, with nothing else heavy running.
- Use release builds: `cargo build --release` for Rust, a built binary for Go.
- The Rust, Go, and Python servers read `WORKERS` (defaulting to one per CPU core); keep it the same across runs. The Bun and Node-based servers run as a single process.
- Run k6 on a different machine from the server when possible, since k6 competes for the same CPU.
