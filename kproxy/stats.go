package main

import (
	"sync"
	"sync/atomic"
	"time"
)

const unknownDomain = "unknown"

type counters struct {
	conns, bytesIn, bytesOut        atomic.Int64
	allowed, denied, held, timedOut atomic.Int64
	firstSeen, lastSeen             atomic.Int64 // unix nanos
}

type Counters struct {
	Conns     int64     `json:"conns"`
	BytesIn   int64     `json:"bytes_in"`
	BytesOut  int64     `json:"bytes_out"`
	Allowed   int64     `json:"allowed"`
	Denied    int64     `json:"denied"`
	Held      int64     `json:"held"`
	TimedOut  int64     `json:"timed_out"`
	FirstSeen time.Time `json:"first_seen"`
	LastSeen  time.Time `json:"last_seen"`
}

type statsKey struct{ domain, scheme string }

type Stats struct {
	mu sync.Mutex
	m  map[statsKey]*counters
}

func newStats() *Stats { return &Stats{m: map[statsKey]*counters{}} }

// conn records a new connection and returns its counters for the caller to
// update as it progresses.
func (s *Stats) conn(domain, scheme string) *counters {
	now := time.Now().UnixNano()
	s.mu.Lock()
	c := s.m[statsKey{domain, scheme}]
	if c == nil {
		c = &counters{}
		c.firstSeen.Store(now)
		s.m[statsKey{domain, scheme}] = c
	}
	s.mu.Unlock()
	c.conns.Add(1)
	c.lastSeen.Store(now)
	return c
}

func (c *counters) record(o Outcome, held bool) {
	if held {
		c.held.Add(1)
	}
	switch o {
	case Allowed:
		c.allowed.Add(1)
	case Denied:
		c.denied.Add(1)
	case TimedOut:
		c.timedOut.Add(1)
	}
}

// Snapshot returns domain -> scheme -> counters.
func (s *Stats) Snapshot() map[string]map[string]Counters {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := map[string]map[string]Counters{}
	for k, c := range s.m {
		if out[k.domain] == nil {
			out[k.domain] = map[string]Counters{}
		}
		out[k.domain][k.scheme] = Counters{
			Conns:     c.conns.Load(),
			BytesIn:   c.bytesIn.Load(),
			BytesOut:  c.bytesOut.Load(),
			Allowed:   c.allowed.Load(),
			Denied:    c.denied.Load(),
			Held:      c.held.Load(),
			TimedOut:  c.timedOut.Load(),
			FirstSeen: time.Unix(0, c.firstSeen.Load()),
			LastSeen:  time.Unix(0, c.lastSeen.Load()),
		}
	}
	return out
}
