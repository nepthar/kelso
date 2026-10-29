package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"
)

// An ingress relays connections arriving on a local port to a fixed host and
// port, without looking at them: it is how traffic reaches an app that sits on
// a network with no other way in.
type ingress struct {
	listen int
	target string // host:port, resolved on every connection
	udp    bool
}

func (in ingress) String() string {
	s := fmt.Sprintf("%d=%s", in.listen, in.target)
	if in.udp {
		s += "/udp"
	}
	return s
}

type ingresses []ingress

func (is *ingresses) String() string {
	parts := make([]string, len(*is))
	for i, in := range *is {
		parts[i] = in.String()
	}
	return strings.Join(parts, ",")
}

// Set parses one LISTEN=HOST:PORT[/udp] flag value.
func (is *ingresses) Set(s string) error {
	bad := fmt.Errorf("%q: want LISTEN=HOST:PORT or LISTEN=HOST:PORT/udp, like 20001=main:8080", s)
	listen, target, ok := strings.Cut(s, "=")
	if !ok {
		return bad
	}
	in := ingress{}
	if t, found := strings.CutSuffix(target, "/udp"); found {
		in.udp, target = true, t
	}
	host, port, err := net.SplitHostPort(target)
	if err != nil || host == "" || !validPort(port) || !validPort(listen) {
		return bad
	}
	in.listen, _ = strconv.Atoi(listen)
	in.target = target
	for _, other := range *is {
		if other.listen == in.listen && other.udp == in.udp {
			return fmt.Errorf("%q: port %d is already an ingress", s, in.listen)
		}
	}
	*is = append(*is, in)
	return nil
}

func validPort(s string) bool {
	p, err := strconv.Atoi(s)
	return err == nil && p >= 1 && p <= 65535 && s[0] >= '0' && s[0] <= '9'
}

// relayTCP pipes one accepted connection to in.target.
func (p *Proxy) relayTCP(in ingress) func(net.Conn) {
	return func(c net.Conn) {
		up, err := p.dial(context.Background(), "tcp4", in.target)
		if err != nil {
			log.Printf("ingress %s: %v", in, err)
			return
		}
		defer up.Close()
		pipe(c, up, nil, &counters{})
	}
}

const (
	udpIdle        = 2 * time.Minute
	maxUDPSessions = 1024
)

// ingressUDP relays datagrams arriving on pc to in.target.
func (p *Proxy) ingressUDP(pc net.PacketConn, in ingress) {
	p.relayUDP(pc, "ingress "+in.String(), func() (string, *counters) { return in.target, &counters{} })
}

type udpSession struct {
	up  net.Conn
	ctr *counters
}

// relayUDP relays datagrams arriving on pc to the address target gives for
// each new client, "" to drop it. Each client address gets its own socket
// towards the target, so replies can be told apart; a session ends after
// udpIdle without traffic from the target.
func (p *Proxy) relayUDP(pc net.PacketConn, name string, target func() (string, *counters)) {
	var mu sync.Mutex
	sessions := map[string]udpSession{}
	buf := make([]byte, 65535)
	for {
		n, client, err := pc.ReadFrom(buf)
		if err != nil {
			if !errors.Is(err, net.ErrClosed) {
				log.Printf("%s: %v", name, err)
			}
			return
		}
		key := client.String()
		mu.Lock()
		s, ok := sessions[key]
		if !ok && len(sessions) < maxUDPSessions {
			if addr, ctr := target(); addr != "" {
				if s.up, err = p.dial(context.Background(), "udp4", addr); err != nil {
					log.Printf("%s: %v", name, err)
				} else {
					s.ctr, ok = ctr, true
					sessions[key] = s
					go func() {
						back := make([]byte, 65535)
						for {
							s.up.SetReadDeadline(time.Now().Add(udpIdle))
							k, err := s.up.Read(back)
							if err != nil {
								break
							}
							s.ctr.bytesIn.Add(int64(k))
							pc.WriteTo(back[:k], client)
						}
						mu.Lock()
						delete(sessions, key)
						mu.Unlock()
						s.up.Close()
					}()
				}
			}
		}
		mu.Unlock()
		if ok {
			s.ctr.bytesOut.Add(int64(n))
			s.up.Write(buf[:n])
		}
	}
}
