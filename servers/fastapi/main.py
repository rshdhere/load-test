import json
import os
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

SPEC = json.loads((Path(__file__).parent.parent / "openapi.json").read_text())
START = time.monotonic()

app = FastAPI(
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
)

# Serve the shared spec instead of the one FastAPI would generate
app.openapi = lambda: SPEC


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "server": "fastapi", "uptime": time.monotonic() - START}


@app.exception_handler(HTTPException)
async def not_found(_request: Request, exc: HTTPException):
    if exc.status_code in (404, 405):
        return JSONResponse({"error": "Not Found"}, status_code=404)
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


if __name__ == "__main__":
    # The GIL keeps one process on one core, so scale out with worker processes
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 3000)),
        workers=int(os.environ.get("WORKERS", os.cpu_count() or 1)),
    )
