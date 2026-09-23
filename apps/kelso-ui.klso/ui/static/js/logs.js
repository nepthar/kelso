// An app's logs page: poll the tail every two seconds, and follow it only
// while the reader is already at the bottom.
(function () {
  var out = document.getElementById("logs-out");
  if (!out) return;
  var stamp = document.getElementById("logs-stamp");
  var toggle = document.getElementById("logs-auto");
  var url = out.getAttribute("data-src");
  var live = true;

  // Only follow the tail while the reader is already at it. Scrolling up to
  // read something is otherwise undone by the next refresh.
  function atBottom() {
    return out.scrollHeight - out.scrollTop - out.clientHeight < 24;
  }

  function refresh() {
    var pinned = atBottom();
    var keep = out.scrollTop;
    fetch(url, { headers: { "Accept": "application/json" } }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (!live) return;
      if (!res.ok) { stamp.textContent = res.body.error || "unavailable"; return; }
      out.textContent = res.body.text || "";
      out.scrollTop = pinned ? out.scrollHeight : keep;
      stamp.textContent = "updated " + new Date().toLocaleTimeString();
    }).catch(function () {
      if (live) stamp.textContent = "kelsod unreachable";
    });
  }

  toggle.addEventListener("click", function () {
    live = !live;
    toggle.setAttribute("aria-pressed", String(live));
    stamp.textContent = live ? "live" : "paused";
    if (live) refresh();
  });

  out.scrollTop = out.scrollHeight;
  setInterval(function () { if (live) refresh(); }, 2000);
})();
