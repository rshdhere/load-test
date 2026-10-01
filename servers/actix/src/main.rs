use std::sync::LazyLock;
use std::time::Instant;

use actix_web::http::header::ContentType;
use actix_web::{App, HttpResponse, HttpServer, Resource, web};
use serde_json::json;

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

async fn health() -> HttpResponse {
    HttpResponse::Ok().json(json!({
        "status": "ok",
        "server": "actix",
        "uptime": START.elapsed().as_secs_f64(),
    }))
}

async fn docs() -> HttpResponse {
    HttpResponse::Ok().content_type(ContentType::html()).body(DOCS_HTML)
}

async fn openapi() -> HttpResponse {
    HttpResponse::Ok().content_type(ContentType::json()).body(SPEC)
}

async fn not_found() -> HttpResponse {
    HttpResponse::NotFound().json(json!({ "error": "Not Found" }))
}

// Answer wrong methods with the same 404 as unknown paths, matching the other servers
fn get_only<F, Args>(path: &str, handler: F) -> Resource
where
    F: actix_web::Handler<Args>,
    Args: actix_web::FromRequest + 'static,
    F::Output: actix_web::Responder + 'static,
{
    web::resource(path)
        .route(web::get().to(handler))
        .default_service(web::to(not_found))
}

fn main() -> std::io::Result<()> {
    LazyLock::force(&START);

    let port: u16 = std::env::var("PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(3000);
    let workers = std::env::var("WORKERS")
        .ok()
        .and_then(|w| w.parse().ok())
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(1, |n| n.get()));

    actix_web::rt::System::new().block_on(async move {
        let server = HttpServer::new(|| {
            App::new()
                .service(get_only("/api/v1/health", health))
                .service(get_only("/api/v1/docs", docs))
                .service(get_only("/api/v1/openapi.json", openapi))
                .default_service(web::to(not_found))
        })
        .workers(workers)
        .bind(("0.0.0.0", port))?;

        println!("actix server listening on http://localhost:{port}/ ({workers} worker threads)");
        server.run().await
    })
}
