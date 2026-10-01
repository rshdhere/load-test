package main

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
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
		log.Fatalf("failed to read openapi spec (run from servers/gin or set SPEC_PATH): %v", err)
	}

	gin.SetMode(gin.ReleaseMode)
	// gin.New skips the per-request logger that gin.Default adds
	r := gin.New()
	r.Use(gin.Recovery())

	r.GET("/api/v1/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok", "server": "gin", "uptime": time.Since(start).Seconds()})
	})
	r.GET("/api/v1/docs", func(c *gin.Context) {
		c.Data(http.StatusOK, "text/html; charset=utf-8", []byte(docsHTML))
	})
	r.GET("/api/v1/openapi.json", func(c *gin.Context) {
		c.Data(http.StatusOK, "application/json", spec)
	})
	r.NoRoute(func(c *gin.Context) {
		c.JSON(http.StatusNotFound, gin.H{"error": "Not Found"})
	})

	fmt.Printf("gin server listening on http://localhost:%d/ (GOMAXPROCS=%d)\n", port, workers)
	log.Fatal(r.Run(fmt.Sprintf("0.0.0.0:%d", port)))
}
