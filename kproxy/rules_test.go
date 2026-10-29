package main

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// rules parses rules file lines under default policy p, panicking on a
// mistake.
func rules(p Policy, lines ...string) *Rules {
	r, err := parseRules(strings.NewReader(strings.Join(lines, "\n")), p)
	if err != nil {
		panic(err)
	}
	return r
}

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

func TestRulePattern(t *testing.T) {
	for in, want := range map[string]string{
		" BBC.co.uk ":       "bbc.co.uk",
		"com":               "com",
		"*.com":             "*.com",
		"*.Googleapis.com.": "*.googleapis.com",
		"api.weather.com.":  "api.weather.com",
	} {
		if got, err := rulePattern(in); err != nil || got != want {
			t.Errorf("rulePattern(%q) = %q, %v", in, got, err)
		}
	}
	for _, bad := range []string{"1.2.3.4", "", "a..com", "*googleapis.com", "a.*.com", "*", "*.", "a b.com"} {
		if _, err := rulePattern(bad); err == nil {
			t.Errorf("rulePattern(%q) accepted", bad)
		}
	}
	if _, err := ruleName("*.com"); err == nil {
		t.Error("ruleName accepted a wildcard")
	}
}

func TestMatchMostSpecificWins(t *testing.T) {
	r := rules(DefaultDeny,
		"https *.github.io deny",
		"https *.tacos.nepthar.github.io",
		"https nepthar.github.io",
		"https *.googleapis.com",
		"https ads.googleapis.com deny",
		"https api.weather.com",
	)
	l := r.Listeners[portKey{false, 443}]
	for host, want := range map[string]Verdict{
		"tacos.nepthar.github.io":   Allow,
		"a.tacos.nepthar.github.io": Allow,
		"nepthar.github.io":         Allow,
		"x.nepthar.github.io":       Deny,
		"github.io":                 Deny,
		"googleapis.com":            Allow,
		"docs.googleapis.com":       Allow,
		"ads.googleapis.com":        Deny,
		"x.ads.googleapis.com":      Allow,
		"evilgoogleapis.com":        "",
		"api.weather.com":           Allow,
		"weather.com":               "",
		"x.api.weather.com":         "",
	} {
		if got, _ := l.match(host); got.Verdict != want {
			t.Errorf("match(%q) = %q, want %q", host, got.Verdict, want)
		}
	}
}

func TestParseAndFormatRules(t *testing.T) {
	r := rules(DefaultDeny,
		"# comment",
		"default ask",
		"",
		"udp :2343 video-stream cam0.lab.my-domain.com:2343",
		"tcp :9669 Redis redis.lab.my-domain.com:09669   # trailing comment",
		"http adguard RPi.lab.my-domain.com.:3000",
		"http :80 *.mylab.com",
		"http admin.mylab.com deny",
		"https *.googleapis.com",
		"https ads.googleapis.com deny",
		"tls :443 api.weather.com *:443",
		"tls :443 mqtt 192.168.0.32:8883",
		"tls :6443 x.someapi.com x.someapi.com:443",
		"tls :6553 *.someapi.com",
		"http :8080 api.example.com",
	)
	got := r.format()
	wantLines := []string{
		rulesFileHeader,
		"default ask",
		"http adguard rpi.lab.my-domain.com:3000",
		"http *.mylab.com",
		"http admin.mylab.com deny",
		"https *.googleapis.com",
		"https ads.googleapis.com deny",
		"https api.weather.com",
		"tls :443 mqtt 192.168.0.32:8883",
		"udp :2343 video-stream cam0.lab.my-domain.com:2343",
		"tls :6443 x.someapi.com x.someapi.com:443",
		"tls :6553 *.someapi.com *:6553",
		"http :8080 api.example.com *:8080",
		"tcp :9669 redis redis.lab.my-domain.com:9669",
	}
	if strings.Join(wantLines, "\n")+"\n" != got {
		t.Fatalf("format:\n%s", got)
	}
	again, err := parseRules(strings.NewReader(got), DefaultDeny)
	if err != nil || again.format() != got {
		t.Fatalf("round trip: %v\n%s", err, again.format())
	}
}

func TestBadRules(t *testing.T) {
	for line, want := range map[string]string{
		"allow example.com":                            "want 'default",
		"allow example.com extra":                      "unknown kind",
		"https":                                        "want 'default",
		"https :8443 example.com":                      "https always means port 443",
		"https adguard 192.168.1.3:443":                "https takes no destination; to send adguard elsewhere write tls :443",
		"http 10.0.0.1":                                "is not a domain name",
		"http *example.com":                            "is not a domain name or *.domain",
		"default loose":                                "unknown default policy",
		"tls example.com":                              "want 'default",
		"tls 443 example.com":                          "need a listening port like :6443",
		"tls 127.0.0.1:443 example.com":                "need a listening port like :6443",
		"tls :0 example.com":                           "need a listening port",
		"tls :65536 example.com":                       "need a listening port",
		"tcp :9669 redis":                              "needs a destination HOST:PORT",
		"tcp :9669 redis deny":                         "deny needs a name to match",
		"udp :2344 video deny":                         "deny needs a name to match",
		"tcp :9669 *.redis.lan redis.lan:9669":         "is not a domain name",
		"tcp :9669 redis *:9669":                       "only http and tls rules can use *",
		"http adguard 192.168.1.3":                     "must be HOST:PORT",
		"http adguard rpi.lab.my-domain.com":           "must be HOST:PORT",
		"http adguard 192.168.1.3:http":                "must be HOST:PORT",
		"http adguard 192.168.1.3:0":                   "must be HOST:PORT",
		"http adguard 192.168.1.3:+80":                 "must be HOST:PORT",
		"http adguard rpi..my-domain.com:80":           "must be HOST:PORT",
		"http adguard [::1]:80":                        "only IPv4",
		"http adguard 192.168.1.3:80 extra":            "want 'default",
		"tcp :80 redis redis.lan:6379":                 "kproxy can't add a tcp listener on port 80 because it already has a http listener",
		"udp :53 dns 1.1.1.1:53\nudp :53 x 1.1.1.1:53": "kproxy can't add udp listener x on port 53 because dns already uses it",
		"tls :6443 a.com\nhttp :6443 b.com":            "kproxy can't add a http listener on port 6443 because it already has a tls listener",
		"https a.com\nhttps A.com. deny":               "a.com on tls :443 is already set (line 1)",
	} {
		_, err := parseRules(strings.NewReader(line), DefaultDeny)
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: got %v, want %q", line, err, want)
		}
	}
	// TCP and UDP ports are separate.
	rules(DefaultDeny, "tcp :53 a a.lan:53", "udp :53 b b.lan:53")
}

// TestReadmeExample keeps the README's rules file example valid.
func TestReadmeExample(t *testing.T) {
	b, err := os.ReadFile("README.md")
	if err != nil {
		t.Fatal(err)
	}
	_, after, _ := strings.Cut(string(b), "## Rules\n")
	_, block, _ := strings.Cut(after, "```\n")
	block, _, _ = strings.Cut(block, "```")
	r, err := parseRules(strings.NewReader(block), DefaultAsk)
	if err != nil {
		t.Fatal(err)
	}
	if r.Default != DefaultDeny || len(r.Listeners) != 5 || len(r.warnings()) != 0 {
		t.Fatalf("got %s, warnings %q", r.format(), r.warnings())
	}
}

func TestResolves(t *testing.T) {
	r := rules(DefaultDeny,
		"https *.googleapis.com",
		"https ads.googleapis.com deny",
		"http adguard rpi.lab.my-domain.com:3000",
		"tcp :9669 redis redis.lab.my-domain.com:9669",
	)
	for host, want := range map[string]bool{
		"docs.googleapis.com": true,
		"googleapis.com":      true,
		"ads.googleapis.com":  false,
		"adguard":             true,
		"redis":               true,
		"redsi":               false,
		"example.com":         false,
	} {
		if got := r.resolves(host); got != want {
			t.Errorf("deny: resolves(%q) = %v", host, got)
		}
	}
	// Under ask, a name resolves unless every listener denies it: the deny is
	// only on 443, and port 80 would still ask.
	r.Default = DefaultAsk
	r.add(KindHTTP, httpPort, "ads.googleapis.com", Deny, "", "test")
	r.add(KindHTTP, httpPort, "tracker.com", Deny, "", "test")
	for host, want := range map[string]bool{
		"example.com":        true,
		"ads.googleapis.com": false,
		"tracker.com":        true,
		"printer":            false,
	} {
		if got := r.resolves(host); got != want {
			t.Errorf("ask: resolves(%q) = %v", host, got)
		}
	}
}

func TestWarnings(t *testing.T) {
	r := rules(DefaultDeny,
		"tls :443 api.weather.com break.api.google.com:443",
		"tls :443 api2.weather.com api2.weather.com:443",
		"tls :6443 bad.someapi.com deny",
		"https *.googleapis.com",
	)
	got := r.warnings()
	if len(got) != 2 || !strings.Contains(got[0], "line 1: tls :443 sends api.weather.com to break.api.google.com:443") ||
		!strings.Contains(got[1], "tls :6443 has only denies") {
		t.Fatalf("warnings: %q", got)
	}
	r.Default = DefaultAsk
	if got := r.warnings(); len(got) != 1 {
		t.Fatalf("ask warnings: %q", got)
	}
}

func TestRemoveDropsEmptyListeners(t *testing.T) {
	r := rules(DefaultDeny, "https a.com", "tcp :9669 redis redis.lan:9669")
	if err := r.remove(KindTCP, 9669, "redis"); err != nil {
		t.Fatal(err)
	}
	if err := r.remove(KindTLS, 443, "a.com"); err != nil {
		t.Fatal(err)
	}
	keys := []portKey{}
	for k := range r.Listeners {
		keys = append(keys, k)
	}
	if len(keys) != 2 || !slices.Contains(keys, portKey{false, 80}) || !slices.Contains(keys, portKey{false, 443}) {
		t.Fatalf("listeners left: %v", keys)
	}
	if err := r.remove(KindTLS, 443, "a.com"); err != errNoRule {
		t.Fatalf("second remove: %v", err)
	}
}

func TestRulesFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rules.txt")
	r, err := loadRulesFile(path, DefaultAllow)
	if err != nil || r.Default != DefaultAllow || len(r.Listeners) != 2 {
		t.Fatalf("missing file: %+v, %v", r, err)
	}
	r.add(KindTLS, 443, "example.com", Deny, "", "test")
	r.add(KindHTTP, 80, "nas", Allow, "192.168.1.20:5000", "test")
	if err := writeRulesFile(path, r); err != nil {
		t.Fatal(err)
	}
	got, err := loadRulesFile(path, DefaultDeny)
	if err != nil || got.format() != r.format() {
		t.Fatalf("reload: %v\n%s", err, got.format())
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
