import http from 'k6/http'
import { check } from 'k6'
import type { Options } from 'k6/options'

const url = __ENV.URL
if (!url) {
  throw new Error('URL is required, e.g. URL=http://localhost:3000/api/v1/health')
}

const rate = Number(__ENV.RATE || 100)
if (!Number.isInteger(rate) || rate <= 0) {
  throw new Error(`RATE must be a positive integer, got "${__ENV.RATE}"`)
}

const duration = __ENV.DURATION || '30s'
const encoding = __ENV.ENCODING || 'identity'
const timeout = __ENV.TIMEOUT || '8s'
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
    },
  },
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  tags: { target: url, encoding },
}

const params = {
  headers: { 'Accept-Encoding': encoding },
  timeout,
}

export default () => {
  const res = http.get(url, params)
  check(res, {
    'status 200': (r) => r.status === 200,
    'got a response': (r) => r.status !== 0,
  })
}
