#!/usr/bin/env python3
"""Check a server against the todo contract in servers/openapi.json.

    python3 servers/conformance.py http://localhost:3104

Standard library only. It creates, changes and deletes todos, and fills the
store past its 1000-todo cap, so point it at a local or CI server, not a live
one. Exits non-zero if any check fails.
"""

import json
import re
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:3000"
TODOS = f"{BASE}/api/v1/todos"
CAP = 1000
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
failures = []


def call(method, url, body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            status, text = res.status, res.read().decode()
    except urllib.error.HTTPError as err:
        status, text = err.code, err.read().decode()
    try:
        payload = json.loads(text) if text else None
    except ValueError:
        payload = text
    return status, payload


def check(name, ok, detail=""):
    if not ok:
        failures.append(name)
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else f'  ({detail})'}")


def is_error(status, payload, expected):
    return status == expected and isinstance(payload, dict) and isinstance(payload.get("error"), str)


def is_todo(t):
    return (isinstance(t, dict) and isinstance(t.get("id"), int) and not isinstance(t.get("id"), bool)
            and t["id"] >= 1 and isinstance(t.get("title"), str) and isinstance(t.get("done"), bool)
            and STAMP.match(str(t.get("createdAt"))) and STAMP.match(str(t.get("updatedAt"))))


# Health and unknown routes
s, p = call("GET", f"{BASE}/api/v1/health")
check("health answers ok", s == 200 and isinstance(p, dict) and p.get("status") == "ok", f"{s} {p}")
s, p = call("GET", f"{BASE}/api/v1/nope")
check("unknown path is 404 with an error body", is_error(s, p, 404), f"{s} {p}")
s, p = call("PUT", TODOS, {"title": "x"})
check("PUT on the collection is 404", is_error(s, p, 404), f"{s} {p}")

# Create
s, a = call("POST", TODOS, {"title": "  write the conformance test  ", "extra": 1})
check("create returns 201 and a todo", s == 201 and is_todo(a), f"{s} {a}")
if not (s == 201 and is_todo(a)):
    print("cannot continue without a created todo")
    sys.exit(1)
check("create trims the title", a["title"] == "write the conformance test", a["title"])
check("create defaults done to false", a["done"] is False, a["done"])
check("createdAt equals updatedAt on create", a["createdAt"] == a["updatedAt"], f"{a['createdAt']} {a['updatedAt']}")
s, b = call("POST", TODOS, {"title": "second", "done": True})
check("create accepts done", s == 201 and b.get("done") is True and b["id"] > a["id"], f"{s} {b}")

for name, body, raw in [
    ("create rejects a body that is not JSON", None, b"{not json"),
    ("create rejects a JSON array", ["title"], None),
    ("create rejects a missing title", {"done": True}, None),
    ("create rejects a blank title", {"title": "   "}, None),
    ("create rejects a 201-character title", {"title": "x" * 201}, None),
    ("create rejects a title that is not a string", {"title": 5}, None),
    ("create rejects done that is not a boolean", {"title": "x", "done": "yes"}, None),
]:
    s, p = call("POST", TODOS, body, raw)
    check(name, is_error(s, p, 400), f"{s} {p}")
s, p = call("POST", TODOS, {"title": "x" * 200})
check("create accepts a 200-character title", s == 201, f"{s}")

# Read
s, p = call("GET", f"{TODOS}/{a['id']}")
check("get returns the todo", s == 200 and p == a, f"{s} {p}")
for bad in ["999999999", "0", "-1", "abc", "1.5"]:
    s, p = call("GET", f"{TODOS}/{bad}")
    check(f"get /todos/{bad} is 404", is_error(s, p, 404), f"{s} {p}")

# Update
s, p = call("PATCH", f"{TODOS}/{a['id']}", {"done": True})
check("patch done", s == 200 and is_todo(p) and p["done"] is True and p["title"] == a["title"], f"{s} {p}")
s, p = call("PATCH", f"{TODOS}/{a['id']}", {"title": " renamed "})
check("patch title trims and keeps done", s == 200 and p.get("title") == "renamed" and p.get("done") is True, f"{s} {p}")
check("patch moves updatedAt, not createdAt",
      is_todo(p) and p["createdAt"] == a["createdAt"] and p["updatedAt"] >= a["updatedAt"], f"{p}")
for name, body, raw in [
    ("patch rejects an empty object", {}, None),
    ("patch rejects only unknown fields", {"colour": "red"}, None),
    ("patch rejects a blank title", {"title": ""}, None),
    ("patch rejects done that is not a boolean", {"done": 1}, None),
    ("patch rejects a body that is not JSON", None, b"nope"),
]:
    s, p = call("PATCH", f"{TODOS}/{a['id']}", body, raw)
    check(name, is_error(s, p, 400), f"{s} {p}")
s, p = call("PATCH", f"{TODOS}/999999999", {})
check("patch on a missing id is 404 even with a bad body", is_error(s, p, 404), f"{s} {p}")

# Delete
s, p = call("DELETE", f"{TODOS}/{b['id']}")
check("delete returns 204 without a body", s == 204 and p is None, f"{s} {p}")
s, p = call("DELETE", f"{TODOS}/{b['id']}")
check("deleting again is 404", is_error(s, p, 404), f"{s} {p}")
s, p = call("GET", f"{TODOS}/{b['id']}")
check("a deleted todo is gone", is_error(s, p, 404), f"{s} {p}")

# List
s, page = call("GET", TODOS)
ok = s == 200 and isinstance(page, dict) and isinstance(page.get("items"), list) and isinstance(page.get("total"), int)
check("list returns items and total", ok, f"{s} {page}")
if ok:
    ids = [t["id"] for t in page["items"]]
    check("list defaults to 20 items at most", len(ids) <= 20, len(ids))
    check("list is newest first", ids == sorted(ids, reverse=True), ids[:5])
    check("list items are todos", all(is_todo(t) for t in page["items"]))
    s, two = call("GET", f"{TODOS}?limit=2&offset=1")
    check("limit and offset page through the list",
          s == 200 and [t["id"] for t in two["items"]] == ids[1:3] and two["total"] == page["total"], f"{s} {two}")
    s, done = call("GET", f"{TODOS}?done=true&limit=100")
    check("done=true filters", s == 200 and all(t["done"] for t in done["items"]) and done["total"] <= page["total"],
          f"{s}")
    s, notdone = call("GET", f"{TODOS}?done=false&limit=100")
    check("done=true and done=false add up to the total",
          s == 200 and done["total"] + notdone["total"] == page["total"], f"{done['total']} + {notdone.get('total')}")
for query in ["limit=0", "limit=101", "limit=x", "offset=-1", "offset=y", "done=maybe"]:
    s, p = call("GET", f"{TODOS}?{query}")
    check(f"list rejects {query}", is_error(s, p, 400), f"{s} {p}")
s, p = call("GET", f"{TODOS}?limit=100")
check("list accepts limit=100", s == 200 and len(p["items"]) <= 100, f"{s}")

# Cap: creating past 1000 todos drops the oldest
first = None
for i in range(CAP + 1):
    s, t = call("POST", TODOS, {"title": f"fill {i}"})
    if s != 201:
        check("filling the store", False, f"{s} {t}")
        break
    first = first or t
s, p = call("GET", f"{TODOS}?limit=1")
check(f"at most {CAP} todos are kept", s == 200 and p["total"] <= CAP, p.get("total") if isinstance(p, dict) else p)
s, p = call("GET", f"{TODOS}/{first['id']}")
check("the oldest todo is dropped first", is_error(s, p, 404), f"{s} {p}")

print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
sys.exit(1 if failures else 0)
