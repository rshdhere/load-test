import http from 'k6/http'
import { check } from 'k6'
import type { Options } from 'k6/options'
import { pickTarget, reqParams, type Target } from './pick-live.ts'
import { summarize } from './summary.ts'

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
}

export function setup(): Target {
  return pickTarget()
}

export default (target: Target) => {
  const res = http.get(target.url, reqParams)
  check(res, {
    'status 200': (r) => r.status === 200,
    'got a response': (r) => r.status !== 0,
  })
}

export const handleSummary = summarize('load')
