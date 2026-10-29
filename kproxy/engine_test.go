package main

import (
	"context"
	"fmt"
	"slices"
	"sync"
	"testing"
	"time"
)

func check(e *Engine, host string) (Outcome, bool) {
	o, held, _ := e.Check(context.Background(), httpsPort, host)
	return o, held
}

func checkAsync(e *Engine, host string) <-chan Outcome {
	ch := make(chan Outcome, 1)
	go func() {
		o, _ := check(e, host)
		ch <- o
	}()
	return ch
}

func decide(e *Engine, name string, v Verdict) error { return e.Decide(KindTLS, httpsPort, name, v) }

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
		policy   Policy
		domain   string
		want     Outcome
		wantHeld bool
	}{
		{DefaultDeny, "ok.com", Allowed, false},
		{DefaultDeny, "bad.com", Denied, false},
		{DefaultDeny, "new.com", Denied, false},
		{DefaultAllow, "bad.com", Denied, false},
		{DefaultAllow, "new.com", Allowed, false},
		{DefaultAllow, "printer", Denied, false},
		{DefaultAsk, "ok.com", Allowed, false},
		{DefaultAsk, "bad.com", Denied, false},
		{DefaultAsk, "new.com", TimedOut, true},
		{DefaultAsk, "printer", Denied, false},
	} {
		e := newEngine(10*time.Millisecond, nil)
		e.Replace(rules(tc.policy, "https ok.com", "https bad.com deny"))
		o, held := check(e, tc.domain)
		if o != tc.want || held != tc.wantHeld {
			t.Errorf("%s %s: got %v held=%v, want %v held=%v", tc.policy, tc.domain, o, held, tc.want, tc.wantHeld)
		}
	}
}

func TestCheckIsPerListener(t *testing.T) {
	e := newEngine(10*time.Millisecond, nil)
	e.Replace(rules(DefaultDeny, "http ok.com", "tls :6443 api.ok.com", "tcp :9669 redis redis.lan:9669"))
	for _, tc := range []struct {
		port int
		host string
		want Outcome
	}{
		{httpPort, "ok.com", Allowed},
		{httpsPort, "ok.com", Denied},
		{6443, "api.ok.com", Allowed},
		{6443, "ok.com", Denied},
		{9669, "redis", Denied}, // not a name-routed listener
		{1234, "ok.com", Denied},
	} {
		if o, _, _ := e.Check(context.Background(), tc.port, tc.host); o != tc.want {
			t.Errorf(":%d %s: got %v, want %v", tc.port, tc.host, o, tc.want)
		}
	}
}

func TestUnloadedHoldsThenTimesOut(t *testing.T) {
	e := newEngine(20*time.Millisecond, nil)
	if o, _ := check(e, "ok.com"); o != TimedOut {
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
	e.Replace(rules(DefaultDeny, "https ok.com"))
	if o := <-ch; o != Allowed {
		t.Fatalf("got %v", o)
	}
}

func TestAskApprovalWakesAllHeld(t *testing.T) {
	e := newEngine(time.Minute, nil)
	e.Replace(rules(DefaultAsk))
	a, b, other := checkAsync(e, "www.new.com"), checkAsync(e, "api.new.com"), checkAsync(e, "other.com")
	waitPending(t, e, 2, 3)
	if err := decide(e, "*.new.com", Allow); err != nil {
		t.Fatal(err)
	}
	if <-a != Allowed || <-b != Allowed {
		t.Fatal("held connections not released")
	}
	waitPending(t, e, 1, 1)
	decide(e, "other.com", Deny)
	if <-other != Denied {
		t.Fatal("deny did not release")
	}
	waitPending(t, e, 0, 0)
}

func TestAskSubdomainApprovalLeavesSiblingsPending(t *testing.T) {
	e := newEngine(time.Minute, nil)
	e.Replace(rules(DefaultAsk))
	a, b, c := checkAsync(e, "update.googleapis.com"), checkAsync(e, "a.other.googleapis.com"), checkAsync(e, "b.other.googleapis.com")
	waitPending(t, e, 1, 3)
	decide(e, "*.other.googleapis.com", Allow)
	if <-b != Allowed || <-c != Allowed {
		t.Fatal("subdomain approval did not release its hosts")
	}
	waitPending(t, e, 1, 1)
	if p := e.Pending(); p[0].Domain != "googleapis.com" || p[0].Kind != KindTLS || p[0].Port != httpsPort ||
		p[0].Hosts["update.googleapis.com"].Hits != 1 || !slices.Equal(p[0].Shared, []string{"googleapis.com"}) {
		t.Fatalf("pending = %+v", p)
	}
	decide(e, "*.googleapis.com", Deny)
	if <-a != Denied {
		t.Fatal("root deny did not release")
	}
}

func TestPendingIsPerListener(t *testing.T) {
	e := newEngine(time.Minute, nil)
	e.Replace(rules(DefaultAsk, "tls :6443 known.com"))
	go e.Check(context.Background(), httpPort, "new.com")
	go e.Check(context.Background(), 6443, "new.com")
	waitPending(t, e, 2, 2)
	if err := e.Decide(KindTLS, 6443, "new.com", Allow); err != nil {
		t.Fatal(err)
	}
	waitPending(t, e, 1, 1)
	if p := e.Pending(); p[0].Kind != KindHTTP || p[0].Port != httpPort {
		t.Fatalf("pending = %+v", p)
	}
	for _, bad := range []struct {
		kind Kind
		port int
	}{{KindHTTP, 6443}, {KindTLS, 7000}, {KindTCP, httpPort}} {
		if err := e.Decide(bad.kind, bad.port, "x.com", Allow); err == nil {
			t.Errorf("decided on %s :%d", bad.kind, bad.port)
		}
	}
}

func TestRemoveReturnsDomainToPending(t *testing.T) {
	e := newEngine(10*time.Millisecond, nil)
	e.Replace(rules(DefaultAsk, "https *.ok.com"))
	if err := e.Remove(KindTLS, httpsPort, "*.OK.com"); err != nil {
		t.Fatal(err)
	}
	if err := e.Remove(KindTLS, httpsPort, "*.ok.com"); err != errNoRule {
		t.Fatalf("second remove: %v", err)
	}
	if o, held := check(e, "www.ok.com"); o != TimedOut || !held {
		t.Fatalf("got %v held=%v", o, held)
	}
	waitPending(t, e, 1, 1)
}

func TestAskTimeoutStaysPending(t *testing.T) {
	e := newEngine(10*time.Millisecond, nil)
	e.Replace(rules(DefaultAsk))
	for _, host := range []string{"a.new.com", "a.new.com", "new.com"} {
		if o, held := check(e, host); o != TimedOut || !held {
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
	if err := decide(newEngine(time.Second, nil), "ok.com", Allow); err != errNotLoaded {
		t.Fatalf("decide before load: %v", err)
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
	var applied []*Rules
	e.apply = func(r *Rules) error {
		applied = append(applied, r)
		return nil
	}
	old := rules(DefaultDeny)
	e.Replace(old)
	fail = true
	if err := decide(e, "ok.com", Allow); err == nil {
		t.Fatal("expected error")
	}
	if _, ok := e.rules().Listeners[portKey{false, httpsPort}].Rules["ok.com"]; ok {
		t.Fatal("rules changed despite persist failure")
	}
	if len(applied) != 3 || applied[2] != old {
		t.Fatalf("listeners not put back: applied %d times", len(applied))
	}
}

func TestApplyFailureKeepsOldRules(t *testing.T) {
	e := newEngine(time.Second, nil)
	e.apply = func(r *Rules) error {
		if _, ok := r.Listeners[portKey{false, 9669}]; ok {
			return fmt.Errorf("port taken")
		}
		return nil
	}
	e.Replace(rules(DefaultDeny, "https ok.com"))
	if err := e.Replace(rules(DefaultDeny, "tcp :9669 redis redis.lan:9669")); err == nil {
		t.Fatal("expected error")
	}
	if _, ok := e.rules().Listeners[portKey{false, httpsPort}].Rules["ok.com"]; !ok {
		t.Fatal("rules changed despite apply failure")
	}
}

func TestConcurrentDecisionsAndSwaps(t *testing.T) {
	e := newEngine(time.Second, nil)
	e.Replace(rules(DefaultAsk))
	var checks, writes sync.WaitGroup
	results := make(chan Outcome, 400)
	for i := range 400 {
		checks.Go(func() {
			o, _ := check(e, fmt.Sprintf("d%d.com", i%20))
			results <- o
		})
	}
	for range 20 {
		writes.Go(func() { e.Replace(rules(DefaultAsk)) })
	}
	writes.Wait()
	for i := range 20 {
		writes.Go(func() { decide(e, fmt.Sprintf("d%d.com", i), Allow) })
	}
	checks.Wait()
	close(results)
	for o := range results {
		if o != Allowed {
			t.Fatalf("got %v", o)
		}
	}
}
