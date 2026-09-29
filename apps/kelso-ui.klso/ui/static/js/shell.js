// The frame every page shares: the nav toggle, the theme menu and the
// helpers that follow the theme, and timestamps in the viewer's own zone.
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

  // --- theme -------------------------------------------------------------
  // What other scripts use to follow the theme: the current value of a token,
  // the same colour at some opacity, and an event when the theme changes.
  // Anything drawn in token colours outside CSS -- a chart, a terminal --
  // reads through these and redraws on `kelso:themechange`.
  function token(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
  }
  // A token as rgba(), resolved by the browser so a theme may write any CSS
  // colour. Drawn to a pixel because computed style reports color-mix() as
  // color(srgb ...), which neither uPlot nor xterm reads.
  var pixel = null;
  function color(name) {
    var probe = document.createElement("span");
    probe.style.color = token(name);
    document.body.appendChild(probe);
    var computed = getComputedStyle(probe).color;
    probe.remove();
    pixel = pixel || document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    pixel.clearRect(0, 0, 1, 1);
    pixel.fillStyle = computed;
    pixel.fillRect(0, 0, 1, 1);
    var d = pixel.getImageData(0, 0, 1, 1).data;
    return "rgba(" + d[0] + "," + d[1] + "," + d[2] + "," + +(d[3] / 255).toFixed(3) + ")";
  }
  function rgba(name, alpha) {
    return color(name).replace(/,[\d.]+\)$/, "," + alpha + ")");
  }
  function setTheme(id) {
    if (window.kelso.themes.indexOf(id) < 0 || id === root.dataset.theme) return;
    root.dataset.theme = id;
    store("kelso-theme", id === window.kelso.themes[0] ? "" : id);
    syncMenu();
    document.dispatchEvent(new CustomEvent("kelso:themechange", { detail: { theme: id } }));
  }
  window.kelso.token = token;
  window.kelso.color = color;
  window.kelso.rgba = rgba;
  window.kelso.setTheme = setTheme;

  var themeBtn = document.querySelector(".nav-theme");
  var menu = document.getElementById("theme-menu");
  var items = menu ? menu.querySelectorAll("[data-theme-id]") : [];
  function syncMenu() {
    var current = root.dataset.theme;
    items.forEach(function (item) {
      var on = item.getAttribute("data-theme-id") === current;
      item.setAttribute("aria-checked", String(on));
      if (on && themeBtn) themeBtn.title = "theme: " + item.textContent.trim();
    });
  }
  function openMenu(open) {
    menu.hidden = !open;
    themeBtn.setAttribute("aria-expanded", String(open));
    if (!open) return;
    // Beside the nav, bottom-aligned with the button. Fixed rather than
    // inside the nav, whose overflow would clip it. Runtime geometry is the
    // one thing set as a style from script; everything else is in kelso.css.
    var nav = themeBtn.closest("nav").getBoundingClientRect();
    var btn = themeBtn.getBoundingClientRect();
    menu.style.left = (nav.right + 8) + "px";
    menu.style.bottom = Math.max(8, window.innerHeight - btn.bottom) + "px";
    var checked = menu.querySelector('[aria-checked="true"]') || items[0];
    if (checked) checked.focus();
  }
  if (themeBtn && menu) {
    syncMenu();
    themeBtn.addEventListener("click", function () { openMenu(menu.hidden); });
    items.forEach(function (item) {
      item.addEventListener("click", function () {
        setTheme(item.getAttribute("data-theme-id"));
        openMenu(false);
        themeBtn.focus();
      });
    });
    menu.addEventListener("keydown", function (event) {
      var list = Array.prototype.slice.call(items);
      var at = list.indexOf(document.activeElement);
      if (event.key === "Escape") { openMenu(false); themeBtn.focus(); }
      else if (event.key === "ArrowDown") { list[(at + 1) % list.length].focus(); }
      else if (event.key === "ArrowUp") { list[(at - 1 + list.length) % list.length].focus(); }
      else return;
      event.preventDefault();
    });
    document.addEventListener("click", function (event) {
      if (!menu.hidden && !menu.contains(event.target) && !themeBtn.contains(event.target)) {
        openMenu(false);
      }
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
