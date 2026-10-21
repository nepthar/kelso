package main

import (
	"context"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestForwardFlag(t *testing.T) {
	f := forwards{}
	if err := f.Set("AdGuard=192.168.1.3:3000"); err != nil {
		t.Fatal(err)
	}
	if f["adguard"] != "192.168.1.3:3000" {
		t.Fatalf("got %v", f)
	}
	for _, bad := range []string{
		"adguard", "adguard=192.168.1.3", "adguard=nas.local:80", "adguard=[::1]:80",
		"=192.168.1.3:80", "a b=192.168.1.3:80", "adguard=192.168.1.4:80",
	} {
		if err := f.Set(bad); err == nil {
			t.Errorf("accepted %q", bad)
		}
	}
}

func TestForwardBypassesRules(t *testing.T) {
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "plain via "+r.Host)
	}))
	defer plain.Close()
	secure := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "secure")
	}))
	defer secure.Close()

	e := newEngine(50*time.Millisecond, nil)
	e.Replace(rules(Strict))
	p := &Proxy{
		engine:   e,
		stats:    newStats(),
		forwards: forwards{"adguard": plain.Listener.Addr().String(), "nas": secure.Listener.Addr().String()},
		dial:     (&net.Dialer{}).DialContext,
		slots:    make(chan struct{}, 8),
	}
	httpLn, _ := net.Listen("tcp", "127.0.0.1:0")
	httpsLn, _ := net.Listen("tcp", "127.0.0.1:0")
	defer httpLn.Close()
	defer httpsLn.Close()
	go p.serve(httpLn, p.handleHTTP)
	go p.serve(httpsLn, p.handleHTTPS)

	if code, _ := httpGet(t, httpLn.Addr().String(), "evil.com", 0); code != 503 {
		t.Fatalf("unforwarded name in strict mode: %d", code)
	}
	client := &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			if addr == "nas:443" {
				return net.Dial("tcp", httpsLn.Addr().String())
			}
			return net.Dial("tcp", httpLn.Addr().String())
		},
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
	}}
	for url, want := range map[string]string{
		"http://adguard/":      "plain via adguard",
		"http://AdGuard:8080/": "plain via AdGuard:8080",
		"https://nas/":         "secure",
	} {
		resp, err := client.Get(url)
		if err != nil {
			t.Fatalf("%s: %v", url, err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if string(body) != want {
			t.Errorf("%s: got %q, want %q", url, body, want)
		}
	}
	s := p.stats.Snapshot()
	if s["adguard"]["http"].Allowed != 2 || s["nas"]["https"].Allowed != 1 {
		t.Errorf("stats: %+v", s)
	}
	if len(e.Pending()) != 0 {
		t.Errorf("forwards went pending: %v", e.Pending())
	}
}
