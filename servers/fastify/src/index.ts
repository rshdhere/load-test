import Fastify from "fastify";
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

const app = Fastify();

app.get("/api/v1/health", async () => {
  return { status: "ok", server: "fastify", uptime: process.uptime() };
});

app.get("/api/v1/docs", async (_req, reply) => {
  return reply.type("text/html; charset=utf-8").send(docsHtml);
});

app.get("/api/v1/openapi.json", async () => spec);

app.setNotFoundHandler(async (_req, reply) => {
  return reply.status(404).send({ error: "Not Found" });
});

await app.listen({ port, host: "0.0.0.0" });
console.log(`fastify server listening on http://localhost:${port}/`);
