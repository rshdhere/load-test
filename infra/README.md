# infra

Runs all 13 servers in containers next to a self-hosted observability stack, and publishes Grafana at **<https://o11y.raashed.com>**.

```
 visitor ── :443 ── Caddy ──┬── /       Grafana ──── Prometheus ──┬── blackbox ──── 13 servers
                            └── /run/   runner ── k6 ──┘  ▲        ├── cAdvisor   (containers)
                                         │                │        └── node_exporter (host)
                                         └── one test ────┘ remote write
```

| Component                      | Role                                                                                       |
| ------------------------------ | ------------------------------------------------------------------------------------------ |
| 13 server containers           | The systems under test, published on `127.0.0.1:3101-3113` (see `servers.json`)            |
| Prometheus                     | Stores everything: scraped metrics plus k6 results pushed via remote write                 |
| blackbox_exporter              | Probes every server's `/api/v1/health` every 5s (status, latency, availability)            |
| cAdvisor                       | Per-container CPU, memory, threads and network                                             |
| node_exporter                  | Host CPU, memory, disk, network and TCP state                                              |
| Grafana                        | Dashboards and alert rules, all provisioned from files in this folder                      |
| runner                         | The **Run a load test** button: starts one k6 run at a time against one server             |
| Caddy                          | Single entrypoint: Grafana at `/`, the runner at `/run/`; automatic HTTPS on the VPS        |

## Dashboards

| Folder                        | Dashboards                                                                                      |
| ----------------------------- | ----------------------------------------------------------------------------------------------- |
| **Fleet**                     | **Fleet Overview** (home): every server's status, availability, CPU, memory and best results · **Run Comparison**: all load-test runs side by side |
| **Servers** → Rust, Go, Python, TypeScript | One per server: what it is, health, container resources, and its load-test history |
| **Load Testing**              | **Live Load Test** for watching a k6 run as it happens · the official k6 dashboard              |
| **Infrastructure**            | **Host**, **Containers**, **Observability Stack** (scrape targets, TSDB, Grafana and Caddy traffic) |

## Visitor load tests

Every server dashboard and the fleet overview link to **Run a load test** (`/run/<server>`). A visitor picks a server, presses one button, and lands on the Live Load Test dashboard filtered to their run. Guard rails keep the VPS healthy:

- **One test at a time** across the whole site; other servers' pages show the running test with a link to watch it.
- **Cooldown** after each test (`RUNNER_COOLDOWN`, default 60s).
- **Per-visitor limit** (`RUNNER_PER_IP_HOURLY`, default 3 per hour), keyed on the client IP Caddy sees.
- **Fixed test settings** from `.env` (`RUNNER_SCRIPT`, `RUNNER_RATE`, `RUNNER_DURATION`); visitors only choose the server, from `servers.json`.
- **Same-site form posts only**, so other websites cannot start tests (CSRF).

The runner's metrics (`o11y_runner_*`) show whether a test is running on the Fleet Overview, and runs and turned-away requests by reason on **Infrastructure → Observability Stack**. The runner and Caddy do not expose `/metrics` publicly.

Alert rules (**Alerting → Alert rules → Fleet**): server down for 1 minute, load-test error rate above 2%, p99 above 500 ms, and accept-queue overflows. Add a contact point to get notified.

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

The first build compiles three Rust servers and takes a few minutes. Then open <http://localhost:8080> (Grafana plus the runner, as on the VPS) and press **Run a load test**, or load-test from a shell in `bench/k6` with `SERVER=<name> npm run load:grafana`. Grafana alone is also on <http://localhost:3001>, where the run buttons don't work.

On Docker Desktop the host dashboards describe Docker's Linux VM rather than your machine, since that is where the containers run. On a VPS they describe the VPS itself.

## Deploy to the VPS

1. **DNS:** add an `A` record (and `AAAA` if the VPS has IPv6) for `o11y.raashed.com` pointing at the VPS.
2. **Firewall:** allow only SSH, HTTP and HTTPS, for example with ufw:
   ```sh
   sudo ufw default deny incoming
   sudo ufw allow OpenSSH && sudo ufw allow 80,443/tcp && sudo ufw allow 443/udp
   sudo ufw enable
   ```
   Everything except Caddy is published on `127.0.0.1` or not published at all, and node_exporter binds only to the internal Docker bridge, so nothing else is reachable from outside.
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
   Caddy requests the certificate on first start; <https://o11y.raashed.com> is live once DNS has propagated.
6. **Run load tests from the VPS** so results land in its Prometheus (install k6 and Node first):
   ```sh
   cd bench/k6 && npm ci
   SERVER=axum npm run break:grafana
   ```

### Updating

```sh
git pull
docker compose -f infra/compose.yaml up -d --build
```

### What visitors can and cannot do

Anonymous visitors can view every dashboard and start rate-limited load tests, but cannot edit, use Explore, or see the Grafana version. The admin login is the only account, sign-up is disabled, and cookies are secure when `GRAFANA_COOKIE_SECURE=true`. Prometheus, its remote-write endpoint, cAdvisor, node_exporter, the servers and every `/metrics` endpoint are not exposed publicly.

## Settings (`infra/.env`)

| Variable                  | Default                  | Meaning                                                          |
| ------------------------- | ------------------------ | ---------------------------------------------------------------- |
| `GRAFANA_ADMIN_USER`      | `admin`                  | Grafana admin login                                              |
| `GRAFANA_ADMIN_PASSWORD`  | (required)               | Grafana admin password; compose refuses to start without it      |
| `O11Y_SITE`               | `http://localhost`       | What Caddy serves; `o11y.raashed.com` on the VPS turns on automatic HTTPS |
| `O11Y_URL`                | `http://localhost:8080`  | Grafana's public URL, used in links; `https://o11y.raashed.com` on the VPS |
| `CADDY_HTTP_PORT`         | `127.0.0.1:8080`         | Host port for Caddy's HTTP listener; `80` on the VPS             |
| `CADDY_HTTPS_PORT`        | `127.0.0.1:8443`         | Host port for HTTPS (TCP and UDP); `443` on the VPS              |
| `GRAFANA_COOKIE_SECURE`   | `false`                  | Set `true` behind HTTPS                                          |
| `WORKERS`                 | `2`                      | Workers/threads for every server (Rust, Go, Python), so they compete on equal terms |
| `RUNNER_SCRIPT`           | `load`                   | Visitor test: `load` (constant rate) or `break` (ramp to 1000 VUs, 60s) |
| `RUNNER_RATE`             | `500`                    | Requests per second for the `load` script                        |
| `RUNNER_DURATION`         | `30s`                    | Length of the `load` script                                      |
| `RUNNER_COOLDOWN`         | `60s`                    | Pause after each visitor test before the next can start          |
| `RUNNER_PER_IP_HOURLY`    | `3`                      | Visitor tests each client IP can start per hour                  |
| `PROMETHEUS_RETENTION`    | `30d`                    | How long Prometheus keeps data                                   |

The internal network is fixed to `172.30.0.0/24` because node_exporter binds to its gateway (`172.30.0.1`). If that range is taken on the VPS, change it in `compose.yaml` and the `host` job in `prometheus/prometheus.yaml` together.
