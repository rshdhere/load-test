import type { Options } from 'k6/options'
import { pickTarget, type Target } from './pick-live.ts'
import { summarize } from './summary.ts'
import { exercise, probe, systemTags, type Api } from './workload.ts'

export const options: Options = {
  scenarios: {
    max_break_test: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '15s', target: 200 },
        { duration: '15s', target: 500 },
        { duration: '15s', target: 1000 },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '0s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  systemTags,
}

export function setup(): Target & { api: Api } {
  const target = pickTarget()
  return { ...target, api: probe(target.url) }
}

export default function (target: Target & { api: Api }) {
  exercise(target.api)
}

export const handleSummary = summarize('break')
