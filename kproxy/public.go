package main

import (
	"fmt"
	"net/netip"
	"syscall"
)

// Ranges netip has no predicate for: shared address space (RFC 6598), which
// Tailscale and carrier NAT use, "this network" (RFC 791), IETF protocol
// assignments (RFC 6890), benchmarking (RFC 2544) and reserved (RFC 1112).
var notPublic = []netip.Prefix{
	netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("0.0.0.0/8"),
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("240.0.0.0/4"),
}

// public reports whether a is an address on the internet rather than on this
// machine or a network it is attached to.
func public(a netip.Addr) bool {
	a = a.Unmap()
	if !a.IsValid() || a.IsPrivate() || a.IsLoopback() || a.IsLinkLocalUnicast() ||
		a.IsMulticast() || a.IsUnspecified() {
		return false
	}
	for _, p := range notPublic {
		if p.Contains(a) {
			return false
		}
	}
	return true
}

// publicOnly is a net.Dialer Control: it sees the address actually being
// connected to, after DNS, so a name can't resolve its way past it.
func publicOnly(_, address string, _ syscall.RawConn) error {
	ap, err := netip.ParseAddrPort(address)
	if err != nil {
		return err
	}
	if !public(ap.Addr()) {
		return fmt.Errorf("%s is on a private network, which a name only reaches through a rule with a destination", ap.Addr())
	}
	return nil
}
