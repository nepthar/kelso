package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"sort"
)

// browserGuard protects a TCP admin listener from other sites in the same
// browser: a foreign Host header means DNS rebinding, and requiring a JSON
// content type on writes forces a CORS preflight, which is never answered.
func browserGuard(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.Host)
		if err != nil {
			host = r.Host
		}
		if host != "localhost" && net.ParseIP(host) == nil {
			http.Error(w, "use an IP address or localhost", http.StatusForbidden)
			return
		}
		if r.Method != http.MethodGet && r.Header.Get("Content-Type") != "application/json" {
			http.Error(w, "Content-Type must be application/json", http.StatusUnsupportedMediaType)
			return
		}
		h.ServeHTTP(w, r)
	})
}

//go:embed ui.html
var uiHTML []byte

type ruleJSON struct {
	Domain  string  `json:"domain"`
	Verdict Verdict `json:"verdict"`
	Target  string  `json:"target,omitempty"`
}

type rulesJSON struct {
	Default Policy     `json:"default"`
	Rules   []ruleJSON `json:"rules"`
}

func adminHandler(e *Engine, s *Stats) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(uiHTML)
	})

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if !e.loaded() {
			http.Error(w, "rules not loaded", http.StatusServiceUnavailable)
			return
		}
		fmt.Fprintln(w, "ok")
	})

	mux.HandleFunc("GET /pending", func(w http.ResponseWriter, r *http.Request) {
		ps := e.Pending()
		sort.Slice(ps, func(i, j int) bool { return ps[i].FirstSeen.Before(ps[j].FirstSeen) })
		writeJSON(w, ps)
	})

	mux.HandleFunc("POST /decide", func(w http.ResponseWriter, r *http.Request) {
		var req ruleJSON
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		d, err := ruleName(req.Domain)
		if err == nil {
			_, err = parseVerdict(string(req.Verdict))
		}
		if err == nil && req.Target != "" {
			err = fmt.Errorf("decide sets a verdict only: send a target with PUT /rules")
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := e.Decide(d, req.Verdict); err != nil {
			http.Error(w, err.Error(), http.StatusConflict)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})

	mux.HandleFunc("PUT /rules", func(w http.ResponseWriter, r *http.Request) {
		var req rulesJSON
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		rules, err := req.toRules()
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if err := e.Replace(rules); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})

	mux.HandleFunc("GET /rules", func(w http.ResponseWriter, r *http.Request) {
		rules := e.rules()
		if rules == nil {
			http.Error(w, "rules not loaded", http.StatusServiceUnavailable)
			return
		}
		if r.URL.Query().Get("format") == "text" {
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			w.Header().Set("Content-Disposition", `attachment; filename="rules.txt"`)
			fmt.Fprint(w, rules.format())
			return
		}
		out := rulesJSON{Default: rules.Default, Rules: []ruleJSON{}}
		for _, d := range rules.sorted() {
			rule := rules.Domains[d]
			out.Rules = append(out.Rules, ruleJSON{d, rule.Verdict, rule.Target})
		}
		writeJSON(w, out)
	})

	mux.HandleFunc("DELETE /rules/{name}", func(w http.ResponseWriter, r *http.Request) {
		name, err := ruleName(r.PathValue("name"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		switch err := e.Remove(name); {
		case errors.Is(err, errNoRule):
			http.Error(w, err.Error(), http.StatusNotFound)
		case err != nil:
			http.Error(w, err.Error(), http.StatusConflict)
		default:
			w.WriteHeader(http.StatusNoContent)
		}
	})

	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, s.Snapshot())
	})

	return mux
}

func (rj rulesJSON) toRules() (*Rules, error) {
	m, err := parsePolicy(string(rj.Default))
	if err != nil {
		return nil, err
	}
	rules := &Rules{Default: m, Domains: map[string]Rule{}}
	for _, r := range rj.Rules {
		d, err := ruleName(r.Domain)
		if err != nil {
			return nil, err
		}
		rule, err := newRule(string(r.Verdict), r.Target)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", d, err)
		}
		rules.Domains[d] = rule
	}
	return rules, nil
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
