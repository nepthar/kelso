package main

import (
	"context"
	"errors"
	"fmt"
	"log"
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

// Pending groups the undecided hosts on one listener under one root domain.
// Shared lists the private public suffixes those hosts fall under.
type Pending struct {
	Kind      Kind                   `json:"kind"`
	Port      int                    `json:"port"`
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

type pendingKey struct {
	kind Kind
	port int
	host string
}

type Engine struct {
	hold    time.Duration
	persist func(*Rules) error
	apply   func(*Rules) error // opens and closes listeners; runs before persist

	snap atomic.Pointer[snapshot]

	mu      sync.Mutex // serializes rule changes and guards pending
	pending map[pendingKey]*PendingHost
}

func newEngine(hold time.Duration, persist func(*Rules) error) *Engine {
	e := &Engine{hold: hold, persist: persist, pending: map[pendingKey]*PendingHost{}}
	e.snap.Store(&snapshot{changed: make(chan struct{})})
	return e
}

func (e *Engine) loaded() bool { return e.snap.Load().rules != nil }

func (e *Engine) rules() *Rules { return e.snap.Load().rules }

// Resolves reports whether DNS should answer host. Before rules load it
// answers everything, since connections are held until they do.
func (e *Engine) Resolves(host string) bool {
	r := e.rules()
	return r == nil || r.resolves(normHost(host))
}

// Check decides whether a connection for host on the HTTP or TLS listener on
// port may proceed, holding it while rules are unloaded or (with the ask
// policy) while no rule matches host. held reports whether the connection had
// to wait; rule is the matching rule, zero if none matched. A host with no
// root domain is only ever allowed by a rule naming it.
func (e *Engine) Check(ctx context.Context, port int, host string) (o Outcome, held bool, rule Rule) {
	var timeout <-chan time.Time
	marked := false
	for {
		s := e.snap.Load()
		if r := s.rules; r != nil {
			l := r.Listeners[portKey{false, port}]
			if l == nil || !l.Kind.named() {
				return Denied, held, Rule{}
			}
			if rule, ok := l.match(host); ok {
				if rule.Verdict == Allow {
					return Allowed, held, rule
				}
				return Denied, held, rule
			}
			if rootDomain(host) == "" {
				return Denied, held, Rule{}
			}
			switch r.Default {
			case DefaultAllow:
				return Allowed, held, Rule{}
			case DefaultDeny:
				return Denied, held, Rule{}
			}
			if !marked {
				e.markPending(pendingKey{l.Kind, port, host})
				marked = true
			}
		}
		if !held {
			timer := time.NewTimer(e.hold)
			defer timer.Stop()
			timeout = timer.C
			held = true
		}
		select {
		case <-s.changed:
		case <-timeout:
			return TimedOut, held, Rule{}
		case <-ctx.Done():
			return TimedOut, held, Rule{}
		}
	}
}

// maxPending bounds the pending list against an app hitting endless random
// hostnames. Hosts beyond it are still held, just not listed.
const maxPending = 1000

func (e *Engine) markPending(k pendingKey) {
	e.mu.Lock()
	defer e.mu.Unlock()
	h := e.pending[k]
	if h == nil {
		if len(e.pending) >= maxPending {
			return
		}
		h = &PendingHost{FirstSeen: time.Now()}
		e.pending[k] = h
	}
	h.Hits++
}

func (e *Engine) Pending() []Pending {
	e.mu.Lock()
	hosts := make(map[pendingKey]PendingHost, len(e.pending))
	for k, h := range e.pending {
		hosts[k] = *h
	}
	e.mu.Unlock()

	type groupKey struct {
		kind Kind
		port int
		root string
	}
	groups := map[groupKey]*Pending{}
	for k, h := range hosts {
		root := rootDomain(k.host)
		g := groupKey{k.kind, k.port, root}
		p := groups[g]
		if p == nil {
			p = &Pending{Kind: k.kind, Port: k.port, Domain: root, FirstSeen: h.FirstSeen,
				Hosts: map[string]PendingHost{}, Shared: []string{}}
			groups[g] = p
		}
		if h.FirstSeen.Before(p.FirstSeen) {
			p.FirstSeen = h.FirstSeen
		}
		p.Hits += h.Hits
		p.Hosts[k.host] = h
		_, private := suffixes(k.host)
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
	if err := e.publish(r); err != nil {
		return err
	}
	for _, w := range r.warnings() {
		log.Printf("kproxy: warning: %s", w)
	}
	return nil
}

var errNotLoaded = errors.New("no rules loaded yet: PUT /rules first")

// Decide sets one name's verdict on an existing HTTP or TLS listener, on top
// of the current rules. An allow connects to the name asked for.
func (e *Engine) Decide(kind Kind, port int, name string, v Verdict) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	cur := e.snap.Load().rules
	if cur == nil {
		return errNotLoaded
	}
	if l := cur.Listeners[keyOf(kind, port)]; l == nil || l.Kind != kind || !kind.named() {
		return fmt.Errorf("no http or tls listener %s :%d to decide on", kind, port)
	}
	name, err := rulePattern(name)
	if err != nil {
		return err
	}
	next := cur.clone()
	next.remove(kind, port, name)
	if err := next.add(ruleSpec{Kind: kind, Port: port, Name: name, Verdict: v}, "decided"); err != nil {
		return err
	}
	return e.publish(next)
}

// Remove deletes one rule from the current rules.
func (e *Engine) Remove(kind Kind, port int, name string) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	cur := e.snap.Load().rules
	if cur == nil {
		return errNotLoaded
	}
	if n, err := rulePattern(name); err == nil {
		name = n
	}
	next := cur.clone()
	if err := next.remove(kind, port, name); err != nil {
		return err
	}
	return e.publish(next)
}

// publish must be called with e.mu held.
func (e *Engine) publish(r *Rules) error {
	if e.apply != nil {
		if err := e.apply(r); err != nil {
			return err
		}
	}
	if e.persist != nil {
		if err := e.persist(r); err != nil {
			if e.apply != nil {
				prev := e.snap.Load().rules
				if prev == nil {
					prev = implicitRules
				}
				e.apply(prev)
			}
			return err
		}
	}
	for k := range e.pending {
		l := r.Listeners[portKey{false, k.port}]
		if l == nil || l.Kind != k.kind || r.Default != DefaultAsk {
			delete(e.pending, k)
		} else if _, ok := l.match(k.host); ok {
			delete(e.pending, k)
		}
	}
	old := e.snap.Swap(&snapshot{rules: r, changed: make(chan struct{})})
	close(old.changed)
	return nil
}
