# Build context: servers/
FROM rust:1.99-slim AS build
WORKDIR /src
COPY openapi.json ./openapi.json
COPY actix/Cargo.toml actix/Cargo.lock ./actix/
COPY actix/src ./actix/src
WORKDIR /src/actix
RUN cargo build --release --locked

FROM debian:trixie-slim
COPY --from=build /src/actix/target/release/actix-server /usr/local/bin/actix-server
USER nobody
ENV PORT=3000
EXPOSE 3000
CMD ["actix-server"]
