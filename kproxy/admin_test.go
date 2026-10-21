package main

import (
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestBrowserGuard(t *testing.T) {
	e := newEngine(time.Second, nil)
	e.Replace(rules(Snitch))
	h := browserGuard(adminHandler(e, newStats()))
	for _, tc := range []struct {
		method, host, ctype string
		want                int
	}{
		{"GET", "127.0.0.1:18099", "", 200},
		{"GET", "localhost:18099", "", 200},
		{"GET", "[::1]:18099", "", 200},
		{"GET", "evil.example:18099", "", 403},
		{"POST", "127.0.0.1:18099", "text/plain", 415},
		{"POST", "127.0.0.1:18099", "application/json", 204},
	} {
		r := httptest.NewRequest(tc.method, "/rules", nil)
		if tc.method == "POST" {
			r = httptest.NewRequest("POST", "/decide", strings.NewReader(`{"domain":"example.com","verdict":"allow"}`))
		}
		r.Host = tc.host
		if tc.ctype != "" {
			r.Header.Set("Content-Type", tc.ctype)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if w.Code != tc.want {
			t.Errorf("%s %s %q: got %d, want %d", tc.method, tc.host, tc.ctype, w.Code, tc.want)
		}
	}
}
