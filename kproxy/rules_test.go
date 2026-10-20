package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBaseDomain(t *testing.T) {
	for host, want := range map[string]string{
		"microsoft.com":       "microsoft.com",
		"a.b.microsoft.com":   "microsoft.com",
		"WWW.Microsoft.COM.":  "microsoft.com",
		"news.bbc.co.uk":      "bbc.co.uk",
		"nepthar.github.io":   "nepthar.github.io",
		"x.nepthar.github.io": "nepthar.github.io",
		"github.io":           "",
		"co.uk":               "",
		"com":                 "",
		"localhost":           "",
		"1.2.3.4":             "",
		"::1":                 "",
		"":                    "",
	} {
		if got := baseDomain(host); got != want {
			t.Errorf("baseDomain(%q) = %q, want %q", host, got, want)
		}
	}
}

func TestRuleDomain(t *testing.T) {
	if d, err := ruleDomain(" BBC.co.uk "); err != nil || d != "bbc.co.uk" {
		t.Errorf("got %q, %v", d, err)
	}
	for _, bad := range []string{"github.io", "co.uk", "api.weather.com", "1.2.3.4", ""} {
		if _, err := ruleDomain(bad); err == nil {
			t.Errorf("ruleDomain(%q) accepted", bad)
		}
	}
}

func TestParseRules(t *testing.T) {
	r, err := parseRules(strings.NewReader("# c\nmode snitch\n\nallow example.com\ndeny Example.org\n"), Strict)
	if err != nil {
		t.Fatal(err)
	}
	if r.Mode != Snitch || r.Domains["example.com"] != Allow || r.Domains["example.org"] != Deny {
		t.Fatalf("got %+v", r)
	}
	if r.format() != "mode snitch\nallow example.com\ndeny example.org\n" {
		t.Fatalf("format: %q", r.format())
	}
	for _, bad := range []string{"allow", "permit example.com", "allow co.uk", "mode loose", "allow a.example.com"} {
		if _, err := parseRules(strings.NewReader(bad), Strict); err == nil {
			t.Errorf("parsed %q", bad)
		}
	}
}

func TestRulesFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rules.txt")
	r, err := loadRulesFile(path, Track)
	if err != nil || r.Mode != Track || len(r.Domains) != 0 {
		t.Fatalf("missing file: %+v, %v", r, err)
	}
	r.Domains["example.com"] = Deny
	if err := writeRulesFile(path, r); err != nil {
		t.Fatal(err)
	}
	got, err := loadRulesFile(path, Strict)
	if err != nil || got.Mode != Track || got.Domains["example.com"] != Deny {
		t.Fatalf("reload: %+v, %v", got, err)
	}
	entries, _ := os.ReadDir(filepath.Dir(path))
	if len(entries) != 1 {
		t.Fatalf("temp files left behind: %v", entries)
	}
}
