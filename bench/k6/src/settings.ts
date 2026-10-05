// A test's settings from the environment, validated, and the k6 scenario they
// describe. The runner passes only values a visitor picked from its menus;
// this checks them again so a hand-run script fails early on a typo.
//
//   RATE=500       peak requests per second, per server
//   DURATION=30s   whole seconds
//   SHAPE=steady   steady | ramp | spike
//   MIX=reads      reads | writes | health (see workload.ts)
import type { Scenario } from 'k6/options'
import { MIXES, type Mix } from './workload.ts'

export const SHAPES = ['steady', 'ramp', 'spike'] as const
export type Shape = (typeof SHAPES)[number]

export type Settings = { rate: number; seconds: number; shape: Shape; mix: Mix }

export function readSettings(): Settings {
  const rate = Number(__ENV.RATE || 100)
  if (!Number.isInteger(rate) || rate <= 0) throw new Error(`RATE must be a positive integer, got "${__ENV.RATE}"`)
  const duration = __ENV.DURATION || '30s'
  const match = /^(\d+)s$/.exec(duration)
  if (!match || Number(match[1]) < 10) throw new Error(`DURATION must be whole seconds, 10s or more, got "${duration}"`)
  const shape = (__ENV.SHAPE || 'steady') as Shape
  if (!SHAPES.includes(shape)) throw new Error(`SHAPE must be one of ${SHAPES.join(', ')}, got "${shape}"`)
  const mix = (__ENV.MIX || 'reads') as Mix
  if (!(mix in MIXES)) throw new Error(`MIX must be one of ${Object.keys(MIXES).join(', ')}, got "${mix}"`)
  return { rate, seconds: Number(match[1]), shape, mix }
}

/**
 * An arrival-rate scenario for these settings. Every shape lasts `seconds` and peaks at `rate`:
 *   steady  the peak rate throughout
 *   ramp    climbs from 0 to the peak over 80% of the time, then holds it: where latency starts to bend
 *   spike   a fifth of the peak, a 2s jump to the peak, a short hold at the peak, then back down
 */
export function arrival(s: Settings): Scenario {
  // VUs are only needed for requests in flight (rate x latency); a slow server needs more, a fast one few.
  // Capped so a big test fits the runner's memory limit; past the cap k6 drops iterations, which the
  // dashboards show as the server falling behind.
  const vus = { preAllocatedVUs: Math.min(Math.max(Math.ceil(s.rate / 20), 10), 50),
    maxVUs: Math.min(Math.max(Math.ceil(s.rate / 4), 50), 300) }
  const common = { timeUnit: '1s', gracefulStop: '10s', ...vus }
  const at = (fraction: number) => `${Math.max(1, Math.round(s.seconds * fraction))}s`
  switch (s.shape) {
    case 'steady':
      return { executor: 'constant-arrival-rate', rate: s.rate, duration: `${s.seconds}s`, ...common }
    case 'ramp':
      return { executor: 'ramping-arrival-rate', startRate: 0, ...common,
        stages: [{ target: s.rate, duration: at(0.8) }, { target: s.rate, duration: at(0.2) }] }
    case 'spike': {
      // The two 2s jumps come out of the time, so the whole test still lasts `seconds`
      const base = Math.max(1, Math.round(s.rate / 5))
      const rest = s.seconds - 4
      const part = (fraction: number) => `${Math.max(1, Math.round(rest * fraction))}s`
      return { executor: 'ramping-arrival-rate', startRate: base, ...common,
        stages: [{ target: base, duration: part(0.4) }, { target: s.rate, duration: '2s' },
          { target: s.rate, duration: part(0.2) }, { target: base, duration: '2s' },
          { target: base, duration: `${rest - Math.round(rest * 0.4) - Math.round(rest * 0.2)}s` }] }
    }
  }
}

/** Tags on every metric of the test, so dashboards can tell runs apart. */
export const settingTags = (s: Settings) => ({ rate: String(s.rate), shape: s.shape, mix: s.mix })
