type Metric = {
  values: Record<string, number>
  thresholds?: Record<string, { ok: boolean }>
}

type SummaryData = {
  metrics: Record<string, Metric | undefined>
  setup_data?: { url?: string; server?: string }
  state: { testRunDurationMs: number }
}

const resultsDir = __ENV.RESULTS_DIR || '../results'

const ms = (v: number | undefined) => (v === undefined ? '-' : `${v.toFixed(2)}ms`)

export function summarize(script: string) {
  return (data: SummaryData): Record<string, string> => {
    const m = data.metrics
    const duration = m.http_req_duration?.values ?? {}
    const thresholds = Object.entries(m).flatMap(([name, metric]) =>
      Object.entries(metric?.thresholds ?? {}).map(([expr, { ok }]) => ({ metric: name, expr, ok })),
    )

    const result = {
      script,
      server: data.setup_data?.server ?? 'unknown',
      url: data.setup_data?.url ?? __ENV.URL ?? null,
      finishedAt: new Date().toISOString(),
      durationSec: data.state.testRunDurationMs / 1000,
      env: {
        rate: __ENV.RATE ?? null,
        duration: __ENV.DURATION ?? null,
        shape: __ENV.SHAPE || 'steady',
        mix: __ENV.MIX || 'reads',
        encoding: __ENV.ENCODING || 'identity',
      },
      requests: m.http_reqs?.values.count ?? 0,
      reqPerSec: m.http_reqs?.values.rate ?? 0,
      failedRate: m.http_req_failed?.values.rate ?? 0,
      droppedIterations: m.dropped_iterations?.values.count ?? 0,
      maxVUs: m.vus_max?.values.max ?? null,
      latencyMs: duration,
      bytesReceived: m.data_received?.values.count ?? 0,
      thresholds,
      passed: thresholds.every((t) => t.ok),
    }

    const stamp = result.finishedAt.replace(/[:.]/g, '-')
    const file = `${resultsDir}/${stamp}_${script}_${result.server}.json`

    const lines = [
      '',
      `  ${script} -> ${result.server} (${result.url})`,
      `  requests      ${result.requests} (${result.reqPerSec.toFixed(0)}/s)`,
      `  failed        ${(result.failedRate * 100).toFixed(2)}%`,
      `  dropped       ${result.droppedIterations}`,
      `  max VUs       ${result.maxVUs ?? '-'}`,
      `  latency       avg ${ms(duration.avg)}  med ${ms(duration.med)}  p95 ${ms(duration['p(95)'])}  p99 ${ms(duration['p(99)'])}  max ${ms(duration.max)}`,
      ...thresholds.map((t) => `  threshold     ${t.ok ? 'PASS' : 'FAIL'} ${t.metric} ${t.expr}`),
      `  saved         ${file}`,
      '',
    ]

    return {
      stdout: lines.join('\n') + '\n',
      [file]: JSON.stringify(result, null, 2) + '\n',
    }
  }
}
