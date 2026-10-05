# The E2B "arena": every server, k6 and the k6 scripts in one Debian image, turned
# into an E2B sandbox template (see infra/arena/README.md). It copies what the
# server images CI already built contain instead of compiling anything again.
# Build context: repository root.
#   docker build -f infra/arena/Dockerfile --build-arg PREFIX=o11y --build-arg TAG=local -t o11y/arena:local .
ARG PREFIX=o11y
ARG TAG=local

FROM ${PREFIX}/actix:${TAG} AS actix
FROM ${PREFIX}/axum:${TAG} AS axum
FROM ${PREFIX}/tokio:${TAG} AS tokio
FROM ${PREFIX}/go:${TAG} AS go
FROM ${PREFIX}/gin:${TAG} AS gin
FROM ${PREFIX}/fiber:${TAG} AS fiber
FROM ${PREFIX}/fastapi:${TAG} AS fastapi
FROM ${PREFIX}/flask:${TAG} AS flask
FROM ${PREFIX}/django:${TAG} AS django
FROM ${PREFIX}/bun:${TAG} AS bun
FROM ${PREFIX}/node:${TAG} AS node
FROM ${PREFIX}/express:${TAG} AS express
FROM ${PREFIX}/fastify:${TAG} AS fastify
FROM grafana/k6:2.2.0 AS k6

# Debian trixie, like the Rust servers' runtime image, with CPython 3.14 for the Python servers
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# taskset (util-linux) pins the server and k6 to separate CPUs; curl for readiness checks
RUN apt-get update && apt-get install -y --no-install-recommends util-linux curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=k6 /usr/bin/k6 /usr/local/bin/k6
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=actix /usr/local/bin/actix-server /usr/local/bin/
COPY --from=axum /usr/local/bin/axum-server /usr/local/bin/
COPY --from=tokio /usr/local/bin/tokio-server /usr/local/bin/

# /app/<name> as in each server image, sharing /app/openapi.json
COPY --from=go /app /app
COPY --from=gin /app /app
COPY --from=fiber /app /app
COPY --from=fastapi /app /app
COPY --from=flask /app /app
COPY --from=django /app /app
COPY --from=bun /app /app
COPY --from=node /app /app
COPY --from=express /app /app
COPY --from=fastify /app /app

COPY servers/fastapi/requirements.txt /tmp/req/fastapi.txt
COPY servers/flask/requirements.txt /tmp/req/flask.txt
COPY servers/django/requirements.txt /tmp/req/django.txt
RUN pip install -r /tmp/req/fastapi.txt -r /tmp/req/flask.txt -r /tmp/req/django.txt && rm -rf /tmp/req

COPY bench/k6/src /scripts
COPY infra/servers.json /arena/servers.json
COPY infra/arena/arena-serve /usr/local/bin/arena-serve
RUN chmod +x /usr/local/bin/arena-serve
