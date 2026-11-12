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
	"os"
	"strconv"
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
	// dialPublic dials a destination that came from the name a client asked
	// for, and refuses private addresses. nil means dial.
	dialPublic func(ctx context.Context, network, addr string) (net.Conn, error)
	slots      chan struct{} // one per open client connection, shared by all listeners
	idle       time.Duration // close a connection quiet both ways this long; 0 for never

	bind     string             // address listeners bind to; "" for every address
	addrs    map[portKey]string // full listen addresses overriding bind, for 80 and 443
	reserved map[portKey]string // ports used by something other than rules, and what

	mu   sync.Mutex
	open map[portKey]io.Closer
}

// implicitRules says what the always-there ports are before rules load.
var implicitRules = newRules(DefaultDeny)

// apply opens a socket for every listener r has and the proxy doesn't yet,
// then closes the ones r no longer has. A socket outlives a change of kind on
// its port: each connection asks the current rules what its port does.
func (p *Proxy) apply(r *Rules) error {
	for k, l := range r.Listeners {
		if what, ok := p.reserved[k]; ok {
			return fmt.Errorf("kproxy can't add a %s listener on port %d because it already has a %s listener", l.Kind, k.port, what)
		}
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.open == nil {
		p.open = map[portKey]io.Closer{}
	}
	var opened []portKey
	for k := range r.Listeners {
		if _, ok := p.open[k]; ok {
			continue
		}
		c, err := p.listenOn(k)
		if err != nil {
			for _, o := range opened {
				p.open[o].Close()
				delete(p.open, o)
			}
			return err
		}
		p.open[k] = c
		opened = append(opened, k)
	}
	for k, c := range p.open {
		if _, ok := r.Listeners[k]; !ok {
			c.Close()
			delete(p.open, k)
		}
	}
	return nil
}

func (p *Proxy) listenOn(k portKey) (io.Closer, error) {
	addr, ok := p.addrs[k]
	if !ok {
		addr = net.JoinHostPort(p.bind, strconv.Itoa(k.port))
	}
	if k.udp {
		pc, err := net.ListenPacket("udp4", addr)
		if err != nil {
			return nil, err
		}
		log.Printf("kproxy: listening on %s/udp", pc.LocalAddr())
		go p.relayUDP(pc, fmt.Sprintf("udp :%d", k.port), p.udpTarget(k.port))
		return pc, nil
	}
	ln, err := net.Listen("tcp4", addr)
	if err != nil {
		return nil, err
	}
	log.Printf("kproxy: listening on %s", ln.Addr())
	go p.serve(ln, p.handleTCP(k.port))
	return ln, nil
}

// serve stops accepting while every slot is taken, so excess connections
// wait in the kernel's listen backlog.
func (p *Proxy) serve(ln net.Listener, handle func(net.Conn)) {
	for {
		c, err := ln.Accept()
		if err != nil {
			if !errors.Is(err, net.ErrClosed) {
				log.Printf("%s: %v", ln.Addr(), err)
			}
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

// listener returns what the TCP or UDP port does under the current rules.
func (p *Proxy) listener(udp bool, port int) *Listener {
	r := p.engine.rules()
	if r == nil {
		r = implicitRules
	}
	return r.Listeners[portKey{udp, port}]
}

// handleTCP serves one connection on a TCP port, by what the port does now.
func (p *Proxy) handleTCP(port int) func(net.Conn) {
	return func(c net.Conn) {
		l := p.listener(false, port)
		if l == nil {
			return
		}
		switch l.Kind {
		case KindHTTP:
			p.handleHTTP(c, port)
		case KindTLS:
			p.handleTLS(c, port)
		case KindTCP:
			p.forwardTCP(c, l)
		}
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

func (p *Proxy) handleTLS(c net.Conn, port int) {
	c.SetReadDeadline(time.Now().Add(headTimeout))
	host, head := readSNI(c)
	c.SetReadDeadline(time.Time{})

	o, ctr, addr, byName := p.route(host, KindTLS, port)
	switch o {
	case Denied:
		c.Write(tlsAccessDenied)
		return
	case TimedOut:
		return
	}
	up, err := p.dialer(byName)(context.Background(), "tcp4", addr)
	if err != nil {
		log.Printf("tls :%d %s: %v", port, host, err)
		return
	}
	defer up.Close()
	pipe(c, up, head, ctr, p.idle)
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

func (p *Proxy) handleHTTP(c net.Conn, port int) {
	c.SetReadDeadline(time.Now().Add(headTimeout))
	host, head := readHost(c)
	c.SetReadDeadline(time.Time{})

	o, ctr, addr, byName := p.route(host, KindHTTP, port)
	switch o {
	case Denied:
		writeHTTPError(c, 503, "blocked by egress proxy")
		return
	case TimedOut:
		return
	}
	up, err := p.dialer(byName)(context.Background(), "tcp4", addr)
	if err != nil {
		log.Printf("http :%d %s: %v", port, host, err)
		writeHTTPError(c, 502, "upstream unreachable")
		return
	}
	defer up.Close()
	pipe(c, up, head, ctr, p.idle)
}

func writeHTTPError(c net.Conn, code int, msg string) {
	fmt.Fprintf(c, "HTTP/1.1 %d %s\r\nRetry-After: 60\r\nContent-Type: text/plain\r\n"+
		"Content-Length: %d\r\nConnection: close\r\n\r\n%s\n",
		code, http.StatusText(code), len(msg)+1, msg)
}

// forwardTCP pipes a connection to a TCP listener's destination. It dials
// straight away and reads nothing first, so protocols where the server
// speaks first work.
func (p *Proxy) forwardTCP(c net.Conn, l *Listener) {
	rule := l.fixed()
	ctr := p.stats.conn(rule.Name, l.String())
	ctr.record(Allowed, false)
	up, err := p.dial(context.Background(), "tcp4", rule.Dest)
	if err != nil {
		log.Printf("%s %s: %v", l, rule.Name, err)
		return
	}
	defer up.Close()
	pipe(c, up, nil, ctr, p.idle)
}

// udpTarget gives a UDP listener's relay its destination for each new client,
// from the current rules.
func (p *Proxy) udpTarget(port int) func() (string, *counters) {
	return func() (string, *counters) {
		l := p.listener(true, port)
		if l == nil {
			return "", nil
		}
		rule := l.fixed()
		ctr := p.stats.conn(rule.Name, l.String())
		ctr.record(Allowed, false)
		return rule.Dest, ctr
	}
}

// dialer picks how to dial: a destination taken from the name a client asked
// for may only be a public address, since whoever runs that name's DNS chooses
// it. A destination a rule spells out may be anywhere.
func (p *Proxy) dialer(byName bool) func(context.Context, string, string) (net.Conn, error) {
	if byName && p.dialPublic != nil {
		return p.dialPublic
	}
	return p.dial
}

// route counts the connection and decides it, returning the address to dial:
// the allowing rule's destination, with the name asked for in place of "*",
// or that name on port. byName reports that the address's host is that name.
// A missing name or IP literal is always denied.
func (p *Proxy) route(host string, kind Kind, port int) (o Outcome, ctr *counters, addr string, byName bool) {
	name := normHost(host)
	if name == "" || net.ParseIP(name) != nil {
		ctr := p.stats.conn(unknownDomain, listenerName(kind, port, false))
		ctr.record(Denied, false)
		return Denied, ctr, "", false
	}
	domain := rootDomain(name)
	if domain == "" {
		domain = name
	}
	ctr = p.stats.conn(domain, listenerName(kind, port, false))
	o, held, rule := p.engine.Check(context.Background(), port, name)
	ctr.record(o, held)
	addr = rule.Dest
	if addr == "" {
		addr = "*:" + strconv.Itoa(port)
	}
	if h, pt, _ := net.SplitHostPort(addr); h == "*" {
		addr, byName = net.JoinHostPort(name, pt), true
	}
	return o, ctr, addr, byName
}

func closeWrite(c net.Conn) {
	if cw, ok := c.(interface{ CloseWrite() error }); ok {
		cw.CloseWrite()
		return
	}
	c.Close()
}

// link is what the two directions of one piped connection share.
// Times are unix nanoseconds.
type link struct {
	idle time.Duration
	// When each direction first found a whole deadline pass with no bytes; 0
	// while it is moving them. A direction only reports when its copy returns,
	// so "bytes moved lately" can't be read from here, but "none since" can.
	quiet    [2]atomic.Int64
	halfIdle time.Duration // the limit once one side has finished sending
	half     atomic.Bool   // one side has
}

// limit is how long the connection may stay quiet: idle, or halfIdle if that
// is shorter and one side has finished sending. 0 is no limit.
func (l *link) limit() time.Duration {
	if l.half.Load() && (l.idle == 0 || l.halfIdle < l.idle) {
		return l.halfIdle
	}
	return l.idle
}

// expired reports whether both directions have been quiet for the limit. A
// direction that has finished sending counts as quiet.
func (l *link) expired() bool {
	a, b, limit := l.quiet[0].Load(), l.quiet[1].Load(), l.limit()
	return limit > 0 && a != 0 && b != 0 && time.Now().UnixNano()-max(a, b) >= int64(limit)
}

// pipe replays head to up, then copies both directions until both finish or
// neither has moved a byte for idle (0 for no limit).
func pipe(client, up net.Conn, head []byte, ctr *counters, idle time.Duration) {
	relay(client, up, head, ctr, &link{idle: idle, halfIdle: halfCloseTimeout})
}

func relay(client, up net.Conn, head []byte, ctr *counters, l *link) {
	if len(head) > 0 {
		if _, err := up.Write(head); err != nil {
			return
		}
		ctr.bytesOut.Add(int64(len(head)))
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		copyCounting(client, up, &ctr.bytesIn, l, 0)
	}()
	copyCounting(up, client, &ctr.bytesOut, l, 1)
	<-done
}

const (
	copyChunk = 1 << 20

	// halfCloseTimeout is how long a connection may stay quiet once one side
	// has finished sending, so a peer that never closes can't hold it for the
	// whole idle timeout, or forever without one.
	halfCloseTimeout = 30 * time.Second
)

// copyCounting copies src to dst, adding to n after each chunk so counts stay
// current on long-lived connections. dst and src must stay unwrapped
// *net.TCPConns (an *io.LimitedReader is fine) for Linux to splice instead of
// copying through a buffer, which is why quiet is found by a read deadline
// every half of the limit rather than by watching the bytes: a deadline that
// passes while the other direction is busy just starts another. On EOF it
// half-closes dst and wakes the other direction, which reads from dst, to
// take up the shorter limit; on error it closes both so the other direction
// stops too.
func copyCounting(dst, src net.Conn, n *atomic.Int64, l *link, dir int) {
	for {
		if limit := l.limit(); limit > 0 {
			src.SetReadDeadline(time.Now().Add(limit / 2))
			if l.limit() != limit {
				continue // the other side finished meanwhile, and its wake-up was overwritten
			}
		}
		k, err := io.Copy(dst, io.LimitReader(src, copyChunk))
		n.Add(k)
		timedOut := errors.Is(err, os.ErrDeadlineExceeded)
		if k > 0 {
			l.quiet[dir].Store(0)
		} else if timedOut {
			l.quiet[dir].CompareAndSwap(0, time.Now().UnixNano())
		}
		if timedOut && !l.expired() {
			continue
		}
		if err != nil {
			dst.Close()
			src.Close()
			return
		}
		if k < copyChunk {
			closeWrite(dst)
			l.quiet[dir].Store(time.Now().UnixNano())
			l.half.Store(true)
			dst.SetReadDeadline(time.Now().Add(l.halfIdle / 2))
			return
		}
	}
}
