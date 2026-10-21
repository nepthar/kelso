package main

import (
	"fmt"
	"net"
	"sort"
	"strings"
)

// forwards maps a name to the IPv4 address and port that HTTP and HTTPS
// connections for exactly that name are piped to, bypassing the rules. It is
// how an app reaches a fixed LAN service, by connecting to http://NAME.
type forwards map[string]string

func (f forwards) String() string {
	names := make([]string, 0, len(f))
	for n := range f {
		names = append(names, n+"="+f[n])
	}
	sort.Strings(names)
	return strings.Join(names, ",")
}

// Set parses one NAME=IP:PORT flag value.
func (f forwards) Set(s string) error {
	name, target, ok := strings.Cut(s, "=")
	if !ok {
		return fmt.Errorf("%q: want NAME=IP:PORT", s)
	}
	n, err := ruleName(name)
	if err != nil {
		return err
	}
	ip, port, err := net.SplitHostPort(target)
	if err != nil || net.ParseIP(ip).To4() == nil || port == "" {
		return fmt.Errorf("%q: target must be an IPv4 address and port, like 192.168.1.3:3000", s)
	}
	if _, dup := f[n]; dup {
		return fmt.Errorf("%q is forwarded twice", n)
	}
	f[n] = net.JoinHostPort(ip, port)
	return nil
}
