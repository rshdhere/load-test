package main

import (
	"fmt"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"
)

// The settings a visitor picks for a test, from fixed menus. A submitted value
// must equal a menu entry exactly; bench/k6/src/settings.ts turns them into the
// k6 scenario and checks them again.

type choice struct{ Value, Label, Hint string }

var shapes = []choice{
	{"steady", "Steady", "the same rate the whole time"},
	{"ramp", "Ramp", "climb from zero to the peak, to see where it starts to bend"},
	{"spike", "Spike", "calm, then a sudden burst at the peak, then calm again"},
}

var mixes = []choice{
	{"reads", "Mostly reads", "list and read todos, some writes, a few bad requests"},
	{"writes", "Write-heavy", "create, update and delete todos"},
	{"health", "Health checks", "no todo logic: the framework's bare overhead"},
}

// share is roughly how many requests a shape sends compared with steady, for the page's estimate.
var share = map[string]float64{"steady": 1, "ramp": 0.6, "spike": 0.4}

type settings struct {
	Shape    string        `json:"shape"`
	Mix      string        `json:"mix"`
	Rate     int           `json:"rate"` // peak requests per second, per server
	Duration time.Duration `json:"duration"`
}

func (s settings) Seconds() int { return int(s.Duration.Seconds()) }

// menu is what visitors may choose from, set by RUNNER_RATES, RUNNER_DURATIONS
// and RUNNER_MAX_TOTAL_RATE, with RUNNER_RATE and RUNNER_DURATION preselected.
type menu struct {
	Rates     []int
	Durations []time.Duration
	MaxTotal  int // requests per second across all servers in one test
	Default   settings
}

func loadMenu() (menu, error) {
	m := menu{MaxTotal: envInt("RUNNER_MAX_TOTAL_RATE", 2000)}
	for _, v := range strings.Split(env("RUNNER_RATES", "100,250,500,1000,2000"), ",") {
		n, err := strconv.Atoi(strings.TrimSpace(v))
		if err != nil || n <= 0 {
			return m, fmt.Errorf("RUNNER_RATES: %q is not a positive integer", v)
		}
		m.Rates = append(m.Rates, n)
	}
	for _, v := range strings.Split(env("RUNNER_DURATIONS", "15s,30s,60s"), ",") {
		d, err := time.ParseDuration(strings.TrimSpace(v))
		// k6 needs whole seconds, and a spike needs 4s for its two jumps
		if err != nil || d < 10*time.Second || d%time.Second != 0 {
			return m, fmt.Errorf("RUNNER_DURATIONS: %q is not whole seconds of 10s or more", v)
		}
		m.Durations = append(m.Durations, d)
	}
	m.Default = settings{Shape: shapes[0].Value, Mix: mixes[0].Value, Rate: m.Rates[0], Duration: m.Durations[0]}
	if rate := envInt("RUNNER_RATE", 500); slices.Contains(m.Rates, rate) {
		m.Default.Rate = rate
	}
	if d := envDuration("RUNNER_DURATION", 30*time.Second); slices.Contains(m.Durations, d) {
		m.Default.Duration = d
	}
	return m, nil
}

// parse reads a test's settings from a submitted form for the given number of
// servers. A field left out takes its default; one that is not on the menu, or
// a total rate over the budget, is an error for the visitor.
func (m menu) parse(form url.Values, servers int) (settings, string) {
	s := m.Default
	if v, ok := field(form, "shape"); ok {
		if !slices.ContainsFunc(shapes, func(c choice) bool { return c.Value == v }) {
			return s, "Pick a test shape from the list."
		}
		s.Shape = v
	}
	if v, ok := field(form, "mix"); ok {
		if !slices.ContainsFunc(mixes, func(c choice) bool { return c.Value == v }) {
			return s, "Pick a request mix from the list."
		}
		s.Mix = v
	}
	if v, ok := field(form, "rate"); ok {
		n, err := strconv.Atoi(v)
		if err != nil || !slices.Contains(m.Rates, n) {
			return s, "Pick a request rate from the list."
		}
		s.Rate = n
	}
	if v, ok := field(form, "duration"); ok {
		d, err := time.ParseDuration(v)
		if err != nil || !slices.Contains(m.Durations, d) {
			return s, "Pick a duration from the list."
		}
		s.Duration = d
	}
	if total := s.Rate * servers; total > m.MaxTotal {
		return s, fmt.Sprintf("That is %d requests per second in total, and this machine allows up to %d. "+
			"Pick a lower rate or fewer servers.", total, m.MaxTotal)
	}
	return s, ""
}

func field(form url.Values, name string) (string, bool) {
	v, ok := form[name]
	if !ok || len(v) == 0 {
		return "", false
	}
	return v[0], true
}
