// Command runner lets visitors start one k6 load test at a time against a
// single server, with fixed test settings, a cooldown and a per-IP limit.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"html/template"
	"log"
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
)

type server struct {
	Name        string `json:"name"`
	Title       string `json:"title"`
	Language    string `json:"language"`
	Framework   string `json:"framework"`
	Concurrency string `json:"concurrency"`
}

type run struct {
	Server  string    `json:"server"`
	TestID  string    `json:"testid"`
	Started time.Time `json:"started"`
	Ends    time.Time `json:"ends"`
}

type config struct {
	addr        string
	scriptsDir  string
	script      string
	rate        int
	duration    time.Duration
	cooldown    time.Duration
	perIPHourly int
	remoteWrite string
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
	}
	if cfg.script != "load" && cfg.script != "break" {
		log.Fatalf("RUNNER_SCRIPT must be load or break, got %q", cfg.script)
	}

	raw, err := os.ReadFile(env("RUNNER_SERVERS_FILE", "/etc/runner/servers.json"))
	if err != nil {
		log.Fatalf("read servers file: %v", err)
	}
	r := &runner{cfg: cfg, byIP: map[string][]time.Time{}, runs: map[string]int{}, rejected: map[string]int{},
		lastRun: map[string]time.Time{}}
	if err := json.Unmarshal(raw, &r.servers); err != nil {
		log.Fatalf("parse servers file: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /run/{$}", r.index)
	mux.HandleFunc("GET /run/status", r.status)
	mux.HandleFunc("GET /run/{name}", r.page)
	mux.HandleFunc("POST /run/{name}", r.start)
	mux.HandleFunc("GET /metrics", r.metrics)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })

	log.Printf("runner listening on %s (script=%s rate=%d duration=%s cooldown=%s per-ip=%d/h)",
		cfg.addr, cfg.script, cfg.rate, cfg.duration, cfg.cooldown, cfg.perIPHourly)
	log.Fatal(http.ListenAndServe(cfg.addr, mux))
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
	if !sameOrigin(req) {
		r.reject(w, &s, http.StatusForbidden, "cross-site", "Load tests can only be started from this site.")
		return
	}

	ip := clientIP(req)
	now := time.Now()

	r.mu.Lock()
	switch {
	case r.current != nil:
		r.mu.Unlock()
		r.reject(w, &s, http.StatusConflict, "busy", "Another load test is already running.")
		return
	case now.Before(r.idleAfter):
		wait := r.idleAfter.Sub(now).Round(time.Second)
		r.mu.Unlock()
		r.reject(w, &s, http.StatusTooManyRequests, "cooldown",
			fmt.Sprintf("The server is cooling down after the last test. Try again in %s.", wait))
		return
	}
	recent := slices.DeleteFunc(r.byIP[ip], func(t time.Time) bool { return now.Sub(t) > time.Hour })
	if len(recent) >= r.cfg.perIPHourly {
		r.byIP[ip] = recent
		r.mu.Unlock()
		r.reject(w, &s, http.StatusTooManyRequests, "rate-limit",
			fmt.Sprintf("You have started %d load tests in the last hour, the limit. Try again later.", len(recent)))
		return
	}
	r.byIP[ip] = append(recent, now)
	cur := &run{Server: s.Name, Started: now, Ends: now.Add(r.expected()),
		TestID: fmt.Sprintf("%s-%s-web-%s", s.Name, r.cfg.script, now.UTC().Format("20060102-150405"))}
	r.current = cur
	r.lastRun[s.Name] = now
	r.mu.Unlock()

	log.Printf("start %s for %s", cur.TestID, ip)
	go r.execute(cur)

	q := url.Values{"var-server": {s.Name}, "var-testid": {cur.TestID}, "from": {"now-5m"}, "to": {"now"},
		"refresh": {"5s"}}
	http.Redirect(w, req, "/d/load-live?"+q.Encode(), http.StatusSeeOther)
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
		busy, running = 1, r.current.Server
	}
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

func (r *runner) execute(cur *run) {
	ctx, cancel := context.WithTimeout(context.Background(), r.expected()+time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "k6", "run", "--no-usage-report", "--quiet",
		"-o", "experimental-prometheus-rw",
		"--tag", "testid="+cur.TestID, "--tag", "server="+cur.Server, "--tag", "script="+r.cfg.script,
		r.cfg.scriptsDir+"/"+r.cfg.script+".ts")
	cmd.Env = append(os.Environ(),
		"URL=http://"+cur.Server+":3000/api/v1/health",
		"RATE="+strconv.Itoa(r.cfg.rate),
		"DURATION="+r.cfg.duration.String(),
		"RESULTS_DIR=/results",
		"K6_PROMETHEUS_RW_SERVER_URL="+r.cfg.remoteWrite,
		"K6_PROMETHEUS_RW_TREND_STATS=p(50),p(90),p(95),p(99),avg,min,max",
		"K6_PROMETHEUS_RW_PUSH_INTERVAL=1s",
	)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr

	result := "ok"
	if err := cmd.Run(); err != nil {
		result = "failed"
		if ctx.Err() != nil {
			result = "timeout"
		}
		log.Printf("%s: %v", cur.TestID, err)
	}
	log.Printf("finish %s: %s", cur.TestID, result)

	r.mu.Lock()
	r.runs[cur.Server+"|"+result]++
	r.current = nil
	r.idleAfter = time.Now().Add(r.cfg.cooldown)
	r.mu.Unlock()
}

// expected is how long a run takes, so pages can show when it ends.
func (r *runner) expected() time.Duration {
	if r.cfg.script == "break" {
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
	Script   string
	Rate     int
	Duration string
	PerIP    int
}

func (r *runner) cfgView() cfgView {
	return cfgView{Script: r.cfg.script, Rate: r.cfg.rate, Duration: r.expected().String(), PerIP: r.cfg.perIPHourly}
}

type pageData struct {
	Server  *server
	Servers []server
	State   state
	Cfg     cfgView
}

func (r *runner) reject(w http.ResponseWriter, s *server, code int, reason, msg string) {
	r.mu.Lock()
	r.rejected[reason]++
	r.mu.Unlock()
	r.render(w, code, pageData{Server: s, State: r.state(msg), Cfg: r.cfgView()})
}

func (r *runner) render(w http.ResponseWriter, code int, d pageData) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	if err := pageTmpl.Execute(w, d); err != nil {
		log.Printf("render: %v", err)
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
// through Caddy, which sets it from the real connection.
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
	"watch": func(c *run) string {
		q := url.Values{"var-server": {c.Server}, "var-testid": {c.TestID}, "from": {"now-5m"}, "to": {"now"},
			"refresh": {"5s"}}
		return "/d/load-live?" + q.Encode()
	},
}).Parse(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{if .Server}}Load test {{.Server.Title}}{{else}}Load tests{{end}} · o11y</title>
{{if or .State.Busy .State.CooldownSecs}}<meta http-equiv="refresh" content="5">{{end}}
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
</style>
</head>
<body><main>
{{if .Server}}
  <p class="sub"><a href="/d/server-{{.Server.Name}}">← {{.Server.Title}} dashboard</a></p>
  <h1>Load test {{.Server.Title}}</h1>
  <p class="sub">{{.Server.Language}} · {{.Server.Framework}}</p>
{{else}}
  <p class="sub"><a href="/d/fleet">← Fleet overview</a></p>
  <h1>Run a load test</h1>
  <p class="sub">Pick a server. One test runs at a time across the whole site.</p>
{{end}}

{{with .State.Message}}<div class="msg">{{.}}</div>{{end}}

<div class="card {{if .State.Busy}}busy{{end}}">
  <div class="status"><span class="dot"></span>
  {{if .State.Busy}}
    <strong>Running:</strong>&nbsp;{{.State.Current.Server}}, about {{secsLeft .State.Current.Ends}}s left ·
    <a href="{{watch .State.Current}}">watch it live</a>
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
{{else}}
<ul>{{range .Servers}}<li><a href="/run/{{.Name}}"><strong>{{.Title}}</strong><br><small>{{.Language}}</small></a></li>{{end}}</ul>
{{end}}
</main></body>
</html>
`))
