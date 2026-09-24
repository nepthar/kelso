// Loaded in <head>, before first paint, so a collapsed nav or a chosen theme
// never flashes the default first. Everything else waits for shell.js.
(function () {
  var root = document.documentElement;
  // The registered theme ids (themes.py), from the data block just above.
  // The first is the default.
  var ids = JSON.parse(document.getElementById("kelso-themes").textContent);
  window.kelso = { themes: ids };
  var theme = ids[0];
  try {
    if (localStorage.getItem("kelso-nav") === "collapsed") root.classList.add("nav-collapsed");
    var saved = localStorage.getItem("kelso-theme");
    // A theme since removed from the registry falls back to the default.
    if (ids.indexOf(saved) >= 0) theme = saved;
  } catch (e) {
    // Storage blocked: the defaults are fine.
  }
  root.dataset.theme = theme;

  // IBM Plex, if the internet is there to serve it. Added from script, not as a
  // <link> in the page, because a stylesheet in <head> holds up rendering until
  // it loads or fails -- and on a network that drops outbound traffic, failing
  // takes a TCP timeout. This way the page draws at once in the fallback faces
  // kelso.css names, and swaps to Plex if and when it arrives.
  var fonts = document.createElement("link");
  fonts.rel = "stylesheet";
  fonts.href = "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500" +
    "&family=IBM+Plex+Sans:wght@400;500;600&display=swap";
  document.head.appendChild(fonts);
})();
