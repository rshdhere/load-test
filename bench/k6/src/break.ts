import http from 'k6/http'
import type { Options } from 'k6/options'
import { pickUrl, reqParams } from './pick-live.ts'

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
}

export function setup() {
  return { url: pickUrl() }
}

export default function (data: { url: string }) {
  http.get(data.url, reqParams)
}
