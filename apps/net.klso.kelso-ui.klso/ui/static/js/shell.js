// What every window's page shares: dialogs, the colour helper for what is
// drawn outside CSS, and timestamps in the viewer's own zone.
(function () {
  var root = document.documentElement;

  // A link marked data-dialog opens as a dialog window over this one, and a
  // data-close control closes the dialog it is in. A page opened on its own
  // has no desktop: the link just navigates, and closing is going back.
  var desktop = window.parent !== window && window.parent.kelsoDesktop;
  document.addEventListener("click", function (event) {
    if (!desktop) {
      if (event.target.closest("[data-close]")) history.back();
      return;
    }
    var link = event.target.closest("a[data-dialog]");
    if (link) {
      event.preventDefault();
      desktop.dialog(link.getAttribute("href"), link.getAttribute("data-dialog"), window);
    } else if (event.target.closest("[data-close]")) {
      desktop.close(window);
    }
  });

  // A token's colour as rgba(), for what is drawn outside CSS (the terminal).
  // Drawn to a pixel because computed style reports color-mix() as
  // color(srgb ...), which xterm does not read.
  var pixel = null;
  function color(name) {
    var probe = document.createElement("span");
    probe.style.color = getComputedStyle(root).getPropertyValue(name).trim();
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
  window.kelso.color = color;

  // Timestamps ship as UTC in `datetime`; only the browser knows the viewer's
  // zone, so the friendly text is filled in here. Absolute local time stays on
  // the tooltip, and the ISO fallback survives with no JS.
  function relTime(then, now) {
    var future = then > now;
    var secs = Math.round(Math.abs(now - then) / 1000);
    if (secs < 45) return future ? "in a moment" : "just now";
    var span;
    var mins = Math.round(secs / 60);
    var hours = Math.round(mins / 60);
    var days = Math.round(hours / 24);
    if (mins < 60) span = mins + "m";
    else if (hours < 24) span = hours + "h";
    else if (days < 30) span = days + "d";
    else return then.toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "numeric" });
    return future ? "in " + span : span + " ago";
  }
  document.querySelectorAll("time[datetime]").forEach(function (el) {
    var then = new Date(el.getAttribute("datetime"));
    if (isNaN(then.getTime())) return;
    el.textContent = relTime(then, new Date());
    el.title = then.toLocaleString();
  });

  // A timeline page (cron) opens with its "now" line mid-screen.
  var cursor = document.querySelector(".now-cursor");
  if (cursor) cursor.scrollIntoView({ block: "center" });
})();
