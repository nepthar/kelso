package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"golang.org/x/net/publicsuffix"
)

// Policy is what a name-routed listener does with a name no rule matches.
type Policy string

const (
	DefaultAllow Policy = "allow"
	DefaultDeny  Policy = "deny"
	DefaultAsk   Policy = "ask" // hold the connection and list the name as pending
)

func parsePolicy(s string) (Policy, error) {
	switch p := Policy(s); p {
	case DefaultAllow, DefaultDeny, DefaultAsk:
		return p, nil
	}
	return "", fmt.Errorf("unknown default policy %q: use allow, deny or ask", s)
}

type Verdict string

const (
	Allow Verdict = "allow"
	Deny  Verdict = "deny"
)

func parseVerdict(s string) (Verdict, error) {
	switch v := Verdict(s); v {
	case Allow, Deny:
		return v, nil
	}
	return "", fmt.Errorf("unknown verdict %q: use allow or deny", s)
}

// Kind is what a listener does with a connection. HTTP and TLS listeners route
// by the name in the traffic (Host header, SNI), so many rules can share one;
// TCP and UDP listeners send everything to their one rule's destination.
type Kind string

const (
	KindHTTP Kind = "http"
	KindTLS  Kind = "tls"
	KindTCP  Kind = "tcp"
	KindUDP  Kind = "udp"
)

func parseKind(s string) (Kind, error) {
	switch k := Kind(s); k {
	case KindHTTP, KindTLS, KindTCP, KindUDP:
		return k, nil
	}
	return "", fmt.Errorf("unknown kind %q: use http, tls, tcp or udp", s)
}

func (k Kind) named() bool { return k == KindHTTP || k == KindTLS }

// portKey is one listening socket. TCP and UDP ports are separate, and HTTP,
// TLS and TCP listeners all use TCP ports.
type portKey struct {
	udp  bool
	port int
}

func keyOf(k Kind, port int) portKey { return portKey{k == KindUDP, port} }

// The listeners that always exist, written as "http" and "https" rules.
const (
	httpPort  = 80
	httpsPort = 443
)

func implicit(k portKey) bool { return k == portKey{false, httpPort} || k == portKey{false, httpsPort} }

// Rule is one line of the rules file. Name is an exact name or "*.name" (name
// and everything below it) on HTTP and TLS listeners, and a label for the stats
// and DNS on TCP and UDP ones. Dest is what an allow connects to: host:port,
// or on HTTP and TLS "*:port" for the name asked for.
type Rule struct {
	Name    string
	Verdict Verdict
	Dest    string
	at      string // where the rule came from, for error messages
}

// Listener is one port and what it does. An ingress listener is a TCP or UDP
// one that relays connections in to the app rather than out of it: the same
// forwarder, but DNS never answers its name.
type Listener struct {
	Kind    Kind
	Port    int
	Ingress bool
	Rules   map[string]Rule
}

// String names a listener the way the rules file writes it.
func (l *Listener) String() string { return listenerName(l.Kind, l.Port, l.Ingress) }

// listenerName names a listener the way the rules file writes it: plain
// "http" and "https" for the ones that always exist.
func listenerName(k Kind, port int, ingress bool) string {
	switch {
	case k == KindHTTP && port == httpPort:
		return "http"
	case k == KindTLS && port == httpsPort:
		return "https"
	case ingress:
		return fmt.Sprintf("ingress %s :%d", k, port)
	}
	return fmt.Sprintf("%s :%d", k, port)
}

// ruleSpec is one rule as written, before add validates it.
type ruleSpec struct {
	Kind    Kind
	Port    int
	Ingress bool
	Name    string
	Verdict Verdict
	Dest    string
}

// match returns the most specific rule matching host: an exact name, then the
// longest wildcard.
func (l *Listener) match(host string) (Rule, bool) {
	if r, ok := l.Rules[host]; ok {
		return r, true
	}
	for name := host; ; {
		if r, ok := l.Rules["*."+name]; ok {
			return r, true
		}
		_, parent, ok := strings.Cut(name, ".")
		if !ok {
			return Rule{}, false
		}
		name = parent
	}
}

// fixed returns a TCP or UDP listener's one rule.
func (l *Listener) fixed() Rule {
	for _, r := range l.Rules {
		return r
	}
	return Rule{}
}

// Rules is immutable once published to an Engine.
type Rules struct {
	Default   Policy
	Listeners map[portKey]*Listener
}

func newRules(p Policy) *Rules {
	return &Rules{Default: p, Listeners: map[portKey]*Listener{
		{false, httpPort}:  {Kind: KindHTTP, Port: httpPort, Rules: map[string]Rule{}},
		{false, httpsPort}: {Kind: KindTLS, Port: httpsPort, Rules: map[string]Rule{}},
	}}
}

func (r *Rules) clone() *Rules {
	out := &Rules{Default: r.Default, Listeners: make(map[portKey]*Listener, len(r.Listeners))}
	for k, l := range r.Listeners {
		c := *l
		c.Rules = make(map[string]Rule, len(l.Rules))
		for n, rule := range l.Rules {
			c.Rules[n] = rule
		}
		out.Listeners[k] = &c
	}
	return out
}

// add validates one rule and adds it, creating its listener if needed. at
// says where the rule came from, for later errors about it.
func (r *Rules) add(s ruleSpec, at string) error {
	kind, port, name, verdict, dest := s.Kind, s.Port, s.Name, s.Verdict, s.Dest
	if port < 1 || port > 65535 {
		return fmt.Errorf("port %d is out of range", port)
	}
	if s.Ingress && kind.named() {
		return fmt.Errorf("ingress is tcp or udp, not %s", kind)
	}
	var err error
	if kind.named() {
		name, err = rulePattern(name)
	} else {
		name, err = ruleName(name)
	}
	if err != nil {
		return err
	}
	switch {
	case verdict == Deny && !kind.named():
		return errors.New("deny needs a name to match; tcp and udp rules can't tell names apart")
	case verdict == Deny && dest != "":
		return errors.New("a deny has no destination")
	case verdict == Deny:
	case verdict != Allow:
		return fmt.Errorf("unknown verdict %q: use allow or deny", verdict)
	case dest == "" && !kind.named():
		return fmt.Errorf("%s rule %s needs a destination HOST:PORT", kind, name)
	case dest == "":
		dest = "*:" + strconv.Itoa(port)
	default:
		if dest, err = parseDest(dest, kind.named()); err != nil {
			return err
		}
	}

	k := keyOf(kind, port)
	l := r.Listeners[k]
	what, a := string(kind), "a"
	if s.Ingress {
		what, a = "ingress "+what, "an"
	}
	switch {
	case l == nil:
		l = &Listener{Kind: kind, Port: port, Ingress: s.Ingress, Rules: map[string]Rule{}}
		r.Listeners[k] = l
	case l.Kind != kind:
		return fmt.Errorf("kproxy can't add %s %s listener on port %d because it already has a %s listener", a, what, port, l.Kind)
	case !kind.named():
		return fmt.Errorf("kproxy can't add %s listener %s on port %d because %s already uses it", what, name, port, l.fixed().Name)
	}
	if prev, ok := l.Rules[name]; ok {
		return fmt.Errorf("%s on %s :%d is already set (%s)", name, kind, port, prev.at)
	}
	l.Rules[name] = Rule{Name: name, Verdict: verdict, Dest: dest, at: at}
	return nil
}

// remove deletes one rule, and its listener once empty unless it's one of the
// listeners that always exist.
func (r *Rules) remove(kind Kind, port int, name string) error {
	k := keyOf(kind, port)
	l := r.Listeners[k]
	if l == nil || l.Kind != kind {
		return errNoRule
	}
	if _, ok := l.Rules[name]; !ok {
		return errNoRule
	}
	delete(l.Rules, name)
	if len(l.Rules) == 0 && !implicit(k) {
		delete(r.Listeners, k)
	}
	return nil
}

var errNoRule = errors.New("no such rule")

// resolves reports whether DNS should answer host: some listener would take a
// connection for it, or it is an outbound TCP or UDP rule's name.
func (r *Rules) resolves(host string) bool {
	for _, l := range r.Listeners {
		if l.Ingress {
			continue
		}
		if !l.Kind.named() {
			if _, ok := l.Rules[host]; ok {
				return true
			}
			continue
		}
		if rule, ok := l.match(host); ok {
			if rule.Verdict == Allow {
				return true
			}
		} else if r.Default != DefaultDeny && rootDomain(host) != "" {
			return true
		}
	}
	return false
}

// each calls fn for every rule, in rules file order: the implicit HTTP and TLS
// listeners, then the other outbound ones by port, then ingress by port, and
// each listener's names grouped by domain.
func (r *Rules) each(fn func(*Listener, Rule)) {
	keys := make([]portKey, 0, len(r.Listeners))
	for k := range r.Listeners {
		keys = append(keys, k)
	}
	slices.SortFunc(keys, func(a, b portKey) int {
		if c := compareBool(!implicit(a), !implicit(b)); c != 0 {
			return c
		}
		if c := compareBool(r.Listeners[a].Ingress, r.Listeners[b].Ingress); c != 0 {
			return c
		}
		if a.port != b.port {
			return a.port - b.port
		}
		return compareBool(a.udp, b.udp)
	})
	for _, k := range keys {
		l := r.Listeners[k]
		names := make([]string, 0, len(l.Rules))
		for n := range l.Rules {
			names = append(names, n)
		}
		slices.SortFunc(names, func(a, b string) int {
			if c := strings.Compare(reversed(a), reversed(b)); c != 0 {
				return c
			}
			return compareBool(!strings.HasPrefix(a, "*."), !strings.HasPrefix(b, "*."))
		})
		for _, n := range names {
			fn(l, l.Rules[n])
		}
	}
}

func compareBool(a, b bool) int {
	switch {
	case a == b:
		return 0
	case a:
		return 1
	}
	return -1
}

// reversed turns "*.ads.google.com" into "com.google.ads", so a domain sorts
// next to its subdomains.
func reversed(name string) string {
	labels := strings.Split(strings.TrimPrefix(name, "*."), ".")
	slices.Reverse(labels)
	return strings.Join(labels, ".")
}

// warnings lists rules that are allowed but probably mistakes.
func (r *Rules) warnings() []string {
	var out []string
	denyOnly := map[*Listener]bool{}
	r.each(func(l *Listener, rule Rule) {
		if _, seen := denyOnly[l]; !seen {
			denyOnly[l] = true
		}
		if rule.Verdict == Allow {
			denyOnly[l] = false
		}
		host, _, _ := net.SplitHostPort(rule.Dest)
		if l.Kind == KindTLS && host != "*" && host != "" && host != rule.Name {
			out = append(out, fmt.Sprintf("%s: tls :%d sends %s to %s, so certificate checks fail unless %s's certificate also covers %s",
				rule.at, l.Port, rule.Name, rule.Dest, host, rule.Name))
		}
	})
	if r.Default == DefaultDeny {
		for l, only := range denyOnly {
			if only && !implicit(keyOf(l.Kind, l.Port)) {
				out = append(out, fmt.Sprintf("%s :%d has only denies, which narrow nothing under default deny", l.Kind, l.Port))
			}
		}
	}
	slices.Sort(out)
	return out
}

func normHost(h string) string { return strings.TrimSuffix(strings.ToLower(h), ".") }

// suffixes returns host's ICANN public suffix ("" if none) and the PSL
// private-section suffixes above it that host falls under, such as
// googleapis.com or s3.amazonaws.com: names whose subdomains belong to many
// different owners.
func suffixes(host string) (icannSuffix string, private []string) {
	suffix, icann := publicsuffix.PublicSuffix(host)
	for !icann {
		private = append(private, suffix)
		_, parent, ok := strings.Cut(suffix, ".")
		if !ok {
			return "", private
		}
		suffix, icann = publicsuffix.PublicSuffix(parent)
	}
	return suffix, private
}

// rootDomain returns host's domain directly under its ICANN suffix
// (bbc.co.uk, googleapis.com), ignoring the PSL's private section. It is ""
// for hosts without one: IP literals, single labels, unknown TLDs, bare
// suffixes.
func rootDomain(host string) string {
	host = normHost(host)
	if host == "" || net.ParseIP(host) != nil {
		return ""
	}
	suffix, _ := suffixes(host)
	if suffix == "" || host == suffix {
		return ""
	}
	rest := strings.TrimSuffix(host, "."+suffix)
	return rest[strings.LastIndex(rest, ".")+1:] + "." + suffix
}

// ruleName normalizes a name: any domain name, down to a bare TLD.
func ruleName(s string) (string, error) {
	n := normHost(strings.TrimSpace(s))
	if n == "" || net.ParseIP(n) != nil {
		return "", fmt.Errorf("%q is not a domain name", s)
	}
	for _, l := range strings.Split(n, ".") {
		if l == "" || strings.Trim(l, "abcdefghijklmnopqrstuvwxyz0123456789-_") != "" {
			return "", fmt.Errorf("%q is not a domain name", s)
		}
	}
	return n, nil
}

// rulePattern normalizes a name-routed rule's name: a name, or "*." and a name
// for that name and everything below it.
func rulePattern(s string) (string, error) {
	base, wild := strings.CutPrefix(strings.TrimSpace(s), "*.")
	n, err := ruleName(base)
	if err != nil {
		return "", fmt.Errorf("%q is not a domain name or *.domain", s)
	}
	if wild {
		n = "*." + n
	}
	return n, nil
}

// parseDest normalizes HOST:PORT, where HOST is a name or IPv4 address and
// PORT is numeric. star allows "*" for the name asked for.
func parseDest(s string, star bool) (string, error) {
	bad := fmt.Errorf("destination %q must be HOST:PORT with a numeric port, like rpi.lan:3000 or 192.168.1.3:3000", s)
	host, port, err := net.SplitHostPort(s)
	if err != nil || !validPort(port) {
		return "", bad
	}
	p, _ := strconv.Atoi(port)
	switch ip := net.ParseIP(host); {
	case host == "*" && star:
	case host == "*":
		return "", fmt.Errorf("destination %q: only http and tls rules can use * for the name asked for", s)
	case ip != nil && ip.To4() == nil:
		return "", fmt.Errorf("destination %q: only IPv4 addresses are supported", s)
	case ip != nil:
	default:
		if host, err = ruleName(host); err != nil {
			return "", bad
		}
	}
	return net.JoinHostPort(host, strconv.Itoa(p)), nil
}

func validPort(s string) bool {
	p, err := strconv.Atoi(s)
	return err == nil && p >= 1 && p <= 65535 && s[0] >= '0' && s[0] <= '9'
}

const ruleSyntax = "want 'default <allow|deny|ask>', 'http <name> [<dest>|deny]', 'https <name> [deny]', " +
	"'<http|tls> :<port> <name> [<dest>|deny]', '<tcp|udp> :<port> <name> <dest>' " +
	"or 'ingress <tcp|udp> :<port> <name> <dest>'"

// parseLine turns one rule line, already split into fields, into a rule for
// add to check. "http NAME" and "https NAME" are short for the listeners on 80
// and 443, and "ingress" in front of a tcp or udp rule makes it relay in.
func parseLine(f []string) (ruleSpec, error) {
	var s ruleSpec
	if f[0] == "ingress" {
		s.Ingress, f = true, f[1:]
		if len(f) == 0 || (f[0] != string(KindTCP) && f[0] != string(KindUDP)) {
			return s, errors.New("ingress needs tcp or udp: ingress <tcp|udp> :<port> <name> <dest>")
		}
	}
	var rest []string
	switch {
	case (f[0] == "http" || f[0] == "https") && len(f) > 1 && !strings.HasPrefix(f[1], ":"):
		s.Kind, s.Port, rest = KindHTTP, httpPort, f[1:]
		if f[0] == "https" {
			s.Kind, s.Port = KindTLS, httpsPort
		}
	case f[0] == "https" && len(f) > 1:
		return s, errors.New("https always means port 443; use tls :<port> for others")
	case len(f) < 3:
		return s, errors.New(ruleSyntax)
	default:
		kind, err := parseKind(f[0])
		if err != nil {
			return s, err
		}
		p, ok := strings.CutPrefix(f[1], ":")
		if !ok || !validPort(p) {
			return s, fmt.Errorf("%s rules need a listening port like :6443, not %q", kind, f[1])
		}
		s.Kind, rest = kind, f[2:]
		s.Port, _ = strconv.Atoi(p)
	}
	if len(rest) != 1 && len(rest) != 2 {
		return s, errors.New(ruleSyntax)
	}
	s.Name, s.Verdict = rest[0], Allow
	if len(rest) == 2 {
		if rest[1] == "deny" {
			s.Verdict = Deny
		} else {
			s.Dest = rest[1]
		}
	}
	if f[0] == "https" && s.Dest != "" {
		return s, fmt.Errorf("https takes no destination; to send %s elsewhere write tls :443 %s %s", s.Name, s.Name, s.Dest)
	}
	return s, nil
}

func parseRules(r io.Reader, defaultPolicy Policy) (*Rules, error) {
	rules := newRules(defaultPolicy)
	sc := bufio.NewScanner(r)
	for n := 1; sc.Scan(); n++ {
		line, _, _ := strings.Cut(sc.Text(), "#")
		f := strings.Fields(line)
		if len(f) == 0 {
			continue
		}
		if f[0] == "default" && len(f) == 2 {
			p, err := parsePolicy(f[1])
			if err != nil {
				return nil, fmt.Errorf("line %d: %w", n, err)
			}
			rules.Default = p
			continue
		}
		s, err := parseLine(f)
		if err == nil {
			err = rules.add(s, fmt.Sprintf("line %d", n))
		}
		if err != nil {
			return nil, fmt.Errorf("line %d: %w", n, err)
		}
	}
	return rules, sc.Err()
}

// line writes a rule the way the rules file does, using the http and https
// shorthands where they say the same thing.
func (l *Listener) line(rule Rule) string {
	tail := rule.Dest
	if rule.Verdict == Deny {
		tail = "deny"
	}
	var head string
	switch star := "*:" + strconv.Itoa(l.Port); {
	case l.Kind == KindHTTP && l.Port == httpPort:
		head = "http"
		if tail == star {
			tail = ""
		}
	case l.Kind == KindTLS && l.Port == httpsPort && (tail == star || tail == "deny"):
		head = "https"
		if tail == star {
			tail = ""
		}
	case l.Ingress:
		head = fmt.Sprintf("ingress %s :%d", l.Kind, l.Port)
	default:
		head = fmt.Sprintf("%s :%d", l.Kind, l.Port)
	}
	return strings.TrimSpace(head + " " + rule.Name + " " + tail)
}

const rulesFileHeader = "# rules.txt for kproxy. Lists allowed domains. See github.com/nepthar/kelso/kproxy for more information"

func (r *Rules) format() string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s\ndefault %s\n", rulesFileHeader, r.Default)
	r.each(func(l *Listener, rule Rule) { fmt.Fprintln(&b, l.line(rule)) })
	return b.String()
}

func loadRulesFile(path string, defaultPolicy Policy) (*Rules, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return newRules(defaultPolicy), nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	rules, err := parseRules(f, defaultPolicy)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return rules, nil
}

func writeRulesFile(path string, r *Rules) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	perm := os.FileMode(0o644)
	if fi, err := os.Stat(path); err == nil {
		perm = fi.Mode().Perm()
	}
	if err := tmp.Chmod(perm); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.WriteString(r.format()); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
