# infra

Runs all 13 servers in containers next to a self-hosted observability stack, and publishes Grafana at **<https://o11y.raashed.com>**.

```
 visitor ── :443 ── nginx ──┬── /       Grafana ─┬── Prometheus ──┬── blackbox ──────── 13 servers
                   (host)   │                    │    ▲  ▲        ├── cAdvisor            ▲
                            │                    │    │  │        ├── node_exporter       │ eBPF
                            │                    │    │  │        └── Beyla ──────────────┘
                            │                    │    │  └ remote write ── k6 ◄── runner
                            │                    ├── Loki ◄── Alloy ◄── container logs
                            │                    └── Tempo ◄── Beyla (sampled traces), runner (spans)
                            └── /run/   runner
```

Metrics, logs and traces are linked in Grafana: a trace ID in a log line opens the trace, and a span opens its service's logs and request metrics.

| Component                      | Role                                                                                       |
| ------------------------------ | ------------------------------------------------------------------------------------------ |
| 13 server containers           | The systems under test, published on `127.0.0.1:3101-3113` (see `servers.json`)            |
| Prometheus                     | Stores everything: scraped metrics plus k6 results pushed via remote write                 |
| blackbox_exporter              | Probes every server's `/api/v1/health` every 5s (status, latency, availability)            |
| cAdvisor                       | Per-container CPU, memory, threads and network                                             |
| node_exporter                  | Host CPU, memory, disk, network and TCP state                                              |
| Beyla                          | eBPF auto-instrumentation of the 13 servers, with no code changes in any language: request rate, errors and duration (RED) for every request, plus a 5% sample of requests as traces. `beyla/beyla.yaml` is generated from `servers.json` and matches the servers by port, so nothing else on the machine is instrumented |
| Loki + Alloy                   | Logs: Alloy tails this project's containers (and only those) and ships them to Loki, 7 days kept |
| Tempo                          | Traces from Beyla and the runner over OTLP, 3 days kept                                      |
| Grafana                        | Dashboards and alert rules, all provisioned from files in this folder                      |
| runner                         | The **Run a load test** and **Head to head** buttons: starts one k6 run at a time. Traced with OpenTelemetry (a span per request and per load test) and logs JSON with trace IDs |
| nginx (on the host)            | Single entrypoint: Grafana at `/`, the runner at `/run/`; HTTPS via certbot. Not part of compose; site config in `nginx/o11y.conf` |

## Dashboards

| Folder                        | Dashboards                                                                                      |
| ----------------------------- | ----------------------------------------------------------------------------------------------- |
| **Fleet**                     | **Fleet Overview** (home): every server's status, availability, CPU, memory and best results · **Leaderboard**: servers ranked by req/s per CPU core and per MB of memory, plus p99 at a fixed rate · **Compare servers**: pick servers and see them side by side · **Run Comparison**: all load-test runs side by side |
| **Servers** → Rust, Go, Python, TypeScript | One per server: what it is, health and SLO, container resources, its load-test history, requests seen from inside (eBPF: status codes, server-side vs k6 latency), and its logs and traces |
| **Load Testing**              | **Live Load Test** for watching a k6 run as it happens · **Head to Head**: a 2-3 server race with winners, a scoreboard and overlaid graphs · **k6 (official)**, the stock k6 dashboard |
| **Fleet → Inside the Servers** | What the servers saw, from eBPF: RED per server, slowest and latest traces, log volume and logs, with a service filter |
| **Infrastructure**            | **Host** (including PSI pressure, OOM kills and a disk forecast), **Containers** (including restarts and OOM kills), **Observability Stack** (deployed commit, public site and TLS expiry, alerts, scrape targets, TSDB, recording rules, visitor tests) |

Tags cut across the folders: `overview`, `comparison`, `live`, `load-testing`, `per-server` (plus the language), `resources`, `ops` and `self-monitoring`. Every dashboard links back to Fleet Overview and has **Servers**, **Compare**, **Load testing** and **Ops** dropdowns built from those tags. The leaderboard's p99 column uses the `rate` tag that `load.ts` adds to every metric, so it only counts runs made after that tag was added.

## Production signals

- **SLO.** Every server has a 99.9% availability objective on its health check over a rolling 30 days. Fleet Overview shows how many servers meet it and each one's remaining error budget; each server dashboard shows its budget and 1h/6h burn rates. Load tests count against the budget on purpose: a server that misses health checks under load is unavailable to everyone else too.
- **Markers.** Every graph marks when load tests ran (orange regions, from k6's own metrics) and when the stack was deployed (green, from `o11y_build_info`, which the runner reports from the `GIT_COMMIT` that `deploy.sh` sets).
- **Alerts** (`grafana/provisioning/alerting/rules.yaml`): load-test errors and latency, servers down, a fast error-budget burn (14.4x over both 1h and 5m), the public site down, the TLS certificate within 14 days of expiry, the disk filling within a day, and container OOM kills. They show on Fleet Overview, the Observability Stack, each server's dashboard (its own alerts) and Head to Head (the racers' alerts). See **Alert notifications** below to get them in chat.
- **Recording rules** (`prometheus/recording.yaml`) pre-compute the per-minute health ratio and per-service CPU and memory, so the 30-day panels stay fast.
- **External probe.** blackbox checks `https://o11y.raashed.com/api/health` through nginx and TLS every 30 seconds, which also reports the certificate's expiry.

## Visitor load tests

Every server dashboard and the fleet overview link to **Run a load test** (`/run/<server>`). A visitor picks a server, presses one button, and lands on the Live Load Test dashboard filtered to their run.

**Head to head** (`/run/compare`) races 2 or 3 servers: `bench/k6/src/match.ts` sends each the same rate (`RUNNER_MATCH_RATE`, default `RUNNER_RATE`) at the same time, one k6 scenario per server, and the visitor lands on the **Head to Head** dashboard. Each server's requests are also tagged as their own run, so a race shows up on the racers' dashboards and in Run Comparison. Since the racers share the machine, a CPU-hungry server can slow the others; the pages say so.

Guard rails keep the VPS healthy:

- **One test at a time** across the whole site; other servers' pages show the running test with a link to watch it.
- **Cooldown** after each test (`RUNNER_COOLDOWN`, default 60s).
- **Per-visitor limit** (`RUNNER_PER_IP_HOURLY`, default 3 per hour), keyed on the client IP nginx sees.
- **Fixed test settings** from `.env` (`RUNNER_SCRIPT`, `RUNNER_RATE`, `RUNNER_MATCH_RATE`, `RUNNER_DURATION`); visitors only choose the servers, from `servers.json`. A race counts as one test.
- **Same-site form posts only**, so other websites cannot start tests (CSRF).

The runner's metrics (`o11y_runner_*`) show whether a test is running on the Fleet Overview, and runs and turned-away requests by reason on **Infrastructure → Observability Stack**. nginx returns 404 for `/metrics`, so neither the runner's nor Grafana's metrics are public.

### Alert notifications

Set `ALERT_WEBHOOK_URL` (and `ALERT_WEBHOOK_TYPE`: `discord`, `slack`, `teams`, `googlechat` or `webhook`) in `infra/.env` and deploy. `deploy.sh` then provisions a contact point and notification policy (`grafana/provisioning/alerting/notifications.yaml`, git-ignored; the URL itself stays in the environment). Alerts are grouped by alert and server and repeat every 4 hours while firing. The three load-test alerts (labelled `kind=load-test`) are muted for notifications, since visitors trip them on purpose; they still show on the dashboards. Remove the URL and deploy again to stop notifications.

The dashboards and `prometheus/targets.json` are generated from `servers.json`:

```sh
python3 infra/grafana/generate.py
```

Edit the generator rather than the JSON; Grafana picks up regenerated files within 30 seconds and Prometheus re-reads `targets.json` on change. `Load Testing/k6-prometheus.json` is Grafana's published dashboard and is left as downloaded.

## Run locally

```sh
cp infra/.env.example infra/.env      # set GRAFANA_ADMIN_PASSWORD; drop the public settings
docker compose -f infra/compose.yaml up -d --build
```

The first build compiles three Rust servers and takes a few minutes. Then open Grafana at <http://localhost:3120>, or load-test from a shell in `bench/k6` with `SERVER=<name> npm run load:grafana`. The runner is at <http://localhost:3121/run/>. Grafana and the runner only share one origin behind nginx, so locally the **Run a load test** buttons and the runner's links back to Grafana don't work; point a local nginx at `nginx/o11y.conf` (with `server_name localhost`) if you need them.

On Docker Desktop the host dashboards describe Docker's Linux VM rather than your machine, since that is where the containers run. On a VPS they describe the VPS itself.

Beyla needs a Linux kernel with BTF (5.8 or newer; check for `/sys/kernel/btf/vmlinux`) and runs privileged in the host's PID namespace to load its eBPF programs. It instruments only processes listening on the ports in `servers.json`: each server listens on that port inside its container too, so a server's address is the same everywhere (`http://fiber:3106` between containers, `http://localhost:3106` from the host). Without eBPF support Beyla exits, and `deploy.sh` fails its Beyla check; locally the "inside the server" panels just stay empty.

## Deploy to the VPS

1. **DNS:** add an `A` record (and `AAAA` if the VPS has IPv6) for `o11y.raashed.com` pointing at the VPS.
2. **Firewall:** allow only SSH, HTTP and HTTPS, for example with ufw:
   ```sh
   sudo ufw default deny incoming
   sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full'
   sudo ufw allow from 172.30.0.0/24 to 172.30.0.1 port 9100 proto tcp comment 'prometheus -> node-exporter'
   sudo ufw enable
   ```
   Every container is published on `127.0.0.1` or not published at all, and node_exporter binds only to the internal Docker bridge, so nothing else is reachable from outside. The third rule lets Prometheus, on that bridge, reach node_exporter; without it the Host dashboard stays empty and `up{job="host"}` is 0.
   **Size:** the stack is meant for a small VPS (built against 2 cores and 4 GB). The logs and traces services have memory caps in `compose.yaml` (Loki 384 MB, Tempo 512 MB, Alloy 256 MB, Beyla 384 MB), Loki keeps 7 days and Tempo 3 days, and Beyla keeps 5% of requests as traces (`TRACE_SAMPLE` in `grafana/generate.py`) while its metrics count every request.
3. **Install Docker Engine** with the Compose plugin ([docs](https://docs.docker.com/engine/install/)).
4. **Clone and configure:**
   ```sh
   git clone https://github.com/rshdhere/load-test.git && cd load-test
   cp infra/.env.example infra/.env
   $EDITOR infra/.env          # strong GRAFANA_ADMIN_PASSWORD, uncomment the public block, size WORKERS and RUNNER_*
   ```
5. **Start everything:**
   ```sh
   docker compose -f infra/compose.yaml up -d --build
   ```
6. **Point nginx at it** and get a certificate (Grafana on `127.0.0.1:3120`, the runner on `127.0.0.1:3121`). If your `nginx.conf` only includes `conf.d/*.conf` and not `sites-enabled`, use `conf.d` as below:
   ```sh
   sudo cp infra/nginx/o11y.conf /etc/nginx/conf.d/o11y.conf
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d o11y.raashed.com
   ```
   <https://o11y.raashed.com> is live once DNS has propagated.
7. **Run load tests from the VPS** so results land in its Prometheus (install k6 and Node first):
   ```sh
   cd bench/k6 && npm ci
   SERVER=axum npm run break:grafana
   ```

### Updating

Pushes to `main` deploy themselves (see below). To update by hand:

```sh
git pull
infra/deploy.sh
```

Deploys from CI pull the images CI already built (`ghcr.io/rshdhere/load-test/<name>:<commit>`), so they take about a minute; `deploy.sh` only builds on the VPS when run by hand without `IMAGE_REGISTRY` and `IMAGE_TAG`, which takes long on a small machine. To deploy a CI-built commit by hand: `docker login ghcr.io` with a token that can read packages, then `IMAGE_REGISTRY=ghcr.io/rshdhere/load-test IMAGE_TAG=<full commit sha> infra/deploy.sh`. `deploy.sh` restarts the stack with `--remove-orphans`, removes this stack's images from earlier deploys (and nothing else), and fails unless Grafana and the runner answer on `127.0.0.1` and Grafana reaches Loki and Tempo. Config files are bind-mounted, so it also reloads what `up` would not: Prometheus, blackbox and Alloy on SIGHUP, Grafana's alert rules through its API, and it restarts Loki, Tempo or Beyla when their config file changed since they started. It never touches the nginx site, because certbot edits the installed copy; after changing `nginx/o11y.conf`, apply the change to `/etc/nginx/conf.d/o11y.conf` yourself. It also refuses to start if another program already holds one of the stack's host ports.

### CI/CD

`.github/workflows/ci.yml` runs on every push and pull request:

- **checks**: dashboards, `prometheus/targets.json` and `beyla/beyla.yaml` match `generate.py`; the compose file, Prometheus, Loki, Alloy and Tempo configs and the nginx site are valid (each checked by its own binary); the runner is gofmt-clean, vets and builds; the k6 scripts typecheck; and the shell scripts pass shellcheck
- **images**: builds all 14 images (cached between runs), runs `servers/conformance.py` against each server that serves the todo API, and on `main` pushes them to GHCR tagged with the commit
- **deploy** (`main` only, after both pass): SSHes into the VPS, logs it into GHCR with the job's short-lived token, resets the checkout to the tested commit, runs `infra/deploy.sh` to pull that commit's images and restart, logs out, then checks <https://o11y.raashed.com> answers

Deploy uses the `production` environment, so it can be gated with required reviewers in the repository settings. It fails with a clear error until these are set in that environment:

| Name              | Kind     | Value                                                              |
| ----------------- | -------- | ------------------------------------------------------------------ |
| `VPS_HOST`        | secret   | The VPS's address                                                  |
| `VPS_USER`        | secret   | The deploy user; must own the checkout and be in the `docker` group |
| `VPS_SSH_KEY`     | secret   | Private key whose public half is in that user's `authorized_keys`   |
| `VPS_KNOWN_HOSTS` | secret   | Output of `ssh-keyscan <host>`, so CI refuses an unexpected host key |
| `VPS_APP_DIR`     | variable | Absolute path of the checkout on the VPS                            |

### What visitors can and cannot do

Anonymous visitors can view every dashboard and start rate-limited load tests, but cannot edit, use Explore, or see the Grafana version. The admin login is the only account, sign-up is disabled, and cookies are secure when `GRAFANA_COOKIE_SECURE=true`. Prometheus, its remote-write endpoint, cAdvisor, node_exporter, the servers and every `/metrics` endpoint are not exposed publicly.

## Settings (`infra/.env`)

| Variable                  | Default                  | Meaning                                                          |
| ------------------------- | ------------------------ | ---------------------------------------------------------------- |
| `GRAFANA_ADMIN_USER`      | `admin`                  | Grafana admin login                                              |
| `GRAFANA_ADMIN_PASSWORD`  | (required)               | Grafana admin password; compose refuses to start without it      |
| `O11Y_URL`                | `http://localhost:3120`  | Grafana's public URL, used in links; `https://o11y.raashed.com` on the VPS |
| `GRAFANA_COOKIE_SECURE`   | `false`                  | Set `true` behind HTTPS                                          |
| `WORKERS`                 | `2`                      | Workers/threads for every server (Rust, Go, Python), so they compete on equal terms |
| `RUNNER_SCRIPT`           | `load`                   | Visitor test: `load` (constant rate) or `break` (ramp to 1000 VUs, 60s) |
| `RUNNER_RATE`             | `500`                    | Requests per second for the `load` script                        |
| `RUNNER_DURATION`         | `30s`                    | Length of the `load` script                                      |
| `RUNNER_COOLDOWN`         | `60s`                    | Pause after each visitor test before the next can start          |
| `RUNNER_MATCH_RATE`       | `RUNNER_RATE`            | Requests per second to each server in a head-to-head race        |
| `RUNNER_PER_IP_HOURLY`    | `3`                      | Visitor tests each client IP can start per hour                  |
| `ALERT_WEBHOOK_URL`       | unset                    | Chat webhook for alert notifications; unset means none           |
| `ALERT_WEBHOOK_TYPE`      | `discord`                | `discord`, `slack`, `teams`, `googlechat` or `webhook`           |
| `PROMETHEUS_RETENTION`    | `30d`                    | How long Prometheus keeps data                                   |

The internal network is fixed to `172.30.0.0/24` because node_exporter binds to its gateway (`172.30.0.1`). If that range is taken on the VPS, change it in `compose.yaml` and the `host` job in `prometheus/prometheus.yaml` together.
