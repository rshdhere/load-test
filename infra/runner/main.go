// Command runner lets visitors start one k6 load test at a time, against a
// single server or as a head-to-head race between 2-3 servers, with fixed test
// settings, a cooldown and a per-IP limit.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"html/template"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

type server struct {
	Name        string `json:"name"`
	Title       string `json:"title"`
	Language    string `json:"language"`
	Framework   string `json:"framework"`
	Concurrency string `json:"concurrency"`
	Port        int    `json:"port"` // the same inside the container and on the host
}

type run struct {
	Servers []string  `json:"servers"`
	Match   bool      `json:"match"`  // a head-to-head race rather than a single-server test
	TestID  string    `json:"testid"` // the k6 testid, or the match id for a race
	Started time.Time `json:"started"`
	Ends    time.Time `json:"ends"`
}

// Label names the run for people and metrics: "go" or "go vs bun".
func (c run) Label() string { return strings.Join(c.Servers, " vs ") }

// Watch is the dashboard that shows the run live.
func (c run) Watch() string {
	q := url.Values{"from": {"now-5m"}, "to": {"now"}, "refresh": {"5s"}}
	if c.Match {
		q.Set("var-match", c.TestID)
		return "/d/match?" + q.Encode()
	}
	q.Set("var-server", c.Servers[0])
	q.Set("var-testid", c.TestID)
	return "/d/load-live?" + q.Encode()
}

const minMatch, maxMatch = 2, 3

type config struct {
	addr        string
	scriptsDir  string
	script      string
	rate        int
	matchRate   int // requests per second to each server in a race
	duration    time.Duration
	cooldown    time.Duration
	perIPHourly int
	remoteWrite string
	commit      string // deployed git commit, for the dashboards' deploy markers
}

type runner struct {
	cfg     config
	servers []server

	mu        sync.Mutex
	current   *run
	idleAfter time.Time              // end of the cooldown after the last run
	byIP      map[string][]time.Time // start times per client IP, last hour
	runs      map[string]int         // "server|result" -> count
	rejected  map[string]int         // reason -> count
	lastRun   map[string]time.Time   // server -> last start
}

func main() {
	cfg := config{
		addr:        env("RUNNER_ADDR", ":8080"),
		scriptsDir:  env("RUNNER_SCRIPTS_DIR", "/scripts"),
		script:      env("RUNNER_SCRIPT", "load"),
		rate:        envInt("RUNNER_RATE", 500),
		duration:    envDuration("RUNNER_DURATION", 30*time.Second),
		cooldown:    envDuration("RUNNER_COOLDOWN", 60*time.Second),
		perIPHourly: envInt("RUNNER_PER_IP_HOURLY", 3),
		remoteWrite: env("RUNNER_REMOTE_WRITE", "http://prometheus:9090/api/v1/write"),
		commit:      env("GIT_COMMIT", "dev"),
	}
	cfg.matchRate = envInt("RUNNER_MATCH_RATE", cfg.rate)
	shutdown := setupTelemetry(cfg.commit)
	defer shutdown(context.Background())
	if cfg.script != "load" && cfg.script != "break" {
		fatal("RUNNER_SCRIPT must be load or break", "got", cfg.script)
	}

	raw, err := os.ReadFile(env("RUNNER_SERVERS_FILE", "/etc/runner/servers.json"))
	if err != nil {
		fatal("cannot read the servers file", "error", err)
	}
	r := &runner{cfg: cfg, byIP: map[string][]time.Time{}, runs: map[string]int{}, rejected: map[string]int{},
		lastRun: map[string]time.Time{}}
	if err := json.Unmarshal(raw, &r.servers); err != nil {
		fatal("cannot parse the servers file", "error", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /run/{$}", r.index)
	mux.HandleFunc("GET /run/status", r.status)
	mux.HandleFunc("GET /run/compare", r.comparePage)
	mux.HandleFunc("POST /run/compare", r.startMatch)
	mux.HandleFunc("GET /run/{name}", r.page)
	mux.HandleFunc("POST /run/{name}", r.start)
	mux.HandleFunc("GET /metrics", r.metrics)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })

	// Trace visitor requests, but not Prometheus scrapes, health checks or the race page's polling
	quiet := map[string]bool{"/metrics": true, "/healthz": true, "/run/status": true}
	handler := otelhttp.NewHandler(mux, "runner",
		otelhttp.WithFilter(func(req *http.Request) bool { return !quiet[req.URL.Path] }),
		otelhttp.WithSpanNameFormatter(func(_ string, req *http.Request) string {
			return req.Method + " " + req.URL.Path
		}))

	slog.Info("runner listening", "addr", cfg.addr, "script", cfg.script, "rate", cfg.rate,
		"match_rate", cfg.matchRate, "duration", cfg.duration.String(), "cooldown", cfg.cooldown.String(),
		"per_ip_hourly", cfg.perIPHourly, "commit", cfg.commit)
	fatal("server stopped", "error", http.ListenAndServe(cfg.addr, handler))
}

func fatal(msg string, args ...any) {
	slog.Error(msg, args...)
	os.Exit(1)
}

// ---------------------------------------------------------------- handlers

func (r *runner) index(w http.ResponseWriter, _ *http.Request) {
	r.render(w, http.StatusOK, pageData{Servers: r.servers, State: r.state(""), Cfg: r.cfgView()})
}

func (r *runner) page(w http.ResponseWriter, req *http.Request) {
	s, ok := r.find(req.PathValue("name"))
	if !ok {
		http.NotFound(w, req)
		return
	}
	r.render(w, http.StatusOK, pageData{Server: &s, State: r.state(""), Cfg: r.cfgView()})
}

func (r *runner) start(w http.ResponseWriter, req *http.Request) {
	s, ok := r.find(req.PathValue("name"))
	if !ok {
		http.NotFound(w, req)
		return
	}
	r.launch(w, req, pageData{Server: &s}, []string{s.Name})
}

func (r *runner) comparePage(w http.ResponseWriter, req *http.Request) {
	// ?server=go pre-ticks servers, for links from their dashboards
	var picked []string
	for _, name := range req.URL.Query()["server"] {
		if _, ok := r.find(name); ok && !slices.Contains(picked, name) && len(picked) < maxMatch {
			picked = append(picked, name)
		}
	}
	r.render(w, http.StatusOK, pageData{Compare: true, Picked: picked, Servers: r.servers, State: r.state(""),
		Cfg: r.cfgView()})
}

func (r *runner) startMatch(w http.ResponseWriter, req *http.Request) {
	page := pageData{Compare: true, Servers: r.servers}
	if err := req.ParseForm(); err != nil {
		http.Error(w, "bad form", http.StatusBadRequest)
		return
	}
	var picked []string
	for _, name := range req.PostForm["server"] {
		if _, ok := r.find(name); ok && !slices.Contains(picked, name) {
			picked = append(picked, name)
		}
	}
	page.Picked = picked
	if len(picked) < minMatch || len(picked) > maxMatch {
		page.State, page.Cfg = r.state(fmt.Sprintf("Pick %d or %d servers to race.", minMatch, maxMatch)), r.cfgView()
		r.render(w, http.StatusBadRequest, page)
		return
	}
	r.launch(w, req, page, picked)
}

// launch applies the guard rails shared by single tests and races, then starts
// k6 and sends the visitor to the dashboard that shows the run live.
func (r *runner) launch(w http.ResponseWriter, req *http.Request, page pageData, servers []string) {
	if !sameOrigin(req) {
		r.reject(w, page, http.StatusForbidden, "cross-site", "Load tests can only be started from this site.")
		return
	}

	ip := clientIP(req)
	now := time.Now()

	r.mu.Lock()
	switch {
	case r.current != nil:
		r.mu.Unlock()
		r.reject(w, page, http.StatusConflict, "busy", "Another load test is already running.")
		return
	case now.Before(r.idleAfter):
		wait := r.idleAfter.Sub(now).Round(time.Second)
		r.mu.Unlock()
		r.reject(w, page, http.StatusTooManyRequests, "cooldown",
			fmt.Sprintf("The server is cooling down after the last test. Try again in %s.", wait))
		return
	}
	recent := slices.DeleteFunc(r.byIP[ip], func(t time.Time) bool { return now.Sub(t) > time.Hour })
	if len(recent) >= r.cfg.perIPHourly {
		r.byIP[ip] = recent
		r.mu.Unlock()
		r.reject(w, page, http.StatusTooManyRequests, "rate-limit",
			fmt.Sprintf("You have started %d load tests in the last hour, the limit. Try again later.", len(recent)))
		return
	}
	r.byIP[ip] = append(recent, now)
	match := len(servers) > 1
	stamp := now.UTC().Format("20060102-150405")
	cur := &run{Servers: servers, Match: match, Started: now, Ends: now.Add(r.expected(match)),
		TestID: fmt.Sprintf("%s-%s-web-%s", servers[0], r.cfg.script, stamp)}
	if match {
		cur.TestID = "match-web-" + stamp
	}
	r.current = cur
	for _, name := range servers {
		r.lastRun[name] = now
	}
	r.mu.Unlock()

	// The test outlives this request, so its span keeps the request's trace but not its cancellation
	ctx, span := tracer.Start(context.WithoutCancel(req.Context()), "load test",
		trace.WithAttributes(
			attribute.StringSlice("loadtest.servers", servers),
			attribute.String("loadtest.testid", cur.TestID),
			attribute.Bool("loadtest.match", match),
			attribute.String("client.address", ip),
		))
	slog.InfoContext(ctx, "load test started", "testid", cur.TestID, "servers", servers, "match", match,
		"client_ip", ip)
	go r.execute(ctx, span, cur)

	http.Redirect(w, req, cur.Watch(), http.StatusSeeOther)
}

func (r *runner) status(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(r.state(""))
}

func (r *runner) metrics(w http.ResponseWriter, _ *http.Request) {
	r.mu.Lock()
	defer r.mu.Unlock()
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")

	busy, running := 0, ""
	if r.current != nil {
		busy, running = 1, r.current.Label()
	}
	fmt.Fprintln(w, "# HELP o11y_build_info The git commit the stack was deployed from.")
	fmt.Fprintln(w, "# TYPE o11y_build_info gauge")
	fmt.Fprintf(w, "o11y_build_info{commit=%q} 1\n", r.cfg.commit)

	fmt.Fprintln(w, "# HELP o11y_runner_busy Whether a visitor-started load test is running.")
	fmt.Fprintln(w, "# TYPE o11y_runner_busy gauge")
	fmt.Fprintf(w, "o11y_runner_busy{server=%q} %d\n", running, busy)

	fmt.Fprintln(w, "# HELP o11y_runner_runs_total Visitor-started load tests by server and result.")
	fmt.Fprintln(w, "# TYPE o11y_runner_runs_total counter")
	for _, key := range sortedKeys(r.runs) {
		srv, result, _ := strings.Cut(key, "|")
		fmt.Fprintf(w, "o11y_runner_runs_total{server=%q,result=%q} %d\n", srv, result, r.runs[key])
	}

	fmt.Fprintln(w, "# HELP o11y_runner_rejections_total Start requests turned away, by reason.")
	fmt.Fprintln(w, "# TYPE o11y_runner_rejections_total counter")
	for _, reason := range []string{"busy", "cooldown", "rate-limit", "cross-site"} {
		fmt.Fprintf(w, "o11y_runner_rejections_total{reason=%q} %d\n", reason, r.rejected[reason])
	}

	fmt.Fprintln(w, "# HELP o11y_runner_last_run_timestamp_seconds When each server was last load-tested by a visitor.")
	fmt.Fprintln(w, "# TYPE o11y_runner_last_run_timestamp_seconds gauge")
	for _, srv := range sortedKeys(r.lastRun) {
		fmt.Fprintf(w, "o11y_runner_last_run_timestamp_seconds{server=%q} %d\n", srv, r.lastRun[srv].Unix())
	}
}

// ---------------------------------------------------------------- k6

func (r *runner) execute(parent context.Context, span trace.Span, cur *run) {
	defer span.End()
	ctx, cancel := context.WithTimeout(parent, r.expected(cur.Match)+time.Minute)
	defer cancel()

	health := func(name string) string {
		s, _ := r.find(name)
		return fmt.Sprintf("http://%s:%d/api/v1/health", name, s.Port)
	}
	args := []string{"run", "--no-usage-report", "--quiet", "-o", "experimental-prometheus-rw"}
	var testEnv []string
	if cur.Match {
		// match.ts tags each server's requests with its own server and testid
		targets := make([]string, len(cur.Servers))
		for i, name := range cur.Servers {
			targets[i] = name + "=" + health(name)
		}
		args = append(args, "--tag", "script=match", r.cfg.scriptsDir+"/match.ts")
		testEnv = []string{"TARGETS=" + strings.Join(targets, ","), "MATCH=" + cur.TestID,
			"RATE=" + strconv.Itoa(r.cfg.matchRate)}
	} else {
		args = append(args, "--tag", "testid="+cur.TestID, "--tag", "server="+cur.Servers[0],
			"--tag", "script="+r.cfg.script, r.cfg.scriptsDir+"/"+r.cfg.script+".ts")
		testEnv = []string{"URL=" + health(cur.Servers[0]), "RATE=" + strconv.Itoa(r.cfg.rate)}
	}

	cmd := exec.CommandContext(ctx, "k6", args...)
	cmd.Env = append(append(os.Environ(), testEnv...),
		"DURATION="+r.cfg.duration.String(),
		"RESULTS_DIR=/results",
		"K6_PROMETHEUS_RW_SERVER_URL="+r.cfg.remoteWrite,
		"K6_PROMETHEUS_RW_TREND_STATS=p(50),p(90),p(95),p(99),avg,min,max",
		"K6_PROMETHEUS_RW_PUSH_INTERVAL=1s",
	)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	span.SetAttributes(attribute.String("loadtest.script", args[len(args)-1]),
		attribute.String("loadtest.duration", r.expected(cur.Match).String()))

	result := "ok"
	if err := cmd.Run(); err != nil {
		result = "failed"
		if ctx.Err() != nil {
			result = "timeout"
		}
		span.RecordError(err)
		span.SetStatus(codes.Error, result)
		slog.ErrorContext(ctx, "k6 failed", "testid", cur.TestID, "error", err)
	}
	span.SetAttributes(attribute.String("loadtest.result", result))
	slog.InfoContext(ctx, "load test finished", "testid", cur.TestID, "result", result,
		"seconds", time.Since(cur.Started).Round(time.Millisecond).Seconds())

	r.mu.Lock()
	for _, name := range cur.Servers {
		r.runs[name+"|"+result]++
	}
	r.current = nil
	r.idleAfter = time.Now().Add(r.cfg.cooldown)
	r.mu.Unlock()
}

// expected is how long a run takes, so pages can show when it ends. Races
// always run at a constant rate, whatever RUNNER_SCRIPT says.
func (r *runner) expected(match bool) time.Duration {
	if r.cfg.script == "break" && !match {
		return 60 * time.Second
	}
	return r.cfg.duration
}

// ---------------------------------------------------------------- state and pages

type state struct {
	Busy         bool   `json:"busy"`
	Current      *run   `json:"current,omitempty"`
	CooldownSecs int    `json:"cooldown_seconds"`
	Message      string `json:"message,omitempty"`
}

func (r *runner) state(msg string) state {
	r.mu.Lock()
	defer r.mu.Unlock()
	st := state{Busy: r.current != nil, Message: msg}
	if r.current != nil {
		c := *r.current
		st.Current = &c
	} else if d := time.Until(r.idleAfter); d > 0 {
		st.CooldownSecs = int(d.Seconds()) + 1
	}
	return st
}

type cfgView struct {
	Script        string
	Rate          int
	MatchRate     int
	Duration      string
	MatchDuration string
	PerIP         int
	MinMatch      int
	MaxMatch      int
}

func (r *runner) cfgView() cfgView {
	return cfgView{Script: r.cfg.script, Rate: r.cfg.rate, MatchRate: r.cfg.matchRate,
		Duration: r.expected(false).String(), MatchDuration: r.expected(true).String(), PerIP: r.cfg.perIPHourly,
		MinMatch: minMatch, MaxMatch: maxMatch}
}

type pageData struct {
	Server  *server  // single-server test page
	Compare bool     // head-to-head page
	Picked  []string // servers ticked on the head-to-head page
	Servers []server
	State   state
	Cfg     cfgView
}

func (r *runner) reject(w http.ResponseWriter, page pageData, code int, reason, msg string) {
	r.mu.Lock()
	r.rejected[reason]++
	r.mu.Unlock()
	page.State, page.Cfg = r.state(msg), r.cfgView()
	r.render(w, code, page)
}

func (r *runner) render(w http.ResponseWriter, code int, d pageData) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	if err := pageTmpl.Execute(w, d); err != nil {
		slog.Error("cannot render the page", "error", err)
	}
}

func (r *runner) find(name string) (server, bool) {
	for _, s := range r.servers {
		if s.Name == name {
			return s, true
		}
	}
	return server{}, false
}

// ---------------------------------------------------------------- helpers

// sameOrigin accepts form posts only from this site, so other pages cannot
// make a visitor's browser start tests.
func sameOrigin(req *http.Request) bool {
	src := req.Header.Get("Origin")
	if src == "" || src == "null" {
		src = req.Header.Get("Referer")
	}
	u, err := url.Parse(src)
	return err == nil && u.Host != "" && u.Host == req.Host
}

// clientIP trusts X-Forwarded-For because the runner is only reachable
// through the host's nginx, which overwrites it with the real client address.
func clientIP(req *http.Request) string {
	if xff := req.Header.Get("X-Forwarded-For"); xff != "" {
		first, _, _ := strings.Cut(xff, ",")
		return strings.TrimSpace(first)
	}
	host, _, err := net.SplitHostPort(req.RemoteAddr)
	if err != nil {
		return req.RemoteAddr
	}
	return host
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envInt(key string, fallback int) int {
	if n, err := strconv.Atoi(os.Getenv(key)); err == nil && n > 0 {
		return n
	}
	return fallback
}

func envDuration(key string, fallback time.Duration) time.Duration {
	if d, err := time.ParseDuration(os.Getenv(key)); err == nil && d > 0 {
		return d
	}
	return fallback
}

var pageTmpl = template.Must(template.New("page").Funcs(template.FuncMap{
	"secsLeft": func(t time.Time) int { return max(0, int(time.Until(t).Seconds())) },
	"has":      slices.Contains[[]string],
}).Parse(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{if .Server}}Load test {{.Server.Title}}{{else if .Compare}}Head to head{{else}}Load tests{{end}} · o11y</title>
{{if and (or .State.Busy .State.CooldownSecs) (not .Compare)}}<meta http-equiv="refresh" content="5">{{end}}
<style>
  :root { color-scheme: dark; --bg: #111217; --panel: #181b1f; --line: #2c3235; --text: #ccccdc;
          --muted: #8e8e9b; --accent: #3d71d9; --ok: #73bf69; --warn: #ff9830; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 15px/1.5 Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 640px; margin: 0 auto; padding: 48px 16px; }
  a { color: #6e9fff; }
  h1 { font-size: 24px; margin: 0 0 4px; color: #fff; }
  .sub { color: var(--muted); margin: 0 0 24px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 20px; margin: 16px 0; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; }
  dt { color: var(--muted); } dd { margin: 0; }
  button { background: var(--accent); color: #fff; border: 0; border-radius: 4px; padding: 10px 18px;
           font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { background: var(--line); color: var(--muted); cursor: not-allowed; }
  .status { display: flex; align-items: center; gap: 8px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--ok); }
  .busy .dot { background: var(--warn); }
  .msg { border-left: 3px solid var(--warn); padding: 8px 12px; background: #2a1f14; margin: 16px 0; }
  ul { padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
  li a { display: block; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 12px;
         text-decoration: none; color: var(--text); }
  li a:hover { border-color: var(--accent); }
  small { color: var(--muted); }
  .cta { display: flex; justify-content: space-between; align-items: center; gap: 16px; text-decoration: none;
         color: var(--text); }
  .cta:hover { border-color: var(--accent); }
  .picks { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; margin: 0 0 20px; }
  .picks label { display: flex; gap: 10px; align-items: flex-start; background: var(--bg); border: 1px solid var(--line);
                 border-radius: 6px; padding: 10px 12px; cursor: pointer; }
  .picks label:has(input:checked) { border-color: var(--accent); background: #1b2436; }
  .picks label:has(input:disabled) { opacity: .45; cursor: not-allowed; }
  .picks input { margin-top: 4px; accent-color: var(--accent); }
</style>
</head>
<body><main>
{{if .Server}}
  <p class="sub"><a href="/d/server-{{.Server.Name}}">← {{.Server.Title}} dashboard</a></p>
  <h1>Load test {{.Server.Title}}</h1>
  <p class="sub">{{.Server.Language}} · {{.Server.Framework}}</p>
{{else if .Compare}}
  <p class="sub"><a href="/run/">← Single-server tests</a></p>
  <h1>Head to head</h1>
  <p class="sub">Race {{.Cfg.MinMatch}} or {{.Cfg.MaxMatch}} servers: each gets the same request rate at the same time,
  and you watch them side by side.</p>
{{else}}
  <p class="sub"><a href="/d/fleet">← Fleet overview</a></p>
  <h1>Run a load test</h1>
  <p class="sub">Pick a server. One test runs at a time across the whole site.</p>
{{end}}

{{with .State.Message}}<div class="msg">{{.}}</div>{{end}}

<div class="card {{if .State.Busy}}busy{{end}}" id="state">
  <div class="status"><span class="dot"></span>
  {{if .State.Busy}}
    <strong>Running:</strong>&nbsp;{{.State.Current.Label}}, about {{secsLeft .State.Current.Ends}}s left ·
    <a href="{{.State.Current.Watch}}">watch it live</a>
  {{else if .State.CooldownSecs}}
    <strong>Cooling down</strong>&nbsp;for {{.State.CooldownSecs}}s after the last test
  {{else}}
    <strong>Ready</strong>
  {{end}}
  </div>
</div>

{{if .Server}}
<div class="card">
  <dl>
    <dt>Test</dt><dd>{{if eq .Cfg.Script "break"}}Ramp 0 → 1000 virtual users{{else}}{{.Cfg.Rate}} requests per second{{end}} for {{.Cfg.Duration}}</dd>
    <dt>Target</dt><dd><code>GET /api/v1/health</code> on {{.Server.Name}}</dd>
    <dt>Model</dt><dd>{{.Server.Concurrency}}</dd>
  </dl>
  <form method="post" action="/run/{{.Server.Name}}" style="margin-top:20px">
    <button type="submit" {{if or .State.Busy .State.CooldownSecs}}disabled{{end}}>▶ Start load test</button>
  </form>
  <p><small>You'll be taken to the live dashboard. Limit: {{.Cfg.PerIP}} tests per hour per visitor.</small></p>
</div>
{{else if .Compare}}
<div class="card">
  <form method="post" action="/run/compare" id="race">
    <div class="picks">{{$picked := .Picked}}{{range .Servers}}
      <label><input type="checkbox" name="server" value="{{.Name}}" {{if has $picked .Name}}checked{{end}}>
        <span><strong>{{.Title}}</strong><br><small>{{.Language}}</small></span></label>{{end}}
    </div>
    <dl>
      <dt>Test</dt><dd>{{.Cfg.MatchRate}} requests per second to each server, all at once, for {{.Cfg.MatchDuration}}</dd>
      <dt>Target</dt><dd><code>GET /api/v1/health</code> on each server</dd>
    </dl>
    <button type="submit" style="margin-top:20px" {{if or .State.Busy .State.CooldownSecs}}disabled{{end}}>▶ Start race</button>
  </form>
  <p><small>They share one machine, so a CPU-hungry server can slow its rivals down; that is part of the race.
  Counts as one test toward the limit of {{.Cfg.PerIP}} per hour per visitor.</small></p>
</div>
<script>
  // Allow at most {{.Cfg.MaxMatch}} picks and only enable the button for {{.Cfg.MinMatch}}-{{.Cfg.MaxMatch}}
  const form = document.getElementById('race')
  const button = form.querySelector('button')
  let blocked = button.disabled
  const sync = () => {
    const boxes = [...form.querySelectorAll('input[name=server]')]
    const n = boxes.filter((b) => b.checked).length
    boxes.forEach((b) => { b.disabled = !b.checked && n >= {{.Cfg.MaxMatch}} })
    button.disabled = blocked || n < {{.Cfg.MinMatch}}
  }
  form.addEventListener('change', sync)
  sync()
  // Instead of reloading (which would lose the picks), poll until the runner is free
  if (blocked) {
    const poll = setInterval(async () => {
      const s = await fetch('/run/status').then((r) => r.json()).catch(() => null)
      if (!s || s.busy || s.cooldown_seconds > 0) return
      clearInterval(poll)
      blocked = false
      document.getElementById('state').outerHTML = '<div class="card" id="state"><div class="status"><span class="dot"></span><strong>Ready</strong></div></div>'
      sync()
    }, 3000)
  }
</script>
{{else}}
<a class="card cta" href="/run/compare"><span><strong>⚔ Head to head</strong><br>
  <small>Race {{.Cfg.MinMatch}}-{{.Cfg.MaxMatch}} servers against each other and compare them live.</small></span><span>→</span></a>
<ul>{{range .Servers}}<li><a href="/run/{{.Name}}"><strong>{{.Title}}</strong><br><small>{{.Language}}</small></a></li>{{end}}</ul>
{{end}}
</main></body>
</html>
`))
