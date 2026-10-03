# Build context: servers/
FROM rust:1.99-slim AS build
WORKDIR /src
COPY openapi.json ./openapi.json
COPY tokio/Cargo.toml tokio/Cargo.lock ./tokio/
COPY tokio/src ./tokio/src
WORKDIR /src/tokio
RUN cargo build --release --locked

FROM debian:trixie-slim
COPY --from=build /src/tokio/target/release/tokio-server /usr/local/bin/tokio-server
USER nobody
ENV PORT=3000
EXPOSE 3000
CMD ["tokio-server"]
