package main

import (
	"fmt"
	"log"
	"os"
	"runtime"
	"strconv"
	"time"

	"github.com/gofiber/fiber/v2"
)

const docsHTML = `<!doctype html>
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
</html>`

func envInt(key string, fallback int) int {
	if n, err := strconv.Atoi(os.Getenv(key)); err == nil && n > 0 {
		return n
	}
	return fallback
}

func main() {
	start := time.Now()
	port := envInt("PORT", 3000)
	workers := envInt("WORKERS", runtime.NumCPU())
	runtime.GOMAXPROCS(workers)

	specPath := os.Getenv("SPEC_PATH")
	if specPath == "" {
		specPath = "../openapi.json"
	}
	spec, err := os.ReadFile(specPath)
	if err != nil {
		log.Fatalf("failed to read openapi spec (run from servers/fiber or set SPEC_PATH): %v", err)
	}

	app := fiber.New(fiber.Config{DisableStartupMessage: true})

	app.Get("/api/v1/health", func(c *fiber.Ctx) error {
		return c.JSON(fiber.Map{"status": "ok", "server": "fiber", "uptime": time.Since(start).Seconds()})
	})
	app.Get("/api/v1/docs", func(c *fiber.Ctx) error {
		c.Set(fiber.HeaderContentType, fiber.MIMETextHTMLCharsetUTF8)
		return c.SendString(docsHTML)
	})
	app.Get("/api/v1/openapi.json", func(c *fiber.Ctx) error {
		c.Set(fiber.HeaderContentType, fiber.MIMEApplicationJSON)
		return c.Send(spec)
	})
	app.Use(func(c *fiber.Ctx) error {
		return c.Status(fiber.StatusNotFound).JSON(fiber.Map{"error": "Not Found"})
	})

	fmt.Printf("fiber server listening on http://localhost:%d/ (GOMAXPROCS=%d)\n", port, workers)
	log.Fatal(app.Listen(fmt.Sprintf("0.0.0.0:%d", port)))
}
