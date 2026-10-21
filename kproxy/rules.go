package main

import (
	"bufio"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"golang.org/x/net/publicsuffix"
)

type Mode string

const (
	Strict Mode = "strict"
	Snitch Mode = "snitch"
	Track  Mode = "track"
)

func parseMode(s string) (Mode, error) {
	switch m := Mode(s); m {
	case Strict, Snitch, Track:
		return m, nil
	}
	return "", fmt.Errorf("unknown mode %q: use strict, snitch or track", s)
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

// Rules is immutable once published to an Engine. A rule for a name matches
// that name and every name ending in "."+name.
type Rules struct {
	Mode    Mode
	Domains map[string]Verdict
}

// lookup returns the verdict of the most specific rule matching host.
func (r *Rules) lookup(host string) (Verdict, bool) {
	for name := host; ; {
		if v, ok := r.Domains[name]; ok {
			return v, true
		}
		_, parent, ok := strings.Cut(name, ".")
		if !ok {
			return "", false
		}
		name = parent
	}
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

// ruleName normalizes a rule entry: any domain name, down to a bare TLD.
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

func parseRules(r io.Reader, defaultMode Mode) (*Rules, error) {
	rules := &Rules{Mode: defaultMode, Domains: map[string]Verdict{}}
	sc := bufio.NewScanner(r)
	for n := 1; sc.Scan(); n++ {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		f := strings.Fields(line)
		if len(f) != 2 {
			return nil, fmt.Errorf("line %d: want '<allow|deny|mode> <value>'", n)
		}
		if f[0] == "mode" {
			m, err := parseMode(f[1])
			if err != nil {
				return nil, fmt.Errorf("line %d: %w", n, err)
			}
			rules.Mode = m
			continue
		}
		v, err := parseVerdict(f[0])
		if err != nil {
			return nil, fmt.Errorf("line %d: %w", n, err)
		}
		d, err := ruleName(f[1])
		if err != nil {
			return nil, fmt.Errorf("line %d: %w", n, err)
		}
		rules.Domains[d] = v
	}
	return rules, sc.Err()
}

func (r *Rules) sorted() []string {
	ds := make([]string, 0, len(r.Domains))
	for d := range r.Domains {
		ds = append(ds, d)
	}
	sort.Strings(ds)
	return ds
}

func (r *Rules) format() string {
	var b strings.Builder
	fmt.Fprintf(&b, "mode %s\n", r.Mode)
	for _, d := range r.sorted() {
		fmt.Fprintf(&b, "%s %s\n", r.Domains[d], d)
	}
	return b.String()
}

func loadRulesFile(path string, defaultMode Mode) (*Rules, error) {
	f, err := os.Open(path)
	if os.IsNotExist(err) {
		return &Rules{Mode: defaultMode, Domains: map[string]Verdict{}}, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	rules, err := parseRules(f, defaultMode)
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
