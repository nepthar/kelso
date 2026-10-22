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
	slots  chan struct{} // one per open client connection, shared by all listeners
}

// serve stops accepting while every slot is taken, so excess connections
// wait in the kernel's listen backlog.
func (p *Proxy) serve(ln net.Listener, handle func(net.Conn)) {
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Printf("%s: %v", ln.Addr(), err)
			return
		}
		p.slots <- struct{}{}
		go func() {
			defer func() {
				c.Close()
				<-p.slots
			}()
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

	o, ctr, addr := p.route(host, "https", "443")
	switch o {
	case Denied:
		c.Write(tlsAccessDenied)
		return
	case TimedOut:
		return
	}
	up, err := p.dial(context.Background(), "tcp4", addr)
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

	o, ctr, addr := p.route(host, "http", "80")
	switch o {
	case Denied:
		writeHTTPError(c, 503, "blocked by egress proxy")
		return
	case TimedOut:
		return
	}
	up, err := p.dial(context.Background(), "tcp4", addr)
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

// route counts the connection and decides it, returning the address to dial:
// the allowing rule's target, or host on port. A host without a root domain
// (single label, unknown TLD, bare suffix) is denied unless the rule matching
// it has a target; a missing host or IP literal always is.
func (p *Proxy) route(host, scheme, port string) (Outcome, *counters, string) {
	name := normHost(host)
	if name == "" || net.ParseIP(name) != nil {
		return p.refuse(scheme)
	}
	root := rootDomain(name)
	if root == "" {
		var rule Rule
		if r := p.engine.rules(); r != nil {
			rule, _ = r.lookup(name)
		}
		if rule.Verdict != Allow || rule.Target == "" {
			return p.refuse(scheme)
		}
		ctr := p.stats.conn(name, scheme)
		ctr.record(Allowed, false)
		return Allowed, ctr, rule.Target
	}
	ctr := p.stats.conn(root, scheme)
	o, held, target := p.engine.Check(context.Background(), name)
	ctr.record(o, held)
	if target == "" {
		target = net.JoinHostPort(name, port)
	}
	return o, ctr, target
}

func (p *Proxy) refuse(scheme string) (Outcome, *counters, string) {
	ctr := p.stats.conn(unknownDomain, scheme)
	ctr.record(Denied, false)
	return Denied, ctr, ""
}

func closeWrite(c net.Conn) {
	if cw, ok := c.(interface{ CloseWrite() error }); ok {
		cw.CloseWrite()
		return
	}
	c.Close()
}

// pipe replays head to up, then copies both directions until both finish.
func pipe(client, up net.Conn, head []byte, ctr *counters) {
	if _, err := up.Write(head); err != nil {
		return
	}
	ctr.bytesOut.Add(int64(len(head)))
	done := make(chan struct{})
	go func() {
		defer close(done)
		copyCounting(client, up, &ctr.bytesIn)
	}()
	copyCounting(up, client, &ctr.bytesOut)
	<-done
}

const (
	copyChunk = 1 << 20

	// halfCloseTimeout is how long the other direction may keep going once one
	// side has finished sending, so a peer that never closes can't hold the
	// connection forever.
	halfCloseTimeout = 30 * time.Second
)

// copyCounting copies src to dst, adding to n after each chunk so counts stay
// current on long-lived connections. dst and src must stay unwrapped
// *net.TCPConns (an *io.LimitedReader is fine) for Linux to splice instead of
// copying through a buffer. On EOF it half-closes dst and puts a deadline on
// the other direction, which reads from dst; on error it closes both so the
// other direction stops too.
func copyCounting(dst, src net.Conn, n *atomic.Int64) {
	for {
		k, err := io.Copy(dst, io.LimitReader(src, copyChunk))
		n.Add(k)
		if err != nil {
			dst.Close()
			src.Close()
			return
		}
		if k < copyChunk {
			closeWrite(dst)
			dst.SetReadDeadline(time.Now().Add(halfCloseTimeout))
			return
		}
	}
}
