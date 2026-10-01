import { createServer, type ServerResponse } from "node:http";
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

const specJson = JSON.stringify(spec);

function send(res: ServerResponse, status: number, contentType: string, body: string) {
  res.writeHead(status, {
    "Content-Type": contentType,
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

function sendJson(res: ServerResponse, status: number, body: string) {
  send(res, status, "application/json; charset=utf-8", body);
}

const server = createServer((req, res) => {
  const path = req.url?.split("?", 1)[0];

  if (req.method === "GET") {
    switch (path) {
      case "/api/v1/health":
        return sendJson(
          res,
          200,
          JSON.stringify({ status: "ok", server: "node", uptime: process.uptime() }),
        );
      case "/api/v1/docs":
        return send(res, 200, "text/html; charset=utf-8", docsHtml);
      case "/api/v1/openapi.json":
        return sendJson(res, 200, specJson);
    }
  }

  sendJson(res, 404, JSON.stringify({ error: "Not Found" }));
});

server.listen(port, () => {
  console.log(`node server listening on http://localhost:${port}/`);
});
