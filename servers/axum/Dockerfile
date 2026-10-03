# Build context: servers/
FROM rust:1.99-slim AS build
WORKDIR /src
COPY openapi.json ./openapi.json
COPY axum/Cargo.toml axum/Cargo.lock ./axum/
COPY axum/src ./axum/src
WORKDIR /src/axum
RUN cargo build --release --locked

FROM debian:trixie-slim
COPY --from=build /src/axum/target/release/axum-server /usr/local/bin/axum-server
USER nobody
ENV PORT=3000
EXPOSE 3000
CMD ["axum-server"]
