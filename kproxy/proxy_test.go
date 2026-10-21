package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/dns/dnsmessage"
)

// clientHello returns the bytes a TLS client sends first for serverName.
func clientHello(t testing.TB, serverName string) []byte {
	t.Helper()
	c, s := net.Pipe()
	go tls.Client(c, &tls.Config{ServerName: serverName, InsecureSkipVerify: true}).Handshake()
	s.SetReadDeadline(time.Now().Add(time.Second))
	var hdr [5]byte
	if _, err := io.ReadFull(s, hdr[:]); err != nil {
		t.Fatal(err)
	}
	body := make([]byte, binary.BigEndian.Uint16(hdr[3:]))
	if _, err := io.ReadFull(s, body); err != nil {
		t.Fatal(err)
	}
	c.Close()
	return append(hdr[:], body...)
}

// fragment re-splits a single handshake record into records of n bytes.
func fragment(rec []byte, n int) []byte {
	var out []byte
	for body := rec[5:]; len(body) > 0; {
		k := min(n, len(body))
		out = append(out, rec[0], rec[1], rec[2], byte(k>>8), byte(k))
		out = append(out, body[:k]...)
		body = body[k:]
	}
	return out
}

// feed writes data to one end of a pipe and returns the other.
func feed(data []byte) net.Conn {
	c, s := net.Pipe()
	go func() {
		c.Write(data)
		io.Copy(io.Discard, c)
	}()
	return s
}

func TestReadSNI(t *testing.T) {
	hello := clientHello(t, "a.b.Example.com")
	for name, data := range map[string][]byte{
		"whole":      hello,
		"fragmented": fragment(hello, 7),
	} {
		sni, raw := readSNI(feed(data))
		if sni != "a.b.Example.com" {
			t.Errorf("%s: sni = %q", name, sni)
		}
		if !bytes.Equal(raw, data) {
			t.Errorf("%s: replay bytes differ (%d vs %d)", name, len(raw), len(data))
		}
	}
	if sni, _ := readSNI(feed(clientHello(t, ""))); sni != "" {
		t.Errorf("no-SNI hello gave %q", sni)
	}
	for _, junk := range [][]byte{[]byte("GET / HTTP/1.1\r\n\r\n"), hello[:20]} {
		c := feed(junk)
		c.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		if sni, _ := readSNI(c); sni != "" {
			t.Errorf("junk gave %q", sni)
		}
	}
}

func TestReadHost(t *testing.T) {
	for req, want := range map[string]string{
		"GET / HTTP/1.1\r\nHost: Example.com:8080\r\n\r\n":  "Example.com",
		"GET http://x.org/ HTTP/1.1\r\nHost: y.org\r\n\r\n": "x.org",
		"GET / HTTP/1.0\r\n\r\n":                            "",
		"garbage\r\n\r\n":                                   "",
	} {
		host, raw := readHost(feed([]byte(req)))
		if host != want {
			t.Errorf("%q: host = %q, want %q", req, host, want)
		}
		if want != "" && string(raw) != req {
			t.Errorf("%q: replay = %q", req, raw)
		}
	}
}

func TestDNSAnswer(t *testing.T) {
	self := [4]byte{10, 0, 0, 2}
	for _, tc := range []struct {
		typ     dnsmessage.Type
		answers int
	}{{dnsmessage.TypeA, 1}, {dnsmessage.TypeAAAA, 0}, {dnsmessage.TypeMX, 0}, {dnsmessage.TypeTXT, 0}} {
		q := dnsmessage.Message{
			Header:    dnsmessage.Header{ID: 42, RecursionDesired: true},
			Questions: []dnsmessage.Question{{Name: dnsmessage.MustNewName("x.example.com."), Type: tc.typ, Class: dnsmessage.ClassINET}},
		}
		qb, _ := q.Pack()
		rb, err := dnsAnswer(qb, self)
		if err != nil {
			t.Fatal(err)
		}
		var m dnsmessage.Message
		if err := m.Unpack(rb); err != nil {
			t.Fatal(err)
		}
		if m.ID != 42 || !m.Response || m.RCode != dnsmessage.RCodeSuccess || len(m.Answers) != tc.answers {
			t.Fatalf("%v: %+v", tc.typ, m)
		}
		if tc.answers == 1 && m.Answers[0].Body.(*dnsmessage.AResource).A != self {
			t.Fatalf("A = %v", m.Answers[0].Body)
		}
	}
}

// testProxy routes every upstream dial to addr.
func testProxy(t testing.TB, r *Rules, addr string) (*Proxy, string, string) {
	return testProxyN(t, r, addr, 64)
}

func testProxyN(t testing.TB, r *Rules, addr string, maxConns int) (*Proxy, string, string) {
	t.Helper()
	e := newEngine(50*time.Millisecond, nil)
	if r != nil {
		e.Replace(r)
	}
	p := &Proxy{engine: e, stats: newStats(), slots: make(chan struct{}, maxConns), dial: func(ctx context.Context, network, _ string) (net.Conn, error) {
		return net.Dial(network, addr)
	}}
	httpLn, _ := net.Listen("tcp", "127.0.0.1:0")
	httpsLn, _ := net.Listen("tcp", "127.0.0.1:0")
	t.Cleanup(func() { httpLn.Close(); httpsLn.Close() })
	go p.serve(httpLn, p.handleHTTP)
	go p.serve(httpsLn, p.handleHTTPS)
	return p, httpLn.Addr().String(), httpsLn.Addr().String()
}

func httpGet(t *testing.T, proxyAddr, host string, size int) (int, int) {
	t.Helper()
	c, err := net.Dial("tcp", proxyAddr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	fmt.Fprintf(c, "GET /?n=%d HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", size, host)
	body, _ := io.ReadAll(c)
	if len(body) == 0 {
		return 0, 0
	}
	var code int
	fmt.Sscanf(string(body), "HTTP/1.1 %d", &code)
	return code, len(body)
}

func TestHTTPThroughProxy(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var n int
		fmt.Sscan(r.URL.Query().Get("n"), &n)
		w.Write(bytes.Repeat([]byte("x"), n))
	}))
	defer up.Close()
	p, httpAddr, _ := testProxy(t, rules(Strict, "example.com", "allow"), up.Listener.Addr().String())

	const big = 8 << 20
	code, n := httpGet(t, httpAddr, "cdn.example.com", big)
	if code != 200 || n < big {
		t.Fatalf("allowed: %d, %d bytes", code, n)
	}
	if code, _ := httpGet(t, httpAddr, "evil.com", 0); code != 503 {
		t.Fatalf("unlisted: %d", code)
	}
	if code, _ := httpGet(t, httpAddr, "1.2.3.4", 0); code != 503 {
		t.Fatalf("ip host: %d", code)
	}

	s := p.stats.Snapshot()
	if c := s["example.com"]["http"]; c.Allowed != 1 || c.BytesIn != int64(n) || c.BytesOut == 0 {
		t.Errorf("example.com stats: %+v (read %d)", c, n)
	}
	if s["evil.com"]["http"].Denied != 1 || s[unknownDomain]["http"].Denied != 1 {
		t.Errorf("stats: %+v", s)
	}
}

func TestHTTPSThroughProxy(t *testing.T) {
	up := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "hello")
	}))
	defer up.Close()
	_, _, httpsAddr := testProxy(t, rules(Track, "evil.com", "deny"), up.Listener.Addr().String())

	get := func(serverName string) (string, error) {
		client := &http.Client{Transport: &http.Transport{
			DialContext:     func(ctx context.Context, _, _ string) (net.Conn, error) { return net.Dial("tcp", httpsAddr) },
			TLSClientConfig: &tls.Config{ServerName: serverName, InsecureSkipVerify: true},
		}}
		resp, err := client.Get("https://" + urlHost(serverName) + "/")
		if err != nil {
			return "", err
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return string(b), nil
	}
	if body, err := get("anything.net"); err != nil || body != "hello" {
		t.Fatalf("track: %q, %v", body, err)
	}
	if _, err := get("evil.com"); err == nil || !strings.Contains(err.Error(), "access denied") {
		t.Fatalf("denied: %v", err)
	}
	if _, err := get(""); err == nil || !strings.Contains(err.Error(), "access denied") {
		t.Fatalf("no SNI in track mode: %v", err)
	}
}

func urlHost(serverName string) string {
	if serverName == "" {
		return "127.0.0.1"
	}
	return serverName
}

func TestMaxConnsWaitsForASlot(t *testing.T) {
	up, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer up.Close()
	go func() {
		for {
			c, err := up.Accept()
			if err != nil {
				return
			}
			go func() { io.Copy(io.Discard, c); c.Close() }()
		}
	}()
	p, httpAddr, _ := testProxyN(t, rules(Strict, "example.com", "allow"), up.Addr().String(), 1)
	conns := func() int64 { return p.stats.Snapshot()["example.com"]["http"].Conns }
	open := func() net.Conn {
		c, err := net.Dial("tcp", httpAddr)
		if err != nil {
			t.Fatal(err)
		}
		fmt.Fprint(c, "GET / HTTP/1.1\r\nHost: example.com\r\n\r\n")
		return c
	}
	waitConns := func(n int64) {
		t.Helper()
		for deadline := time.Now().Add(time.Second); conns() != n; time.Sleep(time.Millisecond) {
			if time.Now().After(deadline) {
				t.Fatalf("conns = %d, want %d", conns(), n)
			}
		}
	}

	a := open()
	waitConns(1)
	b := open()
	defer b.Close()
	time.Sleep(50 * time.Millisecond)
	if conns() != 1 {
		t.Fatal("second connection handled while the only slot was taken")
	}
	a.Close()
	waitConns(2)
}

func TestSnitchHTTPTimeoutClosesWithoutVerdict(t *testing.T) {
	_, httpAddr, _ := testProxy(t, rules(Snitch), "127.0.0.1:1")
	if code, n := httpGet(t, httpAddr, "new.com", 0); code != 0 || n != 0 {
		t.Fatalf("got %d, %d bytes", code, n)
	}
}
