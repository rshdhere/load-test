# infra

Runs all 13 servers in containers next to a self-hosted observability stack, and publishes Grafana at **<https://o11y.raashed.com>**.

```
 visitor ── :443 ── nginx ──┬── /       Grafana ──── Prometheus ──┬── blackbox ──── 13 servers
                   (host)   └── /run/   runner ── k6 ──┘  ▲        ├── cAdvisor   (containers)
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
| nginx (on the host)            | Single entrypoint: Grafana at `/`, the runner at `/run/`; HTTPS via certbot. Not part of compose; site config in `nginx/o11y.conf` |

## Dashboards

| Folder                        | Dashboards                                                                                      |
| ----------------------------- | ----------------------------------------------------------------------------------------------- |
| **Fleet**                     | **Fleet Overview** (home): every server's status, availability, CPU, memory and best results · **Run Comparison**: all load-test runs side by side |
| **Servers** → Rust, Go, Python, TypeScript | One per server: what it is, health, container resources, and its load-test history |
| **Load Testing**              | **Live Load Test** for watching a k6 run as it happens · the official k6 dashboard              |
| **Infrastructure**            | **Host**, **Containers**, **Observability Stack** (scrape targets, TSDB, Grafana traffic, visitor tests) |

## Visitor load tests

Every server dashboard and the fleet overview link to **Run a load test** (`/run/<server>`). A visitor picks a server, presses one button, and lands on the Live Load Test dashboard filtered to their run. Guard rails keep the VPS healthy:

- **One test at a time** across the whole site; other servers' pages show the running test with a link to watch it.
- **Cooldown** after each test (`RUNNER_COOLDOWN`, default 60s).
- **Per-visitor limit** (`RUNNER_PER_IP_HOURLY`, default 3 per hour), keyed on the client IP nginx sees.
- **Fixed test settings** from `.env` (`RUNNER_SCRIPT`, `RUNNER_RATE`, `RUNNER_DURATION`); visitors only choose the server, from `servers.json`.
- **Same-site form posts only**, so other websites cannot start tests (CSRF).

The runner's metrics (`o11y_runner_*`) show whether a test is running on the Fleet Overview, and runs and turned-away requests by reason on **Infrastructure → Observability Stack**. nginx returns 404 for `/metrics`, so neither the runner's nor Grafana's metrics are public.

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

The first build compiles three Rust servers and takes a few minutes. Then open Grafana at <http://localhost:3001>, or load-test from a shell in `bench/k6` with `SERVER=<name> npm run load:grafana`. The runner is at <http://localhost:3002/run/>. Grafana and the runner only share one origin behind nginx, so locally the **Run a load test** buttons and the runner's links back to Grafana don't work; point a local nginx at `nginx/o11y.conf` (with `server_name localhost`) if you need them.

On Docker Desktop the host dashboards describe Docker's Linux VM rather than your machine, since that is where the containers run. On a VPS they describe the VPS itself.

## Deploy to the VPS

1. **DNS:** add an `A` record (and `AAAA` if the VPS has IPv6) for `o11y.raashed.com` pointing at the VPS.
2. **Firewall:** allow only SSH, HTTP and HTTPS, for example with ufw:
   ```sh
   sudo ufw default deny incoming
   sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full'
   sudo ufw enable
   ```
   Every container is published on `127.0.0.1` or not published at all, and node_exporter binds only to the internal Docker bridge, so nothing else is reachable from outside.
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
6. **Point nginx at it** and get a certificate (Grafana on `127.0.0.1:3001`, the runner on `127.0.0.1:3002`):
   ```sh
   sudo cp infra/nginx/o11y.conf /etc/nginx/sites-available/o11y.conf
   sudo ln -s /etc/nginx/sites-available/o11y.conf /etc/nginx/sites-enabled/
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

`deploy.sh` rebuilds, restarts the stack with `--remove-orphans`, and fails unless Grafana and the runner answer on `127.0.0.1`. It never touches the nginx site, because certbot edits the installed copy; after changing `nginx/o11y.conf`, apply the change to `/etc/nginx/sites-available/o11y.conf` yourself.

### CI/CD

`.github/workflows/ci.yml` runs on every push and pull request:

- **checks**: dashboards and `prometheus/targets.json` match `generate.py`, the compose file, Prometheus config and nginx site are valid, the runner vets and builds, the k6 scripts typecheck, and the shell scripts pass shellcheck
- **images**: builds all 14 images (cached between runs)
- **deploy** (`main` only, after both pass): SSHes into the VPS, resets the checkout to the tested commit, runs `infra/deploy.sh`, then checks <https://o11y.raashed.com> answers

Deploy uses the `production` environment, so it can be gated with required reviewers in the repository settings. It is skipped until the variable `VPS_APP_DIR` is set, and needs these secrets:

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
| `O11Y_URL`                | `http://localhost:3001`  | Grafana's public URL, used in links; `https://o11y.raashed.com` on the VPS |
| `GRAFANA_COOKIE_SECURE`   | `false`                  | Set `true` behind HTTPS                                          |
| `WORKERS`                 | `2`                      | Workers/threads for every server (Rust, Go, Python), so they compete on equal terms |
| `RUNNER_SCRIPT`           | `load`                   | Visitor test: `load` (constant rate) or `break` (ramp to 1000 VUs, 60s) |
| `RUNNER_RATE`             | `500`                    | Requests per second for the `load` script                        |
| `RUNNER_DURATION`         | `30s`                    | Length of the `load` script                                      |
| `RUNNER_COOLDOWN`         | `60s`                    | Pause after each visitor test before the next can start          |
| `RUNNER_PER_IP_HOURLY`    | `3`                      | Visitor tests each client IP can start per hour                  |
| `PROMETHEUS_RETENTION`    | `30d`                    | How long Prometheus keeps data                                   |

The internal network is fixed to `172.30.0.0/24` because node_exporter binds to its gateway (`172.30.0.1`). If that range is taken on the VPS, change it in `compose.yaml` and the `host` job in `prometheus/prometheus.yaml` together.
