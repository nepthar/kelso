package main

import (
	"bytes"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAdminCommand(t *testing.T) {
	// A short path: unix socket paths are limited to about 104 bytes on macOS.
	dir, err := os.MkdirTemp("", "kp")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	sock := filepath.Join(dir, "a.sock")
	ln, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	e := newEngine(time.Second, nil)
	e.Replace(rules(DefaultDeny, "https *.example.com"))
	go http.Serve(ln, adminHandler(e, newStats()))
	defer ln.Close()

	run := func(args ...string) (int, string, string) {
		var out, errOut bytes.Buffer
		code := adminCommand(append([]string{"-admin", sock}, args...), &out, &errOut)
		return code, out.String(), errOut.String()
	}

	if code, out, _ := run("GET", "/rules"); code != 0 || !strings.Contains(out, `"*.example.com"`) {
		t.Fatalf("GET /rules: %d %q", code, out)
	}
	decide := `{"kind":"tls","port":443,"name":"*.weather.com","verdict":"allow"}`
	if code, _, errOut := run("post", "/decide", decide); code != 0 {
		t.Fatalf("POST /decide: %d %q", code, errOut)
	}
	if code, out, _ := run("GET", "/rules?format=text"); code != 0 || !strings.Contains(out, "https *.weather.com") {
		t.Fatalf("decision not applied: %q", out)
	}
	if code, out, errOut := run("POST", "/decide", "{nope"); code != 1 || out != "" || !strings.Contains(errOut, "400") {
		t.Fatalf("bad body: %d %q %q", code, out, errOut)
	}
	if code, _, _ := run("GET", "rules"); code != 2 {
		t.Fatalf("path without a slash: %d", code)
	}
	missing := []string{"-admin", filepath.Join(dir, "none.sock"), "GET", "/rules"}
	if code := adminCommand(missing, &bytes.Buffer{}, &bytes.Buffer{}); code != 1 {
		t.Fatalf("missing socket: %d", code)
	}
}

func TestWriteStatsFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "stats.json")
	s := newStats()
	s.conn("example.com", "https").record(Allowed, false)
	if err := writeStatsFile(path, s, newEngine(time.Second, nil)); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(path)
	if !strings.Contains(string(b), `"example.com":{"https":{"conns":1`) || !strings.Contains(string(b), `"pending":[]`) {
		t.Fatalf("stats file: %s", b)
	}
}
