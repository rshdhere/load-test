import spec from "../../openapi.json";

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

const server = Bun.serve({
  port,
  routes: {
    "/api/v1/health": {
      GET: () =>
        Response.json({ status: "ok", server: "bun", uptime: process.uptime() }),
    },
    "/api/v1/docs": {
      GET: () => new Response(docsHtml, { headers: { "Content-Type": "text/html" } }),
    },
    "/api/v1/openapi.json": {
      GET: () => Response.json(spec),
    },
  },
  fetch: () => Response.json({ error: "Not Found" }, { status: 404 }),
});

console.log(`bun server listening on ${server.url}`);
