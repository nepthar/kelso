package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRootDomain(t *testing.T) {
	for host, want := range map[string]string{
		"microsoft.com":           "microsoft.com",
		"a.b.microsoft.com":       "microsoft.com",
		"WWW.Microsoft.COM.":      "microsoft.com",
		"news.bbc.co.uk":          "bbc.co.uk",
		"update.googleapis.com":   "googleapis.com",
		"a.other.googleapis.com":  "googleapis.com",
		"x.nepthar.github.io":     "github.io",
		"github.io":               "github.io",
		"bucket.s3.amazonaws.com": "amazonaws.com",
		"co.uk":                   "",
		"com":                     "",
		"localhost":               "",
		"printer.internal":        "",
		"1.2.3.4":                 "",
		"::1":                     "",
		"":                        "",
	} {
		if got := rootDomain(host); got != want {
			t.Errorf("rootDomain(%q) = %q, want %q", host, got, want)
		}
	}
}

func TestRuleName(t *testing.T) {
	for in, want := range map[string]string{
		" BBC.co.uk ":       "bbc.co.uk",
		"com":               "com",
		"nepthar.github.io": "nepthar.github.io",
		"api.weather.com.":  "api.weather.com",
	} {
		if got, err := ruleName(in); err != nil || got != want {
			t.Errorf("ruleName(%q) = %q, %v", in, got, err)
		}
	}
	for _, bad := range []string{"1.2.3.4", "", "a..com", "*.com", "a b.com"} {
		if _, err := ruleName(bad); err == nil {
			t.Errorf("ruleName(%q) accepted", bad)
		}
	}
}

func TestLookupMostSpecificWins(t *testing.T) {
	r := rules(Strict,
		"nepthar.github.io", "deny",
		"tacos.nepthar.github.io", "allow",
		"google.com", "allow",
		"org", "deny")
	for host, want := range map[string]Verdict{
		"tacos.nepthar.github.io":   Allow,
		"a.tacos.nepthar.github.io": Allow,
		"nepthar.github.io":         Deny,
		"x.nepthar.github.io":       Deny,
		"other.github.io":           "",
		"maps.google.com":           Allow,
		"notgoogle.com":             "",
		"anything.org":              Deny,
	} {
		if got, _ := r.lookup(host); got.Verdict != want {
			t.Errorf("lookup(%q) = %q, want %q", host, got.Verdict, want)
		}
	}
}

func TestParseRules(t *testing.T) {
	r, err := parseRules(strings.NewReader(
		"# c\nmode snitch\n\nallow example.com\ndeny Example.org\nallow AdGuard 192.168.1.3:3000\n"), Strict)
	if err != nil {
		t.Fatal(err)
	}
	if r.Mode != Snitch || r.Domains["example.com"] != (Rule{Verdict: Allow}) ||
		r.Domains["example.org"] != (Rule{Verdict: Deny}) ||
		r.Domains["adguard"] != (Rule{Verdict: Allow, Target: "192.168.1.3:3000"}) {
		t.Fatalf("got %+v", r)
	}
	want := "mode snitch\nallow adguard 192.168.1.3:3000\nallow example.com\ndeny example.org\n"
	if r.format() != want {
		t.Fatalf("format: %q", r.format())
	}
	for _, bad := range []string{
		"allow", "permit example.com", "allow 10.0.0.1", "mode loose", "allow *.example.com",
		"deny adguard 192.168.1.3:3000", "allow adguard nas.local:80", "allow adguard 192.168.1.3",
		"allow adguard [::1]:80", "allow adguard 192.168.1.3:80 extra",
	} {
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
	r.Domains["example.com"] = Rule{Verdict: Deny}
	r.Domains["nas"] = Rule{Verdict: Allow, Target: "192.168.1.20:5000"}
	if err := writeRulesFile(path, r); err != nil {
		t.Fatal(err)
	}
	got, err := loadRulesFile(path, Strict)
	if err != nil || got.Mode != Track || got.Domains["example.com"].Verdict != Deny ||
		got.Domains["nas"].Target != "192.168.1.20:5000" {
		t.Fatalf("reload: %+v, %v", got, err)
	}
	entries, _ := os.ReadDir(filepath.Dir(path))
	if len(entries) != 1 {
		t.Fatalf("temp files left behind: %v", entries)
	}
	if fi, _ := os.Stat(path); fi.Mode().Perm() != 0o644 {
		t.Fatalf("new file perm = %v", fi.Mode().Perm())
	}
	os.Chmod(path, 0o640)
	if err := writeRulesFile(path, r); err != nil {
		t.Fatal(err)
	}
	if fi, _ := os.Stat(path); fi.Mode().Perm() != 0o640 {
		t.Fatalf("perm after rewrite = %v", fi.Mode().Perm())
	}
}
