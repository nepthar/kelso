// An app's Network group: its domains, polled every 3 seconds, and the allow,
// deny and forget clicks held until Save. A held click is dropped when a poll
// shows the saved decision changed under it (the last save wins) or already
// matches it.
(function () {
  var box = document.querySelector("[data-net-src]");
  if (!box) return;
  var url = box.getAttribute("data-net-src");
  var mode = box.getAttribute("data-net-mode");
  var rowsEl = box.querySelector("[data-net-rows]");
  var stamp = box.querySelector("[data-net-stamp]");
  var actions = box.querySelector(".net-actions");
  var save = box.querySelector("[data-net-save]");
  var buttons = box.querySelector("[data-net-buttons]");
  var initial = document.getElementById("net-data");

  var DEFAULT = { ask: "asks you", restrict: "refused", watch: "allowed" };
  var PAST = { allow: "allowed", deny: "denied" };
  var view = initial ? JSON.parse(initial.textContent) : null;
  // domain -> {verdict, base, name}: what the click asks for, the saved
  // verdict it was made against, and the name the decision is saved under.
  var held = {};
  var drawn = "";
  var busy = false;

  function size(n) {
    var units = ["B", "KB", "MB", "GB", "TB"];
    var v = n || 0;
    for (var i = 0; i < units.length; i++) {
      if (v < 1024) return v.toFixed(1) + " " + units[i];
      v /= 1024;
    }
    return v.toFixed(1) + " PB";
  }

  function when(ts) {
    if (!ts) return "—";
    var d = new Date(ts);
    return isNaN(d) ? ts : d.toLocaleString();
  }

  function rule(row) {
    if (row.verdict) {
      var t = PAST[row.verdict] + " by you";
      return row.decided_as !== row.domain ? t + " (" + row.decided_as + ")" : t;
    }
    if (row.waiting) return "waiting for you";
    if (row.listed) return "listed (" + row.listed + ")";
    return DEFAULT[mode] || "";
  }

  function effective(row) {
    var h = held[row.domain];
    return h ? h.verdict : row.verdict;
  }

  function cell(tr, text, cls) {
    var td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    tr.appendChild(td);
    return td;
  }

  function sub(td, text, cls) {
    var span = document.createElement("span");
    span.className = "sub" + (cls ? " " + cls : "");
    span.textContent = text;
    td.appendChild(span);
  }

  function forgetTitle(row) {
    var h = held[row.domain];
    if (h && h.verdict && !row.verdict) return "Drop the unsaved " + h.verdict;
    if (!row.verdict) return "Nothing to forget";
    var back = row.listed ? "the manifest's " + row.listed : DEFAULT[mode];
    return "Forget your " + row.verdict + " of " + row.decided_as + "; back to " + back;
  }

  function actionCell(tr, row) {
    var td = cell(tr, "", "act");
    td.appendChild(buttons.content.cloneNode(true));
    var b = td.querySelectorAll("button");
    var now = effective(row);
    [["allow", b[0]], ["deny", b[1]]].forEach(function (pair) {
      var btn = pair[1];
      btn.classList.add("toggle");
      btn.setAttribute("aria-pressed", String(now === pair[0]));
      var label = (pair[0] === "allow" ? "Allow " : "Deny ") + row.domain;
      btn.title = label;
      btn.setAttribute("aria-label", label);
      btn.addEventListener("click", function () { pick(row, pair[0]); });
    });
    var forget = b[2];
    forget.disabled = !now;
    forget.title = forgetTitle(row);
    forget.setAttribute("aria-label", forget.title);
    forget.addEventListener("click", function () { pick(row, null); });
  }

  function pick(row, verdict) {
    var h = held[row.domain];
    if (verdict === null && h && h.verdict && !row.verdict) {
      delete held[row.domain];
    } else if (verdict === row.verdict) {
      delete held[row.domain];
    } else {
      var name = verdict === null ? row.decided_as : row.domain;
      held[row.domain] = { verdict: verdict, base: row.verdict, name: name };
    }
    draw(true);
  }

  function draw(force) {
    var key = JSON.stringify([view, held]);
    if (!force && key === drawn) return;
    drawn = key;
    rowsEl.textContent = "";
    var rows = view ? view.rows : [];
    if (!rows.length) {
      var tr = document.createElement("tr");
      var td = cell(tr, "Nothing yet.", "muted");
      td.colSpan = 8;
      rowsEl.appendChild(tr);
    }
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      var h = held[row.domain];
      if (h) tr.className = "net-held";
      var name = cell(tr, row.domain, "key mono");
      if (row.waiting && row.hosts.length) sub(name, row.hosts.join(", "));
      var r = cell(tr, rule(row), row.waiting && !row.verdict ? "warnish" : "");
      if (row.verdict && row.listed) sub(r, "over the manifest's " + row.listed);
      if (h) sub(r, "unsaved: " + (h.verdict || "forget " + h.name), "warnish");
      cell(tr, String(row.conns), "muted");
      cell(tr, String(row.refused), "muted");
      cell(tr, size(row.bytes_in), "muted");
      cell(tr, size(row.bytes_out), "muted");
      cell(tr, when(row.last_seen), "muted");
      actionCell(tr, row);
      rowsEl.appendChild(tr);
    });
    actions.hidden = !Object.keys(held).length;
  }

  function note() {
    if (!view) return "";
    if (view.source === "live") return "Live, updated " + new Date().toLocaleTimeString();
    if (view.source === "last_run") return "Not running. Traffic from its last run.";
    return "Not running, and nothing counted yet.";
  }

  // Keep a held click only while it still differs from, and was made against,
  // what is saved now.
  function settle() {
    var saved = {};
    view.rows.forEach(function (row) { saved[row.domain] = row.verdict; });
    Object.keys(held).forEach(function (domain) {
      var now = saved.hasOwnProperty(domain) ? saved[domain] : null;
      var h = held[domain];
      if (now !== h.base || now === h.verdict) delete held[domain];
    });
  }

  function accept(body) {
    view = body;
    settle();
    draw(false);
    stamp.textContent = note();
  }

  function answer(r) {
    return r.json().then(function (body) { return { ok: r.ok, body: body }; });
  }

  function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    fetch(url, { headers: { "Accept": "application/json" } }).then(answer).then(function (res) {
      if (!res.ok) { stamp.textContent = res.body.error || "unavailable"; return; }
      accept(res.body);
    }).catch(function () {
      stamp.textContent = "kelsod unreachable";
    }).then(function () { busy = false; });
  }

  save.addEventListener("click", function () {
    var decisions = {};
    var sent = Object.keys(held);
    sent.forEach(function (domain) { decisions[held[domain].name] = held[domain].verdict; });
    save.disabled = true;
    busy = true;
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decisions: decisions })
    }).then(answer).then(function (res) {
      if (!res.ok) { stamp.textContent = res.body.error || "not saved"; return; }
      sent.forEach(function (domain) { delete held[domain]; });
      accept(res.body);
    }).catch(function () {
      stamp.textContent = "kelsod unreachable; not saved";
    }).then(function () { busy = false; save.disabled = false; });
  });

  box.querySelector("[data-net-cancel]").addEventListener("click", function () {
    held = {};
    draw(true);
  });

  if (view) { draw(true); stamp.textContent = note(); }
  setInterval(refresh, 3000);
})();
