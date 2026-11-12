package main

import (
	"context"
	"net"
	"net/netip"
	"strings"
	"testing"
	"time"
)

func TestPublic(t *testing.T) {
	for addr, want := range map[string]bool{
		"1.1.1.1":         true,
		"93.184.216.34":   true,
		"10.0.0.5":        false,
		"172.16.4.1":      false,
		"192.168.1.20":    false,
		"127.0.0.1":       false,
		"169.254.169.254": false,
		"100.100.1.1":     false,
		"0.0.0.0":         false,
		"0.1.2.3":         false,
		"224.0.0.1":       false,
		"::1":             false,
		"::ffff:10.0.0.5": false,
		"fd00::1":         false,
	} {
		if got := public(netip.MustParseAddr(addr)); got != want {
			t.Errorf("public(%s) = %v, want %v", addr, got, want)
		}
	}
}

// A name the default policy or a wildcard lets through is dialled where its
// DNS says, so that may not be a private address; a rule's own destination may.
func TestNameDerivedDestinationMayNotBePrivate(t *testing.T) {
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
			c.Close()
		}
	}()

	p := &Proxy{
		dial:       (&net.Dialer{Timeout: time.Second}).DialContext,
		dialPublic: (&net.Dialer{Timeout: time.Second, Control: publicOnly}).DialContext,
	}
	_, err = p.dialer(true)(context.Background(), "tcp4", up.Addr().String())
	if err == nil || !strings.Contains(err.Error(), "is on a private network") {
		t.Fatalf("by name: got %v, want a private address refusal", err)
	}
	c, err := p.dialer(false)(context.Background(), "tcp4", up.Addr().String())
	if err != nil {
		t.Fatalf("by rule destination: %v", err)
	}
	c.Close()
}
