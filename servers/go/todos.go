package main

import (
	"container/list"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

// The todo API from ../openapi.json, kept in memory: ids increase from 1 and at
// most maxTodos are kept, dropping the oldest first.
const maxTodos = 1000

type todo struct {
	ID        int    `json:"id"`
	Title     string `json:"title"`
	Done      bool   `json:"done"`
	CreatedAt string `json:"createdAt"`
	UpdatedAt string `json:"updatedAt"`
}

type todoPage struct {
	Items []todo `json:"items"`
	Total int    `json:"total"`
}

type store struct {
	mu    sync.RWMutex
	next  int
	byID  map[int]*list.Element // values are *todo
	order *list.List            // oldest at the front
}

func newStore() *store {
	return &store{next: 1, byID: map[int]*list.Element{}, order: list.New()}
}

func now() string { return time.Now().UTC().Format("2006-01-02T15:04:05.000Z") }

func (s *store) create(title string, done bool) todo {
	s.mu.Lock()
	defer s.mu.Unlock()
	stamp := now()
	t := &todo{ID: s.next, Title: title, Done: done, CreatedAt: stamp, UpdatedAt: stamp}
	s.next++
	s.byID[t.ID] = s.order.PushBack(t)
	if s.order.Len() > maxTodos {
		oldest := s.order.Front()
		delete(s.byID, oldest.Value.(*todo).ID)
		s.order.Remove(oldest)
	}
	return *t
}

func (s *store) get(id int) (todo, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if e, ok := s.byID[id]; ok {
		return *e.Value.(*todo), true
	}
	return todo{}, false
}

func (s *store) update(id int, title *string, done *bool) (todo, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.byID[id]
	if !ok {
		return todo{}, false
	}
	t := e.Value.(*todo)
	if title != nil {
		t.Title = *title
	}
	if done != nil {
		t.Done = *done
	}
	t.UpdatedAt = now()
	return *t, true
}

func (s *store) remove(id int) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.byID[id]
	if ok {
		delete(s.byID, id)
		s.order.Remove(e)
	}
	return ok
}

// page returns todos newest first, optionally only those with the given done state.
func (s *store) page(limit, offset int, done *bool) todoPage {
	s.mu.RLock()
	defer s.mu.RUnlock()
	p := todoPage{Items: []todo{}}
	for e := s.order.Back(); e != nil; e = e.Prev() {
		t := e.Value.(*todo)
		if done != nil && t.Done != *done {
			continue
		}
		if p.Total >= offset && len(p.Items) < limit {
			p.Items = append(p.Items, *t)
		}
		p.Total++
	}
	return p
}

// ---------------------------------------------------------------- HTTP

var logger = slog.New(slog.NewJSONHandler(os.Stdout, nil))

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

// writeError answers {"error": msg} and logs the rejected request.
func writeError(w http.ResponseWriter, r *http.Request, status int, msg string) {
	logger.Warn("request rejected", "method", r.Method, "path", r.URL.Path, "status", status, "error", msg)
	writeJSON(w, status, map[string]string{"error": msg})
}

const badTitle = "title must be a string of 1-200 characters"

// fields reads a JSON object body. title and done are nil when absent; ok is
// false (after answering 400) when the body or either field is invalid.
func fields(w http.ResponseWriter, r *http.Request) (title *string, done *bool, ok bool) {
	var body map[string]json.RawMessage
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body == nil {
		writeError(w, r, http.StatusBadRequest, "body must be a JSON object")
		return nil, nil, false
	}
	if raw, present := body["title"]; present {
		var s string
		if string(raw) == "null" || json.Unmarshal(raw, &s) != nil {
			writeError(w, r, http.StatusBadRequest, badTitle)
			return nil, nil, false
		}
		s = strings.TrimSpace(s)
		if n := utf8.RuneCountInString(s); n < 1 || n > 200 {
			writeError(w, r, http.StatusBadRequest, badTitle)
			return nil, nil, false
		}
		title = &s
	}
	if raw, present := body["done"]; present {
		var b bool
		if string(raw) == "null" || json.Unmarshal(raw, &b) != nil {
			writeError(w, r, http.StatusBadRequest, "done must be a boolean")
			return nil, nil, false
		}
		done = &b
	}
	return title, done, true
}

func (s *store) list(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit, offset := 20, 0
	var done *bool
	if v := q.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 100 {
			writeError(w, r, http.StatusBadRequest, "limit must be an integer from 1 to 100")
			return
		}
		limit = n
	}
	if v := q.Get("offset"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			writeError(w, r, http.StatusBadRequest, "offset must be an integer of 0 or more")
			return
		}
		offset = n
	}
	if v := q.Get("done"); v != "" {
		if v != "true" && v != "false" {
			writeError(w, r, http.StatusBadRequest, "done must be true or false")
			return
		}
		b := v == "true"
		done = &b
	}
	writeJSON(w, http.StatusOK, s.page(limit, offset, done))
}

func (s *store) createHandler(w http.ResponseWriter, r *http.Request) {
	title, done, ok := fields(w, r)
	if !ok {
		return
	}
	if title == nil {
		writeError(w, r, http.StatusBadRequest, badTitle)
		return
	}
	writeJSON(w, http.StatusCreated, s.create(*title, done != nil && *done))
}

// item handles /api/v1/todos/{id}; any id that is not a positive integer is 404.
func (s *store) item(w http.ResponseWriter, r *http.Request, rawID string) {
	id, err := strconv.Atoi(rawID)
	if err != nil || id < 1 {
		writeError(w, r, http.StatusNotFound, "Not Found")
		return
	}
	switch r.Method {
	case http.MethodGet:
		if t, ok := s.get(id); ok {
			writeJSON(w, http.StatusOK, t)
			return
		}
	case http.MethodPatch:
		if _, ok := s.get(id); !ok {
			break
		}
		title, done, ok := fields(w, r)
		if !ok {
			return
		}
		if title == nil && done == nil {
			writeError(w, r, http.StatusBadRequest, "nothing to update: send title or done")
			return
		}
		if t, ok := s.update(id, title, done); ok {
			writeJSON(w, http.StatusOK, t)
			return
		}
	case http.MethodDelete:
		if s.remove(id) {
			w.WriteHeader(http.StatusNoContent)
			return
		}
	}
	writeError(w, r, http.StatusNotFound, "Not Found")
}
