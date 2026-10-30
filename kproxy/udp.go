package main

import (
	"context"
	"errors"
	"log"
	"net"
	"sync"
	"time"
)

const (
	udpIdle        = 2 * time.Minute
	maxUDPSessions = 1024
)

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
