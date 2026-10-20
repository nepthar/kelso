package main

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
)

//go:embed ui.html
var uiHTML []byte

type ruleJSON struct {
	Domain  string  `json:"domain"`
	Verdict Verdict `json:"verdict"`
}

type rulesJSON struct {
	Mode  Mode       `json:"mode"`
	Rules []ruleJSON `json:"rules"`
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
		d, err := ruleDomain(req.Domain)
		if err == nil {
			_, err = parseVerdict(string(req.Verdict))
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
		out := rulesJSON{Mode: rules.Mode, Rules: []ruleJSON{}}
		for _, d := range rules.sorted() {
			out.Rules = append(out.Rules, ruleJSON{d, rules.Domains[d]})
		}
		writeJSON(w, out)
	})

	mux.HandleFunc("GET /stats", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, s.Snapshot())
	})

	return mux
}

func (rj rulesJSON) toRules() (*Rules, error) {
	m, err := parseMode(string(rj.Mode))
	if err != nil {
		return nil, err
	}
	rules := &Rules{Mode: m, Domains: map[string]Verdict{}}
	for _, r := range rj.Rules {
		d, err := ruleDomain(r.Domain)
		if err != nil {
			return nil, err
		}
		v, err := parseVerdict(string(r.Verdict))
		if err != nil {
			return nil, err
		}
		rules.Domains[d] = v
	}
	return rules, nil
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}
