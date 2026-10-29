package main

import (
	"context"
	"net"
	"testing"
	"time"
)

// TestDNSServerAnswersWithArrivalAddress runs the real UDP and TCP servers and
// resolves through them with Go's resolver.
func TestDNSServerAnswersWithArrivalAddress(t *testing.T) {
	pc, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	ln, err := net.Listen("tcp4", pc.LocalAddr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	resolves := func(string) bool { return true }
	go serveDNSUDP(pc, resolves)
	go serveDNSTCP(ln, resolves)

	for _, network := range []string{"udp", "tcp"} {
		r := &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{Timeout: time.Second}).DialContext(ctx, network, pc.LocalAddr().String())
		}}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		addrs, err := r.LookupIP(ctx, "ip4", "anything.example.com")
		cancel()
		if err != nil || len(addrs) != 1 || !addrs[0].Equal(net.IPv4(127, 0, 0, 1)) {
			t.Errorf("%s: got %v, %v", network, addrs, err)
		}
	}
}
