import http from 'k6/http'
import type { RefinedParams, ResponseType } from 'k6/http'

const healthPath = '/api/v1/health'

export const reqParams: RefinedParams<ResponseType> = {
  headers: { 'Accept-Encoding': __ENV.ENCODING || 'identity' },
  timeout: __ENV.TIMEOUT || '8s',
  responseType: 'none',
}

function candidates(): string[] {
  if (__ENV.URL) return [__ENV.URL]
  const host = __ENV.HOST || 'localhost'
  const ports = (__ENV.PORTS || '3000').split(',').map((p) => p.trim()).filter(Boolean)
  return ports.map((port) => `http://${host}:${port}${healthPath}`)
}

export function pickUrl(): string {
  const tried = candidates()
  for (const url of tried) {
    const res = http.get(url, { timeout: '2s', tags: { name: 'pick-live' } })
    if (res.status !== 200) continue

    let server = 'unknown'
    try {
      server = String((res.json() as { server?: string }).server ?? server)
    } catch {}
    console.log(`target: ${server} at ${url}`)
    return url
  }
  throw new Error(`no live server found, tried: ${tried.join(', ')}`)
}
