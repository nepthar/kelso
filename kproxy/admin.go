package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"sort"
	"strconv"
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

// ruleJSON is one rule as the admin API sends it: the long form of a rules
// file line, with no shorthands.
type ruleJSON struct {
	Kind    Kind    `json:"kind"`
	Port    int     `json:"port"`
	Name    string  `json:"name"`
	Verdict Verdict `json:"verdict"`
	Dest    string  `json:"dest,omitempty"`
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
		_, err := parseVerdict(string(req.Verdict))
		if err == nil && req.Dest != "" {
			err = fmt.Errorf("decide sets a verdict only: send a destination with PUT /rules")
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		switch err := e.Decide(req.Kind, req.Port, req.Name, req.Verdict); {
		case errors.Is(err, errNotLoaded):
			http.Error(w, err.Error(), http.StatusConflict)
		case err != nil:
			http.Error(w, err.Error(), http.StatusBadRequest)
		default:
			w.WriteHeader(http.StatusNoContent)
		}
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
			http.Error(w, err.Error(), http.StatusConflict)
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
		rules.each(func(l *Listener, rule Rule) {
			out.Rules = append(out.Rules, ruleJSON{l.Kind, l.Port, rule.Name, rule.Verdict, rule.Dest})
		})
		writeJSON(w, out)
	})

	mux.HandleFunc("DELETE /rules/{kind}/{port}/{name}", func(w http.ResponseWriter, r *http.Request) {
		kind, err := parseKind(r.PathValue("kind"))
		port, perr := strconv.Atoi(r.PathValue("port"))
		if err == nil && perr != nil {
			err = fmt.Errorf("port %q is not a number", r.PathValue("port"))
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		switch err := e.Remove(kind, port, r.PathValue("name")); {
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
	rules := newRules(m)
	for i, r := range rj.Rules {
		at := fmt.Sprintf("rule %d", i+1)
		kind, err := parseKind(string(r.Kind))
		if err == nil {
			err = rules.add(kind, r.Port, r.Name, r.Verdict, r.Dest, at)
		}
		if err != nil {
			return nil, fmt.Errorf("%s: %w", at, err)
		}
	}
	return rules, nil
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
