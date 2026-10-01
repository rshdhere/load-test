import express from "express";
import spec from "../../openapi.json" with { type: "json" };

const port = Number(process.env.PORT ?? 3000);

const docsHtml = `<!doctype html>
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
</html>`;

const app = express();

app.get("/api/v1/health", (_req, res) => {
  res.json({ status: "ok", server: "express", uptime: process.uptime() });
});

app.get("/api/v1/docs", (_req, res) => {
  res.type("html").send(docsHtml);
});

app.get("/api/v1/openapi.json", (_req, res) => {
  res.json(spec);
});

app.use((_req, res) => {
  res.status(404).json({ error: "Not Found" });
});

app.listen(port, () => {
  console.log(`express server listening on http://localhost:${port}/`);
});
