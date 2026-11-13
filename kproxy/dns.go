package main

import (
	"encoding/binary"
	"io"
	"log"
	"net"
	"time"

	"golang.org/x/net/dns/dnsmessage"
	"golang.org/x/net/ipv4"
)

const dnsTTL = 60

// dnsAnswer answers A queries for names resolves accepts with self, and other
// types for them empty. If it accepts none of the names asked about, the
// answer is NXDOMAIN, so an app fails at the lookup instead of connecting and
// being refused.
func dnsAnswer(query []byte, self [4]byte, resolves func(string) bool) ([]byte, error) {
	var p dnsmessage.Parser
	h, err := p.Start(query)
	if err != nil {
		return nil, err
	}
	qs, err := p.AllQuestions()
	if err != nil {
		return nil, err
	}
	known := make([]bool, len(qs))
	rcode := dnsmessage.RCodeSuccess
	if len(qs) > 0 {
		rcode = dnsmessage.RCodeNameError
	}
	for i, q := range qs {
		if known[i] = resolves(q.Name.String()); known[i] {
			rcode = dnsmessage.RCodeSuccess
		}
	}
	b := dnsmessage.NewBuilder(nil, dnsmessage.Header{
		ID:                 h.ID,
		Response:           true,
		OpCode:             h.OpCode,
		RecursionDesired:   h.RecursionDesired,
		RecursionAvailable: true,
		RCode:              rcode,
	})
	b.EnableCompression()
	if err := b.StartQuestions(); err != nil {
		return nil, err
	}
	for _, q := range qs {
		if err := b.Question(q); err != nil {
			return nil, err
		}
	}
	if err := b.StartAnswers(); err != nil {
		return nil, err
	}
	for i, q := range qs {
		if !known[i] || q.Type != dnsmessage.TypeA || q.Class != dnsmessage.ClassINET {
			continue
		}
		rh := dnsmessage.ResourceHeader{Name: q.Name, Class: q.Class, TTL: dnsTTL}
		if err := b.AResource(rh, dnsmessage.AResource{A: self}); err != nil {
			return nil, err
		}
	}
	return b.Finish()
}

// serveDNSUDP answers each query with the address it arrived on, which is the
// proxy's own address on the asker's network. The reply is sent from that
// address too: on a socket bound to every address the kernel might otherwise
// pick another, and the asker would drop the reply.
func serveDNSUDP(pc net.PacketConn, resolves func(string) bool) {
	p := ipv4.NewPacketConn(pc)
	if err := p.SetControlMessage(ipv4.FlagDst|ipv4.FlagInterface, true); err != nil {
		log.Fatalf("dns/udp: %v", err)
	}
	buf := make([]byte, 65535)
	for {
		n, cm, addr, err := p.ReadFrom(buf)
		if err != nil {
			log.Printf("dns/udp: %v", err)
			return
		}
		if cm == nil || cm.Dst.To4() == nil {
			continue
		}
		resp, err := dnsAnswer(buf[:n], [4]byte(cm.Dst.To4()), resolves)
		if err != nil {
			continue
		}
		p.WriteTo(resp, &ipv4.ControlMessage{Src: cm.Dst, IfIndex: cm.IfIndex}, addr)
	}
}

func serveDNSTCP(ln net.Listener, resolves func(string) bool) {
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Printf("dns/tcp: %v", err)
			return
		}
		self := c.LocalAddr().(*net.TCPAddr).IP.To4()
		if self == nil {
			c.Close()
			continue
		}
		go func() {
			defer c.Close()
			for {
				c.SetDeadline(time.Now().Add(10 * time.Second))
				var l [2]byte
				if _, err := io.ReadFull(c, l[:]); err != nil {
					return
				}
				q := make([]byte, binary.BigEndian.Uint16(l[:]))
				if _, err := io.ReadFull(c, q); err != nil {
					return
				}
				resp, err := dnsAnswer(q, [4]byte(self), resolves)
				if err != nil {
					return
				}
				if _, err := c.Write(binary.BigEndian.AppendUint16(nil, uint16(len(resp)))); err != nil {
					return
				}
				if _, err := c.Write(resp); err != nil {
					return
				}
			}
		}()
	}
}
