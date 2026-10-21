package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	headTimeout  = 10 * time.Second
	maxHeadBytes = 64 << 10
)

type Proxy struct {
	engine *Engine
	stats  *Stats
	dial   func(ctx context.Context, network, addr string) (net.Conn, error)
}

func (p *Proxy) serve(ln net.Listener, handle func(net.Conn)) {
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Printf("%s: %v", ln.Addr(), err)
			return
		}
		go func() {
			defer c.Close()
			handle(c)
		}()
	}
}

// recorder keeps every byte read through it so it can be replayed upstream.
type recorder struct {
	r   io.Reader
	buf bytes.Buffer
}

func (r *recorder) Read(b []byte) (int, error) {
	n, err := r.r.Read(b)
	r.buf.Write(b[:n])
	return n, err
}

// helloConn feeds a real connection to crypto/tls for parsing only.
type helloConn struct {
	net.Conn
	rec *recorder
}

func (c helloConn) Read(b []byte) (int, error)  { return c.rec.Read(b) }
func (c helloConn) Write(b []byte) (int, error) { return 0, io.ErrClosedPipe }

var errHelloRead = errors.New("client hello read")

// readSNI parses the ClientHello on c and returns its server name (possibly
// empty) and every byte consumed from c.
func readSNI(c net.Conn) (string, []byte) {
	rec := &recorder{r: io.LimitReader(c, maxHeadBytes)}
	var sni string
	cfg := &tls.Config{GetConfigForClient: func(h *tls.ClientHelloInfo) (*tls.Config, error) {
		sni = h.ServerName
		return nil, errHelloRead
	}}
	tls.Server(helloConn{c, rec}, cfg).Handshake()
	return sni, rec.buf.Bytes()
}

// access_denied fatal alert
var tlsAccessDenied = []byte{21, 3, 3, 0, 2, 2, 49}

func (p *Proxy) handleHTTPS(c net.Conn) {
	c.SetReadDeadline(time.Now().Add(headTimeout))
	host, head := readSNI(c)
	c.SetReadDeadline(time.Time{})

	o, ctr := p.check(host, "https")
	switch o {
	case Denied:
		c.Write(tlsAccessDenied)
		return
	case TimedOut:
		return
	}
	up, err := p.dial(context.Background(), "tcp", net.JoinHostPort(host, "443"))
	if err != nil {
		log.Printf("https %s: %v", host, err)
		return
	}
	defer up.Close()
	pipe(c, up, head, ctr)
}

// readHost parses the HTTP request head on c and returns its host (without
// port; empty if absent) and every byte consumed from c.
func readHost(c net.Conn) (string, []byte) {
	rec := &recorder{r: io.LimitReader(c, maxHeadBytes)}
	req, err := http.ReadRequest(bufio.NewReader(rec))
	if err != nil {
		return "", rec.buf.Bytes()
	}
	host := req.Host
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	return host, rec.buf.Bytes()
}

func (p *Proxy) handleHTTP(c net.Conn) {
	c.SetReadDeadline(time.Now().Add(headTimeout))
	host, head := readHost(c)
	c.SetReadDeadline(time.Time{})

	o, ctr := p.check(host, "http")
	switch o {
	case Denied:
		writeHTTPError(c, 503, "blocked by egress proxy")
		return
	case TimedOut:
		return
	}
	up, err := p.dial(context.Background(), "tcp", net.JoinHostPort(host, "80"))
	if err != nil {
		log.Printf("http %s: %v", host, err)
		writeHTTPError(c, 502, "upstream unreachable")
		return
	}
	defer up.Close()
	pipe(c, up, head, ctr)
}

func writeHTTPError(c net.Conn, code int, msg string) {
	fmt.Fprintf(c, "HTTP/1.1 %d %s\r\nRetry-After: 60\r\nContent-Type: text/plain\r\n"+
		"Content-Length: %d\r\nConnection: close\r\n\r\n%s\n",
		code, http.StatusText(code), len(msg)+1, msg)
}

// check counts the connection and decides it. Hosts without a registrable
// domain (missing, IP literal, bare suffix) are always denied.
func (p *Proxy) check(host, scheme string) (Outcome, *counters) {
	domain := baseDomain(host)
	if domain == "" {
		ctr := p.stats.conn(unknownDomain, scheme)
		ctr.record(Denied, false)
		return Denied, ctr
	}
	ctr := p.stats.conn(domain, scheme)
	o, held := p.engine.Check(context.Background(), domain, strings.TrimSuffix(strings.ToLower(host), "."))
	ctr.record(o, held)
	return o, ctr
}

type countWriter struct {
	w io.Writer
	n *atomic.Int64
}

func (cw countWriter) Write(b []byte) (int, error) {
	n, err := cw.w.Write(b)
	cw.n.Add(int64(n))
	return n, err
}

func closeWrite(c net.Conn) {
	if cw, ok := c.(interface{ CloseWrite() error }); ok {
		cw.CloseWrite()
		return
	}
	c.Close()
}

// pipe replays head to up, then copies both directions until both close.
func pipe(client, up net.Conn, head []byte, ctr *counters) {
	out := countWriter{up, &ctr.bytesOut}
	if _, err := out.Write(head); err != nil {
		return
	}
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		io.Copy(out, client)
		closeWrite(up)
	}()
	go func() {
		defer wg.Done()
		io.Copy(countWriter{client, &ctr.bytesIn}, up)
		closeWrite(client)
	}()
	wg.Wait()
}
