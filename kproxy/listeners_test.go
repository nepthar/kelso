package main

import (
	"fmt"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

// freePort returns a port that was free a moment ago.
func freePort(t *testing.T, network string) int {
	t.Helper()
	if network == "udp" {
		pc, err := net.ListenPacket("udp4", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		defer pc.Close()
		return pc.LocalAddr().(*net.UDPAddr).Port
	}
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	return ln.Addr().(*net.TCPAddr).Port
}

// listenerProxy is a proxy whose listeners follow its rules, bound on
// localhost, with 80 and 443 on random ports.
func listenerProxy(t *testing.T) *Proxy {
	e := newEngine(time.Second, nil)
	p := &Proxy{
		engine: e, stats: newStats(), dial: (&net.Dialer{}).DialContext, slots: make(chan struct{}, 8),
		bind:     "127.0.0.1",
		addrs:    map[portKey]string{{false, httpPort}: "127.0.0.1:0", {false, httpsPort}: "127.0.0.1:0"},
		reserved: map[portKey]string{},
	}
	e.apply = p.apply
	t.Cleanup(func() { p.apply(&Rules{}) })
	return p
}

func tcpEcho(t *testing.T) string {
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				c.Write([]byte("hi:")) // the server speaks first
				io.Copy(c, c)
				c.Close()
			}()
		}
	}()
	return ln.Addr().String()
}

func udpEcho(t *testing.T) string {
	pc, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { pc.Close() })
	go func() {
		buf := make([]byte, 1500)
		for {
			n, from, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			pc.WriteTo(append([]byte("echo:"), buf[:n]...), from)
		}
	}()
	return pc.LocalAddr().String()
}

func TestTCPAndUDPRulesForward(t *testing.T) {
	p := listenerProxy(t)
	tcpPort, udpPort := freePort(t, "tcp"), freePort(t, "udp")
	err := p.engine.Replace(rules(DefaultDeny,
		fmt.Sprintf("tcp :%d redis %s", tcpPort, tcpEcho(t)),
		fmt.Sprintf("udp :%d cam %s", udpPort, udpEcho(t)),
	))
	if err != nil {
		t.Fatal(err)
	}

	c, err := net.Dial("tcp4", fmt.Sprintf("127.0.0.1:%d", tcpPort))
	if err != nil {
		t.Fatal(err)
	}
	c.Write([]byte("ping"))
	c.(*net.TCPConn).CloseWrite()
	got, _ := io.ReadAll(c)
	c.Close()
	if string(got) != "hi:ping" {
		t.Fatalf("tcp: got %q", got)
	}

	u, err := net.Dial("udp4", fmt.Sprintf("127.0.0.1:%d", udpPort))
	if err != nil {
		t.Fatal(err)
	}
	defer u.Close()
	u.SetDeadline(time.Now().Add(2 * time.Second))
	u.Write([]byte("frame"))
	buf := make([]byte, 100)
	n, err := u.Read(buf)
	if err != nil || string(buf[:n]) != "echo:frame" {
		t.Fatalf("udp: got %q, %v", buf[:n], err)
	}

	s := p.stats.Snapshot()
	tc, uc := s["redis"][fmt.Sprintf("tcp :%d", tcpPort)], s["cam"][fmt.Sprintf("udp :%d", udpPort)]
	if tc.Allowed != 1 || tc.BytesOut != 4 || tc.BytesIn != 7 || uc.Allowed != 1 || uc.BytesOut != 5 || uc.BytesIn != 10 {
		t.Fatalf("stats: %+v", s)
	}

	// Removing the rule closes its port.
	if err := p.engine.Remove(KindTCP, tcpPort, "redis"); err != nil {
		t.Fatal(err)
	}
	if c, err := net.DialTimeout("tcp4", fmt.Sprintf("127.0.0.1:%d", tcpPort), time.Second); err == nil {
		c.Close()
		t.Fatal("port still open after its rule was removed")
	}
}

func TestIngressRelaysIn(t *testing.T) {
	p := listenerProxy(t)
	tcpPort, udpPort := freePort(t, "tcp"), freePort(t, "udp")
	err := p.engine.Replace(rules(DefaultDeny,
		fmt.Sprintf("ingress tcp :%d web %s", tcpPort, tcpEcho(t)),
		fmt.Sprintf("ingress udp :%d dns %s", udpPort, udpEcho(t)),
	))
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.Dial("tcp4", fmt.Sprintf("127.0.0.1:%d", tcpPort))
	if err != nil {
		t.Fatal(err)
	}
	c.Write([]byte("in"))
	c.(*net.TCPConn).CloseWrite()
	got, _ := io.ReadAll(c)
	c.Close()
	if string(got) != "hi:in" {
		t.Fatalf("tcp: got %q", got)
	}

	// Two clients, so replies have to find their way back to the right one.
	for _, msg := range []string{"one", "two"} {
		u, err := net.Dial("udp4", fmt.Sprintf("127.0.0.1:%d", udpPort))
		if err != nil {
			t.Fatal(err)
		}
		defer u.Close()
		u.SetDeadline(time.Now().Add(2 * time.Second))
		u.Write([]byte(msg))
		buf := make([]byte, 100)
		n, err := u.Read(buf)
		if err != nil || string(buf[:n]) != "echo:"+msg {
			t.Fatalf("udp %s: got %q, %v", msg, buf[:n], err)
		}
	}

	s := p.stats.Snapshot()
	if s["web"][fmt.Sprintf("ingress tcp :%d", tcpPort)].Allowed != 1 ||
		s["dns"][fmt.Sprintf("ingress udp :%d", udpPort)].Allowed != 2 {
		t.Fatalf("stats: %+v", s)
	}
	if p.engine.Resolves("web") {
		t.Fatal("DNS answers an ingress name")
	}
}

func TestReservedPortsAreRefused(t *testing.T) {
	p := listenerProxy(t)
	p.reserved[portKey{true, 5353}] = "dns"
	err := p.engine.Replace(rules(DefaultDeny, "udp :5353 mdns 224.0.0.251:5353"))
	if err == nil || !strings.Contains(err.Error(), "kproxy can't add a udp listener on port 5353 because it already has a dns listener") {
		t.Fatalf("got %v", err)
	}
}

func TestBindFailureOpensNothing(t *testing.T) {
	p := listenerProxy(t)
	busy, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer busy.Close()
	free := freePort(t, "tcp")
	err = p.engine.Replace(rules(DefaultDeny,
		fmt.Sprintf("tls :%d a.com", free),
		fmt.Sprintf("tls :%d b.com", busy.Addr().(*net.TCPAddr).Port),
	))
	if err == nil {
		t.Fatal("expected a bind error")
	}
	if p.engine.loaded() {
		t.Fatal("rules published despite the bind error")
	}
	if len(p.open) != 0 {
		t.Fatalf("left open: %v", p.open)
	}
}
