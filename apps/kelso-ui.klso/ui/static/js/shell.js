// The frame every page shares: the nav toggle, the theme button, and
// timestamps rewritten into the viewer's own zone.
(function () {
  var root = document.documentElement;

  function store(key, value) {
    try {
      if (value) localStorage.setItem(key, value);
      else localStorage.removeItem(key);
    } catch (e) {}
  }

  var toggle = document.querySelector(".nav-toggle");
  function syncNav() {
    var on = root.classList.contains("nav-collapsed");
    toggle.setAttribute("aria-label", on ? "Expand sidebar" : "Collapse sidebar");
    toggle.textContent = on ? "›" : "‹";
  }
  if (toggle) {
    syncNav();
    toggle.addEventListener("click", function () {
      store("kelso-nav", root.classList.toggle("nav-collapsed") ? "collapsed" : "");
      syncNav();
    });
  }

  var themeBtn = document.querySelector(".nav-theme");
  var themes = window.kelso.themes;
  function current() {
    var id = root.dataset.theme || "";
    for (var i = 0; i < themes.length; i++) if (themes[i].id === id) return i;
    return 0;
  }
  if (themeBtn) {
    themeBtn.title = themes[current()].name;
    themeBtn.addEventListener("click", function () {
      var next = themes[(current() + 1) % themes.length];
      if (next.id) root.dataset.theme = next.id;
      else delete root.dataset.theme;
      store("kelso-theme", next.id);
      themeBtn.title = next.name;
    });
  }

  // Timestamps ship as UTC in `datetime`; only the browser knows the viewer's
  // zone, so the friendly text is filled in here. Absolute local time stays on
  // the tooltip, and the ISO fallback survives with no JS.
  function relTime(then, now) {
    var secs = Math.round((now - then) / 1000);
    if (secs < 45) return "just now";
    var mins = Math.round(secs / 60);
    if (mins < 60) return mins + "m ago";
    var hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    var days = Math.round(hours / 24);
    if (days < 30) return days + "d ago";
    return then.toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "numeric" });
  }
  document.querySelectorAll("time[datetime]").forEach(function (el) {
    var then = new Date(el.getAttribute("datetime"));
    if (isNaN(then.getTime())) return;
    el.textContent = relTime(then, new Date());
    el.title = then.toLocaleString();
  });
})();
