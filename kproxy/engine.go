package main

import (
	"context"
	"fmt"
	"maps"
	"slices"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

type Outcome int

const (
	Allowed Outcome = iota
	Denied
	TimedOut
)

// snapshot.changed is closed when the snapshot is replaced; held connections
// wait on it and re-check against the new one.
type snapshot struct {
	rules   *Rules // nil until the first load
	changed chan struct{}
}

// Pending groups the undecided hosts under one root domain. Shared lists the
// private public suffixes those hosts fall under.
type Pending struct {
	Domain    string                 `json:"domain"`
	FirstSeen time.Time              `json:"first_seen"`
	Hits      int                    `json:"hits"`
	Hosts     map[string]PendingHost `json:"hosts"`
	Shared    []string               `json:"shared"`
}

type PendingHost struct {
	FirstSeen time.Time `json:"first_seen"`
	Hits      int       `json:"hits"`
}

type Engine struct {
	hold    time.Duration
	persist func(*Rules) error

	snap atomic.Pointer[snapshot]

	mu      sync.Mutex // serializes rule changes and guards pending
	pending map[string]*PendingHost
}

func newEngine(hold time.Duration, persist func(*Rules) error) *Engine {
	e := &Engine{hold: hold, persist: persist, pending: map[string]*PendingHost{}}
	e.snap.Store(&snapshot{changed: make(chan struct{})})
	return e
}

func (e *Engine) loaded() bool { return e.snap.Load().rules != nil }

func (e *Engine) rules() *Rules { return e.snap.Load().rules }

// Check decides whether a connection to host may proceed, holding it while
// rules are unloaded or (in snitch mode) while no rule matches host. held
// reports whether the connection had to wait.
func (e *Engine) Check(ctx context.Context, host string) (o Outcome, held bool) {
	timer := time.NewTimer(e.hold)
	defer timer.Stop()
	marked := false
	for {
		s := e.snap.Load()
		if r := s.rules; r != nil {
			if v, ok := r.lookup(host); ok {
				if v == Allow {
					return Allowed, held
				}
				return Denied, held
			}
			switch r.Mode {
			case Track:
				return Allowed, held
			case Strict:
				return Denied, held
			}
			if !marked {
				e.markPending(host)
				marked = true
			}
		}
		held = true
		select {
		case <-s.changed:
		case <-timer.C:
			return TimedOut, held
		case <-ctx.Done():
			return TimedOut, held
		}
	}
}

func (e *Engine) markPending(host string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	h := e.pending[host]
	if h == nil {
		h = &PendingHost{FirstSeen: time.Now()}
		e.pending[host] = h
	}
	h.Hits++
}

func (e *Engine) Pending() []Pending {
	e.mu.Lock()
	defer e.mu.Unlock()
	groups := map[string]*Pending{}
	for host, h := range e.pending {
		root := rootDomain(host)
		p := groups[root]
		if p == nil {
			p = &Pending{Domain: root, FirstSeen: h.FirstSeen, Hosts: map[string]PendingHost{}, Shared: []string{}}
			groups[root] = p
		}
		if h.FirstSeen.Before(p.FirstSeen) {
			p.FirstSeen = h.FirstSeen
		}
		p.Hits += h.Hits
		p.Hosts[host] = *h
		_, private := suffixes(host)
		for _, s := range private {
			if !slices.Contains(p.Shared, s) {
				p.Shared = append(p.Shared, s)
			}
		}
	}
	out := make([]Pending, 0, len(groups))
	for _, p := range groups {
		sort.Strings(p.Shared)
		out = append(out, *p)
	}
	return out
}

// Replace installs a complete rule set.
func (e *Engine) Replace(r *Rules) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.publish(r)
}

// Decide sets one name's verdict on top of the current rules.
func (e *Engine) Decide(domain string, v Verdict) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	cur := e.snap.Load().rules
	if cur == nil {
		return fmt.Errorf("no rules loaded yet: PUT /rules first")
	}
	next := &Rules{Mode: cur.Mode, Domains: maps.Clone(cur.Domains)}
	next.Domains[domain] = v
	return e.publish(next)
}

// publish must be called with e.mu held.
func (e *Engine) publish(r *Rules) error {
	if e.persist != nil {
		if err := e.persist(r); err != nil {
			return err
		}
	}
	for host := range e.pending {
		if _, ok := r.lookup(host); ok || r.Mode != Snitch {
			delete(e.pending, host)
		}
	}
	old := e.snap.Swap(&snapshot{rules: r, changed: make(chan struct{})})
	close(old.changed)
	return nil
}
