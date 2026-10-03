# Build context: repository root (needs bench/k6/src and infra/runner)
FROM golang:1.26 AS build
WORKDIR /src
COPY infra/runner/go.mod infra/runner/*.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/runner .

FROM grafana/k6:2.2.0
COPY --from=build /out/runner /usr/local/bin/runner
COPY bench/k6/src /scripts
USER root
RUN mkdir -p /results && chown k6 /results
USER k6
EXPOSE 8080
ENTRYPOINT ["runner"]
