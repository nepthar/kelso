// kproxy is a DNS server and transparent HTTP/HTTPS proxy that controls
// outbound connections by domain. Every A query is answered with the address
// it arrived on; connections on 80 and 443 are matched by Host header or TLS
// SNI against allow/deny rules and piped upstream, to what DNS says or to an
// allow rule's fixed target, without TLS termination. It is administered over
// a unix socket.
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

	dnsAddr := flag.String("dns", ":53", "DNS listen address (UDP and TCP)")
	httpAddr := flag.String("http", ":80", "HTTP listen address")
	httpsAddr := flag.String("https", ":443", "HTTPS listen address")
	adminSock := flag.String("admin", "/run/kproxy/admin.sock", "admin unix socket")
	adminHTTP := flag.String("admin-http", "", "also serve the admin API and UI on this TCP address, e.g. 127.0.0.1:18099")
	rulesFile := flag.String("rules-file", "", "load rules from and save them to this file")
	defaultFlag := flag.String("default", "deny", "default policy (allow, deny or ask) for a rules file that doesn't set one")
	hold := flag.Duration("hold", 60*time.Second, "how long the ask policy holds a connection no rule matches")
	maxConns := flag.Int("max-conns", 1024, "most client connections open at once; more wait to be accepted")
	var ingress ingresses
	flag.Var(&ingress, "ingress", "relay LISTEN=HOST:PORT[/udp]: connections on port LISTEN go to HOST:PORT, unchecked (repeatable)")
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
	engine := newEngine(*hold, nil)
	if initial != nil {
		engine.Replace(initial)
	}
	engine.persist = persist

	proxy := &Proxy{
		engine: engine,
		stats:  newStats(),
		dial:   (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
		slots:  make(chan struct{}, *maxConns),
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
	admin := adminHandler(engine, proxy.stats)
	if *adminHTTP != "" {
		ln := listen("tcp", *adminHTTP)
		go func() { log.Fatal(http.Serve(ln, browserGuard(admin))) }()
		log.Printf("kproxy: admin UI at http://%s/", ln.Addr())
	}

	for _, in := range ingress {
		addr := fmt.Sprintf(":%d", in.listen)
		if in.udp {
			upc, err := net.ListenPacket("udp4", addr)
			if err != nil {
				log.Fatal(err)
			}
			go proxy.relayUDP(upc, in)
		} else {
			go proxy.serve(listen("tcp4", addr), proxy.relayTCP(in))
		}
	}
	go serveDNSUDP(pc)
	go serveDNSTCP(dnsTCP)
	go proxy.serve(httpLn, proxy.handleHTTP)
	go proxy.serve(httpsLn, proxy.handleHTTPS)
	log.Printf("kproxy: DNS on %s, HTTP on %s, HTTPS on %s, rules loaded: %v",
		pc.LocalAddr(), httpLn.Addr(), httpsLn.Addr(), engine.loaded())
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
