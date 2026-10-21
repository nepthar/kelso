package main

import (
	"context"
	"fmt"
	"slices"
	"sync"
	"testing"
	"time"
)

func rules(m Mode, kv ...string) *Rules {
	r := &Rules{Mode: m, Domains: map[string]Verdict{}}
	for i := 0; i < len(kv); i += 2 {
		r.Domains[kv[i]] = Verdict(kv[i+1])
	}
	return r
}

func checkAsync(e *Engine, host string) <-chan Outcome {
	ch := make(chan Outcome, 1)
	go func() {
		o, _ := e.Check(context.Background(), host)
		ch <- o
	}()
	return ch
}

// waitPending waits until there are n pending domains with hits in total.
func waitPending(t *testing.T, e *Engine, n, hits int) {
	t.Helper()
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); time.Sleep(time.Millisecond) {
		ps, total := e.Pending(), 0
		for _, p := range ps {
			total += p.Hits
		}
		if len(ps) == n && total == hits {
			return
		}
	}
	t.Fatalf("pending = %v, want %d entries with %d hits", e.Pending(), n, hits)
}

func TestModes(t *testing.T) {
	for _, tc := range []struct {
		mode     Mode
		domain   string
		want     Outcome
		wantHeld bool
	}{
		{Strict, "ok.com", Allowed, false},
		{Strict, "bad.com", Denied, false},
		{Strict, "new.com", Denied, false},
		{Track, "bad.com", Denied, false},
		{Track, "new.com", Allowed, false},
		{Snitch, "ok.com", Allowed, false},
		{Snitch, "bad.com", Denied, false},
		{Snitch, "new.com", TimedOut, true},
	} {
		e := newEngine(10*time.Millisecond, nil)
		e.Replace(rules(tc.mode, "ok.com", "allow", "bad.com", "deny"))
		o, held := e.Check(context.Background(), tc.domain)
		if o != tc.want || held != tc.wantHeld {
			t.Errorf("%s %s: got %v held=%v, want %v held=%v", tc.mode, tc.domain, o, held, tc.want, tc.wantHeld)
		}
	}
}

func TestUnloadedHoldsThenTimesOut(t *testing.T) {
	e := newEngine(20*time.Millisecond, nil)
	if o, _ := e.Check(context.Background(), "ok.com"); o != TimedOut {
		t.Fatalf("got %v", o)
	}
	if len(e.Pending()) != 0 {
		t.Fatal("unloaded check marked pending")
	}
}

func TestUnloadedReleasedByFirstPush(t *testing.T) {
	e := newEngine(time.Minute, nil)
	ch := checkAsync(e, "ok.com")
	time.Sleep(5 * time.Millisecond)
	e.Replace(rules(Strict, "ok.com", "allow"))
	if o := <-ch; o != Allowed {
		t.Fatalf("got %v", o)
	}
}

func TestSnitchApprovalWakesAllHeld(t *testing.T) {
	e := newEngine(time.Minute, nil)
	e.Replace(rules(Snitch))
	a, b, other := checkAsync(e, "www.new.com"), checkAsync(e, "api.new.com"), checkAsync(e, "other.com")
	waitPending(t, e, 2, 3)
	if err := e.Decide("new.com", Allow); err != nil {
		t.Fatal(err)
	}
	if <-a != Allowed || <-b != Allowed {
		t.Fatal("held connections not released")
	}
	waitPending(t, e, 1, 1)
	e.Decide("other.com", Deny)
	if <-other != Denied {
		t.Fatal("deny did not release")
	}
	waitPending(t, e, 0, 0)
}

func TestSnitchSubdomainApprovalLeavesSiblingsPending(t *testing.T) {
	e := newEngine(time.Minute, nil)
	e.Replace(rules(Snitch))
	a, b, c := checkAsync(e, "update.googleapis.com"), checkAsync(e, "a.other.googleapis.com"), checkAsync(e, "b.other.googleapis.com")
	waitPending(t, e, 1, 3)
	e.Decide("other.googleapis.com", Allow)
	if <-b != Allowed || <-c != Allowed {
		t.Fatal("subdomain approval did not release its hosts")
	}
	waitPending(t, e, 1, 1)
	if p := e.Pending(); p[0].Domain != "googleapis.com" || p[0].Hosts["update.googleapis.com"].Hits != 1 ||
		!slices.Equal(p[0].Shared, []string{"googleapis.com"}) {
		t.Fatalf("pending = %+v", p)
	}
	e.Decide("googleapis.com", Deny)
	if <-a != Denied {
		t.Fatal("root deny did not release")
	}
}

func TestSnitchTimeoutStaysPending(t *testing.T) {
	e := newEngine(10*time.Millisecond, nil)
	e.Replace(rules(Snitch))
	for _, host := range []string{"a.new.com", "a.new.com", "new.com"} {
		if o, held := e.Check(context.Background(), host); o != TimedOut || !held {
			t.Fatalf("got %v held=%v", o, held)
		}
	}
	p := e.Pending()
	if len(p) != 1 || p[0].Hits != 3 || p[0].Hosts["a.new.com"].Hits != 2 || p[0].Hosts["new.com"].Hits != 1 ||
		len(p[0].Shared) != 0 {
		t.Fatalf("pending = %+v", p)
	}
}

func TestDecideBeforeLoad(t *testing.T) {
	if err := newEngine(time.Second, nil).Decide("ok.com", Allow); err == nil {
		t.Fatal("decide before load succeeded")
	}
}

func TestPersistFailureKeepsOldRules(t *testing.T) {
	fail := false
	e := newEngine(time.Second, func(*Rules) error {
		if fail {
			return fmt.Errorf("disk full")
		}
		return nil
	})
	e.Replace(rules(Strict))
	fail = true
	if err := e.Decide("ok.com", Allow); err == nil {
		t.Fatal("expected error")
	}
	if _, ok := e.rules().Domains["ok.com"]; ok {
		t.Fatal("rules changed despite persist failure")
	}
}

func TestConcurrentDecisionsAndSwaps(t *testing.T) {
	e := newEngine(time.Second, nil)
	e.Replace(rules(Snitch))
	var checks, writes sync.WaitGroup
	results := make(chan Outcome, 400)
	for i := range 400 {
		checks.Go(func() {
			d := fmt.Sprintf("d%d.com", i%20)
			o, _ := e.Check(context.Background(), d)
			results <- o
		})
	}
	for range 20 {
		writes.Go(func() { e.Replace(rules(Snitch)) })
	}
	writes.Wait()
	for i := range 20 {
		writes.Go(func() { e.Decide(fmt.Sprintf("d%d.com", i), Allow) })
	}
	checks.Wait()
	close(results)
	for o := range results {
		if o != Allowed {
			t.Fatalf("got %v", o)
		}
	}
}
