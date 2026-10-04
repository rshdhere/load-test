package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
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

type health struct {
	Status string  `json:"status"`
	Server string  `json:"server"`
	Uptime float64 `json:"uptime"`
}

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
		log.Fatalf("failed to read openapi spec (run from servers/go or set SPEC_PATH): %v", err)
	}

	todos := newStore()
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch path := r.URL.Path; {
		case path == "/api/v1/todos" && r.Method == http.MethodGet:
			todos.list(w, r)
			return
		case path == "/api/v1/todos" && r.Method == http.MethodPost:
			todos.createHandler(w, r)
			return
		case strings.HasPrefix(path, "/api/v1/todos/"):
			todos.item(w, r, strings.TrimPrefix(path, "/api/v1/todos/"))
			return
		}
		if r.Method == http.MethodGet {
			switch r.URL.Path {
			case "/api/v1/health":
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(health{"ok", "go", time.Since(start).Seconds()})
				return
			case "/api/v1/docs":
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.Write([]byte(docsHTML))
				return
			case "/api/v1/openapi.json":
				w.Header().Set("Content-Type", "application/json")
				w.Write(spec)
				return
			}
		}
		writeError(w, r, http.StatusNotFound, "Not Found")
	})

	addr := fmt.Sprintf("0.0.0.0:%d", port)
	fmt.Printf("go server listening on http://localhost:%d/ (GOMAXPROCS=%d)\n", port, workers)
	log.Fatal(http.ListenAndServe(addr, handler))
}
