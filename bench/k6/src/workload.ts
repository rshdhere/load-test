// What each k6 iteration does to a server: a todo-app mix of reads and writes
// (servers/openapi.json), or just the health check for a server that does not
// serve the todo API yet.
import http from 'k6/http'
import { check } from 'k6'
import type { Options } from 'k6/options'
import { reqParams } from './pick-live.ts'

export type Api = { base: string; todos: boolean }

// Every request is named by route, and k6's per-request url tag is dropped: with
// todo ids in the URL it would create a Prometheus series per todo.
export const systemTags: Options['systemTags'] = [
  'status', 'method', 'name', 'group', 'check', 'error', 'error_code', 'scenario', 'expected_response',
]

/** Works out from a server's health URL whether it serves the todo API. Call it in setup(). */
export function probe(healthUrl: string): Api {
  const base = healthUrl.replace(/\/api\/v1\/health$/, '')
  const res = http.get(`${base}/api/v1/todos?limit=1`, { timeout: '2s', tags: { name: 'probe' } })
  return { base, todos: res.status === 200 }
}

// Share of iterations per operation, out of 100
const MIX: [number, Op][] = [
  [35, list],
  [25, read],
  [15, create],
  [12, update],
  [5, remove],
  [3, listDone],
  [3, createInvalid],
  [2, readMissing],
]
type Op = (base: string) => void

// Todos this VU created and has not deleted. Each VU has its own copy of this
// module; the server may still drop them (it keeps 1000), so their reads can 404.
const mine: number[] = []
const pick = () => mine[Math.floor(Math.random() * mine.length)]

/** Request options: named by route, and only these statuses count as success. */
function expect(statuses: number[], name: string, keepBody = false) {
  return {
    ...reqParams,
    responseType: keepBody ? ('text' as const) : ('none' as const),
    headers: { ...reqParams.headers, 'Content-Type': 'application/json' },
    tags: { name },
    responseCallback: http.expectedStatuses(...statuses),
  }
}

function verify(res: http.Response, statuses: number[]) {
  check(res, { 'expected status': (r) => statuses.includes(r.status) })
}

function list(base: string) {
  const offset = Math.floor(Math.random() * 3) * 20
  verify(http.get(`${base}/api/v1/todos?limit=20&offset=${offset}`, expect([200], 'GET /api/v1/todos')), [200])
}

function listDone(base: string) {
  verify(http.get(`${base}/api/v1/todos?done=true&limit=50`, expect([200], 'GET /api/v1/todos?done')), [200])
}

function create(base: string) {
  const title = `todo ${__VU}-${__ITER}`
  const res = http.post(`${base}/api/v1/todos`, JSON.stringify({ title }), expect([201], 'POST /api/v1/todos', true))
  verify(res, [201])
  if (res.status === 201) {
    mine.push((res.json() as { id: number }).id)
    if (mine.length > 50) mine.shift()
  }
}

function read(base: string) {
  if (mine.length === 0) return create(base)
  const statuses = [200, 404]
  verify(http.get(`${base}/api/v1/todos/${pick()}`, expect(statuses, 'GET /api/v1/todos/{id}')), statuses)
}

function update(base: string) {
  if (mine.length === 0) return create(base)
  const statuses = [200, 404]
  const body = Math.random() < 0.5 ? { done: Math.random() < 0.5 } : { title: `renamed ${__VU}-${__ITER}` }
  verify(http.patch(`${base}/api/v1/todos/${pick()}`, JSON.stringify(body),
    expect(statuses, 'PATCH /api/v1/todos/{id}')), statuses)
}

function remove(base: string) {
  if (mine.length === 0) return create(base)
  const id = mine.splice(Math.floor(Math.random() * mine.length), 1)[0]
  const statuses = [204, 404]
  verify(http.del(`${base}/api/v1/todos/${id}`, null, expect(statuses, 'DELETE /api/v1/todos/{id}')), statuses)
}

function createInvalid(base: string) {
  verify(http.post(`${base}/api/v1/todos`, JSON.stringify({ title: '' }),
    expect([400], 'POST /api/v1/todos (invalid)')), [400])
}

function readMissing(base: string) {
  verify(http.get(`${base}/api/v1/todos/999999999`, expect([404], 'GET /api/v1/todos/{id} (missing)')), [404])
}

/** One iteration against a server. */
export function exercise(api: Api) {
  if (!api.todos) {
    const res = http.get(`${api.base}/api/v1/health`, { ...reqParams, tags: { name: 'GET /api/v1/health' } })
    check(res, { 'status 200': (r) => r.status === 200, 'got a response': (r) => r.status !== 0 })
    return
  }
  let roll = Math.random() * 100
  for (const [weight, op] of MIX) {
    roll -= weight
    if (roll < 0) return op(api.base)
  }
  list(api.base)
}
