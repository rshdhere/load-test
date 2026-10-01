import json
import time
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse

from api import START

SPEC = settings.SPEC_PATH.read_text()

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


def not_found(_request, exception=None):
    return JsonResponse({"error": "Not Found"}, status=404)


def get_only(view):
    """Answer wrong methods with the same 404 as unknown paths, matching the other servers."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.method != "GET":
            return not_found(request)
        return view(request, *args, **kwargs)

    return wrapper


@get_only
def health(_request):
    return JsonResponse({"status": "ok", "server": "django", "uptime": time.monotonic() - START})


@get_only
def docs(_request):
    return HttpResponse(DOCS_HTML, content_type="text/html; charset=utf-8")


@get_only
def openapi(_request):
    return HttpResponse(SPEC, content_type="application/json")
