import json
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

SPEC = json.loads((Path(__file__).parent.parent / "openapi.json").read_text())
START = time.monotonic()
MAX_TODOS = 1000
BAD_TITLE = "title must be a string of 1-200 characters"

app = FastAPI(
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
)

# Serve the shared spec instead of the one FastAPI would generate
app.openapi = lambda: SPEC

# Todos in memory, oldest first. Every handler is async, so they all run on the
# event loop's one thread and the store needs no lock.
todos: "OrderedDict[int, dict]" = OrderedDict()
next_id = 1


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def error(request: Request, status: int, message: str) -> JSONResponse:
    """Answer {"error": message} and log the rejected request as one JSON line."""
    print(json.dumps({"time": now(), "level": "WARN", "msg": "request rejected", "method": request.method,
                      "path": request.url.path, "status": status, "error": message}), flush=True)
    return JSONResponse({"error": message}, status_code=status)


class Invalid(Exception):
    def __init__(self, message: str):
        self.message = message


async def fields(request: Request) -> dict:
    """The body's title (trimmed) and done, if present; raises Invalid for a bad body or field."""
    try:
        body = await request.json()
    except ValueError:
        raise Invalid("body must be a JSON object")
    if not isinstance(body, dict):
        raise Invalid("body must be a JSON object")
    out = {}
    if "title" in body:
        title = body["title"]
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise Invalid(BAD_TITLE)
        out["title"] = title.strip()
    if "done" in body:
        if not isinstance(body["done"], bool):
            raise Invalid("done must be a boolean")
        out["done"] = body["done"]
    return out


def find(raw_id: str) -> "dict | None":
    """The todo with this id; anything that is not a positive integer finds nothing."""
    if not raw_id.isascii() or not raw_id.isdigit():
        return None
    return todos.get(int(raw_id))


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "server": "fastapi", "uptime": time.monotonic() - START}


@app.get("/api/v1/todos")
async def list_todos(request: Request):
    q = request.query_params
    try:
        limit = int(q.get("limit", "20"))
        assert 1 <= limit <= 100
    except (ValueError, AssertionError):
        return error(request, 400, "limit must be an integer from 1 to 100")
    try:
        offset = int(q.get("offset", "0"))
        assert offset >= 0
    except (ValueError, AssertionError):
        return error(request, 400, "offset must be an integer of 0 or more")
    done = q.get("done")
    if done not in (None, "true", "false"):
        return error(request, 400, "done must be true or false")
    matching = [t for t in reversed(todos.values()) if done is None or t["done"] == (done == "true")]
    return {"items": matching[offset:offset + limit], "total": len(matching)}


@app.post("/api/v1/todos")
async def create_todo(request: Request):
    global next_id
    try:
        body = await fields(request)
    except Invalid as e:
        return error(request, 400, e.message)
    if "title" not in body:
        return error(request, 400, BAD_TITLE)
    stamp = now()
    todo = {"id": next_id, "title": body["title"], "done": body.get("done", False),
            "createdAt": stamp, "updatedAt": stamp}
    todos[next_id] = todo
    next_id += 1
    if len(todos) > MAX_TODOS:
        todos.popitem(last=False)
    return JSONResponse(todo, status_code=201)


@app.get("/api/v1/todos/{todo_id}")
async def get_todo(request: Request, todo_id: str):
    todo = find(todo_id)
    return todo if todo else error(request, 404, "Not Found")


@app.patch("/api/v1/todos/{todo_id}")
async def update_todo(request: Request, todo_id: str):
    todo = find(todo_id)
    if not todo:
        return error(request, 404, "Not Found")
    try:
        body = await fields(request)
    except Invalid as e:
        return error(request, 400, e.message)
    if not body:
        return error(request, 400, "nothing to update: send title or done")
    todo.update(body, updatedAt=now())
    return todo


@app.delete("/api/v1/todos/{todo_id}")
async def delete_todo(request: Request, todo_id: str):
    todo = find(todo_id)
    if not todo:
        return error(request, 404, "Not Found")
    del todos[todo["id"]]
    return Response(status_code=204)


@app.exception_handler(HTTPException)
async def not_found(request: Request, exc: HTTPException):
    if exc.status_code in (404, 405):
        return error(request, 404, "Not Found")
    return error(request, exc.status_code, str(exc.detail))


if __name__ == "__main__":
    # One process, so every request sees the same in-memory todos (the GIL keeps
    # it on one core, like the single-process Node and Bun servers)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 3000)),
        workers=1,
        # No per-request access log, like gunicorn's accesslog = None for Flask and Django
        access_log=False,
    )
