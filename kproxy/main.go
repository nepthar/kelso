// kproxy is a DNS server and transparent proxy that controls outbound
// connections. DNS answers the names the rules allow with the address each
// query arrived on. HTTP and TLS listeners match each connection's Host header
// or SNI against the rules; TCP and UDP listeners send everything to one
// destination, outbound or, for ingress, in to the app. Nothing is decrypted.
// It is administered over a unix socket.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func main() {
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "healthcheck":
			os.Exit(healthcheck(os.Args[2:]))
		case "admin":
			os.Exit(adminCommand(os.Args[2:], os.Stdout, os.Stderr))
		}
	}

	dnsAddr := flag.String("dns", ":53", "DNS listen address (UDP and TCP)")
	httpAddr := flag.String("http", ":80", "listen address for the http listener on port 80")
	httpsAddr := flag.String("https", ":443", "listen address for the tls listener on port 443")
	bind := flag.String("bind", "", "address every other listener binds to (default every address)")
	adminSock := flag.String("admin", "/run/kproxy/admin.sock", "admin unix socket")
	adminHTTP := flag.String("admin-http", "", "also serve the admin API and UI on this TCP address, e.g. 127.0.0.1:18099")
	rulesFile := flag.String("rules-file", "", "load rules from and save them to this file")
	defaultFlag := flag.String("default", "deny", "default policy (allow, deny or ask) for a rules file that doesn't set one")
	hold := flag.Duration("hold", 60*time.Second, "how long the ask policy holds a connection no rule matches")
	maxConns := flag.Int("max-conns", 1024, "most client connections open at once; more wait to be accepted")
	idle := flag.Duration("idle", 10*time.Minute, "close a connection after this long with no bytes either way; 0 for never")
	statsFile := flag.String("stats-file", "", "on SIGTERM or SIGINT, write the stats and pending hosts to this file, then exit")
	flag.Parse()

	policy, err := parsePolicy(*defaultFlag)
	if err != nil {
		log.Fatal(err)
	}

	var persist func(*Rules) error
	var initial *Rules
	if *rulesFile != "" {
		path := *rulesFile
		persist = func(r *Rules) error { return writeRulesFile(path, r) }
		if initial, err = loadRulesFile(path, policy); err != nil {
			log.Fatal(err)
		}
	}

	reserved := map[portKey]string{}
	if _, p, err := net.SplitHostPort(*dnsAddr); err == nil && validPort(p) {
		n, _ := strconv.Atoi(p)
		reserved[portKey{false, n}], reserved[portKey{true, n}] = "dns", "dns"
	}
	engine := newEngine(*hold, nil)
	proxy := &Proxy{
		engine:     engine,
		stats:      newStats(),
		dial:       (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
		dialPublic: (&net.Dialer{Timeout: 10 * time.Second, Control: publicOnly}).DialContext,
		slots:      make(chan struct{}, *maxConns),
		idle:       *idle,
		bind:       *bind,
		addrs:      map[portKey]string{{false, httpPort}: *httpAddr, {false, httpsPort}: *httpsAddr},
		reserved:   reserved,
	}
	engine.apply = proxy.apply
	if err := proxy.apply(implicitRules); err != nil {
		log.Fatal(err)
	}
	if initial != nil {
		if err := engine.Replace(initial); err != nil {
			log.Fatal(err)
		}
	}
	engine.persist = persist

	pc, err := net.ListenPacket("udp4", *dnsAddr)
	if err != nil {
		log.Fatal(err)
	}
	dnsTCP := listen("tcp4", *dnsAddr)
	os.Remove(*adminSock)
	adminLn := listen("unix", *adminSock)
	admin := adminHandler(engine, proxy.stats)
	if *adminHTTP != "" {
		ln := listen("tcp", *adminHTTP)
		go func() { log.Fatal(http.Serve(ln, browserGuard(admin))) }()
		log.Printf("kproxy: admin UI at http://%s/", ln.Addr())
	}

	if *statsFile != "" {
		sig := make(chan os.Signal, 1)
		signal.Notify(sig, syscall.SIGTERM, os.Interrupt)
		go func() {
			<-sig
			if err := writeStatsFile(*statsFile, proxy.stats, engine); err != nil {
				log.Printf("kproxy: stats file: %v", err)
			}
			os.Exit(0)
		}()
	}

	go serveDNSUDP(pc, engine.Resolves)
	go serveDNSTCP(dnsTCP, engine.Resolves)
	log.Printf("kproxy: DNS on %s, rules loaded: %v", pc.LocalAddr(), engine.loaded())
	log.Fatal(http.Serve(adminLn, admin))
}

// writeStatsFile writes {"stats": GET /stats, "pending": GET /pending}.
func writeStatsFile(path string, s *Stats, e *Engine) error {
	b, err := json.Marshal(map[string]any{"stats": s.Snapshot(), "pending": e.Pending()})
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o644)
}

func listen(network, addr string) net.Listener {
	ln, err := net.Listen(network, addr)
	if err != nil {
		log.Fatal(err)
	}
	return ln
}

func adminClient(sock string) *http.Client {
	return &http.Client{
		Timeout: 3 * time.Second,
		Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", sock)
		}},
	}
}

// healthcheck exits 0 once the proxy on the admin socket has rules loaded.
func healthcheck(args []string) int {
	fs := flag.NewFlagSet("healthcheck", flag.ExitOnError)
	sock := fs.String("admin", "/run/kproxy/admin.sock", "admin unix socket")
	fs.Parse(args)
	resp, err := adminClient(*sock).Get("http://kproxy/healthz")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintln(os.Stderr, resp.Status)
		return 1
	}
	return 0
}

// adminCommand is `kproxy admin [-admin SOCK] METHOD PATH [BODY]`: one admin API
// request, for a client that can run kproxy but not reach its socket. The
// response body goes to stdout; a status other than 2xx exits 1 with it on stderr.
func adminCommand(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("admin", flag.ContinueOnError)
	fs.SetOutput(stderr)
	sock := fs.String("admin", "/run/kproxy/admin.sock", "admin unix socket")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	rest := fs.Args()
	if len(rest) < 2 || len(rest) > 3 || !strings.HasPrefix(rest[1], "/") {
		fmt.Fprintln(stderr, "usage: kproxy admin [-admin SOCK] METHOD /PATH [JSON-BODY]")
		return 2
	}
	var body io.Reader
	if len(rest) == 3 {
		body = strings.NewReader(rest[2])
	}
	req, err := http.NewRequest(strings.ToUpper(rest[0]), "http://kproxy"+rest[1], body)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := adminClient(*sock).Do(req)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	defer resp.Body.Close()
	out := stdout
	if resp.StatusCode/100 != 2 {
		out = stderr
		fmt.Fprintln(stderr, resp.Status)
	}
	io.Copy(out, resp.Body)
	if out == stderr {
		return 1
	}
	return 0
}
