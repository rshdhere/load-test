# Build context: servers/
FROM golang:1.26 AS build
WORKDIR /src/go
COPY go/go.mod go/go.su[m] ./
RUN go mod download
COPY go/*.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/go-server .

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app/go
COPY openapi.json /app/openapi.json
COPY --from=build /out/go-server /app/go/go-server
ENV PORT=3000
EXPOSE 3000
CMD ["/app/go/go-server"]
