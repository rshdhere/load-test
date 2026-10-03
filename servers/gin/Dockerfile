# Build context: servers/
FROM golang:1.26 AS build
WORKDIR /src/gin
COPY gin/go.mod gin/go.su[m] ./
RUN go mod download
COPY gin/*.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gin-server .

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app/gin
COPY openapi.json /app/openapi.json
COPY --from=build /out/gin-server /app/gin/gin-server
ENV PORT=3000
EXPOSE 3000
CMD ["/app/gin/gin-server"]
