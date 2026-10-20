package main

import (
	"encoding/binary"
	"io"
	"log"
	"net"
	"time"

	"golang.org/x/net/dns/dnsmessage"
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

func serveDNSUDP(pc net.PacketConn, self [4]byte) {
	buf := make([]byte, 65535)
	for {
		n, addr, err := pc.ReadFrom(buf)
		if err != nil {
			log.Printf("dns/udp: %v", err)
			return
		}
		resp, err := dnsAnswer(buf[:n], self)
		if err != nil {
			continue
		}
		pc.WriteTo(resp, addr)
	}
}

func serveDNSTCP(ln net.Listener, self [4]byte) {
	for {
		c, err := ln.Accept()
		if err != nil {
			log.Printf("dns/tcp: %v", err)
			return
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
				resp, err := dnsAnswer(q, self)
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
