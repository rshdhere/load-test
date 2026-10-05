import type { Options } from 'k6/options'
import { pickTarget, type Target } from './pick-live.ts'
import { summarize } from './summary.ts'
import { arrival, readSettings, settingTags } from './settings.ts'
import { exercise, probe, systemTags, type Api } from './workload.ts'

// RATE, DURATION, SHAPE and MIX: see settings.ts
const settings = readSettings()

export const options: Options = {
  scenarios: {
    // Tags go on the scenario because the runner's and grafana.sh's --tag flags replace options.tags
    // entirely; rate lets the leaderboard compare latency between runs at the same rate.
    bench: { ...arrival(settings), tags: settingTags(settings) },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  systemTags,
}

export function setup(): Target & { api: Api } {
  const target = pickTarget()
  return { ...target, api: probe(target.url) }
}

export default (target: Target & { api: Api }) => exercise(target.api, settings.mix)

export const handleSummary = summarize('load')
