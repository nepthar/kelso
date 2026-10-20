package main

import (
	"context"
	"fmt"
	"maps"
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

type Pending struct {
	Domain    string    `json:"domain"`
	FirstSeen time.Time `json:"first_seen"`
	Hits      int       `json:"hits"`
}

type Engine struct {
	hold    time.Duration
	persist func(*Rules) error

	snap atomic.Pointer[snapshot]

	mu      sync.Mutex // serializes rule changes and guards pending
	pending map[string]*Pending
}

func newEngine(hold time.Duration, persist func(*Rules) error) *Engine {
	e := &Engine{hold: hold, persist: persist, pending: map[string]*Pending{}}
	e.snap.Store(&snapshot{changed: make(chan struct{})})
	return e
}

func (e *Engine) loaded() bool { return e.snap.Load().rules != nil }

func (e *Engine) rules() *Rules { return e.snap.Load().rules }

// Check decides whether a connection to domain may proceed, holding it while
// rules are unloaded or (in snitch mode) while the domain is undecided.
// held reports whether the connection had to wait.
func (e *Engine) Check(ctx context.Context, domain string) (o Outcome, held bool) {
	timer := time.NewTimer(e.hold)
	defer timer.Stop()
	marked := false
	for {
		s := e.snap.Load()
		if r := s.rules; r != nil {
			switch r.Domains[domain] {
			case Allow:
				return Allowed, held
			case Deny:
				return Denied, held
			}
			switch r.Mode {
			case Track:
				return Allowed, held
			case Strict:
				return Denied, held
			}
			if !marked {
				e.markPending(domain)
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

func (e *Engine) markPending(domain string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	p := e.pending[domain]
	if p == nil {
		p = &Pending{Domain: domain, FirstSeen: time.Now()}
		e.pending[domain] = p
	}
	p.Hits++
}

func (e *Engine) Pending() []Pending {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]Pending, 0, len(e.pending))
	for _, p := range e.pending {
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

// Decide sets one domain's verdict on top of the current rules.
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
	for d := range e.pending {
		if _, ok := r.Domains[d]; ok || r.Mode != Snitch {
			delete(e.pending, d)
		}
	}
	old := e.snap.Swap(&snapshot{rules: r, changed: make(chan struct{})})
	close(old.changed)
	return nil
}
