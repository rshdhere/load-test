#!/usr/bin/env python3
"""Generate Grafana dashboards and Prometheus probe targets from infra/servers.json.

    python3 infra/grafana/generate.py

Writes:
  infra/prometheus/targets.json                     blackbox probe targets
  infra/beyla/beyla.yaml                            which processes Beyla instruments, by port
  infra/grafana/dashboards/Fleet/*.json             fleet overview, leaderboard, server and run comparison
  infra/grafana/dashboards/Servers/<Lang>/*.json    one dashboard per server
  infra/grafana/dashboards/Load Testing/live.json   live k6 run view
  infra/grafana/dashboards/Infrastructure/*.json    host, containers, observability stack
  infra/grafana/dashboards/Load Testing/k6-prometheus.json   stock k6 dashboard; only title, tags and links

Grafana re-reads the files within 30 seconds; Prometheus re-reads targets.json on change.
"""

import json
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent
DASHBOARDS = INFRA / "grafana" / "dashboards"
SERVERS = json.loads((INFRA / "servers.json").read_text())
REPO_URL = "https://github.com/rshdhere/load-test"

DS = {"type": "prometheus", "uid": "${datasource}"}
NAMES = "|".join(s["name"] for s in SERVERS)
LANGS = ["Rust", "Go", "Python", "TypeScript"]

# k6 request metrics, minus the one-off setup() probe from pick-live
K6 = 'testid=~"$testid",server=~"$server",group!~"::setup|::teardown"'
K6_RUN = 'testid=~"$testid",server=~"$server"'
SVC = "container_label_com_docker_compose_service"

ERR_TH = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.01},
                                        {"color": "red", "value": 0.02}]}
LAT_TH = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.1},
                                        {"color": "red", "value": 0.5}]}
AVAIL_TH = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0.99},
                                          {"color": "green", "value": 0.999}]}
BLUE = {"mode": "absolute", "steps": [{"color": "blue", "value": None}]}
UP_MAPPING = [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red", "index": 0},
    "1": {"text": "UP", "color": "green", "index": 1}}}]
UP_TH = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 1}]}

# Availability objective for every server's health check, over a rolling window.
# Load tests count against it on purpose: a server that stops answering its
# health check under load is unavailable to everyone else too.
SLO, SLO_WINDOW = 0.999, "30d"
SLO_RATIO = "server:probe_success:ratio_1m"  # recorded in infra/prometheus/recording.yaml
BUDGET_TH = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": 0},
                                           {"color": "green", "value": 0.5}]}
BURN_TH = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 6},
                                         {"color": "red", "value": 14.4}]}
CPU_REC, MEM_REC = "server:container_cpu:rate1m", "server:container_memory:working_set"


# ---------------------------------------------------------------- primitives

class Layout:
    """Hands out panel ids and grid positions, row by row."""

    def __init__(self):
        self.panels, self.y, self.x, self.h, self.id = [], 0, 0, 0, 0

    def add(self, panel, w, h):
        if self.x + w > 24:
            self.newline()
        self.id += 1
        panel["id"] = self.id
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.panels.append(panel)
        self.x += w
        self.h = max(self.h, h)
        return panel

    def newline(self):
        self.y += self.h
        self.x, self.h = 0, 0

    def row(self, title):
        self.newline()
        self.id += 1
        self.panels.append({"type": "row", "id": self.id, "title": title, "collapsed": False, "panels": [],
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.y += 1


def t(expr, legend="", ref="A", instant=False, table=False):
    q = {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "range": not instant,
         "instant": instant}
    if table:
        q["format"] = "table"
    return q


def stat(title, desc, targets, unit="short", th=None, decimals=None, mappings=None, color_mode="background",
         text_mode="value", graph=False):
    d = {"unit": unit, "color": {"mode": "thresholds"}, "thresholds": th or BLUE, "mappings": mappings or []}
    if decimals is not None:
        d["decimals"] = decimals
    targets = targets if isinstance(targets, list) else [targets]
    if any("k6_" in q["expr"] for q in targets):
        # Load-test stats are empty until someone runs a test; say so instead of "No data"
        d["noValue"] = "No tests in range"
    return {"type": "stat", "title": title, "description": desc, "datasource": DS,
            "targets": targets if isinstance(targets, list) else [targets],
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color_mode, "graphMode": "area" if graph else "none",
                        "justifyMode": "center", "textMode": text_mode, "orientation": "auto",
                        "wideLayout": True, "showPercentChange": False}}


def ts(title, desc, targets, unit="short", overrides=None, stack=False, fill=10, min_=None, max_=None,
       soft_max=None, draw="line", th=None, th_style="off", calcs=("mean", "max"), legend="table",
       interval=None):
    custom = {"drawStyle": draw, "lineWidth": 2 if draw == "line" else 1, "fillOpacity": fill,
              "showPoints": "never", "spanNulls": 15000, "axisSoftMin": 0, "gradientMode": "opacity",
              "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "thresholdsStyle": {"mode": th_style}}
    if soft_max is not None:
        custom["axisSoftMax"] = soft_max
    d = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if min_ is not None:
        d["min"] = min_
    if max_ is not None:
        d["max"] = max_
    if th:
        d["thresholds"] = th
    panel = {"type": "timeseries", "title": title, "description": desc, "datasource": DS,
             "targets": targets if isinstance(targets, list) else [targets],
             "fieldConfig": {"defaults": d, "overrides": overrides or []},
             "options": {"legend": {"displayMode": legend, "placement": "bottom", "calcs": list(calcs),
                                    "showLegend": True, "sortBy": "Max", "sortDesc": True},
                         "tooltip": {"mode": "multi", "sort": "desc"}}}
    targets = panel["targets"]
    if interval is None and any(RED in q["expr"] for q in targets):
        # Beyla is scraped every 5s but the datasource interval is 1s (for k6), so without a floor
        # $__rate_interval would be too short to hold two samples
        interval = "15s"
    if interval:
        panel["interval"] = interval
    return panel


def bars(title, desc, targets, unit, th=None, color="continuous-BlPu"):
    return {"type": "bargauge", "title": title, "description": desc, "datasource": DS,
            "targets": targets if isinstance(targets, list) else [targets],
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "thresholds": th or BLUE,
                                         "color": {"mode": "thresholds" if th else color}}, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "valueMode": "color",
                        "showUnfilled": True, "namePlacement": "left", "sizing": "auto",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}}


def run_link(label, path):
    """Link to the load-test runner, which the host's nginx serves next to Grafana. Grafana's router
    swallows plain same-site links and shows its own 404; target="_self" forces a real page load."""
    return f'<a href="{path}" target="_self">{label}</a>'


def text(content):
    return {"type": "text", "title": "", "transparent": True,
            "options": {"mode": "markdown", "content": content, "code": {"language": "plaintext"}}}


def donut(title, desc, targets, unit):
    return {"type": "piechart", "title": title, "description": desc, "datasource": DS,
            "targets": targets if isinstance(targets, list) else [targets],
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "palette-classic"}}, "overrides": []},
            "options": {"pieType": "donut", "displayLabels": ["name"],
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                                   "values": ["value", "percent"]},
                        "tooltip": {"mode": "single", "sort": "none"}}}


def alert_list(title, desc):
    return {"type": "alertlist", "title": title, "description": desc,
            "options": {"viewMode": "list", "groupMode": "default", "groupBy": [], "maxItems": 20,
                        "sortOrder": 1, "dashboardAlerts": False, "alertName": "", "alertInstanceLabelFilter": "",
                        "showInstances": True, "folder": None,
                        "stateFilter": {"firing": True, "pending": True, "noData": False, "normal": False,
                                        "error": True}}}


LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}
# Beyla's request histogram (OpenTelemetry semantic conventions), one series per server
RED = "http_server_request_duration_seconds"


def logs(title, desc, expr):
    return {"type": "logs", "title": title, "description": desc, "datasource": LOKI,
            "targets": [{"datasource": LOKI, "expr": expr, "refId": "A", "queryType": "range"}],
            "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True,
                        "sortOrder": "Descending", "dedupStrategy": "none", "prettifyLogMessage": False}}


def traces(title, desc, query, limit=20):
    """Recent traces from Tempo as a table; clicking a trace ID opens the trace view."""
    return {"type": "table", "title": title, "description": desc, "datasource": TEMPO,
            "targets": [{"datasource": TEMPO, "queryType": "traceql", "query": query, "limit": limit,
                         "tableType": "traces", "refId": "A"}],
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                            "overrides": []},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}


def red_rate(sel, by=""):
    group = f" by ({by})" if by else ""
    return f"sum{group} (rate({RED}_count{{{sel}}}[$__rate_interval]))"


def red_quantile(q, sel, by="", window="$__rate_interval"):
    group = f"le, {by}" if by else "le"
    return f"histogram_quantile({q}, sum by ({group}) (rate({RED}_bucket{{{sel}}}[{window}])))"


def red_errors(sel, by="", window="$__rate_interval"):
    """Share of requests the server answered with a 5xx."""
    group = f" by ({by})" if by else ""
    total = f"sum{group} (rate({RED}_count{{{sel}}}[{window}]))"
    failed = f'sum{group} (rate({RED}_count{{{sel},http_response_status_code=~"5.."}}[{window}]))'
    return f"({failed} or {total} * 0) / {total}"


def route_table(title, desc, sel, by_service=False):
    """Per route (and per server when by_service) as each server saw it: rate, 4xx and 5xx shares, p50, p99."""
    by = ("service_name, " if by_service else "") + "http_request_method, http_route"
    total = f"sum by ({by}) (rate({RED}_count{{{sel}}}[$__range]))"
    share = lambda codes: (f'(sum by ({by}) (rate({RED}_count{{{sel},http_response_status_code=~"{codes}"}}'
                           f'[$__range])) or {total} * 0) / {total}')
    cols = [("A", total, "Avg req/s", "reqps"), ("B", share("4.."), "4xx", "percentunit"),
            ("C", share("5.."), "5xx", "percentunit"),
            ("D", red_quantile(0.5, sel, by, "$__range"), "p50", "s"),
            ("E", red_quantile(0.99, sel, by, "$__range"), "p99", "s")]
    overrides = [by_name("service_name", prop("displayName", "Server"), server_link()),
                 by_name("http_request_method", prop("displayName", "Method"), prop("custom.width", 90)),
                 by_name("http_route", prop("displayName", "Route"))]
    for ref, _, name, unit in cols:
        props = [prop("displayName", name), prop("unit", unit)]
        if unit == "s":
            props += [prop("custom.cellOptions", {"type": "color-text"}), prop("thresholds", LAT_TH)]
        if name == "5xx":
            props += [prop("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                      prop("thresholds", ERR_TH)]
        props += [prop("decimals", 1 if unit == "reqps" else 2)]
        overrides.append(by_name(f"Value #{ref}", *props))
    order = {"http_request_method": 1, "http_route": 2}
    if by_service:
        order = {"service_name": 0, "http_request_method": 2, "http_route": 3}
    return {"type": "table", "title": title, "description": desc, "datasource": DS,
            "targets": [t(e, ref=r, instant=True, table=True) for r, e, _, _ in cols],
            "transformations": [{"id": "merge", "options": {}},
                                {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                               "indexByName": order}},
                                {"id": "sortBy", "options": {"sort": [{"field": "Value #A", "desc": True}]}}],
            "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}},
                                         "thresholds": BLUE},
                            "overrides": overrides},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}


# k6 requests are named by operation (bench/k6/src/workload.ts); setup's probes are not part of the load
K6_OPS = 'name!~"probe|pick-live",group!~"::setup|::teardown"'


def by_name(name, *props):
    return {"matcher": {"id": "byName", "options": name}, "properties": list(props)}


def prop(key, value):
    return {"id": key, "value": value}


def dashed(color=None):
    props = [prop("custom.lineStyle", {"fill": "dash", "dash": [10, 10]}), prop("custom.fillOpacity", 0)]
    if color:
        props.append(prop("color", {"mode": "fixed", "fixedColor": color}))
    return props


# ---------------------------------------------------------------- variables

def v_datasource():
    return {"name": "datasource", "label": "Data source", "type": "datasource", "query": "prometheus",
            "current": {"text": "Prometheus", "value": "prometheus"}, "hide": 2, "refresh": 1}


def v_query(name, label, query, multi=True, include_all=True, hide=0, current_all=True):
    v = {"name": name, "label": label, "type": "query", "datasource": DS, "definition": query,
         "query": {"query": query, "refId": f"{name}-var"}, "refresh": 2, "multi": multi,
         "includeAll": include_all, "allValue": ".*", "sort": 2, "hide": hide, "regex": "", "options": []}
    if current_all:
        v["current"] = {"selected": True, "text": ["All"], "value": ["$__all"]}
    return v


def v_const(name, value):
    return {"name": name, "type": "constant", "query": value, "hide": 2,
            "current": {"text": value, "value": value}}


def dashboard(uid, title, desc, layout, variables, tags, refresh="10s", time_from="now-1h", links=None):
    return {
        "uid": uid, "title": title, "description": desc, "tags": tags,
        "editable": False, "graphTooltip": 1, "refresh": refresh, "liveNow": False,
        "schemaVersion": 41, "version": 1, "timezone": "browser", "fiscalYearStartMonth": 0,
        "time": {"from": time_from, "to": "now"},
        "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
        "templating": {"list": variables},
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                  "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                  "name": "Annotations & Alerts", "type": "dashboard"},
                                 *ANNOTATIONS]},
        "links": links if links is not None else nav_links(),
        "panels": layout.panels,
    }


# Marked on every graph: when load tests ran (k6 or the visitor runner) and when the stack was deployed
ANNOTATIONS = [
    {"name": "Load tests", "datasource": DS, "enable": True, "hide": False, "iconColor": "rgba(255, 152, 48, 0.5)",
     "expr": 'count by (server, testid) (count_over_time(k6_http_reqs_total{group!~"::setup|::teardown"}[10s]))',
     "step": "5s",
     "titleFormat": "Load test on {{server}}", "textFormat": "{{testid}}", "tagKeys": "server",
     "useValueForTime": False},
    {"name": "Deploys", "datasource": DS, "enable": True, "hide": False, "iconColor": "rgba(115, 191, 105, 1)",
     "expr": "max by (commit) (o11y_build_info) unless max by (commit) (o11y_build_info offset 2m)",
     "step": "30s", "titleFormat": "Deployed", "textFormat": "commit {{commit}}", "tagKeys": "commit",
     "useValueForTime": False},
]


def slo_availability(selector, window=SLO_WINDOW):
    return f"avg_over_time({SLO_RATIO}{{{selector}}}[{window}])"


def slo_budget_left(selector):
    """Share of the window's error budget not yet spent; negative once it is overspent."""
    return f"1 - (1 - {slo_availability(selector)}) / {1 - SLO:g}"


def slo_burn(selector, window):
    """How many times faster than sustainable the budget is being spent (1 = exactly on budget)."""
    return f"(1 - {slo_availability(selector, window)}) / {1 - SLO:g}"


def by_language(expr):
    """Sums a per-server series by language, using the labels on the health probes."""
    return (f'sum by (language) ({expr} * on (server) group_left (language) '
            f'max by (server, language) (probe_success{{job="server-health"}}))')


def nav_links():
    return [
        {"title": "Fleet", "type": "link", "url": "/d/fleet", "icon": "apps", "keepTime": True,
         "targetBlank": False, "tooltip": "All servers at a glance"},
        # New tab: Grafana's router would otherwise catch /run/ and show its 404 (see run_link)
        {"title": "Run a load test", "type": "link", "url": "/run/", "icon": "bolt", "keepTime": False,
         "targetBlank": True, "tooltip": "Start a load test against one server (one at a time)"},
        *(tag_dropdown(title, tag) for title, tag in (("Servers", "per-server"), ("Compare", "comparison"),
                                                       ("Load testing", "load-testing"), ("Ops", "ops"))),
    ]


def tag_dropdown(title, tag):
    return {"title": title, "type": "dashboards", "tags": [tag], "asDropdown": True, "keepTime": True,
            "includeVars": False, "targetBlank": False, "icon": "external link"}


# Shown on dashboards that rank servers, so the numbers are not read as a rigorous benchmark
CONDITIONS = (
    "**Test conditions.** Every server, the k6 load generator, Grafana and Prometheus share one small VPS "
    "(alongside unrelated sites), so results are noisy and k6 competes with the server under test for CPU. "
    "Servers use two threads or processes (`WORKERS=2`) where they can; FastAPI, Node and Bun run one process. "
    "Visitors pick each "
    "test's shape (steady, ramp or spike), rate (100-2000 req/s per server), duration and request mix (mostly reads, "
    "write-heavy or health checks), so compare runs with the same settings. Read this as a rough comparison, "
    "not a rigorous ranking.")


# ---------------------------------------------------------------- shared queries

def svc_cpu(selector):
    return (f'sum by (server) (label_replace(rate(container_cpu_usage_seconds_total{{{selector}}}'
            f'[$__rate_interval]), "server", "$1", "{SVC}", "(.*)"))')


def svc_mem(selector):
    return (f'sum by (server) (label_replace(container_memory_working_set_bytes{{{selector}}}, '
            f'"server", "$1", "{SVC}", "(.*)"))')


def k6_peak_rps(by):
    return (f'max_over_time((sum by ({by}) (rate(k6_http_reqs_total{{{K6}}}[5s])))[$__range:1s])')


def k6_typical(stat_, by):
    return f'quantile_over_time(0.5, (max by ({by}) (k6_http_req_duration_{stat_}{{{K6}}}))[$__range:2s])'


def k6_requests(by):
    return f'sum by ({by}) (max_over_time(k6_http_reqs_total{{{K6}}}[$__range]))'


def k6_error_ratio(by):
    total = k6_requests(by)
    failed = f'sum by ({by}) (max_over_time(k6_http_reqs_total{{{K6},expected_response="false"}}[$__range]))'
    return f"({failed} or {total} * 0) / {total}"


def server_link(field="server"):
    return prop("links", [{"title": "Open ${__value.raw} dashboard",
                           "url": "/d/server-${__value.raw}?${__url_time_range}"}])


# ---------------------------------------------------------------- Fleet

def fleet():
    L = Layout()
    n = len(SERVERS)
    probe = 'probe_success{job="server-health"}'

    L.add(text(
        "### Self-hosted observability for 13 HTTP servers\n"
        "Every server below implements the same API ([OpenAPI spec]"
        f"({REPO_URL}/blob/main/servers/openapi.json)) in a different language and framework, runs in its own "
        "container, and is health-checked every 5 seconds. Load tests are driven by k6 and streamed into "
        f"Prometheus. Source: [{REPO_URL.removeprefix('https://')}]({REPO_URL}).\n\n"
        f"**{run_link('▶ Run a load test', '/run/')}** against any server and watch it live, or "
        f"**{run_link('⚔ race 2-3 servers', '/run/compare')}** head to head. One test runs at a time. "
        "See who wins on the **[Leaderboard](/d/leaderboard)**, or put servers next to each other in "
        "**[Compare servers](/d/compare-servers)**."), 24, 4)

    L.row("Fleet health")
    L.add(stat("Servers up", f"Servers whose /api/v1/health check passed on the last probe, out of {n}.",
               t(f"count({probe} == 1) or vector(0)", instant=True), decimals=0,
               th={"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                 {"color": "orange", "value": n - 2},
                                                 {"color": "green", "value": n}]}), 6, 4)
    L.add(stat("Availability", "Share of passing health checks across the fleet over the time range, while monitoring was running.",
               t(f"avg(avg_over_time({probe}[$__range]))", instant=True), "percentunit", AVAIL_TH, 3), 6, 4)
    L.add(stat("Median health latency", "Median time for a health check to complete, across all servers.",
               t(f'quantile(0.5, probe_duration_seconds{{job="server-health"}})', instant=True), "s"), 6, 4)
    L.add(stat("Server CPU", "CPU used by all 13 server containers, as a share of one core (200% = 2 cores).",
               t(f'sum(rate(container_cpu_usage_seconds_total{{{SVC}=~"{NAMES}"}}[1m]))', instant=True),
               "percentunit", decimals=1), 6, 4)
    L.add(stat("Server memory", "Working-set memory of all 13 server containers.",
               t(f'sum(container_memory_working_set_bytes{{{SVC}=~"{NAMES}"}})', instant=True), "bytes"), 6, 4)
    L.add(stat("Load-test requests", "HTTP requests sent by k6 load tests within the time range.",
               t(f'sum(max_over_time(k6_http_reqs_total{{group!~"::setup|::teardown"}}[$__range])) or vector(0)',
                 instant=True), "short"), 6, 4)
    L.add(stat("Load test now", "Whether a visitor-started load test is running, and against which server.",
               t('max by (server) (o11y_runner_busy)', "{{server}}", instant=True), text_mode="value_and_name",
               mappings=[{"type": "value", "options": {
                   "0": {"text": "Idle", "color": "green", "index": 0},
                   "1": {"text": "Running", "color": "orange", "index": 1}}}],
               th={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                 {"color": "orange", "value": 1}]}), 6, 4)
    L.add(stat("Visitor tests", "Load tests started by visitors within the time range.",
               t('sum(increase(o11y_runner_runs_total[$__range])) or vector(0)', instant=True), decimals=0), 6, 4)

    L.row(f"Reliability · {SLO:.1%} of health checks pass, over a rolling {SLO_WINDOW.removesuffix('d')} days")
    L.add(stat("Servers within objective",
               f"Servers whose health checks passed at least {SLO:.1%} of the time over the last "
               f"{SLO_WINDOW.removesuffix('d')} days, out of {n}.",
               t(f"count({slo_availability('')} >= {SLO}) or vector(0)", instant=True), decimals=0,
               th={"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                 {"color": "orange", "value": n - 2},
                                                 {"color": "green", "value": n}]}), 4, 9)
    L.add(stat("Least budget left", "The server closest to missing its objective, and the share of its error "
                                    "budget it has left. Load tests that make a server miss health checks "
                                    "spend its budget.",
               t(f"bottomk(1, {slo_budget_left('')})", "{{server}}", instant=True), "percentunit", BUDGET_TH, 0,
               text_mode="value_and_name"), 4, 9)
    L.add(bars("Error budget left by server", f"Share of each server's {SLO_WINDOW} error budget "
                                              f"({1 - SLO:.1%} of health checks) still unspent. Below zero means "
                                              "the objective is missed.",
               t(f"sort({slo_budget_left('')})", "{{server}}", instant=True), "percentunit", BUDGET_TH), 8, 9)
    L.add(alert_list("Firing alerts", "Alerts firing or about to fire right now. Empty is good."), 8, 9)

    L.row("Servers")
    by = "server"
    cols = [
        ("A", f"max by (server, language, framework) ({probe})", "Status"),
        ("B", f"avg by (server) (avg_over_time({probe}[$__range]))", "Availability"),
        ("C", f'avg by (server) (probe_duration_seconds{{job="server-health"}})', "Health latency"),
        ("D", svc_cpu(f'{SVC}=~"{NAMES}"').replace("[$__rate_interval]", "[1m]"), "CPU (cores)"),
        ("E", svc_mem(f'{SVC}=~"{NAMES}"'), "Memory"),
        ("F", k6_peak_rps(by).replace('server=~"$server"', 'server!=""').replace('testid=~"$testid",', ""),
         "Best peak req/s"),
        ("G", k6_typical("p99", by).replace('server=~"$server"', 'server!=""').replace('testid=~"$testid",', ""),
         "Typical p99"),
        ("H", slo_budget_left(""), "Error budget left"),
    ]
    overrides = [
        by_name("server", prop("displayName", "Server"), server_link()),
        by_name("language", prop("displayName", "Language")),
        by_name("framework", prop("displayName", "Framework"), prop("custom.width", 230)),
        by_name("Value #A", prop("displayName", "Status"), prop("mappings", UP_MAPPING),
                prop("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                prop("thresholds", {"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                                  {"color": "green", "value": 1}]}),
                prop("custom.width", 90)),
        by_name("Value #B", prop("displayName", "Availability"), prop("unit", "percentunit"),
                prop("decimals", 2), prop("custom.cellOptions", {"type": "color-text"}),
                prop("thresholds", AVAIL_TH)),
        by_name("Value #C", prop("displayName", "Health latency"), prop("unit", "s")),
        by_name("Value #D", prop("displayName", "CPU"), prop("unit", "percentunit"), prop("decimals", 2)),
        by_name("Value #E", prop("displayName", "Memory"), prop("unit", "bytes")),
        by_name("Value #F", prop("displayName", "Best peak req/s"), prop("unit", "reqps"), prop("decimals", 0),
                prop("custom.cellOptions", {"type": "gauge", "mode": "gradient", "valueDisplayMode": "text"}),
                prop("color", {"mode": "continuous-BlPu"}), prop("custom.width", 200)),
        by_name("Value #G", prop("displayName", "Typical p99"), prop("unit", "s"),
                prop("custom.cellOptions", {"type": "color-text"}), prop("thresholds", LAT_TH)),
        by_name("Value #H", prop("displayName", "Error budget left"), prop("unit", "percentunit"),
                prop("decimals", 0), prop("custom.cellOptions", {"type": "color-text"}),
                prop("thresholds", BUDGET_TH)),
    ]
    L.add({"type": "table", "title": "All servers",
           "description": "Live status and resource use for every server, plus its best load-test result in "
                          "the time range. Click a server to open its dashboard.",
           "datasource": DS, "targets": [t(e, ref=r, instant=True, table=True) for r, e, _ in cols],
           "transformations": [
               {"id": "merge", "options": {}},
               {"id": "organize", "options": {
                   "excludeByName": {"Time": True},
                   "indexByName": {"server": 0, "language": 1, "framework": 2, "Value #A": 3}}},
               {"id": "sortBy", "options": {"sort": [{"field": "Value #F", "desc": True}]}}],
           "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                                   "filterable": True}, "thresholds": BLUE},
                           "overrides": overrides},
           "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}, 24, 15)

    L.add({"type": "state-timeline", "title": "Availability timeline",
           "description": "Health-check result per server over time. Red bands are outages.",
           "datasource": DS, "targets": [t(f"max by (server) ({probe})", "{{server}}")],
           "fieldConfig": {"defaults": {"mappings": UP_MAPPING, "color": {"mode": "thresholds"},
                                        "thresholds": {"mode": "absolute",
                                                       "steps": [{"color": "red", "value": None},
                                                                 {"color": "green", "value": 1}]},
                                        "custom": {"fillOpacity": 80, "lineWidth": 0}}, "overrides": []},
           "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8, "alignValue": "left",
                       "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}}, 24, 10)

    L.row("Resources and results by server")
    L.add(bars("CPU by server", "CPU each server container is using right now, as a share of one core.",
               t("sort_desc(" + svc_cpu(f'{SVC}=~"{NAMES}"').replace("[$__rate_interval]", "[1m]") + ")",
                 "{{server}}", instant=True), "percentunit"), 8, 12)
    L.add(bars("Memory by server", "Working-set memory per server container.",
               t(f"sort_desc({svc_mem(f'{SVC}=~\"{NAMES}\"')})", "{{server}}", instant=True), "bytes"), 8, 12)
    L.add(bars("Best peak req/s", "Highest request rate each server sustained in a load test within the "
                                  "time range.",
               t("sort_desc(" + k6_peak_rps("server").replace('server=~"$server"', 'server!=""')
                 .replace('testid=~"$testid",', "") + ")", "{{server}}", instant=True), "reqps"), 8, 12)

    L.row("By language")
    L.add(donut("Memory by language", "Working-set memory of each language's servers combined, right now.",
                t(by_language(MEM_REC), "{{language}}", instant=True), "bytes"), 8, 10)
    L.add(donut("CPU by language", "CPU used by each language's servers combined, over the last minute.",
                t(by_language(CPU_REC), "{{language}}", instant=True), "percentunit"), 8, 10)
    L.add(bars("Memory per server, by language", "Average idle footprint: a language's memory divided by its "
                                                 "number of servers.",
               t(f'sort_desc({by_language(MEM_REC)} / count by (language) '
                 f'(probe_success{{job="server-health"}}))', "{{language}}", instant=True), "bytes"), 8, 10)

    return dashboard("fleet", "Fleet Overview",
                     "Health, resources and best load-test results for every server.",
                     L, [v_datasource()], ["overview"], refresh="10s", time_from="now-24h")


# ---------------------------------------------------------------- per server

def server_dashboard(s):
    name = s["name"]
    L = Layout()
    probe = f'probe_success{{job="server-health",server="{name}"}}'
    sel = f'{SVC}="{name}"'

    L.add(text(
        f"## {s['title']}\n"
        f"| Language | Framework | Runtime | Concurrency model | Host port |\n"
        f"|---|---|---|---|---|\n"
        f"| {s['language']} | {s['framework']} | {s['runtime']} | {s['concurrency']} | `{s['port']}` |\n\n"
        f"[Source]({REPO_URL}/tree/main/servers/{name}) · "
        f"[Dockerfile]({REPO_URL}/blob/main/servers/{name}/Dockerfile) · "
        f"Load-test it from a shell: `SERVER={name} npm run break:grafana` in `bench/k6`\n\n"
        f"### {run_link('▶ Run a load test on ' + s['title'], f'/run/{name}')} · "
        f"{run_link('⚔ Race it against others', f'/run/compare?server={name}')}"), 24, 6)

    L.row("Health")
    L.add(stat("Status", "Result of the most recent /api/v1/health check.", t(probe, instant=True),
               mappings=UP_MAPPING, th={"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                                       {"color": "green", "value": 1}]}), 4, 4)
    L.add(stat("Availability", "Share of passing health checks over the time range, while monitoring was running.",
               t(f"avg_over_time({probe}[$__range])", instant=True), "percentunit", AVAIL_TH, 3), 4, 4)
    L.add(stat("Latency", "Time for the latest health check to complete.",
               t(f'probe_duration_seconds{{job="server-health",server="{name}"}}', instant=True), "s"), 4, 4)
    L.add(stat("Uptime", "Time since the container last started.",
               t(f"time() - max(container_start_time_seconds{{{sel}}})", instant=True), "s", decimals=0), 4, 4)
    L.add(stat("CPU", "CPU in use right now, as a share of one core.",
               t(f"sum(rate(container_cpu_usage_seconds_total{{{sel}}}[1m]))", instant=True), "percentunit",
               decimals=1, graph=True), 4, 4)
    L.add(stat("Memory", "Working-set memory right now.",
               t(f"sum(container_memory_working_set_bytes{{{sel}}})", instant=True), "bytes", graph=True), 4, 4)

    days = SLO_WINDOW.removesuffix("d")
    srv = f'server="{name}"'
    L.row(f"Reliability · objective: {SLO:.1%} of health checks pass over {days} days")
    L.add(stat(f"{days}-day availability", f"Share of passing health checks over the last {days} days.",
               t(slo_availability(srv), instant=True), "percentunit", AVAIL_TH, 3), 5, 4)
    L.add(stat("Error budget left", f"Share of the {days}-day error budget ({1 - SLO:.1%} of checks may fail) "
                                    "not yet spent. Below zero means the objective is missed.",
               t(slo_budget_left(srv), instant=True), "percentunit", BUDGET_TH, 0), 5, 4)
    L.add(stat("Burn rate (1h)", "How fast the budget is being spent over the last hour: 1x would use exactly "
                                 f"the whole budget in {days} days. At 14.4x, a single hour spends 2% of it, "
                                 "which raises an alert.",
               t(slo_burn(srv, "1h"), instant=True), "suffix:×", BURN_TH, 1), 5, 4)
    L.add(stat("Restarts", "Times the container restarted after exiting, within the time range.",
               t(f"sum(changes(container_start_time_seconds{{{sel}}}[$__range])) or vector(0)", instant=True),
               decimals=0, th={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                             {"color": "red", "value": 1}]}), 5, 4)
    L.add(stat("OOM kills", "Processes the kernel killed for running out of memory, within the time range.",
               t(f"sum(increase(container_oom_events_total{{{sel}}}[$__range])) or vector(0)", instant=True),
               decimals=0, th={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                             {"color": "red", "value": 1}]}), 4, 4)
    L.add(ts("Error budget burn rate", "Budget spend relative to sustainable, over 1-hour and 6-hour windows. "
                                       "Dashed lines mark the 6x and 14.4x alerting levels.",
             [t(slo_burn(srv, "1h"), "1h window", "A"), t(slo_burn(srv, "6h"), "6h window", "B")],
             "suffix:×", min_=0, soft_max=20, th=BURN_TH, th_style="dashed", fill=0, interval="1m",
             calcs=("max", "last")), 16, 7)
    mine = alert_list("Alerts", f"Alerts firing or pending for {s['title']} right now. Empty is good.")
    mine["options"]["alertInstanceLabelFilter"] = f'{{server="{name}"}}'
    L.add(mine, 8, 7)

    L.row("Container resources")
    L.add(ts("CPU", "CPU as a share of one core (200% = 2 cores). Single-threaded runtimes cap out near 100%.",
             t(f"sum(rate(container_cpu_usage_seconds_total{{{sel}}}[$__rate_interval]))", "cpu"),
             "percentunit", min_=0, interval="15s"), 8, 8)
    L.add(ts("Memory", "Working set (what the kernel counts against limits) and RSS.",
             [t(f"sum(container_memory_working_set_bytes{{{sel}}})", "working set", "A"),
              t(f"sum(container_memory_rss{{{sel}}})", "rss", "B")], "bytes", min_=0, interval="15s"), 8, 8)
    L.add(ts("Threads and processes", "OS threads and processes inside the container.",
             [t(f"sum(container_threads{{{sel}}})", "threads", "A"),
              t(f"sum(container_processes{{{sel}}})", "processes", "B")], "short", fill=0, interval="15s"), 8, 8)
    L.add(ts("Network", "Bytes per second received (above the axis) and sent (below).",
             [t(f"sum(rate(container_network_receive_bytes_total{{{sel}}}[$__rate_interval]))", "received", "A"),
              t(f"sum(rate(container_network_transmit_bytes_total{{{sel}}}[$__rate_interval]))", "sent", "B")],
             "Bps", overrides=[{"matcher": {"id": "byRegexp", "options": "sent"}, "properties": [prop("custom.transform", "negative-Y")]}], interval="15s"), 12, 8)
    L.add(ts("Health check latency", "Duration of each health check, split by phase.",
             t(f'sum by (phase) (probe_http_duration_seconds{{server="{name}"}})', "{{phase}}"), "s",
             stack=True, fill=40, interval="15s"), 12, 8)

    L.row("Load tests")
    L.add(stat("Requests", "Requests sent to this server by the selected runs.",
               t(k6_requests("testid"), "{{testid}}", instant=True)), 6, 4)
    L.add(stat("Peak req/s", "Highest sustained request rate.",
               t(k6_peak_rps("testid"), "{{testid}}", instant=True), "reqps", decimals=0), 6, 4)
    L.add(stat("Error rate", "Failed requests as a share of all requests.",
               t(k6_error_ratio("testid"), "{{testid}}", instant=True), "percentunit", ERR_TH, 2), 6, 4)
    L.add(stat("Typical p99", "Median over the run of each 1s window's p99 latency.",
               t(k6_typical("p99", "testid"), "{{testid}}", instant=True), "s", LAT_TH), 6, 4)
    rps = f'sum by (testid) (rate(k6_http_reqs_total{{{K6}}}[$__rate_interval]))'
    L.add(ts("Throughput vs virtual users", "Request rate (left) against active VUs (right).",
             [t(rps, "{{testid}} req/s", "A"), t(f"max by (testid) (k6_vus{{{K6_RUN}}})", "{{testid}} VUs", "B")],
             "reqps", overrides=[{"matcher": {"id": "byFrameRefID", "options": "B"},
                                  "properties": [prop("unit", "short"), prop("custom.axisPlacement", "right"),
                                                 *dashed()]}]), 12, 9)
    L.add(ts("Latency percentiles", "Per-window request duration.",
             [t(f"max by (testid) (k6_http_req_duration_{p}{{{K6}}})", f"{{{{testid}}}} {p}", r)
              for p, r in (("p50", "A"), ("p95", "B"), ("p99", "C"))], "s", th=LAT_TH, th_style="dashed"), 12, 9)
    L.add(ts("Container CPU during tests", "This server's CPU while k6 was running, to show how close it got "
                                           "to its core limit.",
             t(f"sum(rate(container_cpu_usage_seconds_total{{{sel}}}[$__rate_interval]))", "cpu"), "percentunit",
             min_=0, interval="15s"), 24, 7)

    beyla = f'service_name="{name}"'
    L.row("Inside the server · every request, seen through eBPF")
    L.add(stat("Requests/s", "Requests the server is answering right now, counted by Beyla from inside the "
                             "kernel: load tests, health checks and anything else.",
               t(red_rate(beyla).replace("$__rate_interval", "1m"), instant=True), "reqps", decimals=1,
               graph=True), 6, 4)
    L.add(stat("Server errors", "Share of requests in the time range the server answered with a 5xx.",
               t(red_errors(beyla, window="$__range"), instant=True), "percentunit", ERR_TH, 2), 6, 4)
    L.add(stat("Server p50", "Median time from the request reaching the server to the response leaving it, "
                             "over the time range.",
               t(red_quantile(0.5, beyla, window="$__range"), instant=True), "s", LAT_TH), 6, 4)
    L.add(stat("Server p99", "99th percentile of the same, over the time range.",
               t(red_quantile(0.99, beyla, window="$__range"), instant=True), "s", LAT_TH), 6, 4)
    L.add(ts("Requests by status code", "Responses per second, by HTTP status, as the server sent them.",
             t(red_rate(beyla, "http_response_status_code"), "{{http_response_status_code}}"), "reqps",
             stack=True, fill=40,
             overrides=[{"matcher": {"id": "byRegexp", "options": "^2\\d\\d$"},
                         "properties": [prop("color", {"mode": "fixed", "fixedColor": "green"})]},
                        {"matcher": {"id": "byRegexp", "options": "^5\\d\\d$"},
                         "properties": [prop("color", {"mode": "fixed", "fixedColor": "red"})]}]), 12, 8)
    L.add(ts("Inside the server vs at the client", "Server-side p50 and p99 (Beyla) against the p99 k6 measured "
                                                   "(dashed). The gap is time spent in the network, kernel "
                                                   "queues and k6 itself, not in the server's code.",
             [t(red_quantile(0.5, beyla), "server p50", "A"), t(red_quantile(0.99, beyla), "server p99", "B"),
              t(f'max(k6_http_req_duration_p99{{server="{name}",group!~"::setup|::teardown"}})', "k6 p99", "C")],
             "s", fill=0, overrides=[by_name("k6 p99", *dashed("orange"))]), 12, 8)

    L.row("Todo API · by route and operation")
    L.add(route_table("Routes, inside the server", "Each route as the server saw it over the time range, from "
                                                   "eBPF. 4xx are mostly the load test's deliberate bad requests "
                                                   "and todos dropped by the 1000-todo cap.", beyla), 24, 8)
    L.add(ts("p99 per route, inside the server", "Each route's 99th-percentile time inside the server (Beyla).",
             t(red_quantile(0.99, beyla, "http_request_method, http_route"),
               "{{http_request_method}} {{http_route}}"), "s", fill=0), 12, 8)
    L.add(ts("p99 per operation, at the client", "Each k6 operation's per-window p99, as k6 measured it: "
                                                 "includes the network and queueing.",
             t(f'max by (name) (k6_http_req_duration_p99{{server="{name}",{K6_OPS}}})', "{{name}}"), "s",
             fill=0), 12, 8)

    L.row("Logs and traces")
    L.add(logs("Logs", f"Everything the {s['title']} container writes, shipped to Loki by Alloy.",
               f'{{service="{name}"}}'), 12, 10)
    L.add(traces("Recent traces", f"A {TRACE_SAMPLE:.0%} sample of requests, traced by Beyla through eBPF. "
                                  "Click a trace ID to see where the time went.",
                 f'{{resource.service.name="{name}"}}'), 12, 10)

    variables = [v_datasource(), v_const("server", name),
                 v_query("testid", "Run", f'label_values(k6_vus{{server="{name}"}}, testid)')]
    links = nav_links() + [{"title": "Live run view", "type": "link", "icon": "dashboard",
                            "url": f"/d/load-live?var-server={name}", "keepTime": True, "targetBlank": False,
                            "tooltip": "Open the live load-test view filtered to this server"}]
    return dashboard(f"server-{name}", s["title"],
                     f"{s['title']} ({s['language']}, {s['framework']}): health, container resources and "
                     "load-test history.",
                     L, variables, ["per-server", s["language"].lower()], links=links)


# ---------------------------------------------------------------- live load test

def live():
    L = Layout()
    rps = f'sum by (testid) (rate(k6_http_reqs_total{{{K6}}}[$__rate_interval]))'

    L.row("Summary · selected runs over the time range")
    L.add(stat("Requests", "HTTP requests sent (setup probe excluded).", t(k6_requests("testid"), "{{testid}}",
                                                                            instant=True)), 6, 4)
    L.add(stat("Peak req/s", "Highest sustained request rate.", t(k6_peak_rps("testid"), "{{testid}}",
                                                                   instant=True), "reqps", decimals=0), 6, 4)
    L.add(stat("Error rate", "Failed requests (non-2xx/3xx, timeouts, connection errors). Orange at 1%, red at "
                             "the 2% break-test threshold.",
               t(k6_error_ratio("testid"), "{{testid}}", instant=True), "percentunit", ERR_TH, 2), 6, 4)
    L.add(stat("Typical p95", "Median over the run of each 1s window's p95.",
               t(k6_typical("p95", "testid"), "{{testid}}", instant=True), "s", LAT_TH), 6, 4)
    L.add(stat("Typical p99", "Median over the run of each 1s window's p99.",
               t(k6_typical("p99", "testid"), "{{testid}}", instant=True), "s", LAT_TH), 6, 4)
    L.add(stat("Worst p99", "Highest p99 in any single 1s window, usually warm-up.",
               t(f"max by (testid) (max_over_time(k6_http_req_duration_p99{{{K6}}}[$__range]))", "{{testid}}",
                 instant=True), "s", LAT_TH), 6, 4)
    L.add(stat("Peak VUs", "Most virtual users active at once.",
               t(f"max by (testid) (max_over_time(k6_vus{{{K6_RUN}}}[$__range]))", "{{testid}}", instant=True)),
          6, 4)
    dropped = f"sum by (testid) (max_over_time(k6_dropped_iterations_total{{{K6_RUN}}}[$__range]))"
    L.add(stat("Dropped", "Iterations k6 could not start on schedule because the target was too slow.",
               t(f"{dropped} or {k6_requests('testid')} * 0", "{{testid}}", instant=True),
               th={"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 1},
                                                 {"color": "red", "value": 1000}]}), 6, 4)

    L.row("Throughput")
    L.add(ts("Requests per second vs virtual users", "Throughput that flattens while VUs keep climbing means the "
                                                     "server is saturated.",
             [t(rps, "{{testid}} req/s", "A"), t(f"max by (testid) (k6_vus{{{K6_RUN}}})", "{{testid}} VUs", "B")],
             "reqps", overrides=[{"matcher": {"id": "byFrameRefID", "options": "B"},
                                  "properties": [prop("unit", "short"), prop("custom.axisPlacement", "right"),
                                                 prop("custom.axisLabel", "VUs"), *dashed()]}]), 16, 9)
    L.add(ts("Responses by status code", "Status 0 means no response (timeout or connection error).",
             t(f"sum by (testid, status) (rate(k6_http_reqs_total{{{K6}}}[$__rate_interval]))",
               "{{testid}} {{status}}"), "reqps", stack=True, fill=40,
             overrides=[{"matcher": {"id": "byRegexp", "options": ".* 2\\d\\d$"},
                         "properties": [prop("color", {"mode": "fixed", "fixedColor": "green"})]},
                        {"matcher": {"id": "byRegexp", "options": ".* ([45]\\d\\d|0)$"},
                         "properties": [prop("color", {"mode": "fixed", "fixedColor": "red"})]}]), 8, 9)

    L.row("Latency")
    L.add(ts("Response time percentiles", "Per-window request duration. Dashed lines mark 100 ms and 500 ms.",
             [t(f"max by (testid) (k6_http_req_duration_{p}{{{K6}}})", f"{{{{testid}}}} {p}", r)
              for p, r in (("p50", "A"), ("p90", "B"), ("p95", "C"), ("p99", "D"), ("max", "E"))],
             "s", th=LAT_TH, th_style="dashed",
             overrides=[{"matcher": {"id": "byRegexp", "options": ".* max$"},
                         "properties": [prop("custom.lineStyle", {"fill": "dot"}), prop("custom.lineWidth", 1),
                                        prop("custom.fillOpacity", 0)]}]), 16, 11)
    L.add(ts("Request phases (p95)", "'waiting' is time to first byte; high 'blocked' means k6 waited for a free "
                                     "connection.",
             [t(f"max by (testid) (k6_http_req_{p}_p95{{{K6}}})", f"{{{{testid}}}} {p}", r)
              for p, r in (("blocked", "A"), ("connecting", "B"), ("sending", "C"), ("waiting", "D"),
                           ("receiving", "E"))], "s"), 8, 11)

    L.row("Errors and saturation")
    L.add(ts("Error rate", "Failed requests as a share of all requests, per window.",
             t(f'(sum by (testid) (rate(k6_http_reqs_total{{{K6},expected_response="false"}}[$__rate_interval])) '
               f"or {rps} * 0) / {rps}", "{{testid}}"),
             "percentunit", min_=0, soft_max=0.05, th=ERR_TH, th_style="line+area"), 8, 8)
    L.add(ts("Dropped iterations", "Iterations per second k6 skipped because no VU was free in time.",
             t(f"sum by (testid) (rate(k6_dropped_iterations_total{{{K6_RUN}}}[$__rate_interval])) or {rps} * 0",
               "{{testid}}"), draw="bars", fill=60), 8, 8)
    L.add(ts("Check pass rate", "Share of passing k6 checks, per check.",
             t(f"avg by (testid, check) (k6_checks_rate{{{K6_RUN}}})", "{{testid}} {{check}}"),
             "percentunit", min_=0, max_=1), 8, 8)

    L.row("Server under test")
    L.add(ts("Server container CPU", "CPU of the selected servers' containers, as a share of one core.",
             t(svc_cpu(f'{SVC}=~"$server",{SVC}=~"{NAMES}"'), "{{server}}"), "percentunit", min_=0, interval="15s"), 8, 8)
    L.add(ts("Server container memory", "Working-set memory of the selected servers' containers.",
             t(svc_mem(f'{SVC}=~"$server",{SVC}=~"{NAMES}"'), "{{server}}"), "bytes", min_=0, interval="15s"), 8, 8)
    L.add(ts("p99 inside the server", "The servers' own 99th-percentile response time, from eBPF (Beyla). "
                                      "Compare with k6's p99 above: the gap is network and queueing.",
             t(red_quantile(0.99, 'service_name=~"$server"', "service_name"), "{{service_name}}"), "s",
             th=LAT_TH, th_style="dashed"), 8, 8)
    L.add(ts("Host CPU per core", "A single core pinned near 100% points to a single-threaded server.",
             t('1 - avg by (cpu) (rate(node_cpu_seconds_total{job="host",mode="idle"}[$__rate_interval]))',
               "cpu {{cpu}}"), "percentunit", min_=0, max_=1, fill=0, interval="15s"), 24, 7)

    return dashboard("load-live", "Live Load Test",
                     "Throughput, latency and errors for k6 runs as they stream in, next to the server's own "
                     "container metrics.",
                     L, [v_datasource(), v_query("server", "Server", "label_values(k6_vus, server)"),
                         v_query("testid", "Run", 'label_values(k6_vus{server=~"$server"}, testid)')],
                     ["live", "load-testing"], refresh="5s", time_from="now-15m")


def compare():
    L = Layout()
    L.add(text(CONDITIONS), 24, 3)
    by = "testid, server, script, shape, mix, rate"
    cols = [
        ("A", k6_requests(by), "Requests", "short"),
        ("B", k6_peak_rps(by), "Peak req/s", "reqps"),
        ("C", k6_error_ratio(by), "Error rate", "percentunit"),
        ("E", k6_typical("p95", by), "Typical p95", "s"),
        ("F", k6_typical("p99", by), "Typical p99", "s"),
        ("G", f"max by ({by}) (max_over_time(k6_http_req_duration_p99{{{K6}}}[$__range]))", "Worst p99", "s"),
        ("H", f"max by ({by}) (max_over_time(k6_vus{{{K6_RUN}}}[$__range]))", "Peak VUs", "short"),
    ]
    overrides = [
        by_name("testid", prop("displayName", "Run"),
                prop("links", [{"title": "Open run in the live view",
                                "url": "/d/load-live?var-testid=${__value.raw}&${__url_time_range}"}])),
        by_name("server", prop("displayName", "Server"), server_link()),
        by_name("script", prop("displayName", "Script")),
        by_name("shape", prop("displayName", "Shape"), prop("custom.width", 80)),
        by_name("mix", prop("displayName", "Mix"), prop("custom.width", 80)),
        by_name("rate", prop("displayName", "Peak rate"), prop("unit", "reqps"), prop("custom.width", 100)),
    ]
    for ref, _, name, unit in cols:
        p = [prop("displayName", name), prop("unit", unit)]
        if unit == "s":
            p += [prop("custom.cellOptions", {"type": "color-text"}), prop("thresholds", LAT_TH)]
        if name == "Error rate":
            p += [prop("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                  prop("thresholds", ERR_TH), prop("decimals", 2)]
        if name == "Peak req/s":
            p += [prop("custom.cellOptions", {"type": "gauge", "mode": "gradient", "valueDisplayMode": "text"}),
                  prop("color", {"mode": "continuous-BlPu"}), prop("decimals", 0), prop("custom.width", 200)]
        overrides.append(by_name(f"Value #{ref}", *p))

    L.add({"type": "table", "title": "Runs side by side",
           "description": "One row per run. 'Typical' is the median of 1s windows; 'Worst' is the slowest window. "
                          "Click a run for the live view or a server for its dashboard.",
           "datasource": DS, "targets": [t(e, ref=r, instant=True, table=True) for r, e, _, _ in cols],
           "transformations": [{"id": "merge", "options": {}},
                               {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                              "indexByName": {"testid": 0, "server": 1,
                                                                              "script": 2, "shape": 3,
                                                                              "mix": 4, "rate": 5}}},
                               {"id": "sortBy", "options": {"sort": [{"field": "Value #B", "desc": True}]}}],
           "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                                   "filterable": True}, "thresholds": BLUE},
                           "overrides": overrides},
           "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}, 24, 13)
    L.add(bars("Peak throughput", "Highest sustained request rate per run.",
               t(f"sort_desc({k6_peak_rps('testid')})", "{{testid}}", instant=True), "reqps"), 12, 12)
    L.add(bars("Typical p99 latency", "Median over the run of each window's p99. Lower is better.",
               t(f"sort({k6_typical('p99', 'testid')})", "{{testid}}", instant=True), "s", th=LAT_TH), 12, 12)

    return dashboard("load-compare", "Run Comparison", "Every k6 run in the time range, side by side.",
                     L, [v_datasource(), v_query("server", "Server", "label_values(k6_vus, server)"),
                         v_query("testid", "Run", 'label_values(k6_vus{server=~"$server"}, testid)')],
                     ["comparison", "load-testing"], refresh="30s", time_from="now-7d")


def leaderboard():
    L = Layout()
    load = 'group!~"::setup|::teardown"'
    probe = 'probe_success{job="server-health"}'
    rps = f"sum by (server) (rate(k6_http_reqs_total{{{load}}}[1m]))"
    under_load = f"and on (server) ({rps} > 0)"
    cpu = f'{CPU_REC}{{server=~"{NAMES}"}}'
    mem = f'{MEM_REC}{{server=~"{NAMES}"}}'
    # Requests served per CPU-second the container spent while k6 was hitting it
    per_core = f"sum_over_time(({rps})[$__range:15s]) / sum_over_time(({cpu} {under_load})[$__range:15s])"
    peak_rps = (f"max_over_time((sum by (server) (rate(k6_http_reqs_total{{{load}}}[5s])))"
                f"[$__range:1s])")
    peak_mem = f"max_over_time(({mem} {under_load})[$__range:15s])"
    per_mb = f"{peak_rps} / ({peak_mem} / 1e6)"
    p99 = (f'quantile_over_time(0.5, (max by (server) (k6_http_req_duration_p99{{{load},script="load",'
           f'rate="$rate",shape!~"ramp|spike",mix!~"writes|health"}}))[$__range:2s])')

    L.add(text(CONDITIONS), 24, 3)
    cols = [
        ("A", f"max by (server, language, framework) ({probe})"),
        ("B", per_core), ("C", per_mb), ("D", p99), ("E", peak_rps), ("F", peak_mem),
    ]
    gauge = [prop("custom.cellOptions", {"type": "gauge", "mode": "gradient", "valueDisplayMode": "text"}),
             prop("color", {"mode": "continuous-BlPu"}), prop("custom.width", 200)]
    overrides = [
        by_name("server", prop("displayName", "Server"), server_link()),
        by_name("language", prop("displayName", "Language")),
        by_name("framework", prop("displayName", "Framework"), prop("custom.width", 230)),
        by_name("Value #B", prop("displayName", "Req/s per core"), prop("unit", "reqps"), prop("decimals", 0),
                *gauge),
        by_name("Value #C", prop("displayName", "Req/s per MB"), prop("unit", "reqps"), prop("decimals", 1),
                *gauge),
        by_name("Value #D", prop("displayName", "p99 at $rate req/s"), prop("unit", "s"),
                prop("custom.cellOptions", {"type": "color-text"}), prop("thresholds", LAT_TH)),
        by_name("Value #E", prop("displayName", "Peak req/s"), prop("unit", "reqps"), prop("decimals", 0)),
        by_name("Value #F", prop("displayName", "Peak memory under load"), prop("unit", "bytes")),
    ]
    L.add({"type": "table", "title": "Leaderboard",
           "description": "Servers ranked by requests served per CPU core, over every load test in the time "
                          "range. Req/s per MB divides peak throughput by peak working-set memory under load. "
                          "The p99 column only counts steady, mostly-reads runs at the selected rate, so it is like for like.",
           "datasource": DS, "targets": [t(e, ref=r, instant=True, table=True) for r, e in cols],
           "transformations": [
               {"id": "merge", "options": {}},
               {"id": "filterByValue", "options": {
                   "type": "include", "match": "any",
                   "filters": [{"fieldName": "Value #E", "config": {"id": "greater", "options": {"value": 0}}}]}},
               {"id": "organize", "options": {
                   "excludeByName": {"Time": True, "Value #A": True},
                   "indexByName": {"server": 0, "language": 1, "framework": 2, "Value #B": 3, "Value #C": 4,
                                   "Value #D": 5, "Value #E": 6, "Value #F": 7}}},
               {"id": "sortBy", "options": {"sort": [{"field": "Value #B", "desc": True}]}}],
           "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"},
                                                   "filterable": True}, "thresholds": BLUE},
                           "overrides": overrides},
           "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}, 24, 15)

    L.add(bars("Req/s per CPU core", "Requests served per CPU-second while under load. Higher is better.",
               t(f"sort_desc({per_core})", "{{server}}", instant=True), "reqps"), 8, 12)
    L.add(bars("Req/s per MB of memory", "Peak throughput divided by peak memory under load. Higher is better.",
               t(f"sort_desc({per_mb})", "{{server}}", instant=True), "reqps"), 8, 12)
    L.add(bars("p99 at $rate req/s", "Median over `load` runs at the selected rate of each 1s window's p99. "
                                      "Lower is better.",
               t(f"sort({p99})", "{{server}}", instant=True), "s", th=LAT_TH), 8, 12)

    rate = v_query("rate", "Fixed rate (req/s)", 'label_values(k6_vus{script="load"}, rate)', multi=False,
                   include_all=False, current_all=False)
    rate["sort"] = 3
    rate["current"] = {"text": "500", "value": "500"}
    return dashboard("leaderboard", "Leaderboard",
                     "The 13 servers ranked by throughput per CPU core and per MB of memory, plus tail latency "
                     "at a fixed request rate.",
                     L, [v_datasource(), rate], ["overview", "comparison", "load-testing"], refresh="1m",
                     time_from="now-7d")


def compare_servers():
    L = Layout()
    load = 'group!~"::setup|::teardown"'
    one = '{job="server-health",server="$server"}'
    sel = f'{SVC}=~"$server"'

    L.row("At a glance · one column per selected server")
    glance = stat("$server", "Health right now, availability and best load-test results over the time range, "
                             "and resources right now.",
                  [t(f"probe_success{one}", "Status", "A", instant=True),
                   t(f"avg_over_time(probe_success{one}[$__range])", "Availability", "B", instant=True),
                   t(f'max_over_time((sum(rate(k6_http_reqs_total{{{load},server="$server"}}[5s])))[$__range:1s])',
                     "Peak req/s", "C", instant=True),
                   t(f'quantile_over_time(0.5, (max(k6_http_req_duration_p99{{{load},server="$server"}}))'
                     f'[$__range:2s])', "Typical p99", "D", instant=True),
                   t(f'sum(rate(container_cpu_usage_seconds_total{{{SVC}="$server"}}[1m]))', "CPU", "E",
                     instant=True),
                   t(f'sum(container_memory_working_set_bytes{{{SVC}="$server"}})', "Memory", "F", instant=True)],
                  text_mode="value_and_name")
    glance["options"]["orientation"] = "horizontal"
    glance["fieldConfig"]["overrides"] = [
        {"matcher": {"id": "byFrameRefID", "options": "A"},
         "properties": [prop("mappings", UP_MAPPING),
                        prop("thresholds", {"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                                          {"color": "green", "value": 1}]})]},
        {"matcher": {"id": "byFrameRefID", "options": "B"},
         "properties": [prop("unit", "percentunit"), prop("decimals", 3), prop("thresholds", AVAIL_TH)]},
        {"matcher": {"id": "byFrameRefID", "options": "C"}, "properties": [prop("unit", "reqps"),
                                                                           prop("decimals", 0)]},
        {"matcher": {"id": "byFrameRefID", "options": "D"}, "properties": [prop("unit", "s"),
                                                                           prop("thresholds", LAT_TH)]},
        {"matcher": {"id": "byFrameRefID", "options": "E"}, "properties": [prop("unit", "percentunit"),
                                                                           prop("decimals", 1)]},
        {"matcher": {"id": "byFrameRefID", "options": "F"}, "properties": [prop("unit", "bytes")]},
    ]
    glance.update({"repeat": "server", "repeatDirection": "h", "maxPerRow": 4,
                   "links": [{"title": "Open the ${server} dashboard", "url": "/d/server-${server}?${__url_time_range}"}]})
    L.add(glance, 24, 10)

    L.row("Load tests · selected servers overlaid")
    L.add(ts("Requests per second", "k6 request rate against each selected server.",
             t(f'sum by (server) (rate(k6_http_reqs_total{{{load},server=~"$server"}}[$__rate_interval]))',
               "{{server}}"), "reqps"), 12, 9)
    L.add(ts("p99 latency", "Per-window p99 request duration for each selected server.",
             t(f'max by (server) (k6_http_req_duration_p99{{{load},server=~"$server"}})', "{{server}}"), "s",
             th=LAT_TH, th_style="dashed"), 12, 9)

    L.row("Resources · selected servers overlaid")
    L.add(ts("CPU", "CPU as a share of one core (200% = 2 cores).", t(svc_cpu(sel), "{{server}}"),
             "percentunit", min_=0, interval="15s"), 8, 8)
    L.add(ts("Memory", "Working-set memory.", t(svc_mem(sel), "{{server}}"), "bytes", min_=0, interval="15s"),
          8, 8)
    L.add(ts("Health check latency", "Time for each /api/v1/health check to complete.",
             t('max by (server) (probe_duration_seconds{job="server-health",server=~"$server"})', "{{server}}"),
             "s", fill=0, interval="15s"), 8, 8)

    names = [s["name"] for s in SERVERS]
    picked = ["go", "bun"]
    server = {"name": "server", "label": "Servers", "type": "custom", "query": ",".join(names),
              "multi": True, "includeAll": False, "hide": 0,
              "current": {"selected": True, "text": picked, "value": picked},
              "options": [{"selected": n in picked, "text": n, "value": n} for n in names]}
    return dashboard("compare-servers", "Compare servers",
                     "Pick two or more servers to see their health, load-test results and resources side by side.",
                     L, [v_datasource(), server], ["overview", "comparison", "resources"], refresh="30s",
                     time_from="now-24h")


def head_to_head():
    """One race from the runner's /run/compare page: 2-3 servers at the same rate, at the same time."""
    L = Layout()
    M = 'match="$match",group!~"::setup|::teardown"'
    rps = f"sum by (server) (rate(k6_http_reqs_total{{{M}}}[$__rate_interval]))"
    requests = f"sum by (server) (max_over_time(k6_http_reqs_total{{{M}}}[$__range]))"
    failed = f'sum by (server) (max_over_time(k6_http_reqs_total{{{M},expected_response="false"}}[$__range]))'
    errors = f"({failed} or {requests} * 0) / {requests}"
    typical = lambda p: (f"quantile_over_time(0.5, (max by (server) (k6_http_req_duration_{p}{{{M}}}))"
                         f"[$__range:2s])")
    racing = f"and on (server) (sum by (server) (rate(k6_http_reqs_total{{{M}}}[1m])) > 0)"
    cpu = f'{CPU_REC}{{server=~"$server"}}'
    avg_cpu = f"avg_over_time(({cpu} {racing})[$__range:15s])"
    per_core = (f"sum_over_time((sum by (server) (rate(k6_http_reqs_total{{{M}}}[1m])))[$__range:15s]) / "
                f"sum_over_time(({cpu} {racing})[$__range:15s])")
    peak_mem = f'max_over_time(({MEM_REC}{{server=~"$server"}} {racing})[$__range:15s])'

    L.add(text(
        "### ⚔ Head to head\n"
        "Every server in this race got the **same request rate at the same moment**, from one k6 process on the "
        "same machine, so they also competed for CPU: a hungry server can slow its rivals. Pick an earlier race "
        "from **Race** above (widen the time range to when it ran). "
        f"**{run_link('Start a new race', '/run/compare')}**."), 24, 3)

    L.row("Winners")
    for title, desc, expr, unit, th in (
        ("Lowest p99", "Lowest typical p99 latency: the median of each 1s window's p99.",
         f"bottomk(1, {typical('p99')})", "s", LAT_TH),
        ("Lowest median", "Lowest typical median latency.", f"bottomk(1, {typical('p50')})", "s", LAT_TH),
        ("Fewest errors", "Lowest share of failed requests.", f"bottomk(1, {errors})", "percentunit", ERR_TH),
        ("Most efficient", "Most requests served per CPU-second during the race.",
         f"topk(1, {per_core})", "reqps", BLUE),
    ):
        L.add(stat(title, desc, t(expr, "{{server}}", instant=True), unit, th, text_mode="value_and_name"), 6, 5)

    L.row("Scoreboard")
    cols = [
        ("A", requests, "Requests", "short"),
        ("B", errors, "Error rate", "percentunit"),
        ("C", typical("p50"), "Typical median", "s"),
        ("D", typical("p95"), "Typical p95", "s"),
        ("E", typical("p99"), "Typical p99", "s"),
        ("F", f"max by (server) (max_over_time(k6_http_req_duration_p99{{{M}}}[$__range]))", "Worst p99", "s"),
        ("G", avg_cpu, "Avg CPU", "percentunit"),
        ("H", per_core, "Req/s per core", "reqps"),
        ("I", peak_mem, "Peak memory", "bytes"),
    ]
    overrides = [by_name("server", prop("displayName", "Server"), server_link(), prop("custom.width", 120))]
    for ref, _, name, unit in cols:
        p = [prop("displayName", name), prop("unit", unit)]
        if unit == "s":
            p += [prop("custom.cellOptions", {"type": "color-text"}), prop("thresholds", LAT_TH)]
        if name == "Error rate":
            p += [prop("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                  prop("thresholds", ERR_TH), prop("decimals", 2)]
        if name == "Req/s per core":
            p += [prop("custom.cellOptions", {"type": "gauge", "mode": "gradient", "valueDisplayMode": "text"}),
                  prop("color", {"mode": "continuous-BlPu"}), prop("decimals", 0)]
        overrides.append(by_name(f"Value #{ref}", *p))
    L.add({"type": "table", "title": "Scoreboard",
           "description": "Every racer side by side, fastest typical p99 first. Click a server for its dashboard.",
           "datasource": DS, "targets": [t(e, ref=r, instant=True, table=True) for r, e, _, _ in cols],
           "transformations": [{"id": "merge", "options": {}},
                               {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                              "indexByName": {"server": 0}}},
                               {"id": "sortBy", "options": {"sort": [{"field": "Value #E"}]}}],
           "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}},
                                        "thresholds": BLUE},
                           "overrides": overrides},
           "options": {"showHeader": True, "cellHeight": "md", "footer": {"show": False}}}, 24, 6)

    ops = {"type": "table", "title": "Per operation",
           "description": "Each racer's typical p99 per operation (median of 1s windows), as k6 measured it.",
           "datasource": DS,
           "targets": [t(f"quantile_over_time(0.5, (max by (server, name) (k6_http_req_duration_p99{{{M},{K6_OPS}}}))"
                         f"[$__range:2s])", ref="A", instant=True, table=True)],
           "transformations": [{"id": "groupingToMatrix", "options": {"columnField": "server", "rowField": "name",
                                                                      "valueField": "Value"}}],
           "fieldConfig": {"defaults": {"unit": "s", "thresholds": LAT_TH,
                                        "custom": {"align": "auto", "cellOptions": {"type": "color-text"}}},
                           "overrides": [by_name("name\\server", prop("displayName", "Operation"),
                                                 prop("unit", "none"), prop("custom.width", 300))]},
           "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}
    L.add(ops, 24, 8)

    L.row("The race, second by second")
    L.add(ts("Requests per second", "Each server's throughput. A line that falls below the others means that "
                                    "server could not keep up and k6 dropped requests.",
             t(rps, "{{server}}"), "reqps"), 12, 9)
    L.add(ts("p99 latency", "Each server's per-window p99. Dashed lines mark 100 ms and 500 ms.",
             t(f"max by (server) (k6_http_req_duration_p99{{{M}}})", "{{server}}"), "s", th=LAT_TH,
             th_style="dashed"), 12, 9)
    L.add(ts("Median latency", "Each server's per-window median.",
             t(f"max by (server) (k6_http_req_duration_p50{{{M}}})", "{{server}}"), "s"), 12, 8)
    L.add(ts("Error rate", "Failed requests as a share of each server's requests.",
             t(f'(sum by (server) (rate(k6_http_reqs_total{{{M},expected_response="false"}}[$__rate_interval])) '
               f"or {rps} * 0) / {rps}", "{{server}}"),
             "percentunit", min_=0, soft_max=0.05, th=ERR_TH, th_style="line+area"), 12, 8)
    L.add(ts("p99 inside the server", "Each racer's own 99th-percentile response time, measured in the kernel "
                                      "by Beyla. Compare with k6's p99 above: the gap is network and queueing.",
             t(red_quantile(0.99, 'service_name=~"$server"', "service_name"), "{{service_name}}"), "s",
             th=LAT_TH, th_style="dashed"), 12, 8)
    L.add(ts("Server errors (5xx)", "Share of each racer's responses that were server errors, from eBPF.",
             t(red_errors('service_name=~"$server"', "service_name"), "{{service_name}}"), "percentunit", min_=0,
             soft_max=0.05, th=ERR_TH, th_style="line+area"), 12, 8)
    L.add(ts("Container CPU", "Each server's CPU as a share of one core (200% = 2 cores).",
             t(svc_cpu(f'{SVC}=~"$server"'), "{{server}}"), "percentunit", min_=0, interval="5s"), 12, 8)
    L.add(ts("Container memory", "Each server's working-set memory.",
             t(svc_mem(f'{SVC}=~"$server"'), "{{server}}"), "bytes", min_=0, interval="5s"), 12, 8)

    L.row("Alerts")
    racers = alert_list("Alerts on these servers", "Alerts firing or pending for the servers in this race, for "
                                                   "example high error rates or latency under load.")
    racers["options"]["alertInstanceLabelFilter"] = '{server=~"${server:regex}"}'
    L.add(racers, 24, 6)

    match = v_query("match", "Race", 'label_values(k6_http_reqs_total{script="match"}, match)', multi=False,
                    include_all=False, current_all=False)
    match["sort"] = 2  # newest first: ids end in a UTC timestamp
    server = v_query("server", "Servers", 'label_values(k6_http_reqs_total{match="$match"}, server)', hide=2)
    # "All" must expand to this race's servers, not ".*", or the container panels and the alert list
    # would match every container and every alert
    server["allValue"] = None
    return dashboard("match", "Head to Head",
                     "Two or three servers raced at the same rate at the same time: winners, a scoreboard and "
                     "the race second by second.",
                     L, [v_datasource(), match, server], ["live", "comparison", "load-testing"], refresh="5s",
                     time_from="now-15m",
                     links=nav_links() + [{"title": "Start a race", "type": "link", "icon": "bolt",
                                           "url": "/run/compare", "keepTime": False, "targetBlank": True,
                                           "tooltip": "Race 2-3 servers against each other"}])


def internals():
    """The three signals from the servers' side: RED metrics and traces from Beyla, logs from Loki."""
    L = Layout()
    sel = 'service_name=~"$service"'
    L.add(text(
        "### Inside the servers\n"
        "k6 shows what a client sees. This page shows what the **servers** saw: every request counted by "
        "[Beyla](https://grafana.com/oss/beyla-ebpf/) through eBPF, without touching their code, a "
        f"{TRACE_SAMPLE:.0%} sample of requests as traces in Tempo, and every container's logs in Loki. Click a "
        "trace ID for the trace, or a log line's trace link to jump from logs to traces."), 24, 3)

    L.row("Requests, errors and duration (RED)")
    cols = [
        ("A", f"sum by (service_name) (rate({RED}_count{{{sel}}}[$__range]))", "Avg req/s", "reqps"),
        ("B", f"sum by (service_name) (increase({RED}_count{{{sel}}}[$__range]))", "Requests", "short"),
        ("C", red_errors(sel, "service_name", "$__range"), "5xx", "percentunit"),
        ("D", red_quantile(0.5, sel, "service_name", "$__range"), "p50", "s"),
        ("E", red_quantile(0.99, sel, "service_name", "$__range"), "p99", "s"),
    ]
    overrides = [by_name("service_name", prop("displayName", "Server"), server_link())]
    for ref, _, name, unit in cols:
        p = [prop("displayName", name), prop("unit", unit)]
        if unit == "s":
            p += [prop("custom.cellOptions", {"type": "color-text"}), prop("thresholds", LAT_TH)]
        if name == "5xx":
            p += [prop("custom.cellOptions", {"type": "color-background", "mode": "basic"}),
                  prop("thresholds", ERR_TH), prop("decimals", 2)]
        if name == "Avg req/s":
            p += [prop("decimals", 1)]
        overrides.append(by_name(f"Value #{ref}", *p))
    table = {"type": "table", "title": "Every server, from the inside",
             "description": "Requests each server answered over the time range, measured in the kernel by Beyla. "
                            "Click a server for its dashboard.",
             "datasource": DS, "targets": [t(e, ref=r, instant=True, table=True) for r, e, _, _ in cols],
             "transformations": [{"id": "merge", "options": {}},
                                 {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                                "indexByName": {"service_name": 0}}},
                                 {"id": "sortBy", "options": {"sort": [{"field": "Value #E"}]}}],
             "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}},
                                          "thresholds": BLUE},
                             "overrides": overrides},
             "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}
    L.add(table, 24, 12)
    server_link_by_service = [prop("links", [{"title": "Open ${__field.labels.service_name} dashboard",
                                              "url": "/d/server-${__field.labels.service_name}?${__url_time_range}"}])]
    L.add(ts("Requests per second", "Requests each server answered.",
             t(red_rate(sel, "service_name"), "{{service_name}}"), "reqps",
             overrides=[{"matcher": {"id": "byType", "options": "number"}, "properties": server_link_by_service}]),
          8, 9)
    L.add(ts("5xx share", "Share of each server's responses that were server errors.",
             t(red_errors(sel, "service_name"), "{{service_name}}"), "percentunit", min_=0, soft_max=0.05,
             th=ERR_TH, th_style="line+area"), 8, 9)
    L.add(ts("p99 inside the server", "Each server's 99th-percentile time from request in to response out.",
             t(red_quantile(0.99, sel, "service_name"), "{{service_name}}"), "s", th=LAT_TH, th_style="dashed"),
          8, 9)

    L.add(route_table("Every route, every server", "The same, split by route: where each server spends its "
                                                   "time and which requests it rejects.", sel, by_service=True),
          24, 12)

    L.row("Traces")
    L.add(traces("Slowest recent requests", "Sampled server requests slower than 5 ms, from the selected "
                                            "servers. Most take well under a millisecond.",
                 f'{{resource.service.name=~"$service" && resource.service.name=~"{NAMES}" && duration > 5ms}}'),
          12, 10)
    L.add(traces("Recent requests", "The latest sampled requests from the selected servers.",
                 '{resource.service.name=~"$service"}'), 12, 10)

    L.row("Logs")
    volume = {"type": "timeseries", "title": "Log lines by container",
              "description": "Lines per second each container wrote, from Loki.", "datasource": LOKI,
              "targets": [{"datasource": LOKI, "refId": "A", "queryType": "range",
                           "expr": 'sum by (service) (count_over_time({project="load-bench"}[$__auto]))',
                           "legendFormat": "{{service}}"}],
              "fieldConfig": {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60, "lineWidth": 1,
                                                      "stacking": {"mode": "normal", "group": "A"}},
                                           "color": {"mode": "palette-classic"}, "unit": "short"},
                              "overrides": []},
              "options": {"legend": {"displayMode": "list", "placement": "right", "showLegend": True},
                          "tooltip": {"mode": "multi", "sort": "desc"}}}
    L.add(volume, 24, 7)
    L.add(logs("Logs", "Lines from the selected containers. Expand a runner line for a link to its trace.",
               '{service=~"$service"}'), 24, 12)

    containers = [s["name"] for s in SERVERS] + ["runner", "grafana", "prometheus", "loki", "tempo", "alloy",
                                                 "beyla", "blackbox", "cadvisor"]
    service = {"name": "service", "label": "Service", "type": "custom", "query": ",".join(containers),
               # "All" means the app's containers; the observability stack's own (chatty) logs are opt-in
               "multi": True, "includeAll": True, "allValue": "|".join(containers[:len(SERVERS) + 1]), "hide": 0,
               "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
               "options": [{"selected": False, "text": c, "value": c} for c in containers]}
    return dashboard("internals", "Inside the Servers",
                     "Requests, errors and duration from inside every server via eBPF, sampled traces and "
                     "container logs.",
                     L, [v_datasource(), service], ["overview", "ops"], refresh="30s",
                     time_from="now-1h")


def k6_official(path):
    """Keep the stock k6 dashboard as downloaded, but name it as such and give it the shared navigation."""
    dash = json.loads(path.read_text())
    dash["title"] = "k6 (official)"
    dash["tags"] = ["live", "load-testing"]
    dash["links"] = [link for link in dash.get("links", []) if link.get("url", "").startswith("http")] + nav_links()
    return dash


# ---------------------------------------------------------------- infrastructure

def host():
    L = Layout()
    h = 'job="host"'
    L.row("Overview")
    L.add(stat("Uptime", "Time since the host booted.",
               t(f"time() - node_boot_time_seconds{{{h}}}", instant=True), "s", decimals=0), 4, 4)
    L.add(stat("CPU cores", "Logical CPUs.", t(f'count(node_cpu_seconds_total{{{h},mode="idle"}})',
                                               instant=True)), 4, 4)
    L.add(stat("CPU busy", "Share of CPU time not idle, across all cores.",
               t(f'1 - avg(rate(node_cpu_seconds_total{{{h},mode="idle"}}[1m]))', instant=True), "percentunit",
               {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.7},
                                              {"color": "red", "value": 0.9}]}, 1, graph=True), 4, 4)
    L.add(stat("Memory used", "Memory in use (total minus available).",
               t(f"1 - node_memory_MemAvailable_bytes{{{h}}} / node_memory_MemTotal_bytes{{{h}}}", instant=True),
               "percentunit", {"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                             {"color": "orange", "value": 0.8},
                                                             {"color": "red", "value": 0.9}]}, 1, graph=True), 4, 4)
    disk = f'{h},fstype=~"ext4|xfs|btrfs|zfs"'
    L.add(stat("Disk used", "Usage of the fullest real (ext4/xfs/btrfs/zfs) filesystem.",
               t(f"max(1 - node_filesystem_avail_bytes{{{disk}}} / node_filesystem_size_bytes{{{disk}}})",
                 instant=True), "percentunit",
               {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.8},
                                              {"color": "red", "value": 0.9}]}, 1), 4, 4)
    L.add(stat("Load (1m)", "1-minute load average; compare against the core count.",
               t(f"node_load1{{{h}}}", instant=True), decimals=2), 4, 4)
    red_from_1 = {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "red", "value": 1}]}
    L.add(stat("OOM kills", "Processes the kernel killed for running out of memory, within the time range.",
               t(f"increase(node_vmstat_oom_kill{{{h}}}[$__range]) or vector(0)", instant=True), decimals=0,
               th=red_from_1), 6, 4)
    L.add(stat("Dropped connections", "Connections the kernel refused because a listen backlog was full, within "
                                      "the time range. Usually a server overwhelmed by a load test.",
               t(f"increase(node_netstat_TcpExt_ListenOverflows{{{h}}}[$__range]) or vector(0)", instant=True),
               decimals=0, th={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                             {"color": "orange", "value": 1}]}), 6, 4)
    L.add(stat("Disk free in 7 days", "Free space on the fullest filesystem a week from now, if it keeps "
                                      "filling at the rate of the last 6 hours.",
               t(f"min(predict_linear(node_filesystem_avail_bytes{{{disk}}}[6h], 7 * 86400))", instant=True),
               "bytes", {"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                       {"color": "orange", "value": 2e9},
                                                       {"color": "green", "value": 10e9}]}), 6, 4)
    L.add(stat("Memory pressure", "Share of the last minute in which some task was stalled waiting for memory "
                                  "(Linux PSI). Above zero means the host is short of memory.",
               t(f"rate(node_pressure_memory_waiting_seconds_total{{{h}}}[1m])", instant=True), "percentunit",
               {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "orange", "value": 0.01},
                                              {"color": "red", "value": 0.1}]}, 1), 6, 4)

    L.row("CPU and memory")
    L.add(ts("CPU by mode", "CPU time by mode, as a share of all cores.",
             t(f'sum by (mode) (rate(node_cpu_seconds_total{{{h},mode!="idle"}}[$__rate_interval])) / '
               f'scalar(count(node_cpu_seconds_total{{{h},mode="idle"}}))', "{{mode}}"),
             "percentunit", stack=True, fill=50, min_=0, max_=1, interval="15s"), 12, 8)
    L.add(ts("Busy % per core", "Each core's busy share.",
             t(f'1 - avg by (cpu) (rate(node_cpu_seconds_total{{{h},mode="idle"}}[$__rate_interval]))',
               "cpu {{cpu}}"), "percentunit", min_=0, max_=1, fill=0, interval="15s"), 12, 8)
    L.add(ts("Memory", "Used, cached and free memory.",
             [t(f"node_memory_MemTotal_bytes{{{h}}} - node_memory_MemAvailable_bytes{{{h}}}", "used", "A"),
              t(f"node_memory_Cached_bytes{{{h}}} + node_memory_Buffers_bytes{{{h}}}", "cache + buffers", "B"),
              t(f"node_memory_MemFree_bytes{{{h}}}", "free", "C"),
              t(f"node_memory_MemTotal_bytes{{{h}}}", "total", "D")], "bytes",
             overrides=[by_name("total", *dashed("red"))], interval="15s"), 12, 8)
    L.add(ts("Load average", "1, 5 and 15 minute load against the number of cores (dashed).",
             [t(f"node_load1{{{h}}}", "1m", "A"), t(f"node_load5{{{h}}}", "5m", "B"),
              t(f"node_load15{{{h}}}", "15m", "C"),
              t(f'count(node_cpu_seconds_total{{{h},mode="idle"}})', "cores", "D")],
             overrides=[by_name("cores", *dashed("red"))], fill=0, interval="15s"), 12, 8)
    L.add(ts("Resource pressure", "Share of time some task was stalled waiting for CPU, memory or I/O (Linux "
                                  "PSI). Unlike usage, pressure shows when work is actually being delayed.",
             [t(f"rate(node_pressure_{r}_waiting_seconds_total{{{h}}}[$__rate_interval])", r, ref)
              for r, ref in (("cpu", "A"), ("memory", "B"), ("io", "C"))],
             "percentunit", min_=0, soft_max=0.1, fill=10, interval="15s"), 24, 8)

    L.row("Network and disk")
    dev = 'device!~"lo|veth.*|docker.*|br-.*"'
    L.add(ts("Network traffic", "Bytes per second in (above the axis) and out (below) per interface.",
             [t(f"rate(node_network_receive_bytes_total{{{h},{dev}}}[$__rate_interval])", "{{device}} in", "A"),
              t(f"rate(node_network_transmit_bytes_total{{{h},{dev}}}[$__rate_interval])", "{{device}} out",
                "B")], "Bps", overrides=[{"matcher": {"id": "byRegexp", "options": ".* out$"}, "properties": [prop("custom.transform", "negative-Y")]}], interval="15s"), 8, 8)
    L.add(ts("TCP connections", "Established connections and sockets in TIME_WAIT.",
             [t(f"node_netstat_Tcp_CurrEstab{{{h}}}", "established", "A"),
              t(f"node_sockstat_TCP_tw{{{h}}}", "time_wait", "B")], fill=0, interval="15s"), 8, 8)
    L.add(ts("Disk I/O", "Bytes per second read (above the axis) and written (below).",
             [t(f"sum(rate(node_disk_read_bytes_total{{{h}}}[$__rate_interval]))", "read", "A"),
              t(f"sum(rate(node_disk_written_bytes_total{{{h}}}[$__rate_interval]))", "write", "B")], "Bps",
             overrides=[{"matcher": {"id": "byRegexp", "options": "write"}, "properties": [prop("custom.transform", "negative-Y")]}], interval="15s"), 8, 8)

    return dashboard("infra-host", "Host", "The machine running the containers: CPU, memory, network and disk.",
                     L, [v_datasource()], ["ops", "resources"], refresh="10s", time_from="now-3h")


def containers():
    L = Layout()
    any_svc = f'{SVC}!=""'
    L.add(ts("CPU by container", "CPU per compose service as a share of one core, servers and stack alike.",
             t(svc_cpu(any_svc), "{{server}}"), "percentunit", min_=0, interval="15s"), 12, 10)
    L.add(ts("Memory by container", "Working-set memory per compose service.",
             t(svc_mem(any_svc), "{{server}}"), "bytes", min_=0, interval="15s"), 12, 10)
    L.add(ts("Network received", "Bytes per second received per compose service.",
             t(f'sum by (server) (label_replace(rate(container_network_receive_bytes_total{{{any_svc}}}'
               f'[$__rate_interval]), "server", "$1", "{SVC}", "(.*)"))', "{{server}}"), "Bps", interval="15s"), 12, 9)
    L.add(ts("Threads by container", "OS threads per compose service.",
             t(f'sum by (server) (label_replace(container_threads{{{any_svc}}}, "server", "$1", "{SVC}", "(.*)"))',
               "{{server}}"), fill=0, interval="15s"), 12, 9)
    relabel = f'"server", "$1", "{SVC}", "(.*)"'
    L.add(bars("Restarts", "Times each container restarted after exiting, within the time range.",
               t(f"sort_desc(sum by (server) (label_replace(changes(container_start_time_seconds{{{any_svc}}}"
                 f"[$__range]), {relabel})) > 0)", "{{server}}", instant=True), "short",
               th={"mode": "absolute", "steps": [{"color": "orange", "value": None}]}), 12, 9)
    L.add(bars("OOM kills", "Processes killed for running out of memory, per container, within the time range.",
               t(f"sort_desc(sum by (server) (label_replace(increase(container_oom_events_total{{{any_svc}}}"
                 f"[$__range]), {relabel})) > 0)", "{{server}}", instant=True), "short",
               th={"mode": "absolute", "steps": [{"color": "red", "value": None}]}), 12, 9)
    return dashboard("infra-containers", "Containers", "Every container in the stack, from cAdvisor.",
                     L, [v_datasource()], ["ops", "resources"], refresh="10s", time_from="now-3h")


def stack():
    L = Layout()
    pub = 'job="public-endpoint"'
    L.row("Production")
    deployed = stat("Deployed commit", "The git commit the stack was last deployed from. Click to open it.",
                    t("max by (commit) (o11y_build_info)", "{{commit}}", instant=True), text_mode="name",
                    color_mode="none")
    deployed["links"] = [{"title": "Open the commit on GitHub", "targetBlank": True,
                          "url": f"{REPO_URL}/commit/${{__field.labels.commit}}"}]
    L.add(deployed, 4, 4)
    L.add(stat("Public site", "Whether https://o11y.raashed.com answers through nginx and TLS, as visitors "
                              "reach it.",
               t(f"probe_success{{{pub}}}", instant=True), mappings=UP_MAPPING, th=UP_TH), 4, 4)
    L.add(stat("Public response time", "Time for the public health check, including DNS, TLS and nginx.",
               t(f"probe_duration_seconds{{{pub}}}", instant=True), "s", LAT_TH, graph=True), 4, 4)
    L.add(stat("TLS certificate expires in", "Time until the site's certificate expires. certbot renews it "
                                             "about 30 days before; an alert fires below 14 days.",
               t(f"probe_ssl_earliest_cert_expiry{{{pub}}} - time()", instant=True), "s",
               {"mode": "absolute", "steps": [{"color": "red", "value": None},
                                              {"color": "orange", "value": 14 * 86400},
                                              {"color": "green", "value": 21 * 86400}]}, 0), 4, 4)
    L.add(stat("Firing alerts", "Alert rules currently firing in Grafana.",
               t('sum(grafana_alerting_alerts{state="alerting"}) or vector(0)', instant=True), decimals=0,
               th={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                 {"color": "red", "value": 1}]}), 4, 4)
    L.add(stat("Prometheus uptime", "Time since Prometheus last started.",
               t('time() - process_start_time_seconds{job="prometheus"}', instant=True), "s", decimals=0), 4, 4)
    L.add(ts("Public check by phase", "Where the time goes for a visitor's request: DNS, connect, TLS, "
                                      "processing and transfer.",
             t(f"sum by (phase) (probe_http_duration_seconds{{{pub}}})", "{{phase}}"), "s", stack=True, fill=40,
             interval="30s"), 12, 8)
    L.add(alert_list("Alerts", "Alerts firing or pending right now."), 12, 8)

    L.row("Prometheus and Grafana")
    L.add({"type": "table", "title": "Scrape targets",
           "description": "Every target Prometheus scrapes and whether the last scrape worked.",
           "datasource": DS, "targets": [t("up", ref="A", instant=True, table=True)],
           "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True},
                                                              "renameByName": {"Value": "Up"}}}],
           "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}},
                                        "thresholds": BLUE},
                           "overrides": [by_name("Up", prop("mappings", UP_MAPPING),
                                                 prop("custom.cellOptions", {"type": "color-background",
                                                                             "mode": "basic"}),
                                                 prop("thresholds", {"mode": "absolute", "steps": [
                                                     {"color": "red", "value": None},
                                                     {"color": "green", "value": 1}]}))]},
           "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False},
                       "sortBy": [{"displayName": "Up", "desc": False}]}}, 12, 12)
    L.add(ts("Scrape duration", "How long each scrape job takes.",
             t("max by (job) (scrape_duration_seconds)", "{{job}}"), "s", fill=0, interval="1m"), 12, 12)
    L.add(ts("Samples ingested", "Samples appended to the TSDB per second (scrapes plus k6 remote write).",
             t("sum(rate(prometheus_tsdb_head_samples_appended_total[$__rate_interval]))", "samples/s"), "short", interval="1m"), 8, 8)
    L.add(ts("Active series", "Series currently in the TSDB head block.",
             t("prometheus_tsdb_head_series", "series"), "short", interval="1m"), 8, 8)
    L.add(ts("TSDB size", "On-disk size of persisted blocks plus the write-ahead log.",
             t("prometheus_tsdb_storage_blocks_bytes + prometheus_tsdb_wal_storage_size_bytes", "bytes"),
             "bytes", interval="1m"), 8, 8)
    L.add(ts("Grafana requests", "Requests served by Grafana per second, by status code.",
             t('sum by (status_code) (rate(grafana_http_request_duration_seconds_count[$__rate_interval]))',
               "{{status_code}}"), "reqps", stack=True, fill=40, interval="1m"), 12, 8)
    L.add(ts("Recording rules", "Time each rule group took to evaluate (left), and failed evaluations (right, "
                                "should stay at zero).",
             [t("max by (rule_group) (prometheus_rule_group_last_duration_seconds)", "{{rule_group}}", "A"),
              t("sum(increase(prometheus_rule_evaluation_failures_total[$__rate_interval]))", "failures", "B")],
             "s", fill=0, interval="1m",
             overrides=[{"matcher": {"id": "byFrameRefID", "options": "B"},
                         "properties": [prop("unit", "short"), prop("custom.axisPlacement", "right"),
                                        prop("color", {"mode": "fixed", "fixedColor": "red"})]}]), 12, 8)
    L.row("Logs and traces pipeline")
    L.add(ts("Log lines into Loki", "Lines per second Loki accepted from Alloy.",
             t("sum(rate(loki_distributor_lines_received_total[$__rate_interval]))", "lines/s"), "short",
             interval="1m"), 8, 7)
    L.add(ts("Spans into Tempo", "Spans per second Tempo accepted, from Beyla's sampled server requests and "
                                 "the runner.",
             t("sum(rate(tempo_distributor_spans_received_total[$__rate_interval]))", "spans/s"), "short",
             interval="1m"), 8, 7)
    L.add(ts("Requests Beyla saw", "Requests per second across all servers, from eBPF. Every request counts, "
                                   "not just the traced sample.",
             t(f"sum(rate({RED}_count[$__rate_interval]))", "req/s"), "reqps", interval="1m"), 8, 7)

    L.row("Runner logs and traces")
    L.add(logs("Runner logs", "The load-test runner's JSON logs. Lines written during a test link to its trace.",
               '{service="runner"}'), 12, 10)
    L.add(traces("Runner traces", "Visitor requests and load tests. A load test's span covers the whole k6 run.",
                 '{resource.service.name="runner"}'), 12, 10)

    L.row("Visitor load tests")
    L.add(ts("Visitor load tests", "Load tests started from the public /run/ page, by server and result.",
             t('sum by (server, result) (increase(o11y_runner_runs_total[$__rate_interval]))',
               "{{server}} {{result}}"), draw="bars", fill=60, interval="1m"), 12, 8)
    L.add(ts("Turned-away test requests", "Start requests the runner refused: busy (a test was running), "
                                          "cooldown, rate-limit (per-visitor hourly cap) or cross-site (CSRF).",
             t('sum by (reason) (increase(o11y_runner_rejections_total[$__rate_interval]))', "{{reason}}"),
             draw="bars", fill=60, stack=True, interval="1m"), 12, 8)
    return dashboard("infra-stack", "Observability Stack",
                     "The public site, deploys and alerts, and the health of Prometheus, Grafana, the exporters and "
                     "the load-test runner.",
                     L, [v_datasource()], ["ops", "self-monitoring"], refresh="30s", time_from="now-6h")


# ---------------------------------------------------------------- beyla

# Routes Beyla groups requests under, so each todo id does not become its own series.
# Written as a JSON array: unquoted, YAML would read {id} as a map.
ROUTES = ["/api/v1/health", "/api/v1/docs", "/api/v1/openapi.json", "/api/v1/todos", "/api/v1/todos/{id}"]

# Share of server requests Beyla keeps as traces. Its metrics count every request;
# keeping every span of a 1500 req/s race would swamp Tempo on a small machine.
TRACE_SAMPLE = 0.05


def beyla_config():
    """eBPF instrumentation for exactly the servers in servers.json, matched by their listen port."""
    instrument = "".join(f"    - open_ports: {s['port']}\n      name: {s['name']}\n" for s in SERVERS)
    return f"""# Generated by infra/grafana/generate.py from infra/servers.json; do not edit.
# Beyla watches the servers through eBPF: no code changes, any language. Each server
# listens on its own port (the same inside the container and on the host), which
# is how Beyla tells them apart and leaves every other process alone.
discovery:
  instrument:
{instrument}  exclude_instrument:
    # Docker's port forwarder also holds the published ports on the host
    - exe_path: "*docker-proxy*"

# Observe only. Context propagation would rewrite the servers' outgoing traffic to
# carry trace IDs (they make no outgoing calls), and the Node.js helper opens each
# Node server's inspector to inject a script, which skews their benchmark.
ebpf:
  context_propagation: disabled
nodejs:
  enabled: false

routes:
  patterns: {json.dumps(ROUTES)}
  unmatched: heuristic

# RED metrics for every request, scraped by Prometheus. These servers answer in well
# under a millisecond, so the duration buckets start far below the 5 ms default.
prometheus_export:
  port: 9400
  path: /metrics
  features: [application]
  buckets:
    duration_histogram: [0.0001, 0.00025, 0.0005, 0.00075, 0.001, 0.0015, 0.002, 0.003, 0.005, 0.0075,
                         0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]

# A sample of requests as traces, sent to Tempo
otel_traces_export:
  endpoint: http://tempo:4318
  sampler:
    name: traceidratio
    arg: "{TRACE_SAMPLE}"
"""


# ---------------------------------------------------------------- write

def write(rel, dash):
    path = DASHBOARDS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dash, indent=2) + "\n")
    return path


def main():
    targets = [{"targets": [f"http://{s['name']}:{s['port']}/api/v1/health"],
                "labels": {"server": s["name"], "language": s["language"], "framework": s["framework"]}}
               for s in SERVERS]
    (INFRA / "prometheus" / "targets.json").write_text(json.dumps(targets, indent=2) + "\n")
    (INFRA / "beyla" / "beyla.yaml").write_text(beyla_config())

    written = [
        write("Fleet/fleet.json", fleet()),
        write("Fleet/compare.json", compare()),
        write("Fleet/leaderboard.json", leaderboard()),
        write("Fleet/compare-servers.json", compare_servers()),
        write("Fleet/internals.json", internals()),
        write("Load Testing/live.json", live()),
        write("Load Testing/match.json", head_to_head()),
        write("Load Testing/k6-prometheus.json", k6_official(DASHBOARDS / "Load Testing" / "k6-prometheus.json")),
        write("Infrastructure/host.json", host()),
        write("Infrastructure/containers.json", containers()),
        write("Infrastructure/stack.json", stack()),
    ]
    for s in SERVERS:
        assert s["language"] in LANGS, s
        written.append(write(f"Servers/{s['language']}/{s['name']}.json", server_dashboard(s)))

    expected = {p.resolve() for p in written}
    for stale in DASHBOARDS.rglob("*.json"):
        if stale.resolve() not in expected:
            stale.unlink()
            print("removed", stale.relative_to(INFRA))
    print(f"wrote {len(written)} dashboards and {len(targets)} probe targets")


if __name__ == "__main__":
    main()
