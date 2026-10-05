// Head-to-head: the same load against 2-3 servers at the same time, one scenario
// per server, so their results land side by side in Grafana. RATE, DURATION,
// SHAPE and MIX apply to each server (see settings.ts).
//
//   TARGETS="go=http://localhost:3104/api/v1/health,bun=http://localhost:3110/api/v1/health" RATE=500 k6 run src/match.ts
import type { Options, Scenario } from 'k6/options'
import { arrival, readSettings, settingTags } from './settings.ts'
import { exercise, probe, systemTags, type Api } from './workload.ts'

type Metric = { values: Record<string, number> }
type SummaryData = { metrics: Record<string, Metric | undefined>; state: { testRunDurationMs: number } }

const targets = (__ENV.TARGETS || '')
  .split(',')
  .map((pair) => pair.trim().split('='))
  .filter(([name, url]) => name && url)
  .map(([name, url]) => ({ name, url }))
if (targets.length < 2 || targets.length > 3) {
  throw new Error(`TARGETS must name 2 or 3 servers as name=url pairs, got "${__ENV.TARGETS ?? ''}"`)
}

const settings = readSettings()
const { rate } = settings
const duration = `${settings.seconds}s`
const match = __ENV.MATCH || `match-${new Date().toISOString().replace(/\D/g, '').slice(0, 14)}`

const scenario = (target: { name: string; url: string }): Scenario => ({
  ...arrival(settings),
  exec: 'hit',
  env: { TARGET: target.url },
  // Each server's requests form their own run, so per-server dashboards pick them up too. Tags go
  // on the scenario because any --tag on the command line replaces options.tags entirely.
  tags: { server: target.name, testid: `${target.name}-${match}`, match, ...settingTags(settings) },
})

export const options: Options = {
  scenarios: Object.fromEntries(targets.map((t) => [t.name, scenario(t)])),
  // Per-server thresholds that always pass, only so the summary has per-server numbers
  thresholds: Object.fromEntries(
    targets.flatMap((t) => [
      [`http_reqs{server:${t.name}}`, ['count>=0']],
      [`http_req_failed{server:${t.name}}`, ['rate>=0']],
      [`http_req_duration{server:${t.name}}`, ['max>=0']],
    ]),
  ),
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  systemTags,
}

// Which racers serve the todo API, keyed by their health URL
export function setup(): Record<string, Api> {
  return Object.fromEntries(targets.map((t) => [t.url, probe(t.url)]))
}

export function hit(apis: Record<string, Api>) {
  exercise(apis[__ENV.TARGET], settings.mix)
}

export function handleSummary(data: SummaryData): Record<string, string> {
  const m = data.metrics
  const seconds = data.state.testRunDurationMs / 1000
  const servers = targets.map(({ name, url }) => {
    const latency = m[`http_req_duration{server:${name}}`]?.values ?? {}
    const requests = m[`http_reqs{server:${name}}`]?.values.count ?? 0
    return {
      server: name,
      url,
      requests,
      reqPerSec: requests / seconds,
      failedRate: m[`http_req_failed{server:${name}}`]?.values.rate ?? 0,
      latencyMs: latency,
    }
  })
  const result = { script: 'match', match, rate, duration, shape: settings.shape, mix: settings.mix,
    finishedAt: new Date().toISOString(), servers }

  const ms = (v: number | undefined) => (v === undefined ? '-' : `${v.toFixed(2)}ms`)
  const lines = [
    '',
    `  ${match}: ${settings.shape}, ${settings.mix} mix, ${rate} req/s peak each for ${duration}`,
    ...servers.map(
      (s) =>
        `  ${s.server.padEnd(10)} ${String(s.requests).padStart(7)} req  failed ${(s.failedRate * 100).toFixed(2)}%  ` +
        `med ${ms(s.latencyMs.med)}  p99 ${ms(s.latencyMs['p(99)'])}`,
    ),
    '',
  ]
  const file = `${__ENV.RESULTS_DIR || '../results'}/${result.finishedAt.replace(/[:.]/g, '-')}_match_${targets
    .map((t) => t.name)
    .join('-')}.json`
  return { stdout: lines.join('\n') + '\n', [file]: JSON.stringify(result, null, 2) + '\n' }
}
