import express from "express";
import type { NextFunction, Request, Response } from "express";
import spec from "../../openapi.json" with { type: "json" };

const port = Number(process.env.PORT ?? 3000);

const docsHtml = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>API Docs</title>
    <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
      SwaggerUIBundle({ url: "/api/v1/openapi.json", dom_id: "#swagger-ui" });
    </script>
  </body>
</html>`;

// ---------------------------------------------------------------- todos

// The todo API from ../../openapi.json, kept in memory. A Map keeps insertion
// order, so its first entry is the oldest: the one dropped past MAX_TODOS.
const MAX_TODOS = 1000;
const BAD_TITLE = "title must be a string of 1-200 characters";

type Todo = { id: number; title: string; done: boolean; createdAt: string; updatedAt: string };

const todos = new Map<number, Todo>();
let nextId = 1;

class Invalid extends Error {}

/** Answers {"error": message} and logs the rejected request as one JSON line. */
function fail(req: Request, res: Response, status: number, message: string) {
  console.log(JSON.stringify({ time: new Date().toISOString(), level: "WARN", msg: "request rejected",
    method: req.method, path: req.path, status, error: message }));
  res.status(status).json({ error: message });
}

/** The body's title (trimmed) and done, if present; throws Invalid for a bad body or field. */
function fields(body: unknown): { title?: string; done?: boolean } {
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new Invalid("body must be a JSON object");
  }
  const input = body as Record<string, unknown>;
  const out: { title?: string; done?: boolean } = {};
  if ("title" in input) {
    const title = typeof input.title === "string" ? input.title.trim() : "";
    const length = [...title].length;
    if (length < 1 || length > 200) throw new Invalid(BAD_TITLE);
    out.title = title;
  }
  if ("done" in input) {
    if (typeof input.done !== "boolean") throw new Invalid("done must be a boolean");
    out.done = input.done;
  }
  return out;
}

/** The todo with this id; anything that is not a positive integer finds nothing. */
function find(rawId: string): Todo | undefined {
  return /^\d+$/.test(rawId) ? todos.get(Number(rawId)) : undefined;
}

/** A query parameter as an integer in [min, max], its default when absent, or undefined when invalid. */
function intParam(value: unknown, fallback: number, min: number, max: number): number | undefined {
  if (value === undefined) return fallback;
  if (typeof value !== "string" || !/^\d+$/.test(value)) return undefined;
  const n = Number(value);
  return n >= min && n <= max ? n : undefined;
}

// ---------------------------------------------------------------- routes

const app = express();
app.use(express.json());

app.get("/api/v1/health", (_req, res) => {
  res.json({ status: "ok", server: "express", uptime: process.uptime() });
});

app.get("/api/v1/docs", (_req, res) => {
  res.type("html").send(docsHtml);
});

app.get("/api/v1/openapi.json", (_req, res) => {
  res.json(spec);
});

app.get("/api/v1/todos", (req, res) => {
  const limit = intParam(req.query.limit, 20, 1, 100);
  if (limit === undefined) return fail(req, res, 400, "limit must be an integer from 1 to 100");
  const offset = intParam(req.query.offset, 0, 0, Number.MAX_SAFE_INTEGER);
  if (offset === undefined) return fail(req, res, 400, "offset must be an integer of 0 or more");
  const done = req.query.done;
  if (done !== undefined && done !== "true" && done !== "false") {
    return fail(req, res, 400, "done must be true or false");
  }
  const matching = [...todos.values()].reverse().filter((t) => done === undefined || t.done === (done === "true"));
  res.json({ items: matching.slice(offset, offset + limit), total: matching.length });
});

app.post("/api/v1/todos", (req, res) => {
  let body;
  try {
    body = fields(req.body);
  } catch (e) {
    if (e instanceof Invalid) return fail(req, res, 400, e.message);
    throw e;
  }
  if (body.title === undefined) return fail(req, res, 400, BAD_TITLE);
  const stamp = new Date().toISOString();
  const todo: Todo = { id: nextId++, title: body.title, done: body.done ?? false, createdAt: stamp, updatedAt: stamp };
  todos.set(todo.id, todo);
  if (todos.size > MAX_TODOS) todos.delete(todos.keys().next().value!);
  res.status(201).json(todo);
});

app.get("/api/v1/todos/:id", (req, res) => {
  const todo = find(req.params.id);
  if (!todo) return fail(req, res, 404, "Not Found");
  res.json(todo);
});

app.patch("/api/v1/todos/:id", (req, res) => {
  const todo = find(req.params.id);
  if (!todo) return fail(req, res, 404, "Not Found");
  let body;
  try {
    body = fields(req.body);
  } catch (e) {
    if (e instanceof Invalid) return fail(req, res, 400, e.message);
    throw e;
  }
  if (body.title === undefined && body.done === undefined) {
    return fail(req, res, 400, "nothing to update: send title or done");
  }
  Object.assign(todo, body, { updatedAt: new Date().toISOString() });
  res.json(todo);
});

app.delete("/api/v1/todos/:id", (req, res) => {
  const todo = find(req.params.id);
  if (!todo) return fail(req, res, 404, "Not Found");
  todos.delete(todo.id);
  res.status(204).end();
});

app.use((req, res) => {
  fail(req, res, 404, "Not Found");
});

// express.json() throws on a body that is not JSON; answer like any other bad body
app.use((err: unknown, req: Request, res: Response, _next: NextFunction) => {
  const status = (err as { status?: number }).status;
  if (status === 400) return fail(req, res, 400, "body must be a JSON object");
  console.error(err);
  fail(req, res, 500, "Internal Server Error");
});

app.listen(port, () => {
  console.log(`express server listening on http://localhost:${port}/`);
});
