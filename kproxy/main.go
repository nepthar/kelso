// kproxy is a DNS server and transparent HTTP/HTTPS proxy that controls
// outbound connections by domain. Every A query is answered with the proxy's
// own address; connections on 80 and 443 are matched by Host header or TLS
// SNI against allow/deny rules (or a forward to a fixed address) and piped
// upstream without TLS termination. It is administered over a unix socket.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"time"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck(os.Args[2:]))
	}

	ipFlag := flag.String("ip", "", "IPv4 address returned for every A query (required)")
	dnsAddr := flag.String("dns", ":53", "DNS listen address (UDP and TCP)")
	httpAddr := flag.String("http", ":80", "HTTP listen address")
	httpsAddr := flag.String("https", ":443", "HTTPS listen address")
	adminSock := flag.String("admin", "/run/kproxy/admin.sock", "admin unix socket")
	adminHTTP := flag.String("admin-http", "", "also serve the admin API and UI on this TCP address, e.g. 127.0.0.1:18099")
	rulesFile := flag.String("rules-file", "", "load rules from and save them to this file")
	modeFlag := flag.String("mode", "strict", "mode for a rules file that does not name one")
	hold := flag.Duration("hold", 60*time.Second, "how long snitch mode holds an undecided connection")
	maxConns := flag.Int("max-conns", 1024, "most client connections open at once; more wait to be accepted")
	fwd := forwards{}
	flag.Var(fwd, "forward", "send HTTP/HTTPS for NAME to a fixed address, as NAME=IP:PORT (repeatable)")
	flag.Parse()

	ip := net.ParseIP(*ipFlag).To4()
	if ip == nil {
		log.Fatalf("-ip must be an IPv4 address, got %q", *ipFlag)
	}
	mode, err := parseMode(*modeFlag)
	if err != nil {
		log.Fatal(err)
	}

	var persist func(*Rules) error
	var initial *Rules
	if *rulesFile != "" {
		path := *rulesFile
		persist = func(r *Rules) error { return writeRulesFile(path, r) }
		if initial, err = loadRulesFile(path, mode); err != nil {
			log.Fatal(err)
		}
	}
	engine := newEngine(*hold, nil)
	if initial != nil {
		engine.Replace(initial)
	}
	engine.persist = persist

	proxy := &Proxy{
		engine:   engine,
		stats:    newStats(),
		forwards: fwd,
		dial:     (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
		slots:    make(chan struct{}, *maxConns),
	}

	pc, err := net.ListenPacket("udp4", *dnsAddr)
	if err != nil {
		log.Fatal(err)
	}
	dnsTCP := listen("tcp4", *dnsAddr)
	httpLn := listen("tcp4", *httpAddr)
	httpsLn := listen("tcp4", *httpsAddr)
	os.Remove(*adminSock)
	adminLn := listen("unix", *adminSock)
	admin := adminHandler(engine, proxy.stats, fwd)
	if *adminHTTP != "" {
		ln := listen("tcp", *adminHTTP)
		go func() { log.Fatal(http.Serve(ln, browserGuard(admin))) }()
		log.Printf("kproxy: admin UI at http://%s/", ln.Addr())
	}

	self := [4]byte(ip)
	go serveDNSUDP(pc, self)
	go serveDNSTCP(dnsTCP, self)
	go proxy.serve(httpLn, proxy.handleHTTP)
	go proxy.serve(httpsLn, proxy.handleHTTPS)
	log.Printf("kproxy: answering %s, rules loaded: %v", ip, engine.loaded())
	log.Fatal(http.Serve(adminLn, admin))
}

func listen(network, addr string) net.Listener {
	ln, err := net.Listen(network, addr)
	if err != nil {
		log.Fatal(err)
	}
	return ln
}

// healthcheck exits 0 once the proxy on the admin socket has rules loaded.
func healthcheck(args []string) int {
	fs := flag.NewFlagSet("healthcheck", flag.ExitOnError)
	sock := fs.String("admin", "/run/kproxy/admin.sock", "admin unix socket")
	fs.Parse(args)
	client := &http.Client{
		Timeout: 3 * time.Second,
		Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", *sock)
		}},
	}
	resp, err := client.Get("http://kproxy/healthz")
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
