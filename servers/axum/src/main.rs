use std::sync::LazyLock;
use std::time::Instant;

use axum::Json;
use axum::Router;
use axum::http::StatusCode;
use axum::http::header::CONTENT_TYPE;
use axum::response::{Html, IntoResponse};
use axum::routing::{MethodRouter, get};
use serde_json::{Value, json};
use tokio::net::TcpListener;

const SPEC: &str = include_str!("../../openapi.json");

const DOCS_HTML: &str = r##"<!doctype html>
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
</html>"##;

static START: LazyLock<Instant> = LazyLock::new(Instant::now);

async fn health() -> Json<Value> {
    Json(json!({
        "status": "ok",
        "server": "axum",
        "uptime": START.elapsed().as_secs_f64(),
    }))
}

async fn docs() -> Html<&'static str> {
    Html(DOCS_HTML)
}

async fn openapi() -> impl IntoResponse {
    ([(CONTENT_TYPE, "application/json")], SPEC)
}

async fn not_found() -> impl IntoResponse {
    (StatusCode::NOT_FOUND, Json(json!({ "error": "Not Found" })))
}

// Answer wrong methods with the same 404 as unknown paths, matching the other servers
fn get_only<H, T>(handler: H) -> MethodRouter
where
    H: axum::handler::Handler<T, ()>,
    T: 'static,
{
    get(handler).fallback(not_found)
}

fn main() {
    LazyLock::force(&START);

    let port: u16 = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(3000);
    let workers = std::env::var("WORKERS")
        .ok()
        .and_then(|w| w.parse().ok())
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(1, |n| n.get()));

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers)
        .enable_io()
        .build()
        .expect("failed to build tokio runtime");

    let app = Router::new()
        .route("/api/v1/health", get_only(health))
        .route("/api/v1/docs", get_only(docs))
        .route("/api/v1/openapi.json", get_only(openapi))
        .fallback(not_found);

    runtime.block_on(async move {
        let listener = TcpListener::bind(("0.0.0.0", port)).await.expect("failed to bind");
        println!("axum server listening on http://localhost:{port}/ ({workers} worker threads)");
        axum::serve(listener, app).await.expect("server error");
    });
}
