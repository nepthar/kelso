package main

import (
	"io"
	"net"
	"testing"
	"time"
)

func TestIngressFlag(t *testing.T) {
	var is ingresses
	for _, good := range []string{"20001=main:8080", "20002=main:10001/udp", "20001=db:5432/udp"} {
		if err := is.Set(good); err != nil {
			t.Errorf("%q: %v", good, err)
		}
	}
	if is[1] != (ingress{listen: 20002, target: "main:10001", udp: true}) {
		t.Fatalf("got %+v", is[1])
	}
	for _, bad := range []string{
		"main:8080", "20001=main", "0=main:80", "20001=main:http", "20001=:80", "20001=main:80",
		"x=main:80", "20003=main:80/tcp",
	} {
		if err := is.Set(bad); err == nil {
			t.Errorf("accepted %q", bad)
		}
	}
}

func testIngressProxy() *Proxy {
	return &Proxy{engine: newEngine(time.Second, nil), stats: newStats(), dial: (&net.Dialer{}).DialContext, slots: make(chan struct{}, 8)}
}

func TestIngressTCP(t *testing.T) {
	echo, _ := net.Listen("tcp", "127.0.0.1:0")
	defer echo.Close()
	go func() {
		for {
			c, err := echo.Accept()
			if err != nil {
				return
			}
			go func() { io.Copy(c, c); c.Close() }()
		}
	}()
	p := testIngressProxy()
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	defer ln.Close()
	go p.serve(ln, p.relayTCP(ingress{target: echo.Addr().String()}))

	c, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.Write([]byte("hello"))
	c.(*net.TCPConn).CloseWrite()
	got, _ := io.ReadAll(c)
	if string(got) != "hello" {
		t.Fatalf("got %q", got)
	}
}

func TestIngressUDP(t *testing.T) {
	echo, _ := net.ListenPacket("udp4", "127.0.0.1:0")
	defer echo.Close()
	go func() {
		buf := make([]byte, 1500)
		for {
			n, from, err := echo.ReadFrom(buf)
			if err != nil {
				return
			}
			echo.WriteTo(append([]byte("echo:"), buf[:n]...), from)
		}
	}()
	p := testIngressProxy()
	pc, _ := net.ListenPacket("udp4", "127.0.0.1:0")
	defer pc.Close()
	go p.ingressUDP(pc, ingress{target: echo.LocalAddr().String(), udp: true})

	// Two clients, so replies have to find their way back to the right one.
	for _, msg := range []string{"one", "two"} {
		c, err := net.Dial("udp4", pc.LocalAddr().String())
		if err != nil {
			t.Fatal(err)
		}
		defer c.Close()
		c.SetDeadline(time.Now().Add(2 * time.Second))
		c.Write([]byte(msg))
		buf := make([]byte, 100)
		n, err := c.Read(buf)
		if err != nil || string(buf[:n]) != "echo:"+msg {
			t.Fatalf("%s: got %q, %v", msg, buf[:n], err)
		}
	}
}
