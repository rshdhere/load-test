import json
import time
from pathlib import Path

from flask import Flask, Response, jsonify

SPEC = (Path(__file__).parent.parent / "openapi.json").read_text()
START = time.monotonic()

DOCS_HTML = """<!doctype html>
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
</html>"""

# Validate the spec once at startup so a broken file fails fast
json.loads(SPEC)

app = Flask(__name__)


@app.get("/api/v1/health")
def health():
    return jsonify(status="ok", server="flask", uptime=time.monotonic() - START)


@app.get("/api/v1/docs")
def docs():
    return Response(DOCS_HTML, mimetype="text/html")


@app.get("/api/v1/openapi.json")
def openapi():
    return Response(SPEC, mimetype="application/json")


# Answer wrong methods with the same 404 as unknown paths, matching the other servers
@app.errorhandler(404)
@app.errorhandler(405)
def not_found(_error):
    return jsonify(error="Not Found"), 404
