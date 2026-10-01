use std::convert::Infallible;
use std::sync::LazyLock;
use std::time::Instant;

use http_body_util::Full;
use hyper::body::{Bytes, Incoming};
use hyper::header::CONTENT_TYPE;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
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

fn respond(status: StatusCode, content_type: &str, body: impl Into<Bytes>) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, content_type)
        .body(Full::new(body.into()))
        .unwrap()
}

async fn handle(req: Request<Incoming>) -> Result<Response<Full<Bytes>>, Infallible> {
    const JSON: &str = "application/json";

    let res = match (req.method(), req.uri().path()) {
        (&Method::GET, "/api/v1/health") => {
            let body = serde_json::json!({
                "status": "ok",
                "server": "tokio",
                "uptime": START.elapsed().as_secs_f64(),
            });
            respond(StatusCode::OK, JSON, body.to_string())
        }
        (&Method::GET, "/api/v1/docs") => respond(StatusCode::OK, "text/html; charset=utf-8", DOCS_HTML),
        (&Method::GET, "/api/v1/openapi.json") => respond(StatusCode::OK, JSON, SPEC),
        _ => respond(StatusCode::NOT_FOUND, JSON, r#"{"error":"Not Found"}"#),
    };
    Ok(res)
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

    runtime.block_on(async move {
        let listener = TcpListener::bind(("0.0.0.0", port)).await.expect("failed to bind");
        println!("tokio server listening on http://localhost:{port}/ ({workers} worker threads)");

        loop {
            let Ok((stream, _)) = listener.accept().await else { continue };
            tokio::spawn(async move {
                let _ = http1::Builder::new()
                    .serve_connection(TokioIo::new(stream), service_fn(handle))
                    .await;
            });
        }
    });
}
