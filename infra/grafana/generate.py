#!/usr/bin/env python3
"""Generate Grafana dashboards and Prometheus probe targets from infra/servers.json.

    python3 infra/grafana/generate.py

Writes:
  infra/prometheus/targets.json                     blackbox probe targets
  infra/grafana/dashboards/Fleet/*.json             fleet overview + run comparison
  infra/grafana/dashboards/Servers/<Lang>/*.json    one dashboard per server
  infra/grafana/dashboards/Load Testing/live.json   live k6 run view
  infra/grafana/dashboards/Infrastructure/*.json    host, containers, observability stack

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


def text(content):
    return {"type": "text", "title": "", "transparent": True,
            "options": {"mode": "markdown", "content": content, "code": {"language": "plaintext"}}}


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
                                  "name": "Annotations & Alerts", "type": "dashboard"}]},
        "links": links if links is not None else nav_links(),
        "panels": layout.panels,
    }


def nav_links():
    return [
        {"title": "Fleet", "type": "link", "url": "/d/fleet", "icon": "apps", "keepTime": True,
         "targetBlank": False, "tooltip": "All servers at a glance"},
        {"title": "Run a load test", "type": "link", "url": "/run/", "icon": "bolt", "keepTime": False,
         "targetBlank": False, "tooltip": "Start a load test against one server (one at a time)"},
        {"title": "Servers", "type": "dashboards", "tags": ["server"], "asDropdown": True, "keepTime": True,
         "includeVars": False, "targetBlank": False, "icon": "external link"},
        {"title": "Load testing", "type": "dashboards", "tags": ["load-testing"], "asDropdown": True,
         "keepTime": True, "includeVars": False, "targetBlank": False, "icon": "external link"},
        {"title": "Infrastructure", "type": "dashboards", "tags": ["infrastructure"], "asDropdown": True,
         "keepTime": True, "includeVars": False, "targetBlank": False, "icon": "external link"},
    ]


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
        "**[▶ Run a load test](/run/)** against any server and watch it live. One test runs at a time."), 24, 4)

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

    return dashboard("fleet", "Fleet Overview",
                     "Health, resources and best load-test results for every server.",
                     L, [v_datasource()], ["fleet"], refresh="10s", time_from="now-24h")


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
        f"### [▶ Run a load test on {s['title']}](/run/{name})"), 24, 6)

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

    variables = [v_datasource(), v_const("server", name),
                 v_query("testid", "Run", f'label_values(k6_vus{{server="{name}"}}, testid)')]
    links = nav_links() + [{"title": "Live run view", "type": "link", "icon": "dashboard",
                            "url": f"/d/load-live?var-server={name}", "keepTime": True, "targetBlank": False,
                            "tooltip": "Open the live load-test view filtered to this server"}]
    return dashboard(f"server-{name}", s["title"],
                     f"{s['title']} ({s['language']}, {s['framework']}): health, container resources and "
                     "load-test history.",
                     L, variables, ["server", s["language"].lower()], links=links)


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
    L.add(ts("Host CPU per core", "A single core pinned near 100% points to a single-threaded server.",
             t('1 - avg by (cpu) (rate(node_cpu_seconds_total{job="host",mode="idle"}[$__rate_interval]))',
               "cpu {{cpu}}"), "percentunit", min_=0, max_=1, fill=0, interval="15s"), 8, 8)

    return dashboard("load-live", "Live Load Test",
                     "Throughput, latency and errors for k6 runs as they stream in, next to the server's own "
                     "container metrics.",
                     L, [v_datasource(), v_query("server", "Server", "label_values(k6_vus, server)"),
                         v_query("testid", "Run", 'label_values(k6_vus{server=~"$server"}, testid)')],
                     ["load-testing"], refresh="5s", time_from="now-15m")


def compare():
    L = Layout()
    by = "testid, server, script"
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
                                                                              "script": 2}}},
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
                     ["fleet", "load-testing"], refresh="30s", time_from="now-7d")


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
                     L, [v_datasource()], ["infrastructure"], refresh="10s", time_from="now-3h")


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
    return dashboard("infra-containers", "Containers", "Every container in the stack, from cAdvisor.",
                     L, [v_datasource()], ["infrastructure"], refresh="10s", time_from="now-3h")


def stack():
    L = Layout()
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
             t("rate(prometheus_tsdb_head_samples_appended_total[$__rate_interval])", "samples/s"), "short", interval="1m"), 8, 8)
    L.add(ts("Active series", "Series currently in the TSDB head block.",
             t("prometheus_tsdb_head_series", "series"), "short", interval="1m"), 8, 8)
    L.add(ts("TSDB size", "On-disk size of persisted blocks plus the write-ahead log.",
             t("prometheus_tsdb_storage_blocks_bytes + prometheus_tsdb_wal_storage_size_bytes", "bytes"),
             "bytes", interval="1m"), 8, 8)
    L.add(ts("Grafana requests", "Requests served by Grafana per second, by status code.",
             t('sum by (status_code) (rate(grafana_http_request_duration_seconds_count[$__rate_interval]))',
               "{{status_code}}"), "reqps", stack=True, fill=40, interval="1m"), 24, 8)
    L.add(ts("Visitor load tests", "Load tests started from the public /run/ page, by server and result.",
             t('sum by (server, result) (increase(o11y_runner_runs_total[$__rate_interval]))',
               "{{server}} {{result}}"), draw="bars", fill=60, interval="1m"), 12, 8)
    L.add(ts("Turned-away test requests", "Start requests the runner refused: busy (a test was running), "
                                          "cooldown, rate-limit (per-visitor hourly cap) or cross-site (CSRF).",
             t('sum by (reason) (increase(o11y_runner_rejections_total[$__rate_interval]))', "{{reason}}"),
             draw="bars", fill=60, stack=True, interval="1m"), 12, 8)
    return dashboard("infra-stack", "Observability Stack",
                     "Health of Prometheus, Grafana, the exporters and the load-test runner.",
                     L, [v_datasource()], ["infrastructure"], refresh="30s", time_from="now-6h")


# ---------------------------------------------------------------- write

def write(rel, dash):
    path = DASHBOARDS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dash, indent=2) + "\n")
    return path


def main():
    targets = [{"targets": [f"http://{s['name']}:3000/api/v1/health"],
                "labels": {"server": s["name"], "language": s["language"], "framework": s["framework"]}}
               for s in SERVERS]
    (INFRA / "prometheus" / "targets.json").write_text(json.dumps(targets, indent=2) + "\n")

    written = [
        write("Fleet/fleet.json", fleet()),
        write("Fleet/compare.json", compare()),
        write("Load Testing/live.json", live()),
        write("Infrastructure/host.json", host()),
        write("Infrastructure/containers.json", containers()),
        write("Infrastructure/stack.json", stack()),
    ]
    for s in SERVERS:
        assert s["language"] in LANGS, s
        written.append(write(f"Servers/{s['language']}/{s['name']}.json", server_dashboard(s)))

    expected = {p.resolve() for p in written} | {(DASHBOARDS / "Load Testing" / "k6-prometheus.json").resolve()}
    for stale in DASHBOARDS.rglob("*.json"):
        if stale.resolve() not in expected:
            stale.unlink()
            print("removed", stale.relative_to(INFRA))
    print(f"wrote {len(written)} dashboards and {len(targets)} probe targets")


if __name__ == "__main__":
    main()
