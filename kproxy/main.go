// kproxy is a DNS server and transparent proxy that controls outbound
// connections. DNS answers the names the rules allow with the address each
// query arrived on. HTTP and TLS listeners match each connection's Host header
// or SNI against the rules; TCP and UDP listeners send everything to one
// destination. Nothing is decrypted. It is administered over a unix socket.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck(os.Args[2:]))
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

	reserved := map[portKey]string{}
	if _, p, err := net.SplitHostPort(*dnsAddr); err == nil && validPort(p) {
		n, _ := strconv.Atoi(p)
		reserved[portKey{false, n}], reserved[portKey{true, n}] = "dns", "dns"
	}
	for _, in := range ingress {
		reserved[portKey{in.udp, in.listen}] = "ingress"
	}
	engine := newEngine(*hold, nil)
	proxy := &Proxy{
		engine:   engine,
		stats:    newStats(),
		dial:     (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
		slots:    make(chan struct{}, *maxConns),
		bind:     *bind,
		addrs:    map[portKey]string{{false, httpPort}: *httpAddr, {false, httpsPort}: *httpsAddr},
		reserved: reserved,
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

	for _, in := range ingress {
		addr := fmt.Sprintf(":%d", in.listen)
		if in.udp {
			upc, err := net.ListenPacket("udp4", addr)
			if err != nil {
				log.Fatal(err)
			}
			go proxy.ingressUDP(upc, in)
		} else {
			go proxy.serve(listen("tcp4", addr), proxy.relayTCP(in))
		}
	}
	go serveDNSUDP(pc, engine.Resolves)
	go serveDNSTCP(dnsTCP, engine.Resolves)
	log.Printf("kproxy: DNS on %s, rules loaded: %v", pc.LocalAddr(), engine.loaded())
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
