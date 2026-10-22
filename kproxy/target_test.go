package main

import (
	"context"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"
)

func TestRuleTargets(t *testing.T) {
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "plain via "+r.Host)
	}))
	defer plain.Close()
	secure := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "secure")
	}))
	defer secure.Close()

	e := newEngine(50*time.Millisecond, nil)
	e.Replace(rules(DefaultDeny,
		"adguard", "allow "+plain.Listener.Addr().String(),
		"nas", "allow "+secure.Listener.Addr().String(),
		"pinned.example.com", "allow "+plain.Listener.Addr().String(),
		"byname", "allow localhost:"+strconv.Itoa(plain.Listener.Addr().(*net.TCPAddr).Port),
		"bare", "allow",
	))
	p := &Proxy{engine: e, stats: newStats(), dial: (&net.Dialer{}).DialContext, slots: make(chan struct{}, 8)}
	httpLn, _ := net.Listen("tcp", "127.0.0.1:0")
	httpsLn, _ := net.Listen("tcp", "127.0.0.1:0")
	defer httpLn.Close()
	defer httpsLn.Close()
	go p.serve(httpLn, p.handleHTTP)
	go p.serve(httpsLn, p.handleHTTPS)

	for _, host := range []string{"evil.com", "bare"} {
		if code, _ := httpGet(t, httpLn.Addr().String(), host, 0); code != 503 {
			t.Errorf("%s: got %d, want 503", host, code)
		}
	}
	client := &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			if _, port, _ := net.SplitHostPort(addr); port == "443" {
				return net.Dial("tcp", httpsLn.Addr().String())
			}
			return net.Dial("tcp", httpLn.Addr().String())
		},
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
	}}
	for url, want := range map[string]string{
		"http://adguard/":            "plain via adguard",
		"http://AdGuard:8080/":       "plain via AdGuard:8080",
		"https://nas/":               "secure",
		"http://pinned.example.com/": "plain via pinned.example.com",
		"http://byname/":             "plain via byname",
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
	if s["adguard"]["http"].Allowed != 2 || s["nas"]["https"].Allowed != 1 ||
		s["example.com"]["http"].Allowed != 1 || s[unknownDomain]["http"].Denied != 1 {
		t.Errorf("stats: %+v", s)
	}
}

func TestBareNameRefusedBeforeRulesLoad(t *testing.T) {
	p := &Proxy{engine: newEngine(time.Second, nil), stats: newStats()}
	if o, _, _ := p.route("adguard", "http", "80"); o != Denied {
		t.Fatalf("got %v", o)
	}
}
