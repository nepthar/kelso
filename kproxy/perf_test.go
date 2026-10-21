package main

import (
	"io"
	"net"
	"testing"
)

// sinkServer accepts connections and, for each, discards what it reads and
// then sends n bytes before closing.
func sinkServer(b *testing.B, n int64) string {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		b.Fatal(err)
	}
	b.Cleanup(func() { ln.Close() })
	payload := make([]byte, 64<<10)
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				defer c.Close()
				io.Copy(io.Discard, c)
				for left := n; left > 0; {
					k, err := c.Write(payload[:min(left, int64(len(payload)))])
					if err != nil {
						return
					}
					left -= int64(k)
				}
			}()
		}
	}()
	return ln.Addr().String()
}

func proxyHTTPS(b *testing.B, httpsAddr string, hello []byte) int64 {
	c, err := net.Dial("tcp", httpsAddr)
	if err != nil {
		b.Fatal(err)
	}
	defer c.Close()
	c.Write(hello)
	c.(*net.TCPConn).CloseWrite()
	n, _ := io.Copy(io.Discard, c)
	return n
}

func BenchmarkHTTPSConn(b *testing.B) {
	hello := clientHello(b, "www.example.com")
	_, _, httpsAddr := testProxy(b, rules(Strict, "example.com", "allow"), sinkServer(b, 0))
	b.ReportAllocs()
	for b.Loop() {
		proxyHTTPS(b, httpsAddr, hello)
	}
}

func BenchmarkHTTPSThroughput(b *testing.B) {
	const size = 64 << 20
	hello := clientHello(b, "www.example.com")
	_, _, httpsAddr := testProxy(b, rules(Strict, "example.com", "allow"), sinkServer(b, size))
	b.ReportAllocs()
	b.SetBytes(size)
	for b.Loop() {
		if n := proxyHTTPS(b, httpsAddr, hello); n != size {
			b.Fatalf("got %d bytes", n)
		}
	}
}
