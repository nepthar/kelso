package main

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestDeleteRule(t *testing.T) {
	e := newEngine(time.Second, nil)
	e.Replace(rules(DefaultAsk, "https *.example.com", "tcp :9669 redis redis.lan:9669"))
	h := adminHandler(e, newStats())
	for path, want := range map[string]int{
		"/rules/tls/443/*.Example.com": 204,
		"/rules/tcp/9669/redis":        204,
		"/rules/http/80/example.com":   404,
		"/rules/nope/443/example.com":  400,
		"/rules/tls/x/example.com":     400,
	} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("DELETE", path, nil))
		if w.Code != want {
			t.Errorf("%s: got %d, want %d", path, w.Code, want)
		}
	}
	if got := e.rules().format(); got != rulesFileHeader+"\ndefault ask\n" {
		t.Fatalf("rules left: %q", got)
	}
}

func TestPutAndGetRules(t *testing.T) {
	e := newEngine(time.Second, nil)
	h := adminHandler(e, newStats())
	put := func(body string) int {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("PUT", "/rules", strings.NewReader(body)))
		return w.Code
	}
	body := `{"default":"ask","rules":[
		{"kind":"http","port":80,"name":"adguard","verdict":"allow","dest":"rpi.lab.my-domain.com:3000"},
		{"kind":"tls","port":443,"name":"*.googleapis.com","verdict":"allow"},
		{"kind":"tls","port":443,"name":"ads.googleapis.com","verdict":"deny"},
		{"kind":"udp","port":2343,"name":"video","verdict":"allow","dest":"cam0.lan:2343"}]}`
	if code := put(body); code != 204 {
		t.Fatalf("put: %d", code)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/rules", nil))
	var got rulesJSON
	json.Unmarshal(w.Body.Bytes(), &got)
	if got.Default != DefaultAsk || len(got.Rules) != 4 ||
		got.Rules[1] != (ruleJSON{KindTLS, 443, "*.googleapis.com", Allow, "*:443"}) ||
		got.Rules[2] != (ruleJSON{KindTLS, 443, "ads.googleapis.com", Deny, ""}) {
		t.Fatalf("get: %+v", got)
	}
	for _, bad := range []string{
		`{"default":"ask","rules":[{"kind":"tcp","port":80,"name":"x","verdict":"allow","dest":"x.lan:1"}]}`,
		`{"default":"ask","rules":[{"kind":"https","port":443,"name":"x.com","verdict":"allow"}]}`,
		`{"default":"loose","rules":[]}`,
	} {
		if code := put(bad); code != 400 {
			t.Errorf("%s: got %d", bad, code)
		}
	}
}

func TestBrowserGuard(t *testing.T) {
	e := newEngine(time.Second, nil)
	e.Replace(rules(DefaultAsk))
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
			r = httptest.NewRequest("POST", "/decide",
				strings.NewReader(`{"kind":"tls","port":443,"name":"example.com","verdict":"allow"}`))
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
