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

// dnsAnswer answers every A query with self and every other query empty.
func dnsAnswer(query []byte, self [4]byte) ([]byte, error) {
	var p dnsmessage.Parser
	h, err := p.Start(query)
	if err != nil {
		return nil, err
	}
	qs, err := p.AllQuestions()
	if err != nil {
		return nil, err
	}
	b := dnsmessage.NewBuilder(nil, dnsmessage.Header{
		ID:                 h.ID,
		Response:           true,
		OpCode:             h.OpCode,
		RecursionDesired:   h.RecursionDesired,
		RecursionAvailable: true,
		RCode:              dnsmessage.RCodeSuccess,
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
	for _, q := range qs {
		if q.Type != dnsmessage.TypeA || q.Class != dnsmessage.ClassINET {
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
func serveDNSUDP(pc net.PacketConn) {
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
		resp, err := dnsAnswer(buf[:n], [4]byte(cm.Dst.To4()))
		if err != nil {
			continue
		}
		p.WriteTo(resp, &ipv4.ControlMessage{Src: cm.Dst, IfIndex: cm.IfIndex}, addr)
	}
}

func serveDNSTCP(ln net.Listener) {
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
				resp, err := dnsAnswer(q, [4]byte(self))
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
