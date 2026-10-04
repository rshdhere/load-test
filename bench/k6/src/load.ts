import type { Options } from 'k6/options'
import { pickTarget, type Target } from './pick-live.ts'
import { summarize } from './summary.ts'
import { exercise, probe, systemTags, type Api } from './workload.ts'

const rate = Number(__ENV.RATE || 100)
if (!Number.isInteger(rate) || rate <= 0) {
  throw new Error(`RATE must be a positive integer, got "${__ENV.RATE}"`)
}

const duration = __ENV.DURATION || '30s'
const preAllocatedVUs = Math.min(Math.max(rate, 20), 200)
const maxVUs = Math.min(Math.max(rate * 2, 100), 800)

export const options: Options = {
  scenarios: {
    bench: {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration,
      preAllocatedVUs,
      maxVUs,
      gracefulStop: '10s',
      // Lets the Grafana leaderboard compare latency between runs at the same rate. On the scenario
      // because the runner's and grafana.sh's --tag flags replace options.tags entirely.
      tags: { rate: String(rate) },
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  systemTags,
}

export function setup(): Target & { api: Api } {
  const target = pickTarget()
  return { ...target, api: probe(target.url) }
}

export default (target: Target & { api: Api }) => exercise(target.api)

export const handleSummary = summarize('load')
