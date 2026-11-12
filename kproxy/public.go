package main

import (
	"fmt"
	"net/netip"
	"syscall"
)

// Shared address space (RFC 6598), which Tailscale and carrier NAT use, and
// "this network" (RFC 791).
var (
	cgnat       = netip.MustParsePrefix("100.64.0.0/10")
	thisNetwork = netip.MustParsePrefix("0.0.0.0/8")
)

// public reports whether a is an address on the internet rather than on this
// machine or a network it is attached to.
func public(a netip.Addr) bool {
	a = a.Unmap()
	return a.IsValid() && !a.IsPrivate() && !a.IsLoopback() && !a.IsLinkLocalUnicast() &&
		!a.IsMulticast() && !a.IsUnspecified() && !cgnat.Contains(a) && !thisNetwork.Contains(a)
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
