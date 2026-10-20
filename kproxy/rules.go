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

// Rules is immutable once published to an Engine.
type Rules struct {
	Mode    Mode
	Domains map[string]Verdict
}

// baseDomain returns the registrable domain for host, or "" if host has none
// (IP literals, bare public suffixes, single labels).
func baseDomain(host string) string {
	host = strings.TrimSuffix(strings.ToLower(host), ".")
	if host == "" || net.ParseIP(host) != nil {
		return ""
	}
	base, err := publicsuffix.EffectiveTLDPlusOne(host)
	if err != nil {
		return ""
	}
	return base
}

// ruleDomain normalizes a rule entry, which must already be a base domain.
func ruleDomain(s string) (string, error) {
	d := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(s)), ".")
	base := baseDomain(d)
	switch {
	case base == "":
		return "", fmt.Errorf("%q is not a registrable domain", s)
	case base != d:
		return "", fmt.Errorf("%q is a subdomain: use %q", s, base)
	}
	return d, nil
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
		d, err := ruleDomain(f[1])
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
