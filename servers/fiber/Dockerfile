# Build context: servers/
FROM golang:1.26 AS build
WORKDIR /src/fiber
COPY fiber/go.mod fiber/go.su[m] ./
RUN go mod download
COPY fiber/*.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/fiber-server .

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app/fiber
COPY openapi.json /app/openapi.json
COPY --from=build /out/fiber-server /app/fiber/fiber-server
ENV PORT=3000
EXPOSE 3000
CMD ["/app/fiber/fiber-server"]
